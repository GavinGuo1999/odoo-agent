from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_DIR / "backend"
for import_path in (PROJECT_DIR, BACKEND_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from app.config import get_settings
from app.observability import (
    configure_langfuse_environment,
    langfuse_is_configured,
)
from app.windows_loop import selector_loop_factory
from evals.run_sales_eval import (
    DATASET_NAME,
    DEFAULT_DATASET,
    answer_grounded_evaluator,
    load_dataset,
    metric_correct_evaluator,
    report,
    run_live,
    sql_safe_evaluator,
)

# docs/20 P1-1：把 “p50 ≤ 18s” 从目标变成会失败的断言。
P50_LATENCY_BUDGET_MS = 18_000


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def _role_usage_summary(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按 generation_role 归因 Token 与成本。

    role 名与 `backend/app/bi/agent.py` 传给 `_usage_fields` 的 role、以及推给
    Langfuse 的 `generation_role` 是同一套（sql / answer / general / sql-repair /
    chart-planner），因此本地报表与 Langfuse 上的 generation 可以对上账。
    """

    totals: dict[str, dict[str, Any]] = {}
    for result in results:
        role_usage = result.get("actual", {}).get("role_usage") or {}
        if not isinstance(role_usage, dict):
            continue
        for role, usage in role_usage.items():
            if not isinstance(usage, dict):
                continue
            bucket = totals.setdefault(
                str(role),
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                },
            )
            bucket["calls"] += int(usage.get("calls") or 0)
            bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
            bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
            bucket["total_tokens"] += int(usage.get("total_tokens") or 0)
            bucket["estimated_cost_usd"] += float(usage.get("estimated_cost_usd") or 0.0)

    cost_total = sum(bucket["estimated_cost_usd"] for bucket in totals.values())
    token_total = sum(bucket["total_tokens"] for bucket in totals.values())
    for bucket in totals.values():
        bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"], 8)
        bucket["cost_share"] = (
            round(bucket["estimated_cost_usd"] / cost_total, 4) if cost_total else 0.0
        )
        bucket["token_share"] = (
            round(bucket["total_tokens"] / token_total, 4) if token_total else 0.0
        )
    return dict(sorted(totals.items()))


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile / 100) - 1)
    return round(ordered[index], 2)


def _provider_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    results = [
        result
        for run_report in reports
        for result in run_report.get("results", [])
        if isinstance(result, dict)
    ]
    latencies = [
        float(result.get("actual", {}).get("latency_ms"))
        for result in results
        if result.get("actual", {}).get("latency_ms") is not None
    ]
    signature_results = [
        result for result in results if "result_signature" in result.get("checks", {})
    ]
    structural_passed = sum(
        all(value for key, value in result.get("checks", {}).items() if key != "result_signature")
        for result in results
    )
    passed = sum(bool(result.get("passed")) for result in results)
    signature_passed = sum(
        bool(result.get("checks", {}).get("result_signature"))
        for result in signature_results
    )

    category_summaries: dict[str, dict[str, Any]] = {}
    for category in sorted({str(result.get("category", "unknown")) for result in results}):
        category_results = [
            result for result in results if str(result.get("category", "unknown")) == category
        ]
        category_latencies = [
            float(result.get("actual", {}).get("latency_ms"))
            for result in category_results
            if result.get("actual", {}).get("latency_ms") is not None
        ]
        category_passed = sum(bool(result.get("passed")) for result in category_results)
        category_summaries[category] = {
            "total": len(category_results),
            "passed": category_passed,
            "pass_rate": _rate(category_passed, len(category_results)),
            "latency_ms": {
                "p50": (
                    round(statistics.median(category_latencies), 2)
                    if category_latencies
                    else None
                ),
                "p95": _percentile(category_latencies, 95),
            },
        }

    rounds = []
    for round_number, run_report in enumerate(reports, 1):
        round_results = list(run_report.get("results", []))
        round_signatures = [
            result
            for result in round_results
            if "result_signature" in result.get("checks", {})
        ]
        rounds.append(
            {
                "round": round_number,
                "total": len(round_results),
                "passed": sum(bool(result.get("passed")) for result in round_results),
                "result_matched": sum(
                    bool(result.get("checks", {}).get("result_signature"))
                    for result in round_signatures
                ),
                "result_total": len(round_signatures),
            }
        )

    return {
        "observations": len(results),
        "passed": passed,
        "pass_rate": _rate(passed, len(results)),
        "structural_passed": structural_passed,
        "structural_pass_rate": _rate(structural_passed, len(results)),
        "result_matched": signature_passed,
        "result_total": len(signature_results),
        "result_match_rate": _rate(signature_passed, len(signature_results)),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2) if latencies else None,
            "p95": _percentile(latencies, 95),
        },
        "total_tokens": sum(
            int(result.get("actual", {}).get("total_tokens") or 0) for result in results
        ),
        "estimated_cost_usd": round(
            sum(
                float(result.get("actual", {}).get("estimated_cost_usd") or 0.0)
                for result in results
            ),
            8,
        ),
        "repair_count": sum(
            int(result.get("actual", {}).get("repair_count") or 0) for result in results
        ),
        "rounds": rounds,
        "categories": category_summaries,
        "by_role": _role_usage_summary(results),
    }


def summarize_benchmark(
    reports_by_provider: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    providers = {
        provider: _provider_summary(reports)
        for provider, reports in reports_by_provider.items()
    }
    comparison: dict[str, Any] = {}
    if "native" in providers and "wren" in providers:
        native = providers["native"]
        wren = providers["wren"]
        comparison = {
            "baseline": "native",
            "candidate": "wren",
            "pass_rate_delta": round(wren["pass_rate"] - native["pass_rate"], 4),
            "structural_pass_rate_delta": round(
                wren["structural_pass_rate"] - native["structural_pass_rate"], 4
            ),
            "result_match_rate_delta": round(
                wren["result_match_rate"] - native["result_match_rate"], 4
            ),
            "p50_latency_delta_ms": (
                round(wren["latency_ms"]["p50"] - native["latency_ms"]["p50"], 2)
                if wren["latency_ms"]["p50"] is not None
                and native["latency_ms"]["p50"] is not None
                else None
            ),
            "p95_latency_delta_ms": (
                round(wren["latency_ms"]["p95"] - native["latency_ms"]["p95"], 2)
                if wren["latency_ms"]["p95"] is not None
                and native["latency_ms"]["p95"] is not None
                else None
            ),
            "token_delta": wren["total_tokens"] - native["total_tokens"],
            "cost_delta_usd": round(
                wren["estimated_cost_usd"] - native["estimated_cost_usd"], 8
            ),
            "repair_delta": wren["repair_count"] - native["repair_count"],
        }
    run_counts = {len(reports) for reports in reports_by_provider.values()}
    breaches = sorted(
        provider
        for provider, metrics in providers.items()
        if metrics["latency_ms"]["p50"] is not None
        and metrics["latency_ms"]["p50"] > P50_LATENCY_BUDGET_MS
    )
    return {
        "report_version": "odoo-agent-semantic-ab-v2",
        "dataset": DATASET_NAME,
        "created_at": datetime.now().astimezone().isoformat(),
        "runs_per_provider": next(iter(run_counts)) if len(run_counts) == 1 else None,
        "providers": providers,
        "comparison": comparison,
        "latency_budget": {
            "p50_budget_ms": P50_LATENCY_BUDGET_MS,
            "within_budget": not breaches,
            "breaches": breaches,
        },
    }


def benchmark_run_name(
    *,
    provider: str,
    model_provider: str | None,
    git_sha: str,
    round_number: int,
    timestamp: str,
) -> str:
    """Langfuse run 名：语义层 + 模型 provider + git sha + 轮次，保证两次 run 可辨识。"""

    return "-".join(
        (
            provider,
            model_provider or "configured",
            git_sha or "nogit",
            f"r{round_number}",
            timestamp,
        )
    )


def current_git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _numeric_evaluation(name: str, value: float, comment: str) -> Any:
    from langfuse import Evaluation

    return Evaluation(
        name=name,
        value=value,
        data_type="NUMERIC",
        comment=comment,
        metadata={"evaluator": "odoo-agent-semantic-benchmark-v1"},
    )


def benchmark_run_evaluations(summary: dict[str, Any], *, provider: str) -> list[Any]:
    """把本地已算好的 p50/p95/Token/Cost 变成 Langfuse run 级别的分数。

    这是 docs/20 P1-1 的“对比层”：同一数据集的两次 run（不同 provider、模型或
    prompt 版本）在 Langfuse UI 上就能直接横比这些指标。
    """

    from langfuse import Evaluation

    metrics = summary["providers"][provider]
    latency = metrics["latency_ms"]
    budget = summary.get("latency_budget", {}).get("p50_budget_ms", P50_LATENCY_BUDGET_MS)
    p50 = latency["p50"]
    within_budget = p50 is not None and p50 <= budget

    return [
        _numeric_evaluation("p50-latency-ms", p50 or 0.0, f"p50 latency for {provider}."),
        _numeric_evaluation(
            "p95-latency-ms", latency["p95"] or 0.0, f"p95 latency for {provider}."
        ),
        _numeric_evaluation(
            "total-tokens",
            metrics["total_tokens"],
            f"Total tokens across all observations for {provider}.",
        ),
        _numeric_evaluation(
            "cost-usd",
            metrics["estimated_cost_usd"],
            f"Estimated USD cost for {provider}.",
        ),
        _numeric_evaluation(
            "pass-rate",
            metrics["pass_rate"],
            f"{metrics['passed']}/{metrics['observations']} observations passed.",
        ),
        Evaluation(
            name="p50-latency-budget",
            value=within_budget,
            data_type="BOOLEAN",
            comment=(
                f"p50 {p50} ms is within the {budget} ms budget."
                if within_budget
                else f"p50 {p50} ms exceeds the {budget} ms budget."
            ),
            metadata={"evaluator": "odoo-agent-semantic-benchmark-v1"},
        ),
    ]


def load_reports_from_dir(report_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """从已归档的目录读回每轮报告，用于把历史基准回填成 Langfuse run。

    这条路径不重跑 Agent、不消耗模型额度，因此既能零成本验证 Langfuse SDK 调用，
    也能把 9-01/9-02/9-03 的历史基准变成可横比的基线。
    """

    reports_by_provider: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(report_dir.glob("*-round-*.json")):
        provider, _, remainder = path.stem.partition("-round-")
        if not provider or not remainder.isdigit():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        reports_by_provider.setdefault(provider, []).append((int(remainder), payload))
    return {
        provider: [payload for _, payload in sorted(entries, key=lambda item: item[0])]
        for provider, entries in sorted(reports_by_provider.items())
    }


def push_benchmark_runs(
    reports_by_provider: dict[str, list[dict[str, Any]]],
    *,
    model_provider: str | None,
    git_sha: str,
    timestamp: str,
    experiment_name: str,
) -> list[dict[str, Any]]:
    """把每一轮基准结果作为一次 Langfuse Dataset Run 推上去。

    任务函数只回放本地已记录的结果，不重新执行 Agent —— 基准跑一次就够了，
    这里要的是让结果落到 Langfuse 上可横比，而不是再花一遍钱。
    """

    if not langfuse_is_configured():
        raise RuntimeError("Langfuse is not configured")
    from langfuse import get_client

    configure_langfuse_environment()
    client = get_client()
    dataset = client.get_dataset(DATASET_NAME)

    pushed: list[dict[str, Any]] = []
    for provider, reports in reports_by_provider.items():
        provider_summary = summarize_benchmark({provider: reports})
        for round_number, run_report in enumerate(reports, 1):
            round_summary = summarize_benchmark({provider: [run_report]})
            results_by_id = {
                str(result.get("id")): result
                for result in run_report.get("results", [])
                if isinstance(result, dict)
            }

            def task(
                *,
                item: Any,
                _results: dict[str, Any] = results_by_id,
                **kwargs: Any,
            ) -> dict[str, Any]:
                raw_metadata = (
                    item.metadata
                    if hasattr(item, "metadata")
                    else item.get("metadata", {})
                ) or {}
                case_id = str(raw_metadata.get("case_id", ""))
                return _results.get(
                    case_id, {"missing_from_report": True, "case_id": case_id}
                )

            def run_evaluator(
                *,
                item_results: list[Any],
                _summary: dict[str, Any] = round_summary,
                _provider: str = provider,
                **kwargs: Any,
            ) -> list[Any]:
                return benchmark_run_evaluations(_summary, provider=_provider)

            run_name = benchmark_run_name(
                provider=provider,
                model_provider=model_provider,
                git_sha=git_sha,
                round_number=round_number,
                timestamp=timestamp,
            )
            result = dataset.run_experiment(
                name=experiment_name,
                run_name=run_name,
                description=(
                    f"Odoo Agent semantic A/B replay: {provider} round {round_number}."
                ),
                task=task,
                evaluators=[
                    sql_safe_evaluator,
                    metric_correct_evaluator,
                    answer_grounded_evaluator,
                ],
                run_evaluators=[run_evaluator],
                max_concurrency=1,
                metadata={
                    "semantic_provider": provider,
                    "model_provider": model_provider or "configured",
                    "git_sha": git_sha or "nogit",
                    "round": round_number,
                    "access_mode": "read-only",
                    "dataset": DATASET_NAME,
                    "round_metrics": round_summary["providers"][provider]["latency_ms"],
                    "provider_aggregate": {
                        "rounds": len(reports),
                        "pass_rate": provider_summary["providers"][provider]["pass_rate"],
                        "latency_ms": provider_summary["providers"][provider]["latency_ms"],
                        "total_tokens": provider_summary["providers"][provider][
                            "total_tokens"
                        ],
                        "estimated_cost_usd": provider_summary["providers"][provider][
                            "estimated_cost_usd"
                        ],
                        "by_role": provider_summary["providers"][provider]["by_role"],
                    },
                },
            )
            pushed.append(
                {
                    "provider": provider,
                    "round": round_number,
                    "run_name": run_name,
                    "dataset_run_id": getattr(result, "dataset_run_id", None),
                }
            )
            print(f"langfuse run pushed: {run_name}", flush=True)

    client.flush()
    return pushed


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Native / Wren 三轮 A/B 基准",
        "",
        f"- 报告版本：`{summary['report_version']}`",
        f"- 数据集：`{summary['dataset']}`",
        f"- 每个语义层轮数：{summary['runs_per_provider']}",
        "",
        "| 语义层 | 总通过率 | 结构通过率 | 结果签名 | p50 | p95 | Token | Cost USD | Repair |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for provider, metrics in summary["providers"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    provider,
                    f"{metrics['pass_rate']:.2%}",
                    f"{metrics['structural_pass_rate']:.2%}",
                    f"{metrics['result_match_rate']:.2%}",
                    f"{metrics['latency_ms']['p50'] or 0:.2f} ms",
                    f"{metrics['latency_ms']['p95'] or 0:.2f} ms",
                    str(metrics["total_tokens"]),
                    f"{metrics['estimated_cost_usd']:.6f}",
                    str(metrics["repair_count"]),
                ]
            )
            + " |"
        )
    budget = summary.get("latency_budget")
    if budget:
        verdict = "通过" if budget["within_budget"] else "**未通过**"
        lines.extend(
            [
                "",
                f"- 延迟门禁（p50 ≤ {budget['p50_budget_ms']} ms）：{verdict}"
                + (
                    f"，超预算：{', '.join(budget['breaches'])}"
                    if budget["breaches"]
                    else ""
                ),
            ]
        )

    role_tables = {
        provider: metrics["by_role"]
        for provider, metrics in summary["providers"].items()
        if metrics.get("by_role")
    }
    if role_tables:
        lines.extend(
            [
                "",
                "## 按 generation_role 的成本归因",
                "",
                "| 语义层 | role | 调用数 | Token | Token 占比 | Cost USD | Cost 占比 |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for provider, by_role in role_tables.items():
            for role, usage in by_role.items():
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            provider,
                            role,
                            str(usage["calls"]),
                            str(usage["total_tokens"]),
                            f"{usage['token_share']:.2%}",
                            f"{usage['estimated_cost_usd']:.6f}",
                            f"{usage['cost_share']:.2%}",
                        ]
                    )
                    + " |"
                )

    if summary.get("comparison"):
        comparison = summary["comparison"]
        lines.extend(
            [
                "",
                "## Wren 相对 Native",
                "",
                f"- 总通过率变化：{comparison['pass_rate_delta']:+.2%}",
                f"- 结果签名变化：{comparison['result_match_rate_delta']:+.2%}",
                f"- p50 变化：{comparison['p50_latency_delta_ms']:+.2f} ms",
                f"- p95 变化：{comparison['p95_latency_delta_ms']:+.2f} ms",
                f"- Token 变化：{comparison['token_delta']:+d}",
                f"- Cost 变化：${comparison['cost_delta_usd']:+.6f}",
                f"- Repair 变化：{comparison['repair_delta']:+d}",
            ]
        )
    return "\n".join(lines) + "\n"


async def _run_all(
    providers: list[str],
    *,
    runs: int,
    items: list[Any],
    output_dir: Path,
    model_provider: str | None,
) -> dict[str, list[dict[str, Any]]]:
    reports_by_provider: dict[str, list[dict[str, Any]]] = {}
    for provider in providers:
        provider_reports = []
        for run_number in range(1, runs + 1):
            results = await run_live(
                items,
                semantic_provider=provider,
                model_provider=model_provider,
                run_number=run_number,
            )
            payload = report(results, f"live-{provider}-round-{run_number}")
            provider_reports.append(payload)
            path = output_dir / f"{provider}-round-{run_number}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"provider={provider} round={run_number} "
                f"passed={payload['passed']}/{payload['total']}",
                flush=True,
            )
        reports_by_provider[provider] = provider_reports
    return reports_by_provider


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeatable Native/Wren semantic A/B.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--providers", default="native,wren")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--cases",
        default="",
        help="Comma-separated case IDs to run after validating the full dataset.",
    )
    parser.add_argument("--model-provider", choices=("deepseek", "siliconflow"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--langfuse-experiment",
        action="store_true",
        help="Push each round to Langfuse as a Dataset Run for cross-version comparison.",
    )
    parser.add_argument(
        "--experiment-name",
        default="odoo-agent-semantic-benchmark",
        help="Langfuse experiment name that groups the runs.",
    )
    parser.add_argument(
        "--push-report",
        type=Path,
        help=(
            "Replay an archived report directory into Langfuse instead of running the "
            "benchmark. Costs nothing and backfills historical baselines."
        ),
    )
    parser.add_argument(
        "--no-latency-gate",
        action="store_true",
        help=f"Do not fail the run when p50 exceeds {P50_LATENCY_BUDGET_MS} ms.",
    )
    args = parser.parse_args()
    if args.runs < 1:
        raise ValueError("--runs must be at least 1")

    if args.push_report:
        report_dir = args.push_report.resolve()
        reports_by_provider = load_reports_from_dir(report_dir)
        if not reports_by_provider:
            raise ValueError(f"no '<provider>-round-<n>.json' reports found in {report_dir}")
        pushed = push_benchmark_runs(
            reports_by_provider,
            model_provider=args.model_provider,
            git_sha=current_git_sha(),
            timestamp=datetime.now().astimezone().strftime("%Y%m%d-%H%M%S"),
            experiment_name=args.experiment_name,
        )
        print(json.dumps({"pushed_runs": pushed}, ensure_ascii=False, indent=2))
        return 0

    providers = [item.strip() for item in args.providers.split(",") if item.strip()]
    if not providers or any(item not in {"native", "wren"} for item in providers):
        raise ValueError("--providers must contain native and/or wren")

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_dir = (args.output_dir or PROJECT_DIR / "evals" / "reports" / timestamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    items = load_dataset(args.dataset)
    if args.cases:
        requested = {item.strip() for item in args.cases.split(",") if item.strip()}
        available = {item.id for item in items}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError("unknown --cases: " + ",".join(unknown))
        items = [item for item in items if item.id in requested]
    if args.limit > 0:
        items = items[: args.limit]

    with asyncio.Runner(loop_factory=selector_loop_factory) as runner:
        reports_by_provider = runner.run(
            _run_all(
                providers,
                runs=args.runs,
                items=items,
                output_dir=output_dir,
                model_provider=args.model_provider,
            )
        )
    summary = summarize_benchmark(reports_by_provider)
    settings = get_settings()
    routing = settings.routing(args.model_provider)
    summary["model_routing"] = {
        role: {
            "provider": config.name,
            "model": config.model,
            "thinking_mode": config.thinking_mode,
        }
        for role, config in (
            ("sql", routing.sql),
            ("answer", routing.answer),
            ("general", routing.general),
        )
    }
    if args.langfuse_experiment:
        summary["langfuse_runs"] = push_benchmark_runs(
            reports_by_provider,
            model_provider=args.model_provider,
            git_sha=current_git_sha(),
            timestamp=timestamp,
            experiment_name=args.experiment_name,
        )

    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    print(render_markdown(summary))
    print(f"output_dir={output_dir}")

    budget = summary["latency_budget"]
    if not budget["within_budget"] and not args.no_latency_gate:
        print(
            f"latency gate failed: p50 exceeds {budget['p50_budget_ms']} ms for "
            + ", ".join(budget["breaches"]),
            file=sys.stderr,
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
