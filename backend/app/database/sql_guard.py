from __future__ import annotations

import re
from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from app.schemas.query_plan import QueryPlan, ranking_requires_row_limit


@dataclass(frozen=True, slots=True)
class SqlValidationResult:
    safe: bool
    sql: str | None
    errors: list[str]
    tables: list[str]


@dataclass(frozen=True, slots=True)
class SqlComplexityLimits:
    """Deterministic query-shape budgets enforced before database execution."""

    max_joins: int = 6
    max_ctes: int = 6
    max_subqueries: int = 12
    max_subquery_depth: int = 3
    require_detail_time_range: bool = True


class ReadOnlySqlGuard:
    _FORBIDDEN_FUNCTIONS = {
        "dblink",
        "lo_export",
        "lo_import",
        "pg_ls_dir",
        "pg_read_binary_file",
        "pg_read_file",
        "pg_sleep",
        "pg_stat_file",
        "set_config",
    }

    def __init__(
        self,
        *,
        table_columns: dict[str, list[str]],
        company_id: int,
        max_rows: int,
        complexity: SqlComplexityLimits | None = None,
    ) -> None:
        self._table_columns = {
            table: set(columns) for table, columns in table_columns.items()
        }
        self._company_id = company_id
        self._max_rows = max_rows
        self._complexity = complexity or SqlComplexityLimits()

    def validate(
        self,
        sql: str,
        *,
        plan: QueryPlan | None = None,
        question: str = "",
    ) -> SqlValidationResult:
        errors: list[str] = []
        try:
            statements = parse(sql.strip(), read="postgres")
        except ParseError:
            return SqlValidationResult(False, None, ["SQL 语法无法解析。"], [])

        if len(statements) != 1:
            return SqlValidationResult(False, None, ["只允许一条 SQL。"], [])
        statement = statements[0]
        if statement is None or not isinstance(statement, exp.Query):
            return SqlValidationResult(False, None, ["只允许 SELECT 查询。"], [])

        errors.extend(self._validate_complexity(statement))

        forbidden_types = tuple(
            expression_type
            for expression_type in (
                getattr(exp, "Alter", None),
                getattr(exp, "Command", None),
                getattr(exp, "Copy", None),
                getattr(exp, "Create", None),
                getattr(exp, "Delete", None),
                getattr(exp, "Drop", None),
                getattr(exp, "Insert", None),
                getattr(exp, "Into", None),
                getattr(exp, "Lock", None),
                getattr(exp, "Merge", None),
                getattr(exp, "Transaction", None),
                getattr(exp, "Update", None),
            )
            if expression_type is not None
        )
        if any(isinstance(node, forbidden_types) for node in statement.walk()):
            errors.append("查询包含写操作、锁或不允许的命令。")

        cte_names = {
            cte.alias_or_name.lower()
            for cte in statement.find_all(exp.CTE)
            if cte.alias_or_name
        }
        cte_origins: dict[str, set[str]] = {}
        for cte in statement.find_all(exp.CTE):
            cte_name = cte.alias_or_name.lower() if cte.alias_or_name else ""
            if not cte_name:
                continue
            origins = {
                table.name.lower()
                for table in cte.this.find_all(exp.Table)
                if table.name.lower() in self._table_columns
                and not (table.name.lower() in cte_names and not table.db and not table.catalog)
            }
            cte_origins[cte_name] = origins
        aliases: dict[str, str] = {}
        real_tables: set[str] = set()
        for table in statement.find_all(exp.Table):
            table_name = table.name.lower()
            if table_name in cte_names and not table.db and not table.catalog:
                origins = cte_origins.get(table_name, set())
                if len(origins) == 1:
                    aliases[(table.alias_or_name or table_name).lower()] = next(iter(origins))
                continue
            if table.db and table.db.lower() != "public":
                errors.append(f"不允许访问 schema：{table.db}。")
                continue
            if table_name not in self._table_columns:
                errors.append(f"不允许访问数据表：{table_name}。")
                continue
            real_tables.add(table_name)
            aliases[(table.alias_or_name or table_name).lower()] = table_name
            aliases[table_name] = table_name

        if not real_tables:
            errors.append("查询没有使用开放的 Odoo 数据表。")

        projection_aliases = {
            projection.alias.lower()
            for select in statement.find_all(exp.Select)
            for projection in select.expressions
            if projection.alias
        }
        for select in statement.find_all(exp.Select):
            for projection in select.expressions:
                if isinstance(projection, exp.Star) or bool(
                    getattr(projection, "is_star", False)
                ):
                    errors.append("不允许 SELECT *，必须明确选择字段。")

        for column in statement.find_all(exp.Column):
            column_name = column.name.lower()
            qualifier = column.table.lower() if column.table else ""
            if qualifier in cte_names:
                continue
            if qualifier:
                table_name = self._resolve_scoped_table(
                    column,
                    cte_origins=cte_origins,
                ) or aliases.get(qualifier)
                if table_name and column_name not in self._table_columns[table_name]:
                    errors.append(f"字段未开放：{qualifier}.{column_name}。")
                continue
            if column_name in projection_aliases:
                continue
            if not any(
                column_name in self._table_columns[table]
                for table in real_tables
            ):
                errors.append(f"字段未开放：{column_name}。")

        for function in statement.find_all(exp.Func):
            function_name = (
                function.name
                if isinstance(function, exp.Anonymous)
                else function.sql_name()
            ).lower()
            if function_name in self._FORBIDDEN_FUNCTIONS:
                errors.append(f"不允许调用函数：{function_name}。")

        if plan is not None:
            errors.extend(self._validate_query_contract(statement, plan, question))

        if {"sale_order", "sale_order_line"} & real_tables:
            company_filter_found = False
            for equality in statement.find_all(exp.EQ):
                left, right = equality.this, equality.expression
                pairs = ((left, right), (right, left))
                for column, literal in pairs:
                    if (
                        isinstance(column, exp.Column)
                        and column.name.lower() == "company_id"
                        and isinstance(literal, exp.Literal)
                        and literal.is_int
                        and int(literal.this) == self._company_id
                    ):
                        qualifier = column.table.lower() if column.table else ""
                        if qualifier:
                            qualified_table = self._resolve_scoped_table(
                                column,
                                cte_origins=cte_origins,
                            ) or aliases.get(qualifier)
                        else:
                            qualified_table = None
                        if qualified_table in {"sale_order", "sale_order_line"}:
                            company_filter_found = True
                        elif not qualifier and len(
                            {"sale_order", "sale_order_line"} & real_tables
                        ) == 1:
                            company_filter_found = True
            if not company_filter_found:
                errors.append(
                    f"销售查询必须包含 company_id = {self._company_id}。"
                )

        if errors:
            return SqlValidationResult(
                safe=False,
                sql=None,
                errors=list(dict.fromkeys(errors)),
                tables=sorted(real_tables),
            )

        limit = statement.args.get("limit")
        replace_limit = limit is None
        if limit is not None:
            expression = limit.expression
            replace_limit = not (
                isinstance(expression, exp.Literal)
                and expression.is_int
                and int(expression.this) <= self._max_rows
            )
        if replace_limit:
            statement.set(
                "limit",
                exp.Limit(expression=exp.Literal.number(self._max_rows)),
            )

        return SqlValidationResult(
            safe=True,
            sql=statement.sql(dialect="postgres", pretty=True),
            errors=[],
            tables=sorted(real_tables),
        )

    def _resolve_scoped_table(
        self,
        column: exp.Column,
        *,
        cte_origins: dict[str, set[str]],
    ) -> str | None:
        qualifier = column.table.lower() if column.table else ""
        if not qualifier:
            return None
        select = column.find_ancestor(exp.Select)
        if select is None:
            return None
        for table in select.find_all(exp.Table):
            if table.find_ancestor(exp.Select) is not select:
                continue
            table_alias = (table.alias_or_name or table.name).lower()
            if table_alias != qualifier:
                continue
            table_name = table.name.lower()
            origins = cte_origins.get(table_name, set())
            if len(origins) == 1:
                return next(iter(origins))
            if table_name in self._table_columns:
                return table_name
        return None

    def _validate_complexity(self, statement: exp.Query) -> list[str]:
        errors: list[str] = []
        joins = list(statement.find_all(exp.Join))
        ctes = list(statement.find_all(exp.CTE))
        subqueries = list(statement.find_all(exp.Subquery))

        if len(joins) > self._complexity.max_joins:
            errors.append(
                "SQL JOIN 数量超过安全上限："
                f"最多 {self._complexity.max_joins} 个，实际 {len(joins)} 个。"
            )
        if len(ctes) > self._complexity.max_ctes:
            errors.append(
                "SQL CTE 数量超过安全上限："
                f"最多 {self._complexity.max_ctes} 个，实际 {len(ctes)} 个。"
            )
        if len(subqueries) > self._complexity.max_subqueries:
            errors.append(
                "SQL 子查询数量超过安全上限："
                f"最多 {self._complexity.max_subqueries} 个，实际 {len(subqueries)} 个。"
            )

        maximum_depth = 0
        for subquery in subqueries:
            depth = 1
            parent = subquery.parent
            while parent is not None:
                if isinstance(parent, exp.Subquery):
                    depth += 1
                parent = parent.parent
            maximum_depth = max(maximum_depth, depth)
        if maximum_depth > self._complexity.max_subquery_depth:
            errors.append(
                "SQL 子查询嵌套深度超过安全上限："
                f"最多 {self._complexity.max_subquery_depth} 层，实际 {maximum_depth} 层。"
            )

        for join in joins:
            kind = str(join.args.get("kind") or "").casefold()
            has_condition = join.args.get("on") is not None or join.args.get("using") is not None
            if kind == "cross" or not has_condition:
                errors.append("SQL 包含笛卡尔积或缺少连接条件的 JOIN。")
                break
        return errors

    @staticmethod
    def _field_name(value: str) -> str:
        return value.rsplit(".", 1)[-1].strip().casefold()

    def _validate_query_contract(
        self,
        statement: exp.Query,
        plan: QueryPlan,
        question: str,
    ) -> list[str]:
        errors: list[str] = []
        if plan.query_type == "detail" and self._complexity.require_detail_time_range:
            if not (plan.time_range.start and plan.time_range.end):
                errors.append("明细查询需要用户补充明确的开始和结束时间范围。")
            elif not self._has_sql_time_bounds(statement):
                errors.append("明细 SQL 必须包含 date_order 的开始和结束边界。")
        final_select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
        if final_select is not None and plan.select_columns:
            actual_columns = [
                projection.alias_or_name.casefold()
                for projection in final_select.expressions
                if projection.alias_or_name
            ]
            expected_columns = [column.casefold() for column in plan.select_columns]
            if actual_columns != expected_columns:
                errors.append(
                    "SQL 最终输出列与 QueryPlan.select_columns 不一致："
                    f"期望 {expected_columns}，实际 {actual_columns}。"
                )

        if (
            plan.query_type == "ranking"
            and plan.row_limit is None
            and ranking_requires_row_limit(question)
        ):
            errors.append("排名查询必须在 QueryPlan.row_limit 中声明 Top N。")
        elif (
            plan.query_type == "ranking"
            and plan.row_limit is not None
            and not ranking_requires_row_limit(question)
        ):
            errors.append("用户未指定 Top N，完整排名不得声明业务 LIMIT。")
        elif plan.row_limit is not None:
            limit = statement.args.get("limit")
            expression = limit.expression if limit is not None else None
            if not (
                isinstance(expression, exp.Literal)
                and expression.is_int
                and int(expression.this) == plan.row_limit
            ):
                errors.append(f"SQL LIMIT 必须与 QueryPlan.row_limit={plan.row_limit} 一致。")

        order = statement.args.get("order")
        if order is not None or plan.sort:
            actual_sort: list[tuple[str, str]] = []
            for ordered in order.expressions if order is not None else []:
                target = ordered.this
                if isinstance(target, exp.Literal) and target.is_int:
                    index = int(target.this) - 1
                    field = (
                        plan.select_columns[index].casefold()
                        if 0 <= index < len(plan.select_columns)
                        else target.this
                    )
                else:
                    field = target.alias_or_name.casefold()
                actual_sort.append(
                    (field, "desc" if ordered.args.get("desc") else "asc")
                )
            expected_sort = [
                (item.field.casefold(), item.direction) for item in plan.sort
            ]
            if actual_sort != expected_sort:
                errors.append(
                    "SQL ORDER BY 与 QueryPlan.sort 不一致："
                    f"期望 {expected_sort}，实际 {actual_sort}。"
                )

        system_fields = {"company_id"}
        metric_fields = {"state", "display_type"}
        question_fields = self._question_filter_fields(question)
        has_declared_time_range = bool(
            plan.time_range.label
            or plan.time_range.start
            or plan.time_range.end
            or plan.time_range.grain != "none"
        )
        declared_fields: set[str] = set()
        for query_filter in plan.filters:
            field = self._field_name(query_filter.field)
            declared_fields.add(field)
            if query_filter.source == "system_required" and field not in system_fields:
                errors.append(f"过滤字段 {field} 不能标记为 system_required。")
            elif query_filter.source == "metric_rule" and field not in metric_fields:
                errors.append(f"过滤字段 {field} 不是已登记的指标口径规则。")
            elif query_filter.source == "user" and field not in question_fields:
                errors.append(f"用户问题没有授权过滤字段 {field}。")

        allowed_where_fields = system_fields | metric_fields | declared_fields
        if has_declared_time_range:
            allowed_where_fields.add("date_order")
        for where in statement.find_all(exp.Where):
            for column in where.find_all(exp.Column):
                field = column.name.casefold()
                if field not in allowed_where_fields:
                    errors.append(f"SQL 包含 QueryPlan 未声明的过滤字段：{field}。")
        return errors

    @staticmethod
    def _has_sql_time_bounds(statement: exp.Query) -> bool:
        lower_bound = False
        upper_bound = False
        for comparison in statement.find_all((exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left = comparison.this
            right = comparison.expression
            if isinstance(left, exp.Column) and left.name.casefold() == "date_order":
                if isinstance(comparison, (exp.GT, exp.GTE)):
                    lower_bound = True
                else:
                    upper_bound = True
            elif isinstance(right, exp.Column) and right.name.casefold() == "date_order":
                if isinstance(comparison, (exp.LT, exp.LTE)):
                    lower_bound = True
                else:
                    upper_bound = True
        if any(
            isinstance(between.this, exp.Column)
            and between.this.name.casefold() == "date_order"
            for between in statement.find_all(exp.Between)
        ):
            return True
        return lower_bound and upper_bound

    @staticmethod
    def _question_filter_fields(question: str) -> set[str]:
        normalized = question.casefold()
        fields: set[str] = set()
        keyword_fields = {
            "客户": {"name", "partner_id", "commercial_partner_id", "customer_rank", "is_company"},
            "customer": {"name", "partner_id", "commercial_partner_id", "customer_rank", "is_company"},
            "伙伴": {"name", "partner_id", "commercial_partner_id"},
            "产品": {"name", "product_id", "default_code", "categ_id"},
            "商品": {"name", "product_id", "default_code", "categ_id"},
            "销售员": {"name", "user_id", "salesman_id", "partner_id"},
            "业务员": {"name", "user_id", "salesman_id", "partner_id"},
            "交付": {"delivery_status", "qty_delivered"},
            "发货": {"delivery_status", "qty_delivered", "is_delivery"},
            "开票": {"invoice_status", "qty_invoiced"},
            "预付款": {"is_downpayment"},
            "折扣": {"discount"},
            "订单": {"name"},
            "今天": {"date_order"},
            "本日": {"date_order"},
            "昨天": {"date_order"},
            "本周": {"date_order"},
            "上周": {"date_order"},
            "本月": {"date_order"},
            "上月": {"date_order"},
            "这个月": {"date_order"},
            "上个月": {"date_order"},
            "今年": {"date_order"},
            "去年": {"date_order"},
            "每月": {"date_order"},
            "每个月": {"date_order"},
            "月份": {"date_order"},
            "季度": {"date_order"},
            "年度": {"date_order"},
        }
        for keyword, related_fields in keyword_fields.items():
            if keyword in normalized:
                fields.update(related_fields)
        if re.search(
            r"(?:19|20|21)\d{2}\s*年|\d{4}-\d{1,2}-\d{1,2}|"
            r"(?:最近|过去|近)\s*\d+\s*(?:天|周|个月|月|季度|年)",
            normalized,
        ):
            fields.add("date_order")
        return fields
