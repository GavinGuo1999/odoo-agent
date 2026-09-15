from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from app.schemas.query_plan import QueryPlan, ranking_requires_row_limit


@dataclass(frozen=True, slots=True)
class SqlValidationResult:
    safe: bool
    sql: str | None
    errors: list[str]
    tables: list[str]

    @property
    def error_codes(self) -> list[str]:
        """拒绝理由的分类码，去重保序。用于可观测与聚合统计。"""

        return list(dict.fromkeys(classify_sql_error(error) for error in self.errors))


@dataclass(frozen=True, slots=True)
class SqlComplexityLimits:
    """Deterministic query-shape budgets enforced before database execution."""

    max_joins: int = 6
    max_ctes: int = 6
    max_subqueries: int = 12
    max_subquery_depth: int = 3
    require_detail_time_range: bool = True


# 拒绝理由的分类码。错误消息是给用户看的中文；要回答"哪类问题在失败、占多少"，
# 需要的是可聚合的标签。
#
# **改动错误消息必须同步这张表**：test_sql_error_codes.py 会把每一类拒绝场景都跑
# 一遍并断言分类码，漏改就会红，不会悄悄退化成 other。
_ERROR_CODES: tuple[tuple[str, str], ...] = (
    ("查询包含写操作", "write_statement"),
    ("只允许一条 SQL", "multiple_statements"),
    ("只允许 SELECT", "not_a_select"),
    ("SQL 语法无法解析", "unparsable"),
    ("不允许访问 schema", "forbidden_schema"),
    ("不允许访问数据表", "unlisted_table"),
    ("查询没有使用开放的 Odoo 数据表", "no_allowed_table"),
    ("不允许 SELECT *", "select_star"),
    ("字段未开放", "unlisted_column"),
    ("不允许调用函数", "forbidden_function"),
    ("笛卡尔积", "cartesian_join"),
    ("必须包含 company_id", "missing_company_filter"),
    ("明细查询需要用户补充", "detail_needs_time_range"),
    ("明细 SQL 必须包含", "detail_missing_time_bounds"),
    ("排名查询必须在 QueryPlan.row_limit", "ranking_needs_top_n"),
    ("完整排名不得声明业务 LIMIT", "ranking_unexpected_limit"),
    ("SQL LIMIT 必须与 QueryPlan.row_limit", "limit_mismatch"),
    ("最终输出列与 QueryPlan.select_columns 不一致", "select_columns_mismatch"),
    ("ORDER BY 与 QueryPlan.sort 不一致", "sort_mismatch"),
    ("产品名称必须优先使用 zh_CN", "product_name_locale"),
    ("不能标记为 system_required", "filter_source_system"),
    ("不是已登记的指标口径规则", "filter_source_metric"),
    ("用户问题没有授权过滤字段", "filter_unauthorized"),
    ("未声明的过滤字段", "filter_undeclared"),
    ("超过", "complexity_budget"),
    ("嵌套", "complexity_budget"),
)


def classify_sql_error(message: str) -> str:
    """把一条拒绝理由归到可聚合的分类码。认不出来的一律 other。"""

    for marker, code in _ERROR_CODES:
        if marker in message:
            return code
    return "other"


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
        enforce_presentation_contract: bool = True,
    ) -> SqlValidationResult:
        """校验一条只读 SQL。

        `enforce_presentation_contract=False` 只跳过**展示层**的契约检查
        （select_columns 对齐、ORDER BY 对齐、Top-N 声明、产品名 COALESCE、
        明细必须带时间范围）。这些规则保证的是"给用户看的表格和图不被悄悄截断
        或串列"，对不展示的补充查询没有意义，卡着它反而让证据查不出来。

        **安全检查一条都不跳过**：语句类型、表/字段白名单、禁用函数、SELECT *、
        company_id 隔离、行数上限，以及"WHERE 里不得出现计划未声明的过滤字段"。
        """
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
        # CTE 的输出列是它自己算出来的（SUM(...) AS sales_amount_2025 这种），
        # 不是物理表字段。外层引用这些列时必须按 CTE 的投影校验，按物理表校验
        # 会把所有带计算列的 CTE 全部误判成"字段未开放"。
        cte_projections = {
            name: self._cte_output_columns(cte)
            for cte in statement.find_all(exp.CTE)
            if (name := (cte.alias_or_name or "").lower())
        }
        # 外层给 CTE 起的别名（FROM sales_2025 s25 里的 s25）也要能查到它属于哪个 CTE。
        cte_aliases: dict[str, str] = {}
        aliases: dict[str, str] = {}
        real_tables: set[str] = set()
        for table in statement.find_all(exp.Table):
            table_name = table.name.lower()
            if table_name in cte_names and not table.db and not table.catalog:
                cte_aliases[(table.alias_or_name or table_name).lower()] = table_name
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
            # 限定符指向 CTE（直接用 CTE 名，或用外层起的别名）时，按该 CTE 的输出列校验。
            # CTE 内部读的物理表和字段在上面已经查过了，这里不需要也不能再查一遍。
            cte_target = cte_aliases.get(qualifier) or (qualifier if qualifier in cte_names else None)
            if cte_target is not None:
                projection = cte_projections.get(cte_target)
                # 投影拿不到（SELECT * 之类）时放行：SELECT * 本身另有一条规则拦。
                if projection and column_name not in projection:
                    errors.append(f"字段未开放：{qualifier}.{column_name}。")
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
            errors.extend(
                self._validate_query_contract(
                    statement,
                    plan,
                    question,
                    enforce_presentation=enforce_presentation_contract,
                )
            )

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
        *,
        enforce_presentation: bool = True,
    ) -> list[str]:
        errors: list[str] = []
        if not enforce_presentation:
            # 只保留"不得偷偷加过滤条件"这一条——它防的是结果被无声地缩小范围，
            # 对补充查询同样成立（被裁过的证据会把答案带偏）。
            return self._validate_declared_filters(
                statement, plan, question, authorize_user_filters=False
            )
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
            final_select is not None
            and "product" in {item.casefold() for item in plan.dimensions}
            and not self._has_product_name_fallback(final_select)
        ):
            errors.append(
                "QueryPlan 产品名称必须优先使用 zh_CN，并以 en_US 回退。"
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

        errors.extend(self._validate_declared_filters(statement, plan, question))
        return errors

    def _validate_declared_filters(
        self,
        statement: exp.Query,
        plan: QueryPlan,
        question: str,
        *,
        authorize_user_filters: bool = True,
    ) -> list[str]:
        """WHERE 里的每个字段都必须在计划里声明过，且 user 来源的要在问题里出现。

        `authorize_user_filters=False` 只跳过"用户问题里出现过"这一条。补充查询的
        "问题"是模型自己写的用途说明，不可能包含字段级细节，拿它当授权依据必然误判。
        "每个过滤字段都必须在计划里声明"这条**不跳过**——它保证证据没有被无声地
        缩小范围，而且声明会存进 artifact 和 Langfuse，事后可审计。
        """

        errors: list[str] = []
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
            elif (
                authorize_user_filters
                and query_filter.source == "user"
                and field not in question_fields
                and not self._question_mentions_value(question, query_filter.value)
            ):
                errors.append(f"用户问题没有授权过滤字段 {field}。")

        allowed_where_fields = system_fields | metric_fields | declared_fields
        if has_declared_time_range:
            allowed_where_fields.add("date_order")
        # 对聚合结果的别名做筛选（HAVING sales_amount > 0）不是"隐藏的物理列过滤"，
        # 它筛的是算出来的值；别名背后的真实列已经被字段白名单查过了。
        allowed_where_fields |= {
            str(projection.alias).casefold()
            for select in statement.find_all(exp.Select)
            for projection in select.expressions
            if projection.alias
        }
        for where in statement.find_all(exp.Where):
            for column in where.find_all(exp.Column):
                field = column.name.casefold()
                if field not in allowed_where_fields:
                    errors.append(f"SQL 包含 QueryPlan 未声明的过滤字段：{field}。")
        return errors

    @staticmethod
    def _cte_output_columns(cte: exp.CTE) -> set[str]:
        """取一个 CTE 对外暴露的列名。

        优先用 `WITH x(a, b) AS ...` 里显式声明的列名；没有就取内部 SELECT 的投影
        别名。两者都拿不到（例如内部是 SELECT *）时返回空集合，调用方据此放行。
        """

        declared = cte.args.get("alias")
        columns = getattr(declared, "columns", None) if declared is not None else None
        if columns:
            return {str(item.alias_or_name).casefold() for item in columns if item.alias_or_name}

        inner = cte.this if isinstance(cte.this, exp.Select) else cte.this.find(exp.Select)
        if inner is None:
            return set()
        names = {
            str(projection.alias_or_name).casefold()
            for projection in inner.expressions
            if projection.alias_or_name
        }
        # 含 SELECT * 时列名不可枚举，返回空集合表示"无法校验"。
        if any(isinstance(item, exp.Star) or getattr(item, "is_star", False)
               for item in inner.expressions):
            return set()
        return names

    @staticmethod
    def _has_product_name_fallback(final_select: exp.Select) -> bool:
        projection = next(
            (
                item
                for item in final_select.expressions
                if item.alias_or_name.casefold() == "product"
            ),
            None,
        )
        if projection is None:
            return False
        expression = projection.this if isinstance(projection, exp.Alias) else projection
        if not isinstance(expression, exp.Coalesce):
            return False
        arguments = [expression.this, *expression.expressions]
        if len(arguments) < 2:
            return False
        primary = ReadOnlySqlGuard._json_name_locale(arguments[0])
        fallback = ReadOnlySqlGuard._json_name_locale(arguments[1])
        return bool(
            primary
            and fallback
            and primary[0] == fallback[0]
            and primary[1] == "zh_cn"
            and fallback[1] == "en_us"
        )

    @staticmethod
    def _json_name_locale(expression: exp.Expression) -> tuple[str, str] | None:
        if not isinstance(expression, exp.JSONExtractScalar):
            return None
        column = expression.this
        path = expression.expression
        if not isinstance(column, exp.Column) or column.name.casefold() != "name":
            return None
        if not isinstance(path, exp.JSONPath):
            return None
        keys = list(path.find_all(exp.JSONPathKey))
        if len(keys) != 1:
            return None
        return column.sql(dialect="postgres").casefold(), str(keys[0].this).casefold()

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
    def _question_mentions_value(question: str, value: Any) -> bool:
        """过滤值本身出现在问题里，就是最直接的授权证据。

        实测缺陷：「莱茵重工的销售额是多少？」整轮失败，因为关键词表要求问题里
        出现"客户"两个字才授权 name 字段——而直接报公司名是最自然的问法。

        这条判据比关键词表**更严**而不是更松：它要求用户逐字说出了那个值，
        而关键词只能证明"提到了这个维度"。太短的值（1 个字符、纯数字）不算，
        避免 `id = 1` 这种碰巧出现在问题里的数字被当成授权。
        """

        normalized = question.casefold()
        candidates = value if isinstance(value, (list, tuple, set)) else [value]
        mentioned = False
        for item in candidates:
            if isinstance(item, bool) or item is None:
                return False
            text = str(item).strip().casefold()
            # 纯数字太容易误命中（年份、金额、序号），必须靠关键词表授权。
            if len(text) < 2 or text.replace(".", "").replace("-", "").isdigit():
                return False
            if text not in normalized:
                return False
            mentioned = True
        return mentioned

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
