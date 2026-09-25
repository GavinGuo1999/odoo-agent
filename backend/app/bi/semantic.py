from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.database.sql_guard import SqlDomainProfile


DEFAULT_DOMAIN = "sales"

# 每个业务域一个定义文件。加一个域 = 加一个文件 + 在这里登记一行，
# 不需要改检索逻辑——检索规则本身也写在定义文件里（见 `_selected_tables`）。
_DOMAIN_FILES = {
    "sales": "sales_semantics.json",
    "crm": "crm_semantics.json",
}


KNOWN_DOMAINS: tuple[str, ...] = tuple(_DOMAIN_FILES)


class UnknownSemanticDomainError(KeyError):
    """请求了一个没有定义文件的业务域。"""


def _domain_path(domain: str) -> Path:
    try:
        filename = _DOMAIN_FILES[domain]
    except KeyError as exc:
        raise UnknownSemanticDomainError(
            f"semantic domain {domain!r} has no definition file; "
            f"known: {sorted(_DOMAIN_FILES)}"
        ) from exc
    return Path(__file__).with_name(filename)


@dataclass(frozen=True, slots=True)
class SemanticContext:
    version: str
    timezone: str
    company_id: int
    tables: dict[str, dict[str, Any]]
    relations: list[str]
    metrics: dict[str, dict[str, Any]]
    examples: list[dict[str, str]]
    provider: str = "native"
    compiled_schema: str | None = None
    business_rules: str | None = None
    domain: str = DEFAULT_DOMAIN
    business_notes: list[str] = ()

    @property
    def metric_ids(self) -> list[str]:
        return list(self.metrics)

    def as_prompt(self) -> str:
        payload: dict[str, Any] = {
            "semantic_provider": self.provider,
            "semantic_domain": self.domain,
            "semantic_version": self.version,
            "timezone": self.timezone,
            "required_company_id": self.company_id,
            "tables": self.tables,
            "relations": self.relations,
            "metrics": self.metrics,
            "verified_examples": self.examples,
        }
        if self.business_notes:
            # 域级硬规则（例如 CRM 的"输单记录 active=false，不要按 active 过滤"）。
            # 它们不是建议，是守卫会真的拒的东西，所以必须进 Prompt。
            payload["domain_rules"] = list(self.business_notes)
        if self.compiled_schema:
            # 语义层接管了 schema：模型不再看物理表，改为对语义层的命名空间写 SQL，
            # 由下一个节点编译成物理 SQL。两种语义层的命名空间语法不同，
            # 所以指令必须分开写。
            #
            # **Wren 这一支的文本不要改动**：文档 18 的 Wren 成绩是在这段提示词下
            # 测出来的，改了就不再与已归档的结果可比。
            if self.provider == "cube":
                payload["cube_schema"] = self.compiled_schema
                payload["sql_namespace"] = (
                    "Generate SQL against the Cube views listed in cube_schema, "
                    "not against physical tables. Wrap every measure in MEASURE(), "
                    "for example SELECT MEASURE(sales_amount) FROM sales_analysis. "
                    "Dimensions are referenced as plain columns. Use exactly the "
                    "names listed in cube_schema; do not invent prefixed variants. "
                    "GROUP BY and ORDER BY by column position (GROUP BY 1, "
                    "ORDER BY 2 DESC). "
                    "When a dimension in cube_schema is annotated with 物理列 X, "
                    "the compiled SQL will filter on the physical column X, so "
                    "QueryPlan.filters must declare X as the field name — not the "
                    "Cube view column name. "
                    "The next graph node compiles it to physical PostgreSQL SQL."
                )
            else:
                payload["wren_mdl_schema"] = self.compiled_schema
                payload["wren_business_rules"] = self.business_rules or ""
                payload["sql_namespace"] = (
                    "Generate SQL against Wren MDL model names. "
                    "The next graph node compiles it to physical PostgreSQL SQL."
                )
            payload.pop("tables", None)
            payload.pop("relations", None)
        return json.dumps(payload, ensure_ascii=False, indent=2)


class SemanticLayer:
    """一个业务域的语义定义。

    M1 里这个类叫 `SalesSemanticLayer`，选表规则和默认指标都硬编码在 `retrieve()` 里。
    加第二个域时那种写法会让 `retrieve()` 里的关键词分支线性膨胀，所以规则搬进了
    定义文件：`base_tables`、`table_selection`、`default_metrics`。
    行为对销售域完全不变——搬的是位置，不是判据。
    """

    def __init__(self, definition: dict[str, Any]) -> None:
        self._definition = definition

    @classmethod
    @lru_cache(maxsize=len(_DOMAIN_FILES))
    def load(cls, domain: str = DEFAULT_DOMAIN) -> "SemanticLayer":
        definition = json.loads(_domain_path(domain).read_text(encoding="utf-8"))
        return cls(definition)

    @property
    def domain(self) -> str:
        return str(self._definition.get("domain", DEFAULT_DOMAIN))

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

    @property
    def metric_definitions(self) -> dict[str, dict[str, Any]]:
        return {
            metric_id: dict(metric)
            for metric_id, metric in self._definition["metrics"].items()
        }

    @property
    def business_notes(self) -> list[str]:
        return list(self._definition.get("business_notes", []))

    @property
    def routing_keywords(self) -> list[str]:
        """域路由用的关键词。判据写在定义文件里，不散在代码里。"""
        return list(self._definition.get("routing_keywords", []))

    @property
    def sql_profile(self) -> SqlDomainProfile:
        """本域的守卫判据（见 `database/sql_guard.SqlDomainProfile`）。

        定义文件里没有 `sql_profile` 就用销售域的默认值，所以老的定义文件仍然可用。
        """
        raw = self._definition.get("sql_profile")
        if not raw:
            return SqlDomainProfile()
        return SqlDomainProfile(
            metric_rule_fields=frozenset(raw["metric_rule_fields"]),
            time_fields=frozenset(raw["time_fields"]),
            scope_required_metrics=MappingProxyType(
                {
                    metric_id: frozenset(fields)
                    for metric_id, fields in raw["scope_required_metrics"].items()
                }
            ),
            forbidden_filter_fields=frozenset(raw["forbidden_filter_fields"]),
            translated_name_dimensions=frozenset(raw["translated_name_dimensions"]),
            company_scoped_tables=frozenset(raw["company_scoped_tables"]),
            metric_expression_fields=MappingProxyType(
                {
                    metric_id: frozenset(fields)
                    for metric_id, fields in raw["metric_expression_fields"].items()
                }
            ),
        )

    def table_description(self, name: str) -> str:
        return str(self._definition["tables"][name]["description"])

    def _selected_tables(self, normalized_question: str) -> set[str]:
        selected = set(self._definition["base_tables"])
        for rule in self._definition.get("table_selection", []):
            if any(keyword.lower() in normalized_question for keyword in rule["keywords"]):
                selected.update(rule["tables"])
        return selected

    def _selected_metrics(self, normalized_question: str) -> dict[str, dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for metric_id, metric in self._definition["metrics"].items():
            if any(keyword.lower() in normalized_question for keyword in metric["keywords"]):
                selected[metric_id] = metric
        if not selected:
            selected = {
                key: self._definition["metrics"][key]
                for key in self._definition["default_metrics"]
            }
        return selected

    def retrieve(
        self,
        question: str,
        *,
        company_id: int,
        discovered_columns: dict[str, list[dict[str, str]]] | None = None,
    ) -> SemanticContext:
        normalized = question.lower()
        selected_tables = self._selected_tables(normalized)
        selected_metrics = self._selected_metrics(normalized)

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
            domain=self.domain,
            business_notes=tuple(self.business_notes),
        )

    def metric_explanation(self, question: str) -> str | None:
        """按指标定义拼口径说明。

        `scope_label` / `scope_field` 来自定义文件：销售域说的是"订单状态限定为"，
        CRM 域说的是"记录范围限定为"——两个域的默认范围根本不是同一种东西
        （一个是 `sale_order.state`，一个是 `type` + `won_status` 的组合），
        用同一句话套会把口径说错。

        `caveat` 是给口径本身就有偏差的指标用的。当前只有 CRM 的线索转化率：
        Odoo 里线索转商机是同一条记录改 `type`、不留副本，分母天生不准。
        这句话必须原样答出来——宁可让人知道它是近似的，也不给一个看起来精确的错数。
        """
        explain = self._definition.get(
            "metric_explanation",
            {"scope_label": "订单状态限定为", "scope_field": "states"},
        )
        scope_label = explain["scope_label"]
        scope_field = explain["scope_field"]

        normalized = question.lower()
        matches = []
        for metric in self._definition["metrics"].values():
            if not any(keyword.lower() in normalized for keyword in metric["keywords"]):
                continue
            scope = metric.get(scope_field) or []
            text = (
                f"{metric['name']}：{metric['description']} 计算式为 "
                f"{metric['expression']}，时间字段为 {metric['date_field']}，"
                f"{scope_label} {', '.join(scope)}。"
            )
            if metric.get("caveat"):
                text = f"{text}（口径说明：{metric['caveat']}）"
            matches.append(text)
        return "\n".join(matches) if matches else None


# M1 起就在用的名字，`load()` 默认仍是销售域，所以现有调用点一个都不用改。
SalesSemanticLayer = SemanticLayer
