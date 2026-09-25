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
    answer_grounded_evaluator,
    load_dataset,
    load_result_assertions,
    metric_correct_evaluator,
    sql_safe_evaluator,
    validate_static,
)
from evals.result_signature import compare_results  # noqa: E402
from evals.run_semantic_benchmark import (  # noqa: E402
    benchmark_run_evaluations,
    benchmark_run_name,
    summarize_benchmark,
)


class GoldenDatasetTests(unittest.TestCase):
    def test_sales_golden_dataset_static_cases_all_validate(self) -> None:
        items = load_dataset(DEFAULT_DATASET)
        results = validate_static(items)

        # 数量会随覆盖面增长；断言下界即可，不必每次扩题都改这个数字。
        self.assertGreaterEqual(len(items), 76)
        self.assertTrue(all(item["passed"] for item in results))
        self.assertTrue(any(item.expected_output.interrupt for item in items))
        self.assertTrue(any(item.id == "safety-prompt-injection" for item in items))

    def test_live_result_references_cover_all_non_interrupt_data_cases(self) -> None:
        items = load_dataset(DEFAULT_DATASET)
        assertions = load_result_assertions(DEFAULT_RESULT_REFERENCES)
        expected_ids = {
            item.id
            for item in items
            if item.expected_output.intent == "data"
            and not item.expected_output.interrupt
            and not item.expected_output.pending_reference
        }
        pending = {
            item.id for item in items if item.expected_output.pending_reference
        }

        self.assertEqual(set(assertions), expected_ids)
        # 待补签名的用例不该悄悄留着：这里让它显式可见，补完签名就把标记去掉。
        self.assertFalse(
            pending & set(assertions),
            "已有参考签名的用例应当移除 pending_reference 标记",
        )

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

    def test_langfuse_experiment_evaluators_emit_deterministic_boolean_scores(self) -> None:
        output = {
            "passed": True,
            "checks": {
                "agent_run": True,
                "intent": True,
                "interrupt": True,
                "query_type": True,
                "metrics": True,
                "dimensions": True,
                "data_accessed": True,
                "readonly_sql": True,
                "result_signature": True,
            },
        }

        evaluations = [
            sql_safe_evaluator(
                input={}, output=output, expected_output={}, metadata=None
            ),
            metric_correct_evaluator(
                input={}, output=output, expected_output={}, metadata=None
            ),
            answer_grounded_evaluator(
                input={}, output=output, expected_output={}, metadata=None
            ),
        ]

        self.assertEqual(
            [evaluation.name for evaluation in evaluations],
            ["sql-safe", "metric-correct", "answer-grounded"],
        )
        self.assertTrue(all(evaluation.value is True for evaluation in evaluations))
        self.assertTrue(all(evaluation.data_type == "BOOLEAN" for evaluation in evaluations))


class BenchmarkCostAttributionTests(unittest.TestCase):
    """docs/20 P1-1: 成本要能按 generation_role 归因，p50 要成为会失败的断言。"""

    @staticmethod
    def _report(
        provider: str,
        *,
        latency: float,
        passed: bool = True,
        role_usage: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "total": 1,
            "passed": int(passed),
            "failed": int(not passed),
            "results": [
                {
                    "id": "kpi-month-sales",
                    "category": "kpi",
                    "passed": passed,
                    "checks": {"intent": True, "result_signature": passed},
                    "actual": {
                        "semantic_provider": provider,
                        "latency_ms": latency,
                        "total_tokens": 100,
                        "estimated_cost_usd": 0.01,
                        "repair_count": 1,
                        "role_usage": role_usage
                        or {
                            "sql": {
                                "calls": 1,
                                "total_tokens": 70,
                                "estimated_cost_usd": 0.007,
                            },
                            "answer": {
                                "calls": 1,
                                "total_tokens": 30,
                                "estimated_cost_usd": 0.003,
                            },
                        },
                    },
                }
            ],
        }

    def test_provider_summary_attributes_tokens_and_cost_by_generation_role(self) -> None:
        summary = summarize_benchmark(
            {"native": [self._report("native", latency=1000) for _ in range(3)]}
        )

        by_role = summary["providers"]["native"]["by_role"]
        self.assertEqual(by_role["sql"]["calls"], 3)
        self.assertEqual(by_role["sql"]["total_tokens"], 210)
        self.assertAlmostEqual(by_role["sql"]["estimated_cost_usd"], 0.021, places=6)
        self.assertEqual(by_role["answer"]["total_tokens"], 90)
        # 每个 role 要能回答“它占了多少钱”，否则报表没法用于归因。
        self.assertAlmostEqual(by_role["sql"]["cost_share"], 0.7, places=4)
        self.assertAlmostEqual(by_role["answer"]["cost_share"], 0.3, places=4)

    def test_summary_compares_every_candidate_against_native(self) -> None:
        """三方对照：comparisons 要给出每个候选相对 native 的差值。

        原先这段差值计算硬编码成 native-vs-wren，加第三种语义层时会静默丢掉它。
        """
        summary = summarize_benchmark(
            {
                "native": [self._report("native", latency=1000) for _ in range(3)],
                "wren": [self._report("wren", latency=2000) for _ in range(3)],
                "cube": [
                    self._report("cube", latency=1500, passed=False) for _ in range(3)
                ],
            }
        )

        self.assertEqual(set(summary["comparisons"]), {"wren", "cube"})
        for candidate, block in summary["comparisons"].items():
            self.assertEqual(block["baseline"], "native")
            self.assertEqual(block["candidate"], candidate)

        self.assertAlmostEqual(summary["comparisons"]["wren"]["p50_latency_delta_ms"], 1000.0)
        self.assertAlmostEqual(summary["comparisons"]["cube"]["p50_latency_delta_ms"], 500.0)
        # cube 全挂，通过率差值必须是负的，不能因为新增语义层而被吞掉。
        self.assertLess(summary["comparisons"]["cube"]["pass_rate_delta"], 0)

        # 单数形式的 comparison 保持向后兼容：仍指向 wren。
        self.assertEqual(summary["comparison"]["candidate"], "wren")

    def test_summary_without_candidates_has_empty_comparisons(self) -> None:
        summary = summarize_benchmark(
            {"native": [self._report("native", latency=1000) for _ in range(3)]}
        )
        self.assertEqual(summary["comparisons"], {})
        self.assertEqual(summary["comparison"], {})

    def test_latency_budget_fails_when_p50_exceeds_eighteen_seconds(self) -> None:
        within = summarize_benchmark(
            {"native": [self._report("native", latency=17_000) for _ in range(3)]}
        )
        exceeded = summarize_benchmark(
            {"native": [self._report("native", latency=19_000) for _ in range(3)]}
        )

        self.assertEqual(within["latency_budget"]["p50_budget_ms"], 18_000)
        self.assertTrue(within["latency_budget"]["within_budget"])
        self.assertFalse(exceeded["latency_budget"]["within_budget"])
        self.assertIn("native", exceeded["latency_budget"]["breaches"])

    def test_benchmark_run_evaluations_carry_latency_token_and_cost_for_comparison(
        self,
    ) -> None:
        summary = summarize_benchmark(
            {"native": [self._report("native", latency=19_000) for _ in range(3)]}
        )

        evaluations = benchmark_run_evaluations(summary, provider="native")
        by_name = {evaluation.name: evaluation for evaluation in evaluations}

        self.assertEqual(
            sorted(by_name),
            sorted(
                [
                    "p50-latency-ms",
                    "p95-latency-ms",
                    "total-tokens",
                    "cost-usd",
                    "pass-rate",
                    "p50-latency-budget",
                ]
            ),
        )
        self.assertEqual(by_name["p50-latency-ms"].value, 19_000)
        self.assertEqual(by_name["total-tokens"].value, 300)
        self.assertEqual(by_name["p50-latency-budget"].data_type, "BOOLEAN")
        # p50 超预算时这条必须是 False，才算“会失败的断言”。
        self.assertFalse(by_name["p50-latency-budget"].value)
        self.assertTrue(
            all(
                evaluation.data_type == "NUMERIC"
                for name, evaluation in by_name.items()
                if name != "p50-latency-budget"
            )
        )

    def test_run_name_identifies_provider_model_and_commit(self) -> None:
        name = benchmark_run_name(
            provider="wren",
            model_provider="deepseek",
            git_sha="abc1234",
            round_number=2,
            timestamp="20260906-120000",
        )

        self.assertIn("wren", name)
        self.assertIn("deepseek", name)
        self.assertIn("abc1234", name)
        self.assertIn("r2", name)


if __name__ == "__main__":
    unittest.main()
