from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.run_wiki_rag_eval import (  # noqa: E402
    _aggregate_ragas,
    _retrieval_scores,
    archive_report,
    load_dataset,
    render_markdown,
    wiki_score_payload,
)


def _sample_report() -> dict:
    def metrics(recall: float, mrr: float) -> dict:
        return {
            "context_precision": round(recall - 0.1, 4),
            "context_recall": recall,
            "mrr": mrr,
            "hit_at_k": recall,
        }

    return {
        "generated_at": "2026-09-07T00:00:00+00:00",
        "dataset": "evals/datasets/wiki_rag_golden.jsonl",
        "case_count": 20,
        "retrieval": {
            "lexical": {
                "metrics": metrics(0.8750, 0.6158),
                "fallback_reasons": [],
                "effective_modes": ["lexical"],
                "ragas_id": {"metrics": {"id_context_recall": 0.875}},
                "cases": [],
            },
            "hybrid": {
                "metrics": metrics(0.9500, 0.7042),
                "fallback_reasons": [],
                "effective_modes": ["hybrid"],
                "ragas_id": {"metrics": {"id_context_recall": 0.95}},
                "cases": [],
            },
        },
        "delta_hybrid_minus_lexical": {
            "context_recall": 0.075,
            "mrr": 0.0884,
            "context_precision": 0.075,
            "hit_at_k": 0.075,
        },
        "ragas": {
            "status": "completed",
            "metrics": {"faithfulness": 0.82, "answer_relevancy": 0.91},
        },
    }


class WikiRagReportTests(unittest.TestCase):
    """docs/20 P1-2: 报告必须可比——要有 summary.md、按日期归档、指标能进 Langfuse。"""

    def test_markdown_reports_both_modes_and_the_delta(self) -> None:
        text = render_markdown(_sample_report())

        self.assertIn("lexical", text)
        self.assertIn("hybrid", text)
        self.assertIn("0.8750", text)
        self.assertIn("0.9500", text)
        # 没有 delta 就没法一眼看出 hybrid 是否真的更好。
        self.assertIn("+0.0750", text)
        self.assertIn("faithfulness", text.casefold())

    def test_markdown_states_when_ragas_never_ran(self) -> None:
        report = _sample_report()
        report["ragas"] = None

        text = render_markdown(report)

        self.assertIn("未运行", text)
        self.assertNotIn("None", text)

    def test_archive_keeps_previous_runs_and_writes_both_formats(self) -> None:
        import tempfile

        report = _sample_report()
        with tempfile.TemporaryDirectory() as directory:
            reports_dir = Path(directory)
            first = archive_report(report, reports_dir=reports_dir, run_id="20260907-090000")
            second = archive_report(report, reports_dir=reports_dir, run_id="20260907-100000")

            self.assertNotEqual(first, second)
            for archive in (first, second):
                self.assertTrue((archive / "report.json").exists())
                self.assertTrue((archive / "summary.md").exists())
            # latest 仍然存在，但旧结果不再被覆盖丢弃。
            self.assertTrue((reports_dir / "wiki-rag-latest.json").exists())
            self.assertEqual(len(list(reports_dir.glob("*-wiki-rag"))), 2)

    def test_score_payload_carries_comparable_retrieval_metrics(self) -> None:
        scores = wiki_score_payload(_sample_report())
        by_name = {name: value for name, value, _ in scores}

        self.assertEqual(by_name["wiki-hybrid-recall"], 0.9500)
        self.assertEqual(by_name["wiki-lexical-recall"], 0.8750)
        self.assertEqual(by_name["wiki-hybrid-mrr"], 0.7042)
        self.assertEqual(by_name["wiki-faithfulness"], 0.82)
        self.assertEqual(by_name["wiki-answer-relevancy"], 0.91)
        self.assertTrue(all(isinstance(value, float) for value in by_name.values()))

    def test_score_payload_omits_ragas_metrics_that_never_ran(self) -> None:
        report = _sample_report()
        report["ragas"] = None

        names = {name for name, _, _ in wiki_score_payload(report)}

        self.assertIn("wiki-hybrid-recall", names)
        self.assertNotIn("wiki-faithfulness", names)


class RagasResilienceTests(unittest.TestCase):
    """一次判官超时不该丢掉整轮结果——检索指标是白算的，成本也白花了。"""

    METRICS = ("faithfulness", "answer_relevancy")

    def test_all_cases_scored_reports_completed(self) -> None:
        cases = [
            {"id": "a", "scores": {"faithfulness": 0.8, "answer_relevancy": 1.0}},
            {"id": "b", "scores": {"faithfulness": 0.6, "answer_relevancy": 0.8}},
        ]
        summary = _aggregate_ragas(cases, self.METRICS)

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["case_count"], 2)
        self.assertAlmostEqual(summary["metrics"]["faithfulness"], 0.7, places=4)
        self.assertEqual(summary["errors"], [])

    def test_partial_failure_keeps_the_scores_that_succeeded(self) -> None:
        cases = [
            {"id": "a", "scores": {"faithfulness": 0.8, "answer_relevancy": 1.0}},
            {
                "id": "b",
                "scores": {"answer_relevancy": 0.5},
                "errors": {"faithfulness": "InstructorRetryException"},
            },
        ]
        summary = _aggregate_ragas(cases, self.METRICS)

        self.assertEqual(summary["status"], "partial")
        # faithfulness 只有一个成功样本，就按一个样本算，并说明基数。
        self.assertAlmostEqual(summary["metrics"]["faithfulness"], 0.8, places=4)
        self.assertEqual(summary["scored_counts"]["faithfulness"], 1)
        self.assertAlmostEqual(summary["metrics"]["answer_relevancy"], 0.75, places=4)
        self.assertEqual(summary["scored_counts"]["answer_relevancy"], 2)
        self.assertIn("InstructorRetryException", str(summary["errors"]))

    def test_metric_with_no_successful_score_is_omitted_not_zeroed(self) -> None:
        cases = [{"id": "a", "scores": {"answer_relevancy": 0.9}, "errors": {"faithfulness": "Timeout"}}]
        summary = _aggregate_ragas(cases, self.METRICS)

        # 全失败的指标必须缺席，绝不能写成 0.0 —— 那会被读成“质量极差”。
        self.assertNotIn("faithfulness", summary["metrics"])
        self.assertIn("answer_relevancy", summary["metrics"])

    def test_no_cases_at_all_reports_failed(self) -> None:
        summary = _aggregate_ragas([], self.METRICS)

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["metrics"], {})


class WikiRagEvaluationTests(unittest.TestCase):
    def test_golden_dataset_has_reviewed_reference_paths(self) -> None:
        items = load_dataset(ROOT / "evals" / "datasets" / "wiki_rag_golden.jsonl")
        self.assertEqual(len(items), 26)
        wiki_root = ROOT.parent / "learn_odoo"
        for item in items:
            self.assertTrue(item.reference_answer)
            for relative_path in item.reference_paths:
                text = (wiki_root / relative_path).read_text(encoding="utf-8")
                self.assertRegex(text, r"status:\s*(reviewed|evergreen)")

    def test_ranked_context_metrics_reward_early_relevant_hits(self) -> None:
        relevant = ("a.md", "b.md")
        early = _retrieval_scores(["a.md", "x.md", "b.md"], relevant)
        late = _retrieval_scores(["x.md", "a.md", "b.md"], relevant)
        self.assertEqual(early["context_recall"], 1.0)
        self.assertGreater(early["context_precision"], late["context_precision"])
        self.assertEqual(early["mrr"], 1.0)


if __name__ == "__main__":
    unittest.main()
