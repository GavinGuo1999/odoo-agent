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
            "filters": [],
            "time_range": {"label": "本月", "start": null, "end": null, "grain": "none"},
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
