from __future__ import annotations

import asyncio
import json
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

    async def execute_readonly(self, sql: str) -> QueryResult:
        return await asyncio.to_thread(self._execute_readonly_sync, sql)

    def _execute_readonly_sync(self, sql: str) -> QueryResult:
        started = perf_counter()
        try:
            with self._connect() as connection:
                cursor = connection.execute(sql)
                raw_rows = cursor.fetchmany(self._config.max_rows + 1)
                columns = [column.name for column in (cursor.description or [])]
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
