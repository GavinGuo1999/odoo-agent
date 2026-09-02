from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
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
from app.windows_loop import selector_loop_factory
from evals.run_sales_eval import (
    DATASET_NAME,
    DEFAULT_DATASET,
    load_dataset,
    report,
    run_live,
)

def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


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
    return {
        "report_version": "odoo-agent-semantic-ab-v1",
        "dataset": DATASET_NAME,
        "created_at": datetime.now().astimezone().isoformat(),
        "runs_per_provider": next(iter(run_counts)) if len(run_counts) == 1 else None,
        "providers": providers,
        "comparison": comparison,
    }


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
    args = parser.parse_args()
    if args.runs < 1:
        raise ValueError("--runs must be at least 1")
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
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    print(render_markdown(summary))
    print(f"output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
