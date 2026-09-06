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
        timeout=answer_config.timeout_seconds,
    )
    embedding_client = AsyncOpenAI(
        api_key=wiki_config.embedding_api_key,
        base_url=wiki_config.embedding_base_url,
        timeout=wiki_config.api_timeout_seconds,
    )
    judge = llm_factory(answer_config.model, client=judge_client)
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
        for name, scorer in scorers.items():
            kwargs: dict[str, Any] = {"user_input": item.question}
            if name in {"faithfulness", "answer_relevancy"}:
                kwargs["response"] = answer.content
            if name in {"faithfulness", "context_precision", "context_recall"}:
                kwargs["retrieved_contexts"] = contexts
            if name in {"context_precision", "context_recall"}:
                kwargs["reference"] = item.reference_answer
            score = await scorer.ascore(**kwargs)
            case_scores[name] = round(float(score.value), 4)
        output.append({"id": item.id, "scores": case_scores})
    aggregates = {
        name: round(statistics.fmean(case["scores"][name] for case in output), 4)
        for name in scorers
    }
    return {"case_count": len(output), "metrics": aggregates, "cases": output}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Wiki lexical/hybrid retrieval and optional Ragas metrics.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mode", choices=("lexical", "hybrid", "both"), default="both")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--ragas-max-cases", type=int, default=5)
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
        report["ragas"] = asyncio.run(
            run_ragas(hybrid_raw or raw, max_cases=max(1, args.ragas_max_cases))
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "case_count": len(items),
        "retrieval": {
            mode: summary["metrics"] for mode, summary in report["retrieval"].items()
        },
        "fallbacks": {
            mode: summary["fallback_reasons"] for mode, summary in report["retrieval"].items()
        },
        "ragas": report.get("ragas", {}).get("metrics"),
        "ragas_id": {
            mode: summary["ragas_id"].get("metrics")
            for mode, summary in report["retrieval"].items()
        },
        "report": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
