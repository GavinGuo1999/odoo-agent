from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.agent import parse_sql_generation_payload  # noqa: E402
from app.bi.deterministic_answer import (  # noqa: E402
    build_deterministic_answer,
    can_answer_deterministically,
)
from app.schemas.query_plan import QueryPlan  # noqa: E402


class QueryPlanTests(unittest.TestCase):
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
