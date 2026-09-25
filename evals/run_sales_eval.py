from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field


PROJECT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_DIR / "backend"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi import SalesAgent, classify_intent  # noqa: E402
from app.bi.domain import build_domain_registry, classify_domain  # noqa: E402
from app.bi.semantic import SalesSemanticLayer  # noqa: E402
from app.bi.time_series import complete_year_months  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import OdooDatabase, ReadOnlySqlGuard  # noqa: E402
from app.observability import (  # noqa: E402
    configure_langfuse_environment,
    langfuse_is_configured,
)
from app.schemas.query_plan import QueryType  # noqa: E402
from app.state import get_state_store  # noqa: E402
from app.windows_loop import selector_loop_factory  # noqa: E402
from evals.result_signature import compare_results  # noqa: E402


DATASET_NAME = "odoo-agent/sales-golden-v1"
DEFAULT_DATASET = PROJECT_DIR / "evals" / "datasets" / "sales_golden.jsonl"
DEFAULT_RESULT_REFERENCES = (
    PROJECT_DIR / "evals" / "datasets" / "sales_result_references.jsonl"
)


class GoldenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4_000)
    history: list[dict[Literal["role", "content"], str]] = Field(default_factory=list)


class GoldenExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["general", "knowledge", "source", "semantic", "data", "hybrid"]
    # 业务域。默认 sales，所以既有 76 题一个字都不用改——它们本来就全是销售域，
    # 而且域路由对不含域关键词的问题会回落到默认域。
    domain: Literal["sales", "crm"] = "sales"
    query_type: QueryType | None = None
    metric_ids: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    data_accessed: bool
    interrupt: bool
    allow_empty: bool = False
    # 新增的数据类用例在参考结果签名补齐前先标这个位。签名依赖真实业务数据，
    # 没有数据时编一个签名比没有签名更糟——那会让回归测试对着假基线跑绿。
    pending_reference: bool = False


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    input: GoldenInput
    expected_output: GoldenExpected


class GoldenResultAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    reference_sql: str = Field(min_length=1, max_length=20_000)
    absolute_tolerance: float = Field(default=0.000001, ge=0)
    relative_tolerance: float = Field(default=0.000001, ge=0)
    order_sensitive: bool = True


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


def load_result_assertions(path: Path) -> dict[str, GoldenResultAssertion]:
    assertions: dict[str, GoldenResultAssertion] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        assertion = GoldenResultAssertion.model_validate_json(raw_line)
        if assertion.id in assertions:
            raise ValueError(
                f"duplicate result reference at line {line_number}: {assertion.id}"
            )
        assertions[assertion.id] = assertion
    return assertions


def validate_static(items: list[GoldenItem]) -> list[dict[str, object]]:
    """静态校验：意图和业务域两个确定性判定。

    域路由和意图路由一样不调模型，所以它能进静态门禁——不花额度、不碰数据库，
    每次提交都能跑。域判错的后果比意图判错更隐蔽：意图错了通常答不出来，
    域错了会拿另一个业务域的语义层去答，给出一个像样但答非所问的结果。
    """
    registry = build_domain_registry(database=get_settings().database())
    results = []
    for item in items:
        actual_intent = classify_intent(item.input.question, item.input.history)
        actual_domain, domain_scores = classify_domain(item.input.question, registry)
        intent_ok = actual_intent == item.expected_output.intent
        domain_ok = actual_domain == item.expected_output.domain
        results.append(
            {
                "id": item.id,
                "category": case_category(item),
                "passed": intent_ok and domain_ok,
                "checks": {"intent": intent_ok, "domain": domain_ok},
                "actual": {
                    "intent": actual_intent,
                    "domain": actual_domain,
                    "domain_scores": domain_scores,
                },
            }
        )
    return results


def case_category(item: GoldenItem) -> str:
    if item.expected_output.interrupt:
        return "clarification"
    return item.expected_output.query_type or item.expected_output.intent


async def run_live(
    items: list[GoldenItem],
    *,
    semantic_provider: Literal["native", "wren"] | None = None,
    model_provider: Literal["deepseek", "siliconflow"] | None = None,
    run_number: int = 1,
    result_assertions: dict[str, GoldenResultAssertion] | None = None,
) -> list[dict[str, object]]:
    settings = get_settings()
    routing = settings.routing(model_provider)
    for role in (routing.sql, routing.answer, routing.general):
        if not role.configured:
            raise RuntimeError(f"provider not configured: {role.name}")

    semantic_config = settings.semantic()
    if semantic_provider is not None:
        semantic_config = replace(semantic_config, provider=semantic_provider)
    database_config = settings.database()
    reference_database = OdooDatabase(database_config)
    reference_guard = ReadOnlySqlGuard(
        table_columns=SalesSemanticLayer.load().table_columns,
        company_id=database_config.company_id,
        max_rows=database_config.max_rows,
    )
    assertions = (
        result_assertions
        if result_assertions is not None
        else load_result_assertions(DEFAULT_RESULT_REFERENCES)
    )
    reference_cache: dict[str, tuple[list[str], list[dict[str, object]]]] = {}

    async def expected_result(
        item: GoldenItem,
        assertion: GoldenResultAssertion,
    ) -> tuple[list[str], list[dict[str, object]]]:
        cached = reference_cache.get(item.id)
        if cached is not None:
            return cached
        resolved_sql = assertion.reference_sql.replace(
            "{company_id}", str(database_config.company_id)
        )
        validation = reference_guard.validate(resolved_sql)
        if not validation.safe or not validation.sql:
            raise RuntimeError("InvalidReferenceSql")
        query_result = await reference_database.execute_readonly(validation.sql)
        rows, _ = complete_year_months(
            question=item.input.question,
            columns=query_result.columns,
            rows=query_result.rows,
        )
        cached = (query_result.columns, rows)
        reference_cache[item.id] = cached
        return cached

    state_store = get_state_store()
    await state_store.start(settings.state_database())
    try:
        agent = SalesAgent(
            routing.general,
            settings.database(),
            routing=routing,
            checkpointer=state_store.checkpointer,
            semantic_config=semantic_config,
        )
        results = []
        for item in items:
            started = perf_counter()
            try:
                outcome = await agent.run(
                    question=item.input.question,
                    history=item.input.history,
                    session_id=(
                        f"eval-{semantic_config.provider}-{run_number}-{item.id}-"
                        f"{uuid4().hex[:8]}"
                    ),
                )
            except Exception as exc:
                results.append(
                    {
                        "id": item.id,
                        "category": case_category(item),
                        "passed": False,
                        "checks": {"agent_run": False},
                        "actual": {
                            "error_type": type(exc).__name__,
                            "latency_ms": round((perf_counter() - started) * 1000, 2),
                            "semantic_provider": semantic_config.provider,
                            "run": run_number,
                        },
                    }
                )
                continue
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
            assertion = assertions.get(item.id)
            signature_payload: dict[str, object] | None = None
            if assertion is not None:
                try:
                    expected_columns, expected_rows = await expected_result(item, assertion)
                    comparison = compare_results(
                        actual_columns=outcome.columns,
                        actual_rows=outcome.rows,
                        expected_columns=expected_columns,
                        expected_rows=expected_rows,
                        absolute_tolerance=assertion.absolute_tolerance,
                        relative_tolerance=assertion.relative_tolerance,
                        order_sensitive=assertion.order_sensitive,
                    )
                    checks["result_signature"] = comparison.matches
                    signature_payload = {
                        "version": "result-signature-v1",
                        "matched": comparison.matches,
                        "actual": comparison.actual_signature,
                        "expected": comparison.expected_signature,
                        "diffs": [
                            {"path": diff.path, "message": diff.message}
                            for diff in comparison.diffs
                        ],
                    }
                except Exception as exc:
                    checks["result_signature"] = False
                    signature_payload = {
                        "version": "result-signature-v1",
                        "matched": False,
                        "error_type": type(exc).__name__,
                    }
            results.append(
                {
                    "id": item.id,
                    "category": case_category(item),
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
                        "role_usage": outcome.role_usage,
                        "semantic_provider": outcome.semantic_provider,
                        "repair_count": outcome.repair_count,
                        "latency_ms": round((perf_counter() - started) * 1000, 2),
                        "run": run_number,
                        "result_signature": signature_payload,
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

    configure_langfuse_environment()
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


def _boolean_evaluation(name: str, value: bool, comment: str) -> Any:
    from langfuse import Evaluation

    return Evaluation(
        name=name,
        value=value,
        data_type="BOOLEAN",
        comment=comment,
        metadata={"evaluator": "odoo-agent-deterministic-v1"},
    )


def sql_safe_evaluator(
    *,
    input: Any,
    output: Any,
    expected_output: Any,
    metadata: dict[str, Any] | None,
    **kwargs: Any,
) -> Any:
    checks = output.get("checks", {}) if isinstance(output, dict) else {}
    value = bool(checks.get("agent_run", True) and checks.get("readonly_sql", False))
    return _boolean_evaluation(
        "sql-safe",
        value,
        "Agent completed and the final SQL was empty or read-only." if value
        else "Agent failed or the final SQL did not satisfy the read-only contract.",
    )


def metric_correct_evaluator(
    *,
    input: Any,
    output: Any,
    expected_output: Any,
    metadata: dict[str, Any] | None,
    **kwargs: Any,
) -> Any:
    checks = output.get("checks", {}) if isinstance(output, dict) else {}
    fields = ("intent", "query_type", "metrics", "dimensions", "data_accessed")
    value = bool(checks.get("agent_run", True) and all(checks.get(field) for field in fields))
    return _boolean_evaluation(
        "metric-correct",
        value,
        "Intent, QueryPlan metric/dimension shape, and data access match the golden contract."
        if value
        else "At least one intent, metric, dimension, query type, or data access check failed.",
    )


def answer_grounded_evaluator(
    *,
    input: Any,
    output: Any,
    expected_output: Any,
    metadata: dict[str, Any] | None,
    **kwargs: Any,
) -> Any:
    checks = output.get("checks", {}) if isinstance(output, dict) else {}
    if "result_signature" in checks:
        value = bool(checks["result_signature"])
        basis = "reference result signature"
    elif expected_output and expected_output.get("interrupt"):
        value = bool(checks.get("interrupt"))
        basis = "expected human clarification"
    else:
        value = bool(checks.get("intent") and checks.get("data_accessed"))
        basis = "non-data behavior contract"
    return _boolean_evaluation(
        "answer-grounded",
        value,
        f"Deterministic grounding proxy: {basis}.",
    )


def run_langfuse_experiment(
    items: list[GoldenItem],
    *,
    semantic_provider: Literal["native", "wren"] | None,
    model_provider: Literal["deepseek", "siliconflow"] | None,
    experiment_name: str,
) -> Any:
    if not langfuse_is_configured():
        raise RuntimeError("Langfuse is not configured")
    from langfuse import Evaluation, get_client

    configure_langfuse_environment()
    client = get_client()
    dataset = client.get_dataset(DATASET_NAME)
    assertions = load_result_assertions(DEFAULT_RESULT_REFERENCES)

    async def task(*, item: Any, **kwargs: Any) -> dict[str, object]:
        raw_input = item.input if hasattr(item, "input") else item["input"]
        raw_expected = (
            item.expected_output
            if hasattr(item, "expected_output")
            else item["expected_output"]
        )
        raw_metadata = item.metadata if hasattr(item, "metadata") else item.get("metadata", {})
        golden = GoldenItem.model_validate(
            {
                "id": raw_metadata["case_id"],
                "input": raw_input,
                "expected_output": raw_expected,
            }
        )
        return (
            await run_live(
                [golden],
                semantic_provider=semantic_provider,
                model_provider=model_provider,
                result_assertions=assertions,
            )
        )[0]

    def pass_rate_evaluator(*, item_results: list[Any], **kwargs: Any) -> Any:
        passed = sum(
            1
            for item_result in item_results
            if isinstance(item_result.output, dict) and item_result.output.get("passed")
        )
        value = passed / len(item_results) if item_results else 0.0
        return Evaluation(
            name="pass-rate",
            value=value,
            data_type="NUMERIC",
            comment=f"{passed}/{len(item_results)} golden cases passed.",
        )

    run_name = (
        f"{semantic_provider or 'configured'}-{model_provider or 'configured'}-"
        f"{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}"
    )
    return dataset.run_experiment(
        name=experiment_name,
        run_name=run_name,
        description="Odoo Agent read-only sales golden regression.",
        task=task,
        evaluators=[
            sql_safe_evaluator,
            metric_correct_evaluator,
            answer_grounded_evaluator,
        ],
        run_evaluators=[pass_rate_evaluator],
        max_concurrency=1,
        metadata={
            "semantic_provider": semantic_provider or "configured",
            "model_provider": model_provider or "configured",
            "access_mode": "read-only",
            "dataset": DATASET_NAME,
        },
    )


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
    parser.add_argument(
        "--langfuse-experiment",
        action="store_true",
        help="Run the live golden set as a Langfuse Dataset Experiment.",
    )
    parser.add_argument(
        "--experiment-name",
        default="odoo-agent-sales-regression",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--semantic-provider", choices=("native", "wren"))
    parser.add_argument("--model-provider", choices=("deepseek", "siliconflow"))
    parser.add_argument("--run-number", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    items = load_dataset(args.dataset)
    if args.limit > 0:
        items = items[: args.limit]
    if args.langfuse_experiment and (not args.live or args.limit > 0):
        parser.error("--langfuse-experiment requires --live and the complete dataset")
    if args.sync_langfuse:
        sync_langfuse_dataset(items)

    if args.langfuse_experiment:
        sync_langfuse_dataset(items)
        experiment = run_langfuse_experiment(
            items,
            semantic_provider=args.semantic_provider,
            model_provider=args.model_provider,
            experiment_name=args.experiment_name,
        )
        experiment_payload = {
            "dataset": DATASET_NAME,
            "experiment_name": args.experiment_name,
            "dataset_run_id": getattr(experiment, "dataset_run_id", None),
            "dataset_run_url": getattr(experiment, "dataset_run_url", None),
            "total": len(experiment.item_results),
            "passed": sum(
                1
                for item_result in experiment.item_results
                if isinstance(item_result.output, dict)
                and item_result.output.get("passed")
            ),
        }
        experiment_payload["failed"] = (
            experiment_payload["total"] - experiment_payload["passed"]
        )
        rendered = json.dumps(experiment_payload, ensure_ascii=False, indent=2)
        print(rendered)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        return 0 if experiment_payload["failed"] == 0 else 1

    mode = "live" if args.live else "static"
    if args.live:
        with asyncio.Runner(loop_factory=selector_loop_factory) as runner:
            results = runner.run(
                run_live(
                    items,
                    semantic_provider=args.semantic_provider,
                    model_provider=args.model_provider,
                    run_number=args.run_number,
                )
            )
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
