from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from langfuse import get_client  # noqa: E402

from app.bi.prompts import (  # noqa: E402
    answer_synthesis_prompt,
    chart_planning_prompt,
    general_system_prompt,
    knowledge_answer_prompt,
    sql_generation_prompt,
    sql_repair_prompt,
)
from app.observability import configure_langfuse_environment  # noqa: E402


def prompt_templates() -> dict[str, str]:
    previous = os.environ.get("LANGFUSE_PROMPTS_FETCH_ENABLED")
    os.environ["LANGFUSE_PROMPTS_FETCH_ENABLED"] = "false"
    try:
        prompts = {
            "odoo-general-system": general_system_prompt(
                provider="__PROVIDER__",
                model="__MODEL__",
            ),
            "odoo-wiki-answer": knowledge_answer_prompt(
                question="__QUESTION__",
                history=[{"role": "user", "content": "__HISTORY__"}],
                knowledge_context="__KNOWLEDGE__",
                source_mode=False,
            ),
            "odoo-sql-generation": sql_generation_prompt(
                question="__QUESTION__",
                history=[{"role": "user", "content": "__HISTORY__"}],
                semantic_context="__SEMANTIC_CONTEXT__",
            ),
            "odoo-sql-repair": sql_repair_prompt(
                question="__QUESTION__",
                semantic_context="__SEMANTIC_CONTEXT__",
                previous_sql="SELECT '__PREVIOUS_SQL__'",
                previous_plan={"marker": "__PREVIOUS_PLAN__"},
                errors=["__ERRORS__"],
                error_analysis={"marker": "__ERROR_ANALYSIS__"},
            ),
            "odoo-chart-planner": chart_planning_prompt(
                question="__QUESTION__",
                query_plan={"marker": "__QUERY_PLAN__"},
                data_profile={"marker": "__DATA_PROFILE__"},
            ),
            "odoo-answer-synthesis": answer_synthesis_prompt(
                question="__QUESTION__",
                currency="__CURRENCY__",
                metric_ids=["__METRIC__"],
                sql="SELECT '__SQL__'",
                columns=["__COLUMN__"],
                rows=[{"__COLUMN__": "__ROW__"}],
                truncated=False,
                data_profile={"marker": "__PROFILE__"},
                knowledge_context="__KNOWLEDGE__",
                hybrid=True,
            ),
        }
        return {name: prompt.fallback_template for name, prompt in prompts.items()}
    finally:
        if previous is None:
            os.environ.pop("LANGFUSE_PROMPTS_FETCH_ENABLED", None)
        else:
            os.environ["LANGFUSE_PROMPTS_FETCH_ENABLED"] = previous


def sync(*, dry_run: bool = False) -> list[dict[str, Any]]:
    configure_langfuse_environment()
    client = get_client()
    results: list[dict[str, Any]] = []
    for name, template in prompt_templates().items():
        current = None
        try:
            current = client.get_prompt(
                name,
                label="production",
                type="text",
                cache_ttl_seconds=0,
                max_retries=1,
                fetch_timeout_seconds=3,
            )
        except Exception:
            current = None
        if current is not None and current.prompt == template:
            results.append({"name": name, "status": "unchanged", "version": current.version})
            continue
        if dry_run:
            results.append({"name": name, "status": "would-create"})
            continue
        created = client.create_prompt(
            name=name,
            prompt=template,
            type="text",
            labels=["production"],
            config={"owner": "odoo-agent", "source": "repository"},
        )
        results.append({"name": name, "status": "created", "version": created.version})
    if not dry_run:
        client.flush()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync versioned Odoo Agent prompts to Langfuse.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for result in sync(dry_run=args.dry_run):
        print(f"{result['name']}: {result['status']} v{result.get('version', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
