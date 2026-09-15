from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvalReportService:
    """读取 `evals/reports/` 下的归档，供“评测与质量”页展示。

    这些报告由评测脚本生成，而 `evals/reports/` 在 `.gitignore` 里，所以任何一次
    新克隆都必然没有数据。本服务只读、容错：目录缺失、文件损坏都返回可展示的状态，
    绝不抛异常打断页面。
    """

    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = reports_dir

    def summary(self) -> dict[str, Any]:
        skipped: list[str] = []
        semantic = self._collect_semantic(skipped)
        wiki = self._collect_wiki(skipped)
        return {
            "available": bool(semantic or wiki),
            "semantic_benchmark": semantic[0] if semantic else None,
            "semantic_history": [self._semantic_history_entry(run) for run in semantic],
            "wiki_rag": wiki[0] if wiki else None,
            # RAGAS 用 LLM 当判官，又慢又费钱，本来就不会每次都跑。所以"最近一次
            # 运行"未必是"最近一次有 RAGAS 的运行"——只看前者，页面会在一次不带
            # --ragas 的检索评测之后显示"未运行"，把已经建立的 Faithfulness 基线
            # 藏起来。这里单独取最近一条真正跑过的，并带上它来自哪一次。
            "wiki_ragas_latest": self._latest_ragas(wiki),
            "wiki_history": [self._wiki_history_entry(run) for run in wiki],
            "skipped": sorted(skipped),
        }

    @staticmethod
    def _latest_ragas(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
        """最近一条真正执行过 RAGAS 的运行（runs 已按时间倒序）。"""

        for run in runs:
            ragas = run.get("ragas") or {}
            if ragas.get("status") == "completed" and ragas.get("metrics"):
                return {
                    "run_id": run.get("run_id"),
                    "generated_at": run.get("generated_at"),
                    "ragas": ragas,
                }
        return None

    def _read_json(self, path: Path, skipped: list[str]) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            skipped.append(path.parent.name)
            return None
        return payload if isinstance(payload, dict) else None

    def _run_directories(self) -> list[Path]:
        if not self._reports_dir.is_dir():
            return []
        try:
            return sorted(
                (path for path in self._reports_dir.iterdir() if path.is_dir()),
                key=lambda path: path.name,
            )
        except OSError:
            return []

    @staticmethod
    def _run_moment(run: dict[str, Any], timestamp_key: str) -> datetime:
        """把一次运行归一成可比较的时间点。

        目录名的字母序不足以定序：同一天的 `20260903-native-wren-ab-final`（09:58）
        排在 `20260903-product-name-guard`（09:31）之后，会让页面把更旧的一次当成
        最近一次。优先用报告里的时间戳；缺失时退回目录名前缀里的日期/时间。
        """

        raw = run.get(timestamp_key)
        if isinstance(raw, str) and raw:
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                parsed = None
            if parsed is not None:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        match = re.match(r"(\d{8})(?:-(\d{6}))?", str(run.get("run_id") or ""))
        if match:
            stamp = match.group(1) + (match.group(2) or "000000")
            try:
                return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return datetime.min.replace(tzinfo=timezone.utc)

    def _sort_newest_first(
        self,
        runs: list[dict[str, Any]],
        timestamp_key: str,
    ) -> list[dict[str, Any]]:
        return sorted(
            runs,
            key=lambda run: (self._run_moment(run, timestamp_key), str(run.get("run_id") or "")),
            reverse=True,
        )

    def _collect_semantic(self, skipped: list[str]) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for directory in self._run_directories():
            summary_path = directory / "summary.json"
            if not summary_path.is_file():
                continue
            payload = self._read_json(summary_path, skipped)
            if payload is None or not isinstance(payload.get("providers"), dict):
                continue
            runs.append({"run_id": directory.name, **payload})
        return self._sort_newest_first(runs, "created_at")

    def _collect_wiki(self, skipped: list[str]) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for directory in self._run_directories():
            report_path = directory / "report.json"
            if not report_path.is_file():
                continue
            payload = self._read_json(report_path, skipped)
            if payload is None or not isinstance(payload.get("retrieval"), dict):
                continue
            runs.append(
                {
                    "run_id": directory.name,
                    "generated_at": payload.get("generated_at"),
                    "dataset": payload.get("dataset"),
                    "case_count": payload.get("case_count"),
                    "retrieval": {
                        mode: dict(section.get("metrics") or {})
                        for mode, section in payload["retrieval"].items()
                        if isinstance(section, dict)
                    },
                    "fallbacks": {
                        mode: list(section.get("fallback_reasons") or [])
                        for mode, section in payload["retrieval"].items()
                        if isinstance(section, dict)
                    },
                    "delta_hybrid_minus_lexical": payload.get("delta_hybrid_minus_lexical") or {},
                    "ragas": self._normalize_ragas(payload.get("ragas")),
                }
            )
        return self._sort_newest_first(runs, "generated_at")

    @staticmethod
    def _normalize_ragas(ragas: Any) -> dict[str, Any]:
        """把“没跑过”和“跑了但得分低”明确区分开。

        报告里 `ragas` 为 null 表示这一层从未执行，绝不能在前端显示成 0 分。
        """

        if not isinstance(ragas, dict):
            return {"status": "not_run", "metrics": {}, "case_count": 0, "errors": []}
        return {
            "status": ragas.get("status") or "unknown",
            "metrics": dict(ragas.get("metrics") or {}),
            "scored_counts": dict(ragas.get("scored_counts") or {}),
            "case_count": ragas.get("case_count") or 0,
            "errors": list(ragas.get("errors") or []),
            "error_type": ragas.get("error_type"),
        }

    @staticmethod
    def _semantic_history_entry(run: dict[str, Any]) -> dict[str, Any]:
        """趋势只需要少量标量，不把整份报告塞给前端。"""

        providers = run.get("providers") or {}
        return {
            "run_id": run.get("run_id"),
            "created_at": run.get("created_at"),
            "runs_per_provider": run.get("runs_per_provider"),
            "providers_summary": {
                name: {
                    "pass_rate": metrics.get("pass_rate"),
                    "p50": (metrics.get("latency_ms") or {}).get("p50"),
                    "p95": (metrics.get("latency_ms") or {}).get("p95"),
                    "total_tokens": metrics.get("total_tokens"),
                    "estimated_cost_usd": metrics.get("estimated_cost_usd"),
                }
                for name, metrics in providers.items()
                if isinstance(metrics, dict)
            },
        }

    @staticmethod
    def _wiki_history_entry(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run.get("run_id"),
            "generated_at": run.get("generated_at"),
            "case_count": run.get("case_count"),
            "retrieval": {
                mode: {
                    "context_recall": metrics.get("context_recall"),
                    "mrr": metrics.get("mrr"),
                }
                for mode, metrics in (run.get("retrieval") or {}).items()
            },
            "ragas_status": (run.get("ragas") or {}).get("status"),
        }
