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
from app.schemas.query_plan import QueryPlan  # noqa: E402


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

    def test_query_plan_contract_checks_output_and_filter_source(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "ranking",
                "metric_ids": ["sales_amount"],
                "dimensions": ["customer"],
                "filters": [
                    {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                    {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                ],
                "result_shape": "ranking",
                "select_columns": ["customer", "sales_amount"],
                "sort": [{"field": "sales_amount", "direction": "desc"}],
                "row_limit": 10,
            }
        )
        safe = self.guard.validate(
            "SELECT rp.name AS customer, SUM(so.amount_untaxed) AS sales_amount "
            "FROM sale_order so JOIN res_partner rp ON rp.id = so.partner_id "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
            "GROUP BY rp.name ORDER BY sales_amount DESC LIMIT 10",
            plan=plan,
            question="销售额最高的十个客户是谁？",
        )
        self.assertTrue(safe.safe, safe.errors)

        unsafe = self.guard.validate(
            "SELECT rp.name AS customer, SUM(so.amount_untaxed) AS sales_amount "
            "FROM sale_order so JOIN res_partner rp ON rp.id = so.partner_id "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done') AND rp.active = TRUE "
            "GROUP BY rp.name ORDER BY sales_amount DESC LIMIT 10",
            plan=plan,
            question="销售额最高的十个客户是谁？",
        )
        self.assertFalse(unsafe.safe)
        self.assertTrue(any("未声明" in error for error in unsafe.errors))

    def test_wren_cte_can_wrap_a_same_named_physical_table(self) -> None:
        result = self.guard.validate(
            'WITH sale_order AS ('
            'SELECT __source.amount_untaxed, __source.company_id, __source.state '
            'FROM "public".sale_order AS __source) '
            'SELECT SUM(amount_untaxed) AS sales_amount FROM sale_order '
            "WHERE company_id = 1 AND state IN ('sale', 'done')"
        )
        self.assertTrue(result.safe, result.errors)
        self.assertEqual(result.tables, ["sale_order"])


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
