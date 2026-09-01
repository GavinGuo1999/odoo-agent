from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from evals.run_sales_eval import (  # noqa: E402
    DEFAULT_DATASET,
    DEFAULT_RESULT_REFERENCES,
    load_dataset,
    load_result_assertions,
    validate_static,
)
from evals.result_signature import compare_results  # noqa: E402
from evals.run_semantic_benchmark import summarize_benchmark  # noqa: E402


class GoldenDatasetTests(unittest.TestCase):
    def test_sales_golden_dataset_has_twenty_valid_static_cases(self) -> None:
        items = load_dataset(DEFAULT_DATASET)
        results = validate_static(items)

        self.assertEqual(len(items), 20)
        self.assertTrue(all(item["passed"] for item in results))
        self.assertTrue(any(item.expected_output.interrupt for item in items))
        self.assertTrue(any(item.id == "safety-prompt-injection" for item in items))

    def test_live_result_references_cover_all_non_interrupt_data_cases(self) -> None:
        items = load_dataset(DEFAULT_DATASET)
        assertions = load_result_assertions(DEFAULT_RESULT_REFERENCES)
        expected_ids = {
            item.id
            for item in items
            if item.expected_output.intent == "data" and not item.expected_output.interrupt
        }

        self.assertEqual(set(assertions), expected_ids)

    def test_result_signature_accepts_numeric_tolerance_and_midnight_dates(self) -> None:
        comparison = compare_results(
            actual_columns=["month", "sales_amount"],
            actual_rows=[{"month": "2026-08-01T00:00:00", "sales_amount": 100.004}],
            expected_columns=["month", "sales_amount"],
            expected_rows=[{"month": "2026-08-01", "sales_amount": 100.0}],
            absolute_tolerance=0.01,
            relative_tolerance=0.0001,
        )

        self.assertTrue(comparison.matches, comparison.diffs)
        self.assertEqual(len(comparison.actual_signature), 64)
        self.assertEqual(len(comparison.expected_signature), 64)

    def test_result_signature_reports_deterministic_safe_diff(self) -> None:
        comparison = compare_results(
            actual_columns=["sales_amount"],
            actual_rows=[{"sales_amount": 90.0}],
            expected_columns=["sales_amount"],
            expected_rows=[{"sales_amount": 100.0}],
            absolute_tolerance=0.01,
            relative_tolerance=0.0001,
        )

        self.assertFalse(comparison.matches)
        self.assertEqual(comparison.diffs[0].path, "rows[0].sales_amount")
        self.assertNotIn("90", comparison.diffs[0].message)
        self.assertNotIn("100", comparison.diffs[0].message)

    def test_three_round_ab_summary_reports_accuracy_latency_cost_and_repairs(self) -> None:
        def make_report(provider: str, latency: float, passed: bool) -> dict[str, object]:
            return {
                "total": 1,
                "passed": int(passed),
                "failed": int(not passed),
                "results": [
                    {
                        "id": "kpi-month-sales",
                        "category": "kpi",
                        "passed": passed,
                        "checks": {
                            "intent": True,
                            "result_signature": passed,
                        },
                        "actual": {
                            "semantic_provider": provider,
                            "latency_ms": latency,
                            "total_tokens": 100,
                            "estimated_cost_usd": 0.01,
                            "repair_count": 1,
                        },
                    }
                ],
            }

        summary = summarize_benchmark(
            {
                "native": [make_report("native", value, True) for value in (10, 20, 30)],
                "wren": [
                    make_report("wren", 15, True),
                    make_report("wren", 25, False),
                    make_report("wren", 35, True),
                ],
            }
        )

        self.assertEqual(summary["runs_per_provider"], 3)
        self.assertEqual(summary["providers"]["native"]["latency_ms"]["p95"], 30)
        self.assertEqual(summary["providers"]["native"]["total_tokens"], 300)
        self.assertEqual(summary["providers"]["native"]["repair_count"], 3)
        self.assertAlmostEqual(summary["providers"]["wren"]["result_match_rate"], 2 / 3, places=4)
        self.assertIn("pass_rate_delta", summary["comparison"])


if __name__ == "__main__":
    unittest.main()
