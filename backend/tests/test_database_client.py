from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import DatabaseConfig  # noqa: E402
from app.database import OdooDatabase, QueryCostExceededError  # noqa: E402


class _Cursor:
    def __init__(self, *, one=None, many=None, columns=None) -> None:
        self._one = one
        self._many = many or []
        self.description = [SimpleNamespace(name=name) for name in (columns or [])]

    def fetchone(self):
        return self._one

    def fetchmany(self, _size: int):
        return self._many


class _Connection:
    def __init__(self, plan_cost: float) -> None:
        self.plan_cost = plan_cost
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, _params=None):
        self.calls.append(sql)
        if sql.startswith("EXPLAIN (FORMAT JSON)"):
            return _Cursor(one={"QUERY PLAN": [{"Plan": {"Total Cost": self.plan_cost}}]})
        return _Cursor(many=[{"count": 3}], columns=["count"])


def _config(limit: float) -> DatabaseConfig:
    return DatabaseConfig(
        host="127.0.0.1",
        port=55432,
        database="test",
        user="readonly",
        password=None,
        company_id=1,
        statement_timeout_ms=15_000,
        max_rows=500,
        explain_total_cost_limit=limit,
    )


class DatabaseQueryCostTests(unittest.TestCase):
    def test_explain_cost_is_recorded_before_query_execution(self) -> None:
        connection = _Connection(plan_cost=42.5)
        database = OdooDatabase(_config(100.0))
        with patch.object(database, "_connect", return_value=connection):
            result = database._execute_readonly_sync("SELECT 3 AS count")

        self.assertEqual(result.estimated_plan_cost, 42.5)
        self.assertEqual(result.rows, [{"count": 3}])
        self.assertEqual(len(connection.calls), 2)
        self.assertTrue(connection.calls[0].startswith("EXPLAIN (FORMAT JSON)"))

    def test_query_above_cost_budget_is_not_executed(self) -> None:
        connection = _Connection(plan_cost=101.0)
        database = OdooDatabase(_config(100.0))
        with (
            patch.object(database, "_connect", return_value=connection),
            self.assertRaises(QueryCostExceededError),
        ):
            database._execute_readonly_sync("SELECT 3 AS count")

        self.assertEqual(len(connection.calls), 1)


if __name__ == "__main__":
    unittest.main()
