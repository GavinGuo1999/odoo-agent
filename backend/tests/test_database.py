from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.chart import build_chart_spec, build_data_profile, parse_chart_plan  # noqa: E402
from app.bi.semantic import SalesSemanticLayer  # noqa: E402
from app.database import ReadOnlySqlGuard  # noqa: E402
from app.database.sql_guard import SqlComplexityLimits  # noqa: E402
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

    def test_full_ranking_without_explicit_top_n_uses_guard_safety_limit(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "ranking",
                "metric_ids": ["sales_amount"],
                "dimensions": ["salesperson"],
                "filters": [
                    {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                    {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                ],
                "result_shape": "ranking",
                "select_columns": ["salesperson", "sales_amount"],
                "sort": [{"field": "sales_amount", "direction": "desc"}],
                "row_limit": None,
            }
        )
        result = self.guard.validate(
            "SELECT rp.name AS salesperson, SUM(so.amount_untaxed) AS sales_amount "
            "FROM sale_order so JOIN res_users ru ON ru.id = so.user_id "
            "JOIN res_partner rp ON rp.id = ru.partner_id "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
            "GROUP BY rp.name ORDER BY sales_amount DESC",
            plan=plan,
            question="今年各销售员的销售额排名如何？",
        )

        self.assertTrue(result.safe, result.errors)
        self.assertIn("LIMIT 500", result.sql or "")

    def test_declared_time_range_authorizes_date_filter(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "trend",
                "metric_ids": ["sales_amount"],
                "dimensions": ["month"],
                "filters": [
                    {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                    {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                    {"field": "date_order", "operator": "gte", "value": "2099-01-01", "source": "user"},
                    {"field": "date_order", "operator": "lt", "value": "2100-01-01", "source": "user"},
                ],
                "time_range": {"label": "2099 年", "start": "2099-01-01", "end": "2099-12-31", "grain": "month"},
                "result_shape": "time_series",
                "select_columns": ["month", "sales_amount"],
                "sort": [{"field": "month", "direction": "asc"}],
                "row_limit": None,
            }
        )
        result = self.guard.validate(
            "SELECT date_trunc('month', so.date_order)::date AS month, "
            "SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
            "AND so.date_order >= '2099-01-01' AND so.date_order < '2100-01-01' "
            "GROUP BY 1 ORDER BY 1",
            plan=plan,
            question="2099 年每个月的销售额是多少？",
        )

        self.assertTrue(result.safe, result.errors)

    def test_english_customer_term_authorizes_name_filter(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "kpi",
                "metric_ids": ["sales_amount"],
                "dimensions": [],
                "filters": [
                    {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                    {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                    {"field": "name", "operator": "eq", "value": "CODEX Website Customer 20260627", "source": "user"},
                ],
                "result_shape": "scalar",
                "select_columns": ["sales_amount"],
                "sort": [],
                "row_limit": None,
            }
        )
        result = self.guard.validate(
            "SELECT SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
            "JOIN res_partner rp ON rp.id = so.partner_id "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
            "AND rp.name = 'CODEX Website Customer 20260627'",
            plan=plan,
            question="今年 CODEX Website Customer 20260627 的销售额是多少？",
        )

        self.assertTrue(result.safe, result.errors)

    def test_model_time_metadata_does_not_authorize_unasked_date_filter(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "kpi",
                "metric_ids": ["sales_amount"],
                "filters": [
                    {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                    {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                    {"field": "date_order", "operator": "gte", "value": "2026-01-01", "source": "user"},
                ],
                "time_range": {"label": "今年", "start": "2026-01-01", "end": "2026-12-31", "grain": "year"},
                "result_shape": "scalar",
                "select_columns": ["sales_amount"],
                "sort": [],
                "row_limit": None,
            }
        )
        result = self.guard.validate(
            "SELECT SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
            "AND so.date_order >= '2026-01-01'",
            plan=plan,
            question="销售额是多少？",
        )

        self.assertFalse(result.safe)
        self.assertTrue(any("没有授权过滤字段 date_order" in item for item in result.errors))

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

    def test_wren_cte_alias_preserves_sales_company_lineage(self) -> None:
        result = self.guard.validate(
            'WITH sale_order AS ('
            'SELECT __source.amount_untaxed, __source.company_id, __source.state '
            'FROM "public".sale_order AS __source) '
            'SELECT SUM(so.amount_untaxed) AS sales_amount FROM sale_order AS so '
            "WHERE so.company_id = 1 AND so.state IN ('sale', 'done')"
        )

        self.assertTrue(result.safe, result.errors)
        self.assertEqual(result.tables, ["sale_order"])

    def test_wren_reused_source_alias_is_resolved_per_select_scope(self) -> None:
        result = self.guard.validate(
            'WITH sale_order AS ('
            'SELECT __source.amount_untaxed, __source.company_id, __source.partner_id '
            'FROM "public".sale_order AS __source), '
            'res_partner AS ('
            'SELECT __source.id, __source.name '
            'FROM "public".res_partner AS __source) '
            'SELECT rp.name AS customer, SUM(so.amount_untaxed) AS sales_amount '
            'FROM sale_order AS so JOIN res_partner AS rp ON rp.id = so.partner_id '
            'WHERE so.company_id = 1 GROUP BY rp.name'
        )

        self.assertTrue(result.safe, result.errors)

    def test_default_complexity_budget_allows_four_model_wren_wrappers(self) -> None:
        self.assertGreaterEqual(SqlComplexityLimits().max_subqueries, 8)

    def test_blocks_queries_above_join_limit(self) -> None:
        guard = ReadOnlySqlGuard(
            table_columns=SalesSemanticLayer.load().table_columns,
            company_id=1,
            max_rows=500,
            complexity=SqlComplexityLimits(max_joins=1),
        )
        result = guard.validate(
            "SELECT so.id FROM sale_order so "
            "JOIN res_partner rp ON rp.id = so.partner_id "
            "JOIN res_users ru ON ru.id = so.user_id "
            "WHERE so.company_id = 1"
        )

        self.assertFalse(result.safe)
        self.assertTrue(any("JOIN 数量" in error for error in result.errors))

    def test_blocks_excessive_ctes_and_nested_subqueries(self) -> None:
        guard = ReadOnlySqlGuard(
            table_columns=SalesSemanticLayer.load().table_columns,
            company_id=1,
            max_rows=500,
            complexity=SqlComplexityLimits(
                max_ctes=1,
                max_subqueries=1,
                max_subquery_depth=1,
            ),
        )
        too_many_ctes = guard.validate(
            "WITH orders AS ("
            "SELECT so.id, so.company_id FROM sale_order so WHERE so.company_id = 1"
            "), partners AS (SELECT rp.id FROM res_partner rp) "
            "SELECT orders.id FROM orders JOIN partners ON partners.id = orders.id"
        )
        nested_subqueries = guard.validate(
            "SELECT so.id FROM sale_order so WHERE so.company_id = 1 "
            "AND so.partner_id IN (SELECT rp.id FROM res_partner rp WHERE rp.id IN ("
            "SELECT ru.partner_id FROM res_users ru))"
        )

        self.assertTrue(any("CTE 数量" in error for error in too_many_ctes.errors))
        self.assertTrue(
            any("子查询" in error for error in nested_subqueries.errors),
            nested_subqueries.errors,
        )

    def test_blocks_cartesian_and_conditionless_joins(self) -> None:
        cases = [
            "SELECT so.id FROM sale_order so CROSS JOIN res_partner rp "
            "WHERE so.company_id = 1",
            "SELECT so.id FROM sale_order so, res_partner rp "
            "WHERE so.company_id = 1",
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                result = self.guard.validate(sql)
                self.assertFalse(result.safe)
                self.assertTrue(any("笛卡尔积" in error for error in result.errors))

    def test_detail_query_requires_concrete_time_bounds(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "detail",
                "metric_ids": ["sales_amount"],
                "result_shape": "table",
                "select_columns": ["order_name", "sales_amount"],
            }
        )
        result = self.guard.validate(
            "SELECT so.name AS order_name, so.amount_untaxed AS sales_amount "
            "FROM sale_order so WHERE so.company_id = 1",
            plan=plan,
            question="列出销售订单明细",
        )

        self.assertFalse(result.safe)
        self.assertTrue(any("用户补充" in error for error in result.errors))

    def test_detail_sql_must_apply_both_declared_time_bounds(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "detail",
                "metric_ids": ["sales_amount"],
                "filters": [
                    {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                    {"field": "date_order", "operator": "gte", "value": "2026-09-01", "source": "user"},
                    {"field": "date_order", "operator": "lt", "value": "2026-10-01", "source": "user"},
                ],
                "time_range": {
                    "label": "2026 年 9 月",
                    "start": "2026-09-01",
                    "end": "2026-09-30",
                    "grain": "day",
                },
                "result_shape": "table",
                "select_columns": ["order_name", "sales_amount"],
            }
        )
        one_sided = self.guard.validate(
            "SELECT so.name AS order_name, so.amount_untaxed AS sales_amount "
            "FROM sale_order so WHERE so.company_id = 1 "
            "AND so.date_order >= DATE '2026-09-01'",
            plan=plan,
            question="列出 2026 年 9 月销售订单明细",
        )
        bounded = self.guard.validate(
            "SELECT so.name AS order_name, so.amount_untaxed AS sales_amount "
            "FROM sale_order so WHERE so.company_id = 1 "
            "AND so.date_order >= DATE '2026-09-01' "
            "AND so.date_order < DATE '2026-10-01'",
            plan=plan,
            question="列出 2026 年 9 月销售订单明细",
        )

        self.assertFalse(one_sided.safe)
        self.assertTrue(any("开始和结束边界" in error for error in one_sided.errors))
        self.assertTrue(bounded.safe, bounded.errors)


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

    def test_uninvoiced_quantity_is_a_retrievable_metric(self) -> None:
        semantics = SalesSemanticLayer.load()
        metric = semantics.metric_definitions["uninvoiced_quantity"]
        context = semantics.retrieve(
            "今年各产品销售数量与已开票数量的差额是多少？",
            company_id=1,
        )

        self.assertIn("product_uom_qty", metric["expression"])
        self.assertIn("qty_invoiced", metric["expression"])
        self.assertIn("uninvoiced_quantity", context.metric_ids)

    def test_full_ranking_chart_defaults_to_top_ten(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "ranking",
                "metric_ids": ["sales_amount"],
                "dimensions": ["salesperson"],
                "result_shape": "ranking",
                "select_columns": ["salesperson", "sales_amount"],
                "sort": [{"field": "sales_amount", "direction": "desc"}],
                "row_limit": None,
            }
        )
        chart = build_chart_spec(
            "今年各销售员的销售额排名如何？",
            ["salesperson", "sales_amount"],
            [
                {"salesperson": "A", "sales_amount": 100.0},
                {"salesperson": "B", "sales_amount": 90.0},
            ],
            query_plan=plan,
        )

        self.assertEqual(chart["type"], "bar")
        self.assertEqual(chart["top_n"], 10)

        model_plan = parse_chart_plan(
            '{"type":"bar","title":"销售员排名","x_field":"salesperson",'
            '"series":[{"field":"sales_amount","label":null}],'
            '"sort_by":null,"sort_order":null,"top_n":null,"reason":"类别排名"}',
            profile=build_data_profile(
                ["salesperson", "sales_amount"],
                [
                    {"salesperson": "A", "sales_amount": 100.0},
                    {"salesperson": "B", "sales_amount": 90.0},
                ],
            ),
            query_plan=plan,
        )
        self.assertEqual(model_plan.top_n, 10)


if __name__ == "__main__":
    unittest.main()
