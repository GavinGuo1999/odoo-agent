from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.eval_reports import EvalReportService  # noqa: E402


def _semantic_summary(*, created_at: str, native_p50: float, wren_pass: float) -> dict:
    def provider(pass_rate: float, p50: float, cost: float) -> dict:
        return {
            "observations": 60,
            "passed": int(round(pass_rate * 60)),
            "pass_rate": pass_rate,
            "structural_pass_rate": 1.0,
            "result_match_rate": 1.0,
            "latency_ms": {"p50": p50, "p95": p50 * 5},
            "total_tokens": 203370,
            "estimated_cost_usd": cost,
            "repair_count": 4,
            "rounds": [],
            "categories": {},
            "by_role": {
                "sql": {
                    "calls": 60,
                    "total_tokens": 140000,
                    "estimated_cost_usd": cost * 0.7,
                    "cost_share": 0.7,
                    "token_share": 0.69,
                },
                "answer": {
                    "calls": 60,
                    "total_tokens": 63370,
                    "estimated_cost_usd": cost * 0.3,
                    "cost_share": 0.3,
                    "token_share": 0.31,
                },
            },
        }

    return {
        "report_version": "odoo-agent-semantic-ab-v2",
        "dataset": "odoo-agent/sales-golden-v1",
        "created_at": created_at,
        "runs_per_provider": 3,
        "providers": {
            "native": provider(1.0, native_p50, 0.107130),
            "wren": provider(wren_pass, 11660.0, 0.140127),
        },
        "comparison": {"pass_rate_delta": round(wren_pass - 1.0, 4)},
        "latency_budget": {
            "p50_budget_ms": 18000,
            "within_budget": True,
            "breaches": [],
        },
    }


def _wiki_report(*, generated_at: str, hybrid_recall: float, ragas: dict | None) -> dict:
    def metrics(recall: float) -> dict:
        return {
            "context_precision": recall - 0.05,
            "context_recall": recall,
            "mrr": recall - 0.2,
            "hit_at_k": recall,
        }

    return {
        "generated_at": generated_at,
        "dataset": "evals/datasets/wiki_rag_golden.jsonl",
        "case_count": 20,
        "retrieval": {
            "lexical": {"metrics": metrics(0.8750), "fallback_reasons": [], "cases": []},
            "hybrid": {"metrics": metrics(hybrid_recall), "fallback_reasons": [], "cases": []},
        },
        "delta_hybrid_minus_lexical": {"context_recall": round(hybrid_recall - 0.8750, 4)},
        "ragas": ragas,
    }


class EvalReportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_semantic(self, name: str, payload: dict) -> None:
        directory = self.reports / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "summary.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def _write_wiki(self, name: str, payload: dict) -> None:
        directory = self.reports / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_missing_reports_directory_is_reported_not_crashed(self) -> None:
        service = EvalReportService(self.reports / "does-not-exist")

        summary = service.summary()

        # evals/reports 是 gitignore 的，新克隆必然没有数据，页面要能明确说明。
        self.assertFalse(summary["available"])
        self.assertIsNone(summary["semantic_benchmark"])
        self.assertIsNone(summary["wiki_rag"])
        self.assertEqual(summary["semantic_history"], [])

    def test_latest_semantic_benchmark_wins_and_history_is_newest_first(self) -> None:
        self._write_semantic(
            "20260901-ab", _semantic_summary(created_at="2026-09-01T10:00:00+08:00", native_p50=7000.0, wren_pass=0.90)
        )
        self._write_semantic(
            "20260903-ab", _semantic_summary(created_at="2026-09-03T10:00:00+08:00", native_p50=6270.0, wren_pass=0.9833)
        )
        service = EvalReportService(self.reports)

        summary = service.summary()
        benchmark = summary["semantic_benchmark"]

        self.assertTrue(summary["available"])
        self.assertEqual(benchmark["run_id"], "20260903-ab")
        self.assertEqual(benchmark["providers"]["native"]["latency_ms"]["p50"], 6270.0)
        self.assertEqual([run["run_id"] for run in summary["semantic_history"]], ["20260903-ab", "20260901-ab"])
        # 历史条目要足够画趋势，但不该把整份报告塞给前端。
        self.assertNotIn("providers", summary["semantic_history"][0])
        self.assertAlmostEqual(summary["semantic_history"][0]["providers_summary"]["wren"]["pass_rate"], 0.9833)

    def test_latest_wiki_report_includes_ragas_status_and_limits(self) -> None:
        self._write_wiki(
            "20260907-095649-wiki-rag",
            _wiki_report(
                generated_at="2026-09-07T01:16:07+00:00",
                hybrid_recall=0.95,
                ragas={
                    "status": "completed",
                    "case_count": 5,
                    "metrics": {"faithfulness": 0.9075, "answer_relevancy": 0.8376},
                    "scored_counts": {"faithfulness": 5, "answer_relevancy": 5},
                    "errors": [],
                },
            ),
        )
        service = EvalReportService(self.reports)

        wiki = service.summary()["wiki_rag"]

        self.assertEqual(wiki["run_id"], "20260907-095649-wiki-rag")
        self.assertAlmostEqual(wiki["retrieval"]["hybrid"]["context_recall"], 0.95)
        self.assertEqual(wiki["ragas"]["status"], "completed")
        self.assertAlmostEqual(wiki["ragas"]["metrics"]["faithfulness"], 0.9075)
        # 样本量必须一起给出，否则 0.9075 会被当成全量结论。
        self.assertEqual(wiki["ragas"]["case_count"], 5)

    def test_ragas_that_never_ran_is_distinguished_from_a_zero_score(self) -> None:
        self._write_wiki(
            "20260906-wiki-rag",
            _wiki_report(generated_at="2026-09-06T00:00:00+00:00", hybrid_recall=0.95, ragas=None),
        )
        service = EvalReportService(self.reports)

        wiki = service.summary()["wiki_rag"]

        self.assertEqual(wiki["ragas"]["status"], "not_run")
        self.assertEqual(wiki["ragas"]["metrics"], {})

    def test_latest_is_decided_by_timestamp_not_directory_name(self) -> None:
        # 真实归档里 20260903-native-wren-ab-final 比 20260903-product-name-guard
        # 晚跑，但字母序会把后者排在前面，导致页面显示错误的“最近一次”。
        self._write_semantic(
            "20260903-product-name-guard",
            _semantic_summary(created_at="2026-09-03T09:31:22+08:00", native_p50=6500.0, wren_pass=0.95),
        )
        self._write_semantic(
            "20260903-native-wren-ab-final",
            _semantic_summary(created_at="2026-09-03T09:58:56+08:00", native_p50=6270.0, wren_pass=0.9833),
        )
        service = EvalReportService(self.reports)

        summary = service.summary()

        self.assertEqual(summary["semantic_benchmark"]["run_id"], "20260903-native-wren-ab-final")
        self.assertEqual(
            [run["run_id"] for run in summary["semantic_history"]],
            ["20260903-native-wren-ab-final", "20260903-product-name-guard"],
        )

    def test_report_without_a_timestamp_still_sorts_deterministically(self) -> None:
        payload = _semantic_summary(created_at="2026-09-03T09:58:56+08:00", native_p50=6270.0, wren_pass=0.98)
        payload.pop("created_at")
        self._write_semantic("20260902-no-timestamp", payload)
        self._write_semantic(
            "20260901-ab", _semantic_summary(created_at="2026-09-01T10:00:00+08:00", native_p50=7000.0, wren_pass=0.9)
        )
        service = EvalReportService(self.reports)

        history = service.summary()["semantic_history"]

        # 缺时间戳时退回目录名排序，不能抛异常也不能丢条目。
        self.assertEqual([run["run_id"] for run in history], ["20260902-no-timestamp", "20260901-ab"])

    def test_malformed_report_is_skipped_rather_than_breaking_the_page(self) -> None:
        broken = self.reports / "20260904-ab"
        broken.mkdir(parents=True)
        (broken / "summary.json").write_text("{ not json", encoding="utf-8")
        self._write_semantic(
            "20260903-ab", _semantic_summary(created_at="2026-09-03T10:00:00+08:00", native_p50=6270.0, wren_pass=0.9833)
        )
        service = EvalReportService(self.reports)

        summary = service.summary()

        self.assertEqual(summary["semantic_benchmark"]["run_id"], "20260903-ab")
        self.assertIn("20260904-ab", summary["skipped"])


if __name__ == "__main__":
    unittest.main()
