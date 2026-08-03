from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError


@dataclass(frozen=True, slots=True)
class SqlValidationResult:
    safe: bool
    sql: str | None
    errors: list[str]
    tables: list[str]


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
    ) -> None:
        self._table_columns = {
            table: set(columns) for table, columns in table_columns.items()
        }
        self._company_id = company_id
        self._max_rows = max_rows

    def validate(self, sql: str) -> SqlValidationResult:
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
        aliases: dict[str, str] = {}
        real_tables: set[str] = set()
        for table in statement.find_all(exp.Table):
            table_name = table.name.lower()
            if table_name in cte_names:
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
                table_name = aliases.get(qualifier)
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
                        qualified_table = aliases.get(qualifier) if qualifier else None
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
