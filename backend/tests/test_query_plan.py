from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.agent import (  # noqa: E402
    format_plan_validation_error,
    parse_sql_generation_payload,
)
from app.bi.prompts import sql_repair_prompt  # noqa: E402
from app.bi.deterministic_answer import (  # noqa: E402
    build_deterministic_answer,
    can_answer_deterministically,
)
from app.schemas.query_plan import QueryPlan  # noqa: E402


class QueryPlanTests(unittest.TestCase):
    @staticmethod
    def _ranking_payload(*, row_limit: int | None, with_limit: bool) -> str:
        import json

        limit_sql = f" LIMIT {row_limit}" if with_limit and row_limit else ""
        return json.dumps(
            {
                "plan": {
                    "query_type": "ranking",
                    "metric_ids": ["sales_amount"],
                    "dimensions": ["salesperson"],
                    "filters": [
                        {
                            "field": "company_id",
                            "operator": "eq",
                            "value": 1,
                            "source": "system_required",
                        },
                        {
                            "field": "state",
                            "operator": "in",
                            "value": ["sale", "done"],
                            "source": "metric_rule",
                        },
                    ],
                    "time_range": {
                        "label": "今年",
                        "start": "2026-01-01",
                        "end": "2026-12-31",
                        "grain": "year",
                    },
                    "result_shape": "ranking",
                    "select_columns": ["salesperson", "sales_amount"],
                    "sort": [{"field": "sales_amount", "direction": "desc"}],
                    "row_limit": row_limit,
                    "assumptions": [],
                    "ambiguities": [],
                    "requires_clarification": False,
                    "clarification_question": None,
                },
                "sql": (
                    "SELECT rp.name AS salesperson, "
                    "SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
                    "JOIN res_users ru ON ru.id = so.user_id "
                    "JOIN res_partner rp ON rp.id = ru.partner_id "
                    "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
                    "GROUP BY rp.name ORDER BY sales_amount DESC" + limit_sql
                ),
            },
            ensure_ascii=False,
        )

    def test_generation_payload_is_typed_and_filters_unknown_metrics(self) -> None:
        content = """{
          "plan": {
            "query_type": "kpi",
            "metric_ids": ["sales_amount", "unknown"],
            "dimensions": [],
            "filters": [
              {"field":"company_id","operator":"eq","value":1,"source":"system_required"},
              {"field":"state","operator":"in","value":["sale","done"],"source":"metric_rule"}
            ],
            "time_range": {"label": "本月", "start": null, "end": null, "grain": "none"},
            "result_shape": "scalar",
            "select_columns": ["sales_amount"],
            "sort": [],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": " SELECT 1 AS sales_amount "
        }"""

        payload = parse_sql_generation_payload(
            content,
            allowed_metric_ids=["sales_amount"],
        )

        self.assertEqual(payload.plan.metric_ids, ["sales_amount"])
        self.assertEqual(payload.sql, "SELECT 1 AS sales_amount")

    def test_clarification_requires_an_actionable_question(self) -> None:
        with self.assertRaises(ValidationError):
            QueryPlan.model_validate(
                {
                    "query_type": "kpi",
                    "requires_clarification": True,
                    "clarification_question": None,
                }
            )

    def test_filters_accept_strict_range_operators(self) -> None:
        plan = QueryPlan.model_validate(
            {
                "query_type": "kpi",
                "filters": [
                    {"field": "date_order", "operator": "gte", "value": "2026-08-01"},
                    {"field": "date_order", "operator": "lt", "value": "2026-09-01"},
                    {"field": "amount_total", "operator": "gt", "value": 0},
                ],
            }
        )

        self.assertEqual([item.operator for item in plan.filters], ["gte", "lt", "gt"])

    def test_dynamic_time_range_metadata_is_normalized_without_changing_sql(self) -> None:
        content = """{
          "plan": {
            "query_type": "kpi",
            "metric_ids": [],
            "dimensions": [],
            "filters": [
              {"field":"company_id","operator":"eq","value":1,"source":"system_required"},
              {"field":"date_order","operator":"gte","value":"date_trunc('month', CURRENT_DATE)","source":"user"},
              {"field":"date_order","operator":"lt","value":"date_trunc('month', CURRENT_DATE) + interval '1 month'","source":"user"}
            ],
            "time_range": {
              "label": "本月",
              "start": "date_trunc('month', CURRENT_DATE)",
              "end": "date_trunc('month', CURRENT_DATE) + interval '1 month'",
              "grain": "month"
            },
            "result_shape": "scalar",
            "select_columns": ["order_count"],
            "sort": [],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": "SELECT COUNT(*) AS order_count FROM sale_order WHERE company_id = 1 AND date_order >= date_trunc('month', CURRENT_DATE)"
        }"""

        payload = parse_sql_generation_payload(content, allowed_metric_ids=[])

        self.assertIsNone(payload.plan.time_range.start)
        self.assertIsNone(payload.plan.time_range.end)
        self.assertIn("date_trunc", payload.sql)

    def test_validation_error_summary_has_field_location_without_input_value(self) -> None:
        secret_marker = "should-not-be-copied"
        try:
            QueryPlan.model_validate(
                {
                    "query_type": "kpi",
                    "time_range": {"start": secret_marker},
                }
            )
        except ValidationError as exc:
            summary = format_plan_validation_error(exc)
        else:  # pragma: no cover - defensive assertion
            self.fail("expected ValidationError")

        self.assertIn("time_range.start", summary)
        self.assertNotIn(secret_marker, summary)

    def test_repair_prompt_repeats_complete_query_plan_contract(self) -> None:
        prompt = sql_repair_prompt(
            question="本月销售额",
            semantic_context="semantic context",
            previous_sql="SELECT 1",
            previous_plan={},
            errors=["plan.filters.1.operator:literal_error"],
        )

        self.assertIn('"query_type": "kpi|trend|ranking|detail|comparison"', prompt)
        self.assertIn("eq|neq|gt|gte|lt|lte|in|not_in|contains", prompt)
        self.assertIn('"result_shape": "scalar|time_series|ranking|table"', prompt)
        self.assertIn('"requires_clarification": false', prompt)

    def test_plan_semantics_are_normalized_to_stable_dimension_ids(self) -> None:
        content = """{
          "plan": {
            "query_type": "ranking",
            "metric_ids": ["sales_quantity", "delivered_quantity"],
            "dimensions": ["产品"],
            "filters": [
              {"field":"company_id","operator":"eq","value":1,"source":"system_required"},
              {"field":"state","operator":"in","value":["sale","done"],"source":"metric_rule"}
            ],
            "time_range": {"label":"今年","start":"2026-01-01","end":"2026-12-31","grain":"year"},
            "result_shape": "ranking",
            "select_columns": ["产品", "sales_quantity", "delivered_quantity"],
            "sort": [{"field":"sales_quantity","direction":"desc"}],
            "row_limit": 100,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": "SELECT product AS 产品, 1 AS sales_quantity, 1 AS delivered_quantity FROM sale_order WHERE company_id = 1 AND state IN ('sale','done') LIMIT 100"
        }"""

        payload = parse_sql_generation_payload(
            content,
            allowed_metric_ids=["sales_quantity", "delivered_quantity"],
            question="今年各产品的销售数量和交付数量分别是多少？",
        )

        self.assertEqual(payload.plan.query_type, "comparison")
        self.assertEqual(payload.plan.result_shape, "table")
        self.assertEqual(payload.plan.dimensions, ["product"])

    def test_year_dimension_is_inferred_from_result_contract(self) -> None:
        content = """{
          "plan": {
            "query_type": "comparison",
            "metric_ids": ["sales_amount"],
            "dimensions": [],
            "filters": [],
            "time_range": {"label":"今年和去年","start":"2025-01-01","end":"2026-12-31","grain":"year"},
            "result_shape": "table",
            "select_columns": ["year", "sales_amount"],
            "sort": [{"field":"year","direction":"asc"}],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": ""
        }"""

        payload = parse_sql_generation_payload(
            content,
            allowed_metric_ids=["sales_amount"],
            question="今年销售额与去年相比怎么样？",
        )

        self.assertEqual(payload.plan.dimensions, ["year"])

    def test_average_order_value_uses_currency_formatting(self) -> None:
        plan = QueryPlan.model_validate(
            {"query_type": "kpi", "metric_ids": ["average_order_value"]}
        )
        answer = build_deterministic_answer(
            plan=plan,
            columns=["average_order_value"],
            rows=[{"average_order_value": 123.45}],
            currency="USD",
        )

        self.assertIn("123.45 USD", answer)

    def test_ambiguous_filter_keeps_requested_kpi_semantics(self) -> None:
        content = """{
          "plan": {
            "query_type": "detail",
            "metric_ids": ["sales_amount"],
            "dimensions": ["客户"],
            "filters": [],
            "time_range": {"label":"今年","start":"2026-01-01","end":"2026-12-31","grain":"year"},
            "result_shape": "table",
            "select_columns": ["sales_amount"],
            "sort": [],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": ["客户不明确"],
            "requires_clarification": true,
            "clarification_question": "请提供客户名称或 ID。"
          },
          "sql": ""
        }"""

        payload = parse_sql_generation_payload(
            content,
            allowed_metric_ids=["sales_amount"],
            question="帮我查一下那个客户今年的销售额。",
        )

        self.assertEqual(payload.plan.query_type, "kpi")
        self.assertEqual(payload.plan.result_shape, "scalar")
        self.assertEqual(payload.plan.dimensions, [])

    @staticmethod
    def _pronoun_payload() -> str:
        """代词指代场景下模型可能返回的、本身不带澄清标记的计划。"""

        return """{
          "plan": {
            "query_type": "kpi",
            "metric_ids": ["sales_amount"],
            "dimensions": [],
            "filters": [
              {"field":"company_id","operator":"eq","value":1,"source":"system_required"},
              {"field":"state","operator":"in","value":["sale","done"],"source":"metric_rule"}
            ],
            "time_range": {"label":"今年","start":"2026-01-01","end":"2026-12-31","grain":"year"},
            "result_shape": "scalar",
            "select_columns": ["sales_amount"],
            "sort": [],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": "SELECT SUM(amount_untaxed) AS sales_amount FROM sale_order WHERE company_id = 1 AND state IN ('sale','done')"
        }"""

    def test_unresolved_customer_reference_forces_clarification_without_history(self) -> None:
        content = """{
          "plan": {
            "query_type": "kpi",
            "metric_ids": ["sales_amount"],
            "dimensions": [],
            "filters": [
              {"field":"company_id","operator":"eq","value":1,"source":"system_required"},
              {"field":"state","operator":"in","value":["sale","done"],"source":"metric_rule"}
            ],
            "time_range": {"label":"今年","start":"2026-01-01","end":"2026-12-31","grain":"year"},
            "result_shape": "scalar",
            "select_columns": ["sales_amount"],
            "sort": [],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": "SELECT SUM(amount_untaxed) AS sales_amount FROM sale_order WHERE company_id = 1 AND state IN ('sale','done')"
        }"""

        payload = parse_sql_generation_payload(
            content,
            allowed_metric_ids=["sales_amount"],
            question="帮我查一下那个客户今年的销售额。",
            history=[],
        )

        self.assertTrue(payload.plan.requires_clarification)
        self.assertIn("客户", payload.plan.clarification_question or "")
        self.assertEqual(payload.sql, "")

    def test_unrelated_history_does_not_resolve_a_customer_pronoun(self) -> None:
        # 实测缺陷：此前只要对话里有任何历史就跳过澄清，哪怕前面聊的完全无关。
        # 结果"那个客户今年的销售额"返回了全部 31 个客户的数据，冒充成某一个客户的答案。
        payload = parse_sql_generation_payload(
            self._pronoun_payload(),
            allowed_metric_ids=["sales_amount"],
            question="帮我查一下那个客户今年的销售额。",
            history=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，有什么可以帮你？"},
            ],
        )

        self.assertTrue(
            payload.plan.requires_clarification,
            "历史里没提过任何客户时，代词仍然无所指，必须澄清",
        )
        self.assertEqual(payload.sql, "")

    def test_history_naming_a_customer_does_resolve_the_pronoun(self) -> None:
        payload = parse_sql_generation_payload(
            self._pronoun_payload(),
            allowed_metric_ids=["sales_amount"],
            question="那个客户今年的销售额是多少？",
            history=[
                {"role": "user", "content": "客户 莱茵重工 上个月买了多少？"},
                {"role": "assistant", "content": "莱茵重工上月销售额为 12,000 USD。"},
            ],
        )

        # 前面确实点过名，这时代词有所指，不该再打断用户。
        self.assertFalse(payload.plan.requires_clarification)

    def test_full_ranking_without_explicit_top_n_accepts_null_row_limit(self) -> None:
        payload = parse_sql_generation_payload(
            self._ranking_payload(row_limit=None, with_limit=False),
            allowed_metric_ids=["sales_amount"],
            question="今年各销售员的销售额排名如何？",
        )

        self.assertEqual(payload.plan.query_type, "ranking")
        self.assertIsNone(payload.plan.row_limit)
        self.assertNotIn("LIMIT", payload.sql.upper())

        with self.assertRaisesRegex(ValueError, "QueryPlanUnexpectedRankingLimit"):
            parse_sql_generation_payload(
                self._ranking_payload(row_limit=10, with_limit=True),
                allowed_metric_ids=["sales_amount"],
                question="今年各销售员的销售额排名如何？",
            )

    def test_explicit_top_n_still_requires_row_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "QueryPlanRankingLimitMissing"):
            parse_sql_generation_payload(
                self._ranking_payload(row_limit=None, with_limit=False),
                allowed_metric_ids=["sales_amount"],
                question="今年销售额最高的十个销售员是谁？",
            )

    def test_invoice_difference_requires_component_and_difference_columns(self) -> None:
        content = """{
          "plan": {
            "query_type": "comparison",
            "metric_ids": ["uninvoiced_quantity"],
            "dimensions": ["product"],
            "filters": [
              {"field":"company_id","operator":"eq","value":1,"source":"system_required"},
              {"field":"state","operator":"in","value":["sale","done"],"source":"metric_rule"},
              {"field":"display_type","operator":"eq","value":null,"source":"metric_rule"}
            ],
            "time_range": {"label":"今年","start":"2026-01-01","end":"2026-12-31","grain":"year"},
            "result_shape": "table",
            "select_columns": ["product", "uninvoiced_quantity"],
            "sort": [],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": "SELECT pt.name AS product, SUM(sol.product_uom_qty - sol.qty_invoiced) AS uninvoiced_quantity FROM sale_order_line sol JOIN sale_order so ON so.id = sol.order_id JOIN product_product pp ON pp.id = sol.product_id JOIN product_template pt ON pt.id = pp.product_tmpl_id WHERE so.company_id = 1 AND so.state IN ('sale','done') AND sol.display_type IS NULL GROUP BY pt.name"
        }"""

        with self.assertRaisesRegex(
            ValueError,
            "QueryPlanInvoiceDifferenceColumnsMissing",
        ):
            parse_sql_generation_payload(
                content,
                allowed_metric_ids=[
                    "sales_quantity",
                    "invoiced_quantity",
                    "uninvoiced_quantity",
                ],
                question="今年各产品销售数量与已开票数量的差额是多少？",
            )

    def test_customer_name_filter_is_aligned_with_sql_where_field(self) -> None:
        content = """{
          "plan": {
            "query_type": "kpi",
            "metric_ids": ["sales_amount"],
            "dimensions": [],
            "filters": [
              {"field":"company_id","operator":"eq","value":1,"source":"system_required"},
              {"field":"state","operator":"in","value":["sale","done"],"source":"metric_rule"},
              {"field":"partner_name","operator":"eq","value":"Example Customer","source":"user"}
            ],
            "time_range": {"label":"今年","start":"2026-01-01","end":"2026-12-31","grain":"year"},
            "result_shape": "scalar",
            "select_columns": ["sales_amount"],
            "sort": [],
            "row_limit": null,
            "assumptions": [],
            "ambiguities": [],
            "requires_clarification": false,
            "clarification_question": null
          },
          "sql": "SELECT SUM(so.amount_untaxed) AS sales_amount FROM sale_order so JOIN res_partner rp ON rp.id = so.partner_id WHERE so.company_id = 1 AND so.state IN ('sale','done') AND rp.name = 'Example Customer'"
        }"""

        payload = parse_sql_generation_payload(
            content,
            allowed_metric_ids=["sales_amount"],
            question="今年 Example Customer 的销售额。",
            history=[],
        )

        user_filters = [item for item in payload.plan.filters if item.source == "user"]
        self.assertEqual(user_filters[0].field, "name")

    def test_model_payload_must_explicitly_supply_result_contract(self) -> None:
        with self.assertRaises(ValueError):
            parse_sql_generation_payload(
                '{"plan":{"query_type":"kpi"},"sql":"SELECT 1 AS value"}',
                allowed_metric_ids=[],
            )

    def test_simple_kpi_uses_deterministic_answer(self) -> None:
        plan = QueryPlan.model_validate(
            {"query_type": "kpi", "metric_ids": ["sales_amount"]}
        )
        rows = [{"sales_amount": 8768.0}]

        self.assertTrue(
            can_answer_deterministically(
                plan,
                ["sales_amount"],
                rows,
                truncated=False,
            )
        )
        answer = build_deterministic_answer(
            plan=plan,
            columns=["sales_amount"],
            rows=rows,
            currency="USD",
        )
        self.assertIn("8,768 USD", answer)


if __name__ == "__main__":
    unittest.main()
