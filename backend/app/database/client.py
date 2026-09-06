from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from app.config import DatabaseConfig


class DatabaseConnectionError(RuntimeError):
    pass


class QueryCostExceededError(DatabaseConnectionError):
    pass


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    connected: bool
    user: str
    database: str
    read_only: bool
    company_id: int
    company_name: str | None
    currency: str | None
    order_count: int
    response_ms: float
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    duration_ms: float
    estimated_plan_cost: float | None = None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, default=str))
    return str(value)


def _explain_total_cost(row: Any) -> float | None:
    """Extract PostgreSQL's root Total Cost without executing the query."""

    if not isinstance(row, dict):
        return None
    payload = row.get("QUERY PLAN") or row.get("query_plan")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        return None
    plan = payload.get("Plan", payload)
    if not isinstance(plan, dict):
        return None
    value = plan.get("Total Cost")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class OdooDatabase:
    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config

    def _connect(self) -> Connection[dict[str, Any]]:
        if not self._config.configured:
            raise DatabaseConnectionError("Odoo database is not configured.")

        kwargs: dict[str, Any] = {
            "host": self._config.host,
            "port": self._config.port,
            "dbname": self._config.database,
            "user": self._config.user,
            "connect_timeout": 3,
            "application_name": "odoo-sales-agent-readonly",
            "row_factory": dict_row,
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={self._config.statement_timeout_ms} "
                "-c lock_timeout=3000 "
                "-c idle_in_transaction_session_timeout=15000"
            ),
        }
        if self._config.password:
            kwargs["password"] = self._config.password

        try:
            return Connection.connect(**kwargs)
        except Exception as exc:
            raise DatabaseConnectionError(type(exc).__name__) from exc

    async def healthcheck(self) -> DatabaseHealth:
        return await asyncio.to_thread(self._healthcheck_sync)

    def _healthcheck_sync(self) -> DatabaseHealth:
        started = perf_counter()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    SELECT
                        current_user AS database_user,
                        current_database() AS database_name,
                        current_setting('transaction_read_only') = 'on' AS read_only,
                        company.name AS company_name,
                        currency.name AS currency,
                        (
                            SELECT COUNT(*)
                            FROM sale_order
                            WHERE company_id = %s
                        ) AS order_count
                    FROM res_company AS company
                    JOIN res_currency AS currency ON currency.id = company.currency_id
                    WHERE company.id = %s
                    """,
                    (self._config.company_id, self._config.company_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise DatabaseConnectionError("ConfiguredCompanyNotFound")
        except DatabaseConnectionError as exc:
            return DatabaseHealth(
                connected=False,
                user=self._config.user,
                database=self._config.database,
                read_only=False,
                company_id=self._config.company_id,
                company_name=None,
                currency=None,
                order_count=0,
                response_ms=round((perf_counter() - started) * 1000, 2),
                error_type=str(exc),
            )
        except Exception as exc:
            return DatabaseHealth(
                connected=False,
                user=self._config.user,
                database=self._config.database,
                read_only=False,
                company_id=self._config.company_id,
                company_name=None,
                currency=None,
                order_count=0,
                response_ms=round((perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
            )

        return DatabaseHealth(
            connected=bool(row["read_only"]),
            user=str(row["database_user"]),
            database=str(row["database_name"]),
            read_only=bool(row["read_only"]),
            company_id=self._config.company_id,
            company_name=str(row["company_name"]),
            currency=str(row["currency"]),
            order_count=int(row["order_count"]),
            response_ms=round((perf_counter() - started) * 1000, 2),
        )

    async def execute_readonly(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> QueryResult:
        return await asyncio.to_thread(self._execute_readonly_sync, sql, params)

    def _execute_readonly_sync(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> QueryResult:
        started = perf_counter()
        estimated_plan_cost: float | None = None
        try:
            with self._connect() as connection:
                explain_cursor = connection.execute(
                    f"EXPLAIN (FORMAT JSON) {sql}",
                    params,
                )
                explain_row = explain_cursor.fetchone()
                estimated_plan_cost = _explain_total_cost(explain_row)
                if (
                    estimated_plan_cost is not None
                    and estimated_plan_cost > self._config.explain_total_cost_limit
                ):
                    raise QueryCostExceededError("QueryCostExceeded")
                cursor = connection.execute(sql, params)
                raw_rows = cursor.fetchmany(self._config.max_rows + 1)
                columns = [column.name for column in (cursor.description or [])]
        except QueryCostExceededError:
            raise
        except Exception as exc:
            raise DatabaseConnectionError(type(exc).__name__) from exc

        truncated = len(raw_rows) > self._config.max_rows
        selected_rows = raw_rows[: self._config.max_rows]
        rows = [
            {key: _json_value(value) for key, value in row.items()}
            for row in selected_rows
        ]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            estimated_plan_cost=estimated_plan_cost,
        )
    async def discover_columns(
        self,
        table_columns: dict[str, list[str]],
    ) -> dict[str, list[dict[str, str]]]:
        return await asyncio.to_thread(self._discover_columns_sync, table_columns)

    def _discover_columns_sync(
        self,
        table_columns: dict[str, list[str]],
    ) -> dict[str, list[dict[str, str]]]:
        discovered: dict[str, list[dict[str, str]]] = {}
        with self._connect() as connection:
            for table, allowed_columns in table_columns.items():
                cursor = connection.execute(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s
                      AND column_name = ANY(%s)
                    ORDER BY ordinal_position
                    """,
                    (table, allowed_columns),
                )
                rows = cursor.fetchall()
                discovered[table] = [
                    {
                        "name": str(row["column_name"]),
                        "type": str(row["data_type"]),
                    }
                    for row in rows
                ]
        return discovered

    async def inspect_semantic_metadata(
        self,
        scope: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Inspect allow-listed physical and ORM metadata without reading row values."""

        return await asyncio.to_thread(self._inspect_semantic_metadata_sync, scope)

    def _inspect_semantic_metadata_sync(
        self,
        scope: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        tables = sorted(scope)
        models = sorted({str(item["odoo_model"]) for item in scope.values()})
        expected_by_table = {
            table: set(map(str, item["columns"])) for table, item in scope.items()
        }
        expected_by_model = {
            str(item["odoo_model"]): set(map(str, item["columns"]))
            for item in scope.values()
        }

        with self._connect() as connection:
            read_only_row = connection.execute(
                "SELECT current_setting('transaction_read_only') = 'on' AS read_only"
            ).fetchone()
            physical_rows = connection.execute(
                """
                SELECT
                    table_name,
                    column_name,
                    data_type,
                    udt_name,
                    is_nullable = 'NO' AS not_null,
                    ordinal_position
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                ORDER BY table_name, ordinal_position
                """,
                (tables,),
            ).fetchall()
            orm_rows = connection.execute(
                """
                SELECT
                    field.id,
                    field.model,
                    field.name,
                    field.ttype,
                    field.relation,
                    field.relation_field,
                    field.field_description,
                    field.help,
                    field.related,
                    field.required,
                    field.readonly,
                    field.index,
                    field.translate,
                    field.company_dependent,
                    field.state,
                    field.store,
                    field.currency_field,
                    field.compute IS NOT NULL AND field.compute <> '' AS computed,
                    (
                        SELECT string_agg(DISTINCT model_data.module, ',' ORDER BY model_data.module)
                        FROM ir_model_data AS model_data
                        WHERE model_data.model = 'ir.model.fields'
                          AND model_data.res_id = field.id
                    ) AS modules
                FROM ir_model_fields AS field
                WHERE field.model = ANY(%s)
                ORDER BY field.model, field.name
                """,
                (models,),
            ).fetchall()

            selected_field_ids = [
                int(row["id"])
                for row in orm_rows
                if str(row["name"]) in expected_by_model.get(str(row["model"]), set())
            ]
            selection_rows = (
                connection.execute(
                    """
                    SELECT field_id, value, name, sequence
                    FROM ir_model_fields_selection
                    WHERE field_id = ANY(%s)
                    ORDER BY field_id, sequence, id
                    """,
                    (selected_field_ids,),
                ).fetchall()
                if selected_field_ids
                else []
            )

        selections: dict[int, list[dict[str, Any]]] = {}
        for row in selection_rows:
            selections.setdefault(int(row["field_id"]), []).append(
                {
                    "value": str(row["value"]),
                    "label": _json_value(row["name"]),
                }
            )

        physical: dict[str, dict[str, dict[str, Any]]] = {table: {} for table in tables}
        for row in physical_rows:
            table = str(row["table_name"])
            column = str(row["column_name"])
            if column not in expected_by_table.get(table, set()):
                continue
            physical[table][column] = {
                "data_type": str(row["data_type"]),
                "udt_name": str(row["udt_name"]),
                "not_null": bool(row["not_null"]),
                "ordinal_position": int(row["ordinal_position"]),
            }

        orm: dict[str, dict[str, dict[str, Any]]] = {model: {} for model in models}
        for row in orm_rows:
            model = str(row["model"])
            field_name = str(row["name"])
            if field_name not in expected_by_model.get(model, set()):
                continue
            field_id = int(row["id"])
            orm[model][field_name] = {
                key: _json_value(row[key])
                for key in (
                    "ttype",
                    "relation",
                    "relation_field",
                    "field_description",
                    "help",
                    "related",
                    "required",
                    "readonly",
                    "index",
                    "translate",
                    "company_dependent",
                    "state",
                    "modules",
                    "store",
                    "currency_field",
                    "computed",
                )
            }
            orm[model][field_name]["selection"] = selections.get(field_id, [])

        return {
            "read_only": bool(read_only_row and read_only_row["read_only"]),
            "physical": physical,
            "orm": orm,
        }
