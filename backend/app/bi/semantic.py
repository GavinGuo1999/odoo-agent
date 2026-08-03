from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


_SEMANTICS_PATH = Path(__file__).with_name("sales_semantics.json")


@dataclass(frozen=True, slots=True)
class SemanticContext:
    version: str
    timezone: str
    company_id: int
    tables: dict[str, dict[str, Any]]
    relations: list[str]
    metrics: dict[str, dict[str, Any]]
    examples: list[dict[str, str]]

    @property
    def metric_ids(self) -> list[str]:
        return list(self.metrics)

    def as_prompt(self) -> str:
        payload = {
            "semantic_version": self.version,
            "timezone": self.timezone,
            "required_company_id": self.company_id,
            "tables": self.tables,
            "relations": self.relations,
            "metrics": self.metrics,
            "verified_examples": self.examples,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class SalesSemanticLayer:
    def __init__(self, definition: dict[str, Any]) -> None:
        self._definition = definition

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> "SalesSemanticLayer":
        definition = json.loads(_SEMANTICS_PATH.read_text(encoding="utf-8"))
        return cls(definition)

    @property
    def version(self) -> str:
        return str(self._definition["version"])

    @property
    def allowed_tables(self) -> set[str]:
        return set(self._definition["tables"])

    @property
    def table_columns(self) -> dict[str, list[str]]:
        return {
            name: list(table["columns"])
            for name, table in self._definition["tables"].items()
        }

    def table_description(self, name: str) -> str:
        return str(self._definition["tables"][name]["description"])

    def retrieve(
        self,
        question: str,
        *,
        company_id: int,
        discovered_columns: dict[str, list[dict[str, str]]] | None = None,
    ) -> SemanticContext:
        normalized = question.lower()
        selected_tables = {"sale_order", "res_company", "res_currency"}
        if any(word in normalized for word in ("产品", "商品", "销量", "数量", "交付", "开票")):
            selected_tables.update(
                {"sale_order_line", "product_product", "product_template", "uom_uom"}
            )
        if any(word in normalized for word in ("客户", "伙伴", "公司")):
            selected_tables.add("res_partner")
        if any(word in normalized for word in ("销售员", "业务员", "团队")):
            selected_tables.update({"res_users", "res_partner"})

        selected_metrics: dict[str, dict[str, Any]] = {}
        for metric_id, metric in self._definition["metrics"].items():
            if any(keyword.lower() in normalized for keyword in metric["keywords"]):
                selected_metrics[metric_id] = metric
        if not selected_metrics:
            selected_metrics = {
                key: self._definition["metrics"][key]
                for key in ("sales_amount", "order_count")
            }

        tables: dict[str, dict[str, Any]] = {}
        for name in sorted(selected_tables):
            definition = self._definition["tables"][name]
            columns = (
                discovered_columns.get(name, [])
                if discovered_columns is not None
                else [{"name": column, "type": "unknown"} for column in definition["columns"]]
            )
            tables[name] = {
                "description": definition["description"],
                "columns": columns,
            }

        examples = [
            {
                "question": example["question"],
                "sql": example["sql"].format(company_id=company_id),
            }
            for example in self._definition["examples"]
        ]
        return SemanticContext(
            version=self.version,
            timezone=str(self._definition["timezone"]),
            company_id=company_id,
            tables=tables,
            relations=list(self._definition["relations"]),
            metrics=selected_metrics,
            examples=examples,
        )

    def metric_explanation(self, question: str) -> str | None:
        normalized = question.lower()
        matches = []
        for metric in self._definition["metrics"].values():
            if any(keyword.lower() in normalized for keyword in metric["keywords"]):
                matches.append(
                    f"{metric['name']}：{metric['description']} 计算式为 "
                    f"{metric['expression']}，时间字段为 {metric['date_field']}，"
                    f"订单状态限定为 {', '.join(metric['states'])}。"
                )
        return "\n".join(matches) if matches else None
