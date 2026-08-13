from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field


PROJECT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi import SalesAgent, classify_intent  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.observability import langfuse_is_configured  # noqa: E402
from app.schemas.query_plan import QueryType  # noqa: E402
from app.state import get_state_store  # noqa: E402
from app.windows_loop import selector_loop_factory  # noqa: E402


DATASET_NAME = "odoo-agent/sales-golden-v1"
DEFAULT_DATASET = PROJECT_DIR / "evals" / "datasets" / "sales_golden.jsonl"


class GoldenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4_000)
    history: list[dict[Literal["role", "content"], str]] = Field(default_factory=list)


class GoldenExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["general", "semantic", "data"]
    query_type: QueryType | None = None
    metric_ids: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    data_accessed: bool
    interrupt: bool
    allow_empty: bool = False


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    input: GoldenInput
    expected_output: GoldenExpected


def load_dataset(path: Path) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        item = GoldenItem.model_validate_json(raw_line)
        if item.id in ids:
            raise ValueError(f"duplicate dataset id at line {line_number}: {item.id}")
        ids.add(item.id)
        items.append(item)
    if len(items) < 20:
        raise ValueError("sales golden dataset must contain at least 20 items")
    return items


def validate_static(items: list[GoldenItem]) -> list[dict[str, object]]:
    results = []
    for item in items:
        actual_intent = classify_intent(item.input.question, item.input.history)
        passed = actual_intent == item.expected_output.intent
        results.append(
            {
                "id": item.id,
                "passed": passed,
                "checks": {"intent": passed},
                "actual": {"intent": actual_intent},
            }
        )
    return results


async def run_live(items: list[GoldenItem]) -> list[dict[str, object]]:
    settings = get_settings()
    routing = settings.routing()
    for role in (routing.sql, routing.answer, routing.general):
        if not role.configured:
            raise RuntimeError(f"provider not configured: {role.name}")

    state_store = get_state_store()
    await state_store.start(settings.state_database())
    try:
        agent = SalesAgent(
            routing.general,
            settings.database(),
            routing=routing,
            checkpointer=state_store.checkpointer,
            semantic_config=settings.semantic(),
        )
        results = []
        for item in items:
            outcome = await agent.run(
                question=item.input.question,
                history=item.input.history,
                session_id=f"eval-{item.id}-{uuid5(NAMESPACE_URL, item.id).hex[:8]}",
            )
            plan = outcome.query_plan
            checks = {
                "intent": outcome.intent == item.expected_output.intent,
                "interrupt": outcome.interrupted == item.expected_output.interrupt,
                "query_type": (
                    item.expected_output.query_type is None
                    or (plan is not None and plan.query_type == item.expected_output.query_type)
                ),
                "metrics": (
                    not item.expected_output.metric_ids
                    or (
                        plan is not None
                        and set(item.expected_output.metric_ids).issubset(plan.metric_ids)
                    )
                ),
                "dimensions": (
                    not item.expected_output.dimensions
                    or (
                        plan is not None
                        and set(item.expected_output.dimensions).issubset(plan.dimensions)
                    )
                ),
                "data_accessed": (
                    outcome.data_accessed == item.expected_output.data_accessed
                    or (item.expected_output.allow_empty and outcome.data_accessed)
                ),
                "readonly_sql": not outcome.sql
                or outcome.sql.lstrip().upper().startswith(("SELECT", "WITH")),
            }
            results.append(
                {
                    "id": item.id,
                    "passed": all(checks.values()),
                    "checks": checks,
                    "actual": {
                        "intent": outcome.intent,
                        "query_type": plan.query_type if plan else None,
                        "metric_ids": plan.metric_ids if plan else [],
                        "dimensions": plan.dimensions if plan else [],
                        "data_accessed": outcome.data_accessed,
                        "interrupted": outcome.interrupted,
                        "row_count": len(outcome.rows),
                        "answer_mode": outcome.answer_mode,
                        "total_tokens": outcome.total_tokens,
                        "estimated_cost_usd": outcome.estimated_cost_usd,
                        "semantic_provider": settings.semantic_provider,
                    },
                }
            )
        return results
    finally:
        await state_store.close()


def sync_langfuse_dataset(items: list[GoldenItem]) -> None:
    if not langfuse_is_configured():
        raise RuntimeError("Langfuse is not configured")
    from langfuse import get_client

    client = get_client()
    client.create_dataset(
        name=DATASET_NAME,
        description="Odoo Agent 销售 Text2SQL 黄金问题集；仅包含问题和期望结构，不含业务结果明细。",
        metadata={"semantic_scope": "sales", "access_mode": "read-only", "version": 1},
    )
    for item in items:
        client.create_dataset_item(
            id=str(uuid5(NAMESPACE_URL, f"{DATASET_NAME}/{item.id}")),
            dataset_name=DATASET_NAME,
            input=item.input.model_dump(mode="json"),
            expected_output=item.expected_output.model_dump(mode="json"),
            metadata={"case_id": item.id},
        )
    client.flush()


def report(results: list[dict[str, object]], mode: str) -> dict[str, object]:
    passed = sum(1 for item in results if item["passed"])
    return {
        "dataset": DATASET_NAME,
        "mode": mode,
        "created_at": datetime.now().astimezone().isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or run the sales golden dataset.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--live", action="store_true", help="Call the configured models and Odoo read-only database.")
    parser.add_argument("--sync-langfuse", action="store_true", help="Upsert the local cases into a Langfuse Dataset.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    items = load_dataset(args.dataset)
    if args.limit > 0:
        items = items[: args.limit]
    if args.sync_langfuse:
        sync_langfuse_dataset(items)

    mode = "live" if args.live else "static"
    if args.live:
        with asyncio.Runner(loop_factory=selector_loop_factory) as runner:
            results = runner.run(run_live(items))
    else:
        results = validate_static(items)
    payload = report(results, mode)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
