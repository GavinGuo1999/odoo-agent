from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.bi.prompts import knowledge_answer_prompt  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm import LLMGateway  # noqa: E402
from app.services.wiki_knowledge import WikiKnowledgeService, WikiSearchResult  # noqa: E402


DEFAULT_DATASET = ROOT / "evals" / "datasets" / "wiki_rag_golden.jsonl"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "wiki-rag-latest.json"


@dataclass(frozen=True, slots=True)
class GoldenWikiItem:
    id: str
    question: str
    reference_paths: tuple[str, ...]
    reference_answer: str


def load_dataset(path: Path) -> list[GoldenWikiItem]:
    items: list[GoldenWikiItem] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        try:
            item = GoldenWikiItem(
                id=str(payload["id"]),
                question=str(payload["question"]),
                reference_paths=tuple(str(value) for value in payload["reference_paths"]),
                reference_answer=str(payload["reference_answer"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Invalid Wiki dataset line {line_number}") from exc
        if not item.reference_paths:
            raise ValueError(f"Wiki dataset line {line_number} has no reference paths")
        items.append(item)
    if len({item.id for item in items}) != len(items):
        raise ValueError("Wiki dataset IDs must be unique")
    return items


def _retrieval_scores(retrieved: list[str], relevant: tuple[str, ...]) -> dict[str, float]:
    expected = set(relevant)
    matched = 0
    precision_sum = 0.0
    reciprocal_rank = 0.0
    for rank, path in enumerate(retrieved, start=1):
        if path not in expected:
            continue
        matched += 1
        precision_sum += matched / rank
        if not reciprocal_rank:
            reciprocal_rank = 1.0 / rank
    return {
        "context_precision": precision_sum / max(min(len(expected), len(retrieved)), 1),
        "context_recall": matched / max(len(expected), 1),
        "mrr": reciprocal_rank,
        "hit_at_k": 1.0 if matched else 0.0,
    }


def evaluate_retriever(
    *,
    items: list[GoldenWikiItem],
    mode: str,
    limit: int,
) -> tuple[dict[str, Any], list[tuple[GoldenWikiItem, WikiSearchResult]]]:
    config = replace(Settings().wiki(), retrieval_mode=mode)
    service = WikiKnowledgeService(config)
    cases: list[dict[str, Any]] = []
    raw_results: list[tuple[GoldenWikiItem, WikiSearchResult]] = []
    for item in items:
        result = service.search(item.question, limit=limit)
        retrieved = [hit.relative_path for hit in result.hits]
        scores = _retrieval_scores(retrieved, item.reference_paths)
        cases.append(
            {
                "id": item.id,
                "retrieved_paths": retrieved,
                "effective_mode": result.retrieval_mode,
                "reranked": result.reranked,
                "fallback_reason": result.fallback_reason,
                **{key: round(value, 4) for key, value in scores.items()},
            }
        )
        raw_results.append((item, result))
    metrics = {
        key: round(statistics.fmean(case[key] for case in cases), 4)
        for key in ("context_precision", "context_recall", "mrr", "hit_at_k")
    }
    return (
        {
            "requested_mode": mode,
            "case_count": len(cases),
            "metrics": metrics,
            "effective_modes": sorted({case["effective_mode"] for case in cases}),
            "fallback_reasons": sorted(
                {case["fallback_reason"] for case in cases if case["fallback_reason"]}
            ),
            "cases": cases,
        },
        raw_results,
    )


async def run_ragas_id_metrics(
    cases: list[dict[str, Any]],
    items: list[GoldenWikiItem],
) -> dict[str, Any]:
    try:
        from ragas import SingleTurnSample
        from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall
    except ImportError as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__}

    item_by_id = {item.id: item for item in items}
    precision_metric = IDBasedContextPrecision()
    recall_metric = IDBasedContextRecall()
    scored: list[dict[str, Any]] = []
    for case in cases:
        item = item_by_id[case["id"]]
        sample = SingleTurnSample(
            retrieved_context_ids=case["retrieved_paths"],
            reference_context_ids=list(item.reference_paths),
        )
        precision = await precision_metric.single_turn_ascore(sample)
        recall = await recall_metric.single_turn_ascore(sample)
        scored.append(
            {
                "id": item.id,
                "id_context_precision": round(float(precision), 4),
                "id_context_recall": round(float(recall), 4),
            }
        )
    return {
        "status": "completed",
        "implementation": "ragas-0.4.3-id-based",
        "case_count": len(scored),
        "metrics": {
            "id_context_precision": round(
                statistics.fmean(case["id_context_precision"] for case in scored),
                4,
            ),
            "id_context_recall": round(
                statistics.fmean(case["id_context_recall"] for case in scored),
                4,
            ),
        },
        "cases": scored,
    }


async def run_ragas(
    raw_results: list[tuple[GoldenWikiItem, WikiSearchResult]],
    *,
    max_cases: int,
    judge_timeout_seconds: float | None = None,
    judge_max_tokens: int = 8192,
) -> dict[str, Any]:
    try:
        from openai import AsyncOpenAI
        from ragas.embeddings.base import embedding_factory
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )
    except ImportError as exc:
        raise RuntimeError("RagasNotInstalled: install evals/requirements.txt") from exc

    settings = Settings()
    answer_config = settings.routing().answer
    wiki_config = settings.wiki()
    if not answer_config.configured:
        raise RuntimeError("AnswerModelNotConfigured")
    if not wiki_config.embedding_api_key:
        raise RuntimeError("EmbeddingModelNotConfigured")

    answer_gateway = LLMGateway(answer_config)
    judge_client = AsyncOpenAI(
        api_key=answer_config.api_key,
        base_url=answer_config.base_url,
        # 判官是推理模型，NLI 判定比一次普通问答慢得多，默认 90s 会超时。
        timeout=judge_timeout_seconds or answer_config.timeout_seconds,
    )
    embedding_client = AsyncOpenAI(
        api_key=wiki_config.embedding_api_key,
        base_url=wiki_config.embedding_base_url,
        timeout=wiki_config.api_timeout_seconds,
    )
    # RAGAS 的 InstructorModelArgs 默认 max_tokens=1024。判官是带推理的模型，
    # 思考 token 会先吃掉预算，结构化输出被截断后 instructor 抛
    # IncompleteOutputException，表现为 faithfulness 每个 case 都失败。
    judge = llm_factory(
        answer_config.model,
        client=judge_client,
        max_tokens=judge_max_tokens,
    )
    embeddings = embedding_factory(
        "openai",
        model=wiki_config.embedding_model,
        client=embedding_client,
    )
    scorers = {
        "faithfulness": Faithfulness(llm=judge),
        "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=judge),
        "context_recall": ContextRecall(llm=judge),
    }
    output: list[dict[str, Any]] = []
    for item, result in raw_results[:max_cases]:
        contexts = [hit.content for hit in result.hits]
        answer = await answer_gateway.complete(
            messages=[
                {
                    "role": "user",
                    "content": knowledge_answer_prompt(
                        question=item.question,
                        history=[],
                        knowledge_context=result.context(),
                        source_mode=False,
                    ),
                }
            ],
            generation_name="evaluate-wiki-answer",
            generation_role="wiki-ragas-answer",
            metadata={"dataset": "wiki-rag-golden", "case_id": item.id},
            config=answer_config,
        )
        case_scores: dict[str, float] = {}
        case_errors: dict[str, str] = {}
        for name, scorer in scorers.items():
            kwargs: dict[str, Any] = {"user_input": item.question}
            if name in {"faithfulness", "answer_relevancy"}:
                kwargs["response"] = answer.content
            if name in {"faithfulness", "context_precision", "context_recall"}:
                kwargs["retrieved_contexts"] = contexts
            if name in {"context_precision", "context_recall"}:
                kwargs["reference"] = item.reference_answer
            try:
                score = await scorer.ascore(**kwargs)
            except Exception as exc:
                # 判官是会超时的推理模型。单个指标失败不能连累其余指标、其余
                # 用例，更不能让整轮（含已算好的检索指标）付诸东流。
                case_errors[name] = type(exc).__name__
                print(f"ragas {name} failed on {item.id}: {type(exc).__name__}", flush=True)
                continue
            case_scores[name] = round(float(score.value), 4)
        entry: dict[str, Any] = {"id": item.id, "scores": case_scores}
        if case_errors:
            entry["errors"] = case_errors
        output.append(entry)
    return _aggregate_ragas(output, tuple(scorers))


def _aggregate_ragas(
    cases: list[dict[str, Any]],
    metric_names: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """按指标各自的成功样本聚合，并如实报告缺口。

    关键约定：某个指标一次都没成功时**省略**它，而不是记 0.0 —— 后者会被读成
    “质量极差”，比没有数字更糟。
    """

    metrics: dict[str, float] = {}
    scored_counts: dict[str, int] = {}
    for name in metric_names:
        values = [
            case["scores"][name]
            for case in cases
            if isinstance(case.get("scores"), dict) and name in case["scores"]
        ]
        scored_counts[name] = len(values)
        if values:
            metrics[name] = round(statistics.fmean(values), 4)

    errors = [
        {"id": case.get("id"), "metric": metric, "error_type": error_type}
        for case in cases
        for metric, error_type in (case.get("errors") or {}).items()
    ]
    if not cases or not metrics:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "completed"
    return {
        "status": status,
        "implementation": "ragas-0.4.3-collections",
        "case_count": len(cases),
        "metrics": metrics,
        "scored_counts": scored_counts,
        "errors": errors,
        "cases": cases,
    }


_METRIC_LABELS = (
    ("context_recall", "recall"),
    ("hit_at_k", "hit@k"),
    ("mrr", "MRR"),
    ("context_precision", "precision"),
)


def render_markdown(report: dict[str, Any]) -> str:
    """把报告渲染成可直接对比两次运行的 Markdown。

    对齐 `run_semantic_benchmark.py` 的做法：JSON 给机器，Markdown 给人。
    """

    retrieval = report.get("retrieval", {})
    lines = [
        "# Wiki RAG 检索评测",
        "",
        f"- 生成时间：{report.get('generated_at')}",
        f"- 数据集：`{report.get('dataset')}`",
        f"- 用例数：{report.get('case_count')}",
        "",
        "| 模式 | " + " | ".join(label for _, label in _METRIC_LABELS) + " | 降级原因 |",
        "| --- | " + " | ".join("---:" for _ in _METRIC_LABELS) + " | --- |",
    ]
    for mode, summary in retrieval.items():
        metrics = summary.get("metrics", {})
        fallbacks = summary.get("fallback_reasons") or []
        lines.append(
            "| "
            + " | ".join(
                [mode]
                + [f"{metrics.get(key, 0.0):.4f}" for key, _ in _METRIC_LABELS]
                + ["、".join(fallbacks) if fallbacks else "无"]
            )
            + " |"
        )

    delta = report.get("delta_hybrid_minus_lexical")
    if delta:
        lines.extend(
            [
                "",
                "## hybrid 相对 lexical",
                "",
                *(
                    f"- {label}：{delta.get(key, 0.0):+.4f}"
                    for key, label in _METRIC_LABELS
                    if key in delta
                ),
            ]
        )

    lines.extend(["", "## 需 LLM 评审的 RAGAS", ""])
    ragas = report.get("ragas")
    ragas_metrics = (ragas or {}).get("metrics") if isinstance(ragas, dict) else None
    if ragas_metrics:
        lines.append("| 指标 | 值 |")
        lines.append("| --- | ---: |")
        for name, value in ragas_metrics.items():
            lines.append(f"| {name} | {float(value):.4f} |")
    else:
        # 明确写成“未运行”，避免把缺失当成 0 分解读。
        lines.append("未运行（需 `--ragas`，会消耗 answer 模型与 embedding 额度）。")

    return "\n".join(lines) + "\n"


def wiki_score_payload(report: dict[str, Any]) -> list[tuple[str, float, str]]:
    """抽出可跨运行比较的关键指标，作为 (name, value, comment) 推给 Langfuse。"""

    scores: list[tuple[str, float, str]] = []
    for mode, summary in report.get("retrieval", {}).items():
        metrics = summary.get("metrics", {})
        for key, label in _METRIC_LABELS:
            if key not in metrics:
                continue
            short = {"context_recall": "recall", "context_precision": "precision"}.get(
                key, key.replace("_", "-")
            )
            scores.append(
                (
                    f"wiki-{mode}-{short}",
                    float(metrics[key]),
                    f"{label} for {mode} retrieval over {report.get('case_count')} cases.",
                )
            )

    ragas = report.get("ragas")
    ragas_metrics = (ragas or {}).get("metrics") if isinstance(ragas, dict) else None
    for name, value in (ragas_metrics or {}).items():
        if value is None:
            continue
        scores.append(
            (f"wiki-{name.replace('_', '-')}", float(value), f"RAGAS {name} on the hybrid path.")
        )
    return scores


def archive_report(
    report: dict[str, Any],
    *,
    reports_dir: Path,
    run_id: str,
) -> Path:
    """归档一次运行，同时刷新 latest 指针。

    此前只覆盖写 `wiki-rag-latest.json`，上一次结果直接丢失，两次运行无法对比。
    """

    archive_dir = reports_dir / f"{run_id}-wiki-rag"
    archive_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    (archive_dir / "report.json").write_text(payload, encoding="utf-8")
    (archive_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    (reports_dir / "wiki-rag-latest.json").write_text(payload, encoding="utf-8")
    return archive_dir


def push_wiki_scores(report: dict[str, Any], *, run_id: str) -> str | None:
    """把关键指标作为 Score 挂到一条 Langfuse trace 上，供跨运行查看趋势。"""

    from app.observability import configure_langfuse_environment, langfuse_is_configured

    if not langfuse_is_configured():
        raise RuntimeError("Langfuse is not configured")
    from langfuse import get_client

    configure_langfuse_environment()
    client = get_client()
    with client.start_as_current_span(name="wiki-rag-eval") as span:
        span.update_trace(
            name=f"wiki-rag-eval-{run_id}",
            input={"dataset": report.get("dataset"), "case_count": report.get("case_count")},
            output={
                mode: summary.get("metrics")
                for mode, summary in report.get("retrieval", {}).items()
            },
            metadata={"run_id": run_id, "evaluator": "odoo-agent-wiki-rag-v1"},
        )
        trace_id = span.trace_id
    for name, value, comment in wiki_score_payload(report):
        client.create_score(
            trace_id=trace_id,
            name=name,
            value=value,
            data_type="NUMERIC",
            comment=comment,
        )
    client.flush()
    return trace_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Wiki lexical/hybrid retrieval and optional Ragas metrics.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mode", choices=("lexical", "hybrid", "both"), default="both")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--ragas-max-cases", type=int, default=5)
    parser.add_argument(
        "--ragas-timeout",
        type=float,
        default=300.0,
        help="Judge LLM timeout in seconds for RAGAS scoring (reasoning models are slow).",
    )
    parser.add_argument(
        "--ragas-max-tokens",
        type=int,
        default=8192,
        help="Judge LLM max_tokens; RAGAS defaults to 1024, which truncates reasoning models.",
    )
    parser.add_argument(
        "--push-langfuse",
        action="store_true",
        help="Attach the retrieval and RAGAS metrics to a Langfuse trace as Scores.",
    )
    args = parser.parse_args()

    items = load_dataset(args.dataset)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "case_count": len(items),
        "retrieval": {},
    }
    hybrid_raw: list[tuple[GoldenWikiItem, WikiSearchResult]] = []
    modes = ("lexical", "hybrid") if args.mode == "both" else (args.mode,)
    for mode in modes:
        summary, raw = evaluate_retriever(items=items, mode=mode, limit=args.limit)
        summary["ragas_id"] = asyncio.run(run_ragas_id_metrics(summary["cases"], items))
        report["retrieval"][mode] = summary
        if mode == "hybrid":
            hybrid_raw = raw
    if len(modes) == 2:
        report["delta_hybrid_minus_lexical"] = {
            name: round(
                report["retrieval"]["hybrid"]["metrics"][name]
                - report["retrieval"]["lexical"]["metrics"][name],
                4,
            )
            for name in report["retrieval"]["lexical"]["metrics"]
        }
    if args.ragas:
        try:
            report["ragas"] = asyncio.run(
                run_ragas(
                    hybrid_raw or raw,
                    max_cases=max(1, args.ragas_max_cases),
                    judge_timeout_seconds=args.ragas_timeout,
                    judge_max_tokens=args.ragas_max_tokens,
                )
            )
        except Exception as exc:
            # 检索指标已经算完了，不能因为 RAGAS 这一层失败就整轮丢弃。
            report["ragas"] = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "metrics": {},
            }
            print(f"ragas stage failed: {type(exc).__name__}: {exc}", flush=True)

    run_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    reports_dir = args.output.parent
    reports_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = archive_report(report, reports_dir=reports_dir, run_id=run_id)

    trace_id = None
    push_error = None
    if args.push_langfuse:
        try:
            trace_id = push_wiki_scores(report, run_id=run_id)
        except Exception as exc:
            # 归档已经落盘，推送失败不该让整轮看起来像失败。
            push_error = f"{type(exc).__name__}: {exc}"
            print(f"langfuse push failed: {push_error}", flush=True)

    print(json.dumps({
        "case_count": len(items),
        "retrieval": {
            mode: summary["metrics"] for mode, summary in report["retrieval"].items()
        },
        "fallbacks": {
            mode: summary["fallback_reasons"] for mode, summary in report["retrieval"].items()
        },
        "ragas": (report.get("ragas") or {}).get("metrics"),
        "ragas_id": {
            mode: summary["ragas_id"].get("metrics")
            for mode, summary in report["retrieval"].items()
        },
        "archive": str(archive_dir),
        "latest": str(reports_dir / "wiki-rag-latest.json"),
        "langfuse_trace_id": trace_id,
        "langfuse_error": push_error,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
