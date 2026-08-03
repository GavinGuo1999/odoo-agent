from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.chart import build_chart_spec  # noqa: E402
from app.bi.semantic import SalesSemanticLayer  # noqa: E402
from app.database import ReadOnlySqlGuard  # noqa: E402


class SqlGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        semantics = SalesSemanticLayer.load()
        self.guard = ReadOnlySqlGuard(
            table_columns=semantics.table_columns,
            company_id=1,
            max_rows=500,
        )

    def test_safe_sales_query_is_normalized_and_limited(self) -> None:
        result = self.guard.validate(
            "SELECT so.id, so.amount_untaxed FROM sale_order so "
            "WHERE so.company_id = 1 AND so.state = 'sale'"
        )
        self.assertTrue(result.safe)
        self.assertIn("LIMIT 500", result.sql or "")
        self.assertEqual(result.tables, ["sale_order"])

    def test_blocks_writes_multiple_statements_and_wildcards(self) -> None:
        cases = [
            "DELETE FROM sale_order WHERE company_id = 1",
            "SELECT id FROM sale_order WHERE company_id = 1; SELECT 1",
            "SELECT so.* FROM sale_order so WHERE so.company_id = 1",
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertFalse(self.guard.validate(sql).safe)

    def test_blocks_unknown_tables_sensitive_columns_and_functions(self) -> None:
        cases = [
            "SELECT value FROM ir_config_parameter",
            "SELECT ru.password FROM res_users ru",
            "SELECT pg_read_file('/tmp/x') FROM sale_order so WHERE so.company_id = 1",
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertFalse(self.guard.validate(sql).safe)

    def test_company_filter_must_apply_to_sales_fact(self) -> None:
        result = self.guard.validate(
            "SELECT so.id FROM sale_order so JOIN res_users ru ON ru.id = so.user_id "
            "WHERE ru.company_id = 1"
        )
        self.assertFalse(result.safe)
        self.assertTrue(any("company_id = 1" in error for error in result.errors))


class SemanticAndChartTests(unittest.TestCase):
    def test_semantic_layer_does_not_expose_user_credentials(self) -> None:
        columns = SalesSemanticLayer.load().table_columns["res_users"]
        self.assertNotIn("login", columns)
        self.assertNotIn("password", columns)

    def test_time_series_uses_line_chart(self) -> None:
        chart = build_chart_spec(
            "今年每月销售趋势",
            ["month", "sales_amount"],
            [
                {"month": "2026-06-01", "sales_amount": 100.0},
                {"month": "2026-07-01", "sales_amount": 200.0},
            ],
        )
        self.assertEqual(chart["type"], "line")
        self.assertEqual(chart["x_field"], "month")


if __name__ == "__main__":
    unittest.main()
