from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.bi.chart import build_chart_spec
from app.bi.prompts import (
    answer_synthesis_prompt,
    general_system_prompt,
    sql_generation_prompt,
    sql_repair_prompt,
)
from app.bi.semantic import SalesSemanticLayer
from app.bi.time_series import complete_year_months
from app.config import DatabaseConfig, ProviderConfig
from app.database import OdooDatabase, ReadOnlySqlGuard
from app.llm import LLMGateway, LLMResult
from app.observability import (
    trace_agent,
    trace_retrieval,
    trace_tool,
    update_observation,
)


Intent = Literal["general", "semantic", "data"]


class AgentState(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    intent: Intent
    answer: str
    provider: str
    model: str
    semantic_context: str
    metric_ids: list[str]
    sql: str
    safe_sql: str
    sql_errors: list[str]
    tables: list[str]
    database_ready: bool
    currency: str | None
    columns: list[str]
    rows: list[dict[str, Any]]
    query_ms: float
    truncated: bool
    data_accessed: bool
    chart: dict[str, Any]
    warnings: list[str]
    filled_time_buckets: int
    repair_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    answer: str
    intent: Intent
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    data_accessed: bool = False
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    chart: dict[str, Any] | None = None
    metrics: list[str] = field(default_factory=list)
    query_ms: float | None = None
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    currency: str | None = None

    @property
    def phase(self) -> str:
        return {
            "general": "general-chat",
            "semantic": "semantic-layer",
            "data": "text2sql",
        }[self.intent]


_DATA_WORDS = (
    "销售", "订单", "客户", "产品", "商品", "销量", "业绩", "收入", "交付",
    "发货", "开票", "报价", "成交", "销售员", "业务员", "排行榜", "趋势", "同比",
    "环比", "本月", "上月", "本年", "今年", "去年", "odoo", "sql",
)
_SEMANTIC_WORDS = ("口径", "定义", "怎么算", "怎么计算", "什么意思", "包括什么")
_FOLLOW_UP_WORDS = ("那", "再", "呢", "上个月", "去年", "同比", "环比", "换成")


def classify_intent(question: str, history: list[dict[str, str]]) -> Intent:
    normalized = question.lower().strip()
    has_business_term = any(word in normalized for word in _DATA_WORDS)
    if has_business_term and any(word in normalized for word in _SEMANTIC_WORDS):
        return "semantic"
    if has_business_term:
        return "data"

    previous_context = " ".join(item.get("content", "") for item in history[-4:]).lower()
    if any(word in normalized for word in _FOLLOW_UP_WORDS) and any(
        word in previous_context for word in _DATA_WORDS
    ):
        return "data"
    return "general"


def _json_object(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip(), flags=re.I)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("NoJsonObject")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("NotJsonObject")
    return parsed


def _usage_fields(state: AgentState, result: LLMResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "model": result.model,
        "input_tokens": state.get("input_tokens", 0) + (result.input_tokens or 0),
        "output_tokens": state.get("output_tokens", 0) + (result.output_tokens or 0),
        "total_tokens": state.get("total_tokens", 0) + (result.total_tokens or 0),
    }


class SalesAgent:
    """LangGraph orchestration for general chat, metric help, and safe Text2SQL."""

    def __init__(self, provider: ProviderConfig, database: DatabaseConfig) -> None:
        self._provider = provider
        self._database_config = database
        self._gateway = LLMGateway(provider)
        self._database = OdooDatabase(database)
        self._semantics = SalesSemanticLayer.load()
        self._guard = ReadOnlySqlGuard(
            table_columns=self._semantics.table_columns,
            company_id=database.company_id,
            max_rows=database.max_rows,
        )
        self._graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("classify_intent", self._classify)
        builder.add_node("answer_general", self._answer_general)
        builder.add_node("explain_metric", self._explain_metric)
        builder.add_node("retrieve_sales_context", self._retrieve_sales_context)
        builder.add_node("generate_sales_sql", self._generate_sales_sql)
        builder.add_node("validate_sales_sql", self._validate_sales_sql)
        builder.add_node("repair_sales_sql", self._repair_sales_sql)
        builder.add_node("execute_sales_sql", self._execute_sales_sql)
        builder.add_node("synthesize_sales_answer", self._synthesize_sales_answer)
        builder.add_node("safe_failure_answer", self._safe_failure_answer)

        builder.add_edge(START, "classify_intent")
        builder.add_conditional_edges(
            "classify_intent",
            lambda state: state["intent"],
            {
                "general": "answer_general",
                "semantic": "explain_metric",
                "data": "retrieve_sales_context",
            },
        )
        builder.add_edge("answer_general", END)
        builder.add_edge("explain_metric", END)
        builder.add_conditional_edges(
            "retrieve_sales_context",
            lambda state: "ready" if state.get("database_ready") else "unavailable",
            {"ready": "generate_sales_sql", "unavailable": "safe_failure_answer"},
        )
        builder.add_edge("generate_sales_sql", "validate_sales_sql")
        builder.add_conditional_edges(
            "validate_sales_sql",
            self._route_after_validation,
            {"execute": "execute_sales_sql", "repair": "repair_sales_sql", "fail": "safe_failure_answer"},
        )
        builder.add_edge("repair_sales_sql", "validate_sales_sql")
        builder.add_conditional_edges(
            "execute_sales_sql",
            self._route_after_execution,
            {"answer": "synthesize_sales_answer", "repair": "repair_sales_sql", "fail": "safe_failure_answer"},
        )
        builder.add_edge("synthesize_sales_answer", END)
        builder.add_edge("safe_failure_answer", END)
        return builder.compile()

    async def run(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
    ) -> AgentOutcome:
        initial: AgentState = {
            "question": question,
            "history": history,
            "provider": self._provider.name,
            "model": self._provider.model,
            "metric_ids": [],
            "columns": [],
            "rows": [],
            "warnings": [],
            "repair_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "data_accessed": False,
            "truncated": False,
        }
        with trace_agent(
            name="route-and-answer-odoo-question",
            input_data={"question": question, "history_length": len(history)},
        ) as observation:
            state = await self._graph.ainvoke(initial, config={"recursion_limit": 20})
            update_observation(
                observation,
                output={
                    "intent": state.get("intent"),
                    "data_accessed": state.get("data_accessed", False),
                    "tables": state.get("tables", []),
                    "row_count": len(state.get("rows", [])),
                    "repair_count": state.get("repair_count", 0),
                },
            )

        return AgentOutcome(
            answer=state.get("answer", "没有生成可用回答。"),
            intent=state.get("intent", "general"),
            provider=state.get("provider", self._provider.name),
            model=state.get("model", self._provider.model),
            input_tokens=state.get("input_tokens", 0),
            output_tokens=state.get("output_tokens", 0),
            total_tokens=state.get("total_tokens", 0),
            data_accessed=state.get("data_accessed", False),
            sql=state.get("safe_sql"),
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            chart=state.get("chart"),
            metrics=state.get("metric_ids", []),
            query_ms=state.get("query_ms"),
            truncated=state.get("truncated", False),
            warnings=state.get("warnings", []),
            currency=state.get("currency"),
        )

    def _classify(self, state: AgentState) -> dict[str, Any]:
        return {"intent": classify_intent(state["question"], state.get("history", []))}

    async def _answer_general(self, state: AgentState) -> dict[str, Any]:
        result = await self._gateway.complete(
            messages=[
                {
                    "role": "system",
                    "content": general_system_prompt(
                        provider=self._provider.name,
                        model=self._provider.model,
                    ),
                },
                *state.get("history", [])[-10:],
                {"role": "user", "content": state["question"]},
            ],
            generation_name="answer-general-question",
            metadata={"feature": "general-chat", "data_accessed": False},
        )
        return {"answer": result.content, **_usage_fields(state, result)}

    def _explain_metric(self, state: AgentState) -> dict[str, Any]:
        explanation = self._semantics.metric_explanation(state["question"])
        if explanation is None:
            explanation = "当前销售语义层已定义销售额、含税销售额、订单数、平均订单额、销售数量、交付数量和开票数量。"
        return {"answer": explanation, "data_accessed": False}

    async def _retrieve_sales_context(self, state: AgentState) -> dict[str, Any]:
        with trace_tool(
            name="check-odoo-readonly-database",
            input_data={"company_id": self._database_config.company_id},
        ) as health_observation:
            health = await self._database.healthcheck()
            update_observation(
                health_observation,
                output={
                    "connected": health.connected,
                    "read_only": health.read_only,
                    "company_id": health.company_id,
                    "currency": health.currency,
                    "response_ms": health.response_ms,
                    "error_type": health.error_type,
                },
            )
        if not health.connected or not health.read_only:
            return {
                "database_ready": False,
                "currency": health.currency,
                "warnings": ["Odoo 只读数据库当前不可用，请到设置页检查数据库连接。"],
            }

        with trace_retrieval(
            name="retrieve-sales-semantic-context",
            input_data={
                "question": state["question"],
                "semantic_version": self._semantics.version,
                "company_id": self._database_config.company_id,
            },
        ) as observation:
            discovered = await self._database.discover_columns(self._semantics.table_columns)
            context = self._semantics.retrieve(
                state["question"],
                company_id=self._database_config.company_id,
                discovered_columns=discovered,
            )
            update_observation(
                observation,
                output={
                    "tables": list(context.tables),
                    "metric_ids": context.metric_ids,
                    "semantic_version": context.version,
                },
            )
        return {
            "database_ready": True,
            "currency": health.currency,
            "semantic_context": context.as_prompt(),
            "metric_ids": context.metric_ids,
        }

    async def _generate_sales_sql(self, state: AgentState) -> dict[str, Any]:
        result = await self._gateway.complete(
            messages=[
                {
                    "role": "user",
                    "content": sql_generation_prompt(
                        question=state["question"],
                        history=state.get("history", []),
                        semantic_context=state["semantic_context"],
                    ),
                }
            ],
            generation_name="generate-sales-sql",
            metadata={"feature": "text2sql", "semantic_version": self._semantics.version},
            json_mode=True,
        )
        try:
            payload = _json_object(result.content)
            sql = str(payload.get("sql") or "").strip()
            requested_metrics = payload.get("metric_ids") or []
            allowed_metrics = set(state.get("metric_ids", []))
            metric_ids = [str(item) for item in requested_metrics if str(item) in allowed_metrics]
        except (ValueError, TypeError, json.JSONDecodeError):
            sql = ""
            metric_ids = state.get("metric_ids", [])
        return {
            "sql": sql,
            "metric_ids": metric_ids or state.get("metric_ids", []),
            **_usage_fields(state, result),
        }

    def _validate_sales_sql(self, state: AgentState) -> dict[str, Any]:
        sql = state.get("sql", "")
        with trace_tool(
            name="validate-readonly-sales-sql",
            input_data={"sql": sql, "company_id": self._database_config.company_id},
        ) as observation:
            if not sql:
                errors = ["模型没有返回可用 SQL。"]
                safe_sql = ""
                tables: list[str] = []
                safe = False
            else:
                validation = self._guard.validate(sql)
                errors = validation.errors
                safe_sql = validation.sql or ""
                tables = validation.tables
                safe = validation.safe
            update_observation(
                observation,
                output={"safe": safe, "errors": errors, "tables": tables},
            )
        return {"safe_sql": safe_sql, "sql_errors": errors, "tables": tables}

    def _route_after_validation(self, state: AgentState) -> str:
        if state.get("safe_sql") and not state.get("sql_errors"):
            return "execute"
        return "repair" if state.get("repair_count", 0) < 2 else "fail"

    async def _repair_sales_sql(self, state: AgentState) -> dict[str, Any]:
        errors = state.get("sql_errors", [])
        result = await self._gateway.complete(
            messages=[
                {
                    "role": "user",
                    "content": sql_repair_prompt(
                        question=state["question"],
                        semantic_context=state["semantic_context"],
                        previous_sql=state.get("sql", ""),
                        errors=errors,
                    ),
                }
            ],
            generation_name="repair-sales-sql",
            metadata={
                "feature": "text2sql-repair",
                "repair_number": state.get("repair_count", 0) + 1,
            },
            json_mode=True,
        )
        try:
            payload = _json_object(result.content)
            sql = str(payload.get("sql") or "").strip()
        except (ValueError, TypeError, json.JSONDecodeError):
            sql = ""
        return {
            "sql": sql,
            "safe_sql": "",
            "repair_count": state.get("repair_count", 0) + 1,
            **_usage_fields(state, result),
        }

    async def _execute_sales_sql(self, state: AgentState) -> dict[str, Any]:
        safe_sql = state["safe_sql"]
        with trace_tool(
            name="execute-readonly-sales-sql",
            input_data={"sql": safe_sql, "company_id": self._database_config.company_id},
        ) as observation:
            try:
                result = await self._database.execute_readonly(safe_sql)
            except Exception as exc:
                errors = [f"数据库执行失败：{type(exc).__name__}。"]
                update_observation(
                    observation,
                    level="ERROR",
                    status_message=type(exc).__name__,
                    output={"status": "error", "error_type": type(exc).__name__},
                )
                return {"sql_errors": errors, "data_accessed": False}

            display_rows, filled_time_buckets = complete_year_months(
                question=state["question"],
                columns=result.columns,
                rows=result.rows,
            )
            update_observation(
                observation,
                output={
                    "status": "ok",
                    "columns": result.columns,
                    "raw_row_count": result.row_count,
                    "display_row_count": len(display_rows),
                    "filled_time_buckets": filled_time_buckets,
                    "truncated": result.truncated,
                    "duration_ms": result.duration_ms,
                },
            )
        warnings = state.get("warnings", [])
        if filled_time_buckets:
            warnings = warnings + ["无订单月份已按 0 展示；这只补齐时间刻度，不代表新增业务数据。"]
        return {
            "columns": result.columns,
            "rows": display_rows,
            "query_ms": result.duration_ms,
            "truncated": result.truncated,
            "data_accessed": True,
            "sql_errors": [],
            "filled_time_buckets": filled_time_buckets,
            "warnings": warnings,
        }

    def _route_after_execution(self, state: AgentState) -> str:
        if state.get("data_accessed"):
            return "answer"
        return "repair" if state.get("repair_count", 0) < 2 else "fail"

    async def _synthesize_sales_answer(self, state: AgentState) -> dict[str, Any]:
        result = await self._gateway.complete(
            messages=[
                {
                    "role": "user",
                    "content": answer_synthesis_prompt(
                        question=state["question"],
                        currency=state.get("currency"),
                        metric_ids=state.get("metric_ids", []),
                        sql=state["safe_sql"],
                        columns=state.get("columns", []),
                        rows=state.get("rows", []),
                        truncated=state.get("truncated", False),
                    ),
                }
            ],
            generation_name="explain-sales-result",
            metadata={
                "feature": "chatbi-answer",
                "row_count": len(state.get("rows", [])),
                "data_accessed": True,
            },
        )
        chart = build_chart_spec(
            state["question"],
            state.get("columns", []),
            state.get("rows", []),
        )
        return {"answer": result.content, "chart": chart, **_usage_fields(state, result)}

    def _safe_failure_answer(self, state: AgentState) -> dict[str, Any]:
        warnings = state.get("warnings", [])
        if not state.get("database_ready", True):
            answer = "Odoo 只读数据库当前没有连通。我没有查询或编造业务数据；请先在设置页检查数据库连接。"
        else:
            answer = "这次查询没有通过只读安全校验或数据库执行失败，因此没有返回业务数据。你可以换一种更明确的销售问题再试。"
            warnings = warnings + state.get("sql_errors", [])
        return {
            "answer": answer,
            "data_accessed": False,
            "warnings": list(dict.fromkeys(warnings)),
        }
