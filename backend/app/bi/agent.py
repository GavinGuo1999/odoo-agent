from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from app.bi.chart import build_chart_spec
from app.bi.deterministic_answer import (
    build_deterministic_answer,
    can_answer_deterministically,
)
from app.bi.prompts import (
    answer_synthesis_prompt,
    general_system_prompt,
    sql_generation_prompt,
    sql_repair_prompt,
)
from app.bi.semantic import SalesSemanticLayer
from app.bi.time_series import complete_year_months
from app.config import DatabaseConfig, ModelRoutingConfig, ProviderConfig
from app.database import OdooDatabase, ReadOnlySqlGuard
from app.llm import LLMGateway, LLMResult
from app.observability import trace_agent, trace_retrieval, trace_tool, update_observation
from app.schemas.query_plan import QueryPlan, SqlGenerationPayload
from app.state import get_state_store


Intent = Literal["general", "semantic", "data"]
AnswerMode = Literal["llm", "deterministic", "semantic", "failure"]


class AgentState(TypedDict, total=False):
    question: str
    display_question: str
    clarification_answer: str
    history: list[dict[str, str]]
    conversation: list[dict[str, str]]
    intent: Intent
    answer: str
    answer_mode: AnswerMode
    provider: str
    model: str
    model_roles: dict[str, dict[str, str]]
    semantic_context: str
    metric_ids: list[str]
    query_plan: dict[str, Any] | None
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
    estimated_cost_usd: float


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    answer: str
    intent: Intent
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
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
    query_plan: QueryPlan | None = None
    answer_mode: AnswerMode = "llm"
    model_roles: dict[str, dict[str, str]] = field(default_factory=dict)
    interrupted: bool = False
    interrupt_payload: dict[str, Any] | None = None
    conversation: list[dict[str, str]] = field(default_factory=list)

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


def parse_sql_generation_payload(
    content: str,
    *,
    allowed_metric_ids: list[str],
) -> SqlGenerationPayload:
    payload = SqlGenerationPayload.model_validate(_json_object(content))
    allowed = set(allowed_metric_ids)
    filtered_metrics = [item for item in payload.plan.metric_ids if item in allowed]
    plan = payload.plan.model_copy(update={"metric_ids": filtered_metrics})
    return payload.model_copy(update={"plan": plan, "sql": payload.sql.strip()})


def _usage_fields(state: AgentState, result: LLMResult, *, role: str) -> dict[str, Any]:
    model_roles = dict(state.get("model_roles", {}))
    model_roles[role] = {"provider": result.provider, "model": result.model}
    return {
        "provider": result.provider,
        "model": result.model,
        "model_roles": model_roles,
        "input_tokens": state.get("input_tokens", 0) + (result.input_tokens or 0),
        "output_tokens": state.get("output_tokens", 0) + (result.output_tokens or 0),
        "total_tokens": state.get("total_tokens", 0) + (result.total_tokens or 0),
        "estimated_cost_usd": state.get("estimated_cost_usd", 0.0)
        + result.total_cost_usd,
    }


def _emit_stage(stage: str, label: str) -> None:
    try:
        writer = get_stream_writer()
        writer({"type": "progress", "stage": stage, "label": label})
    except Exception:
        # Nodes are also invoked by non-streaming API/tests, where no writer exists.
        return


class SalesAgent:
    """LangGraph orchestration for general chat, metric help, and safe Text2SQL."""

    def __init__(
        self,
        provider: ProviderConfig,
        database: DatabaseConfig,
        *,
        routing: ModelRoutingConfig | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._provider = provider
        self._routing = routing or ModelRoutingConfig(
            sql=provider,
            answer=provider,
            general=provider,
        )
        self._database_config = database
        self._gateway = LLMGateway(provider)
        self._database = OdooDatabase(database)
        self._semantics = SalesSemanticLayer.load()
        self._guard = ReadOnlySqlGuard(
            table_columns=self._semantics.table_columns,
            company_id=database.company_id,
            max_rows=database.max_rows,
        )
        self._checkpointer = checkpointer or get_state_store().checkpointer
        self._graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("classify_intent", self._classify)
        builder.add_node("answer_general", self._answer_general)
        builder.add_node("explain_metric", self._explain_metric)
        builder.add_node("retrieve_sales_context", self._retrieve_sales_context)
        builder.add_node("generate_sales_sql", self._generate_sales_sql)
        builder.add_node("clarify_query_plan", self._clarify_query_plan)
        builder.add_node("validate_sales_sql", self._validate_sales_sql)
        builder.add_node("repair_sales_sql", self._repair_sales_sql)
        builder.add_node("execute_sales_sql", self._execute_sales_sql)
        builder.add_node("format_simple_answer", self._format_simple_answer)
        builder.add_node("synthesize_sales_answer", self._synthesize_sales_answer)
        builder.add_node("safe_failure_answer", self._safe_failure_answer)
        builder.add_node("finalize_turn", self._finalize_turn)

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
        builder.add_edge("answer_general", "finalize_turn")
        builder.add_edge("explain_metric", "finalize_turn")
        builder.add_conditional_edges(
            "retrieve_sales_context",
            lambda state: "ready" if state.get("database_ready") else "unavailable",
            {"ready": "generate_sales_sql", "unavailable": "safe_failure_answer"},
        )
        builder.add_conditional_edges(
            "generate_sales_sql",
            self._route_after_planning,
            {"clarify": "clarify_query_plan", "validate": "validate_sales_sql"},
        )
        builder.add_edge("clarify_query_plan", "retrieve_sales_context")
        builder.add_conditional_edges(
            "validate_sales_sql",
            self._route_after_validation,
            {
                "execute": "execute_sales_sql",
                "repair": "repair_sales_sql",
                "fail": "safe_failure_answer",
            },
        )
        builder.add_edge("repair_sales_sql", "validate_sales_sql")
        builder.add_conditional_edges(
            "execute_sales_sql",
            self._route_after_execution,
            {
                "deterministic": "format_simple_answer",
                "answer": "synthesize_sales_answer",
                "repair": "repair_sales_sql",
                "fail": "safe_failure_answer",
            },
        )
        builder.add_edge("format_simple_answer", "finalize_turn")
        builder.add_edge("synthesize_sales_answer", "finalize_turn")
        builder.add_edge("safe_failure_answer", "finalize_turn")
        builder.add_edge("finalize_turn", END)
        return builder.compile(checkpointer=self._checkpointer)

    @staticmethod
    def _config(session_id: str) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": f"odoo-sales-agent-v1:{session_id}",
            },
            "recursion_limit": 24,
        }

    async def _persisted_history(
        self,
        session_id: str,
        supplied: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if supplied:
            return supplied[-20:]
        try:
            snapshot = await self._graph.aget_state(self._config(session_id))
        except Exception:
            return []
        values = snapshot.values or {}
        history = values.get("conversation", [])
        return list(history[-20:]) if isinstance(history, list) else []

    async def _initial_state(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        session_id: str,
    ) -> AgentState:
        persisted_history = await self._persisted_history(session_id, history)
        return {
            "question": question,
            "display_question": question,
            "clarification_answer": "",
            "history": persisted_history,
            "conversation": persisted_history,
            "intent": "general",
            "answer": "",
            "answer_mode": "llm",
            "provider": self._routing.general.name,
            "model": self._routing.general.model,
            "model_roles": {},
            "semantic_context": "",
            "metric_ids": [],
            "query_plan": None,
            "sql": "",
            "safe_sql": "",
            "sql_errors": [],
            "tables": [],
            "database_ready": False,
            "currency": None,
            "columns": [],
            "rows": [],
            "query_ms": 0.0,
            "truncated": False,
            "data_accessed": False,
            "chart": {"type": "none", "title": "", "x_field": None, "y_fields": []},
            "warnings": [],
            "filled_time_buckets": 0,
            "repair_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

    @staticmethod
    def _interrupt_from_result(state: dict[str, Any]) -> dict[str, Any] | None:
        raw_interrupts = state.get("__interrupt__") or []
        if not raw_interrupts:
            return None
        first = raw_interrupts[0]
        value = getattr(first, "value", first)
        return value if isinstance(value, dict) else {"type": "clarification", "question": str(value)}

    @staticmethod
    def _interrupt_from_snapshot(snapshot: Any) -> dict[str, Any] | None:
        for task in getattr(snapshot, "tasks", ()):
            for pending in getattr(task, "interrupts", ()):
                value = getattr(pending, "value", pending)
                if isinstance(value, dict):
                    return value
                return {"type": "clarification", "question": str(value)}
        return None

    def _outcome(
        self,
        state: dict[str, Any],
        *,
        interrupt_payload: dict[str, Any] | None = None,
    ) -> AgentOutcome:
        plan = None
        if state.get("query_plan"):
            try:
                plan = QueryPlan.model_validate(state["query_plan"])
            except ValidationError:
                plan = None
        interrupted = interrupt_payload is not None
        answer = state.get("answer") or (
            interrupt_payload.get("question", "需要你补充查询条件。")
            if interrupt_payload
            else "没有生成可用回答。"
        )
        return AgentOutcome(
            answer=answer,
            intent=state.get("intent", "general"),
            provider=state.get("provider", self._provider.name),
            model=state.get("model", self._provider.model),
            input_tokens=state.get("input_tokens", 0),
            output_tokens=state.get("output_tokens", 0),
            total_tokens=state.get("total_tokens", 0),
            estimated_cost_usd=state.get("estimated_cost_usd", 0.0),
            data_accessed=state.get("data_accessed", False),
            sql=state.get("safe_sql") or None,
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            chart=state.get("chart"),
            metrics=state.get("metric_ids", []),
            query_ms=state.get("query_ms"),
            truncated=state.get("truncated", False),
            warnings=state.get("warnings", []),
            currency=state.get("currency"),
            query_plan=plan,
            answer_mode=state.get("answer_mode", "llm"),
            model_roles=state.get("model_roles", {}),
            interrupted=interrupted,
            interrupt_payload=interrupt_payload,
            conversation=state.get("conversation", []),
        )

    async def _invoke(
        self,
        value: AgentState | Command,
        *,
        session_id: str,
    ) -> AgentOutcome:
        config = self._config(session_id)
        with trace_agent(
            name="route-and-answer-odoo-question",
            input_data={"session_id": session_id},
        ) as observation:
            state = await self._graph.ainvoke(value, config=config)
            interrupt_payload = self._interrupt_from_result(state)
            update_observation(
                observation,
                output={
                    "intent": state.get("intent"),
                    "data_accessed": state.get("data_accessed", False),
                    "tables": state.get("tables", []),
                    "row_count": len(state.get("rows", [])),
                    "repair_count": state.get("repair_count", 0),
                    "interrupted": bool(interrupt_payload),
                    "answer_mode": state.get("answer_mode"),
                },
            )
        return self._outcome(state, interrupt_payload=interrupt_payload)

    async def run(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        session_id: str | None = None,
    ) -> AgentOutcome:
        active_session = session_id or f"ephemeral-{uuid4().hex}"
        initial = await self._initial_state(
            question=question,
            history=history,
            session_id=active_session,
        )
        return await self._invoke(initial, session_id=active_session)

    async def resume(self, *, session_id: str, answer: str) -> AgentOutcome:
        return await self._invoke(Command(resume=answer), session_id=session_id)

    async def stream(
        self,
        *,
        question: str | None,
        history: list[dict[str, str]] | None,
        session_id: str,
        resume_answer: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if resume_answer is not None:
            value: AgentState | Command = Command(resume=resume_answer)
        else:
            if question is None:
                raise ValueError("question is required for a new turn")
            value = await self._initial_state(
                question=question,
                history=history or [],
                session_id=session_id,
            )

        config = self._config(session_id)
        with trace_agent(
            name="route-and-answer-odoo-question",
            input_data={"session_id": session_id, "streaming": True},
        ) as observation:
            async for chunk in self._graph.astream(
                value,
                config=config,
                stream_mode=["custom", "values"],
                version="v2",
            ):
                if chunk.get("type") == "custom":
                    event = chunk.get("data")
                    if isinstance(event, dict):
                        yield event

            snapshot = await self._graph.aget_state(config)
            state = dict(snapshot.values or {})
            interrupt_payload = self._interrupt_from_snapshot(snapshot)
            outcome = self._outcome(state, interrupt_payload=interrupt_payload)
            update_observation(
                observation,
                output={
                    "intent": outcome.intent,
                    "data_accessed": outcome.data_accessed,
                    "row_count": len(outcome.rows),
                    "interrupted": outcome.interrupted,
                    "answer_mode": outcome.answer_mode,
                },
            )
        yield {"type": "outcome", "outcome": outcome}

    async def session_state(
        self,
        session_id: str,
    ) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
        snapshot = await self._graph.aget_state(self._config(session_id))
        values = dict(snapshot.values or {})
        conversation = list(values.get("conversation", []))
        pending = self._interrupt_from_snapshot(snapshot)
        if pending and values.get("display_question"):
            question = str(values["display_question"])
            if not conversation or conversation[-1] != {"role": "user", "content": question}:
                conversation.append({"role": "user", "content": question})
        return conversation, pending

    def _classify(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("classify", "正在判断问题类型")
        return {"intent": classify_intent(state["question"], state.get("history", []))}

    async def _answer_general(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("general", "正在组织普通回答")
        config = self._routing.general
        result = await self._gateway.complete(
            messages=[
                {
                    "role": "system",
                    "content": general_system_prompt(provider=config.name, model=config.model),
                },
                *state.get("history", [])[-10:],
                {"role": "user", "content": state["question"]},
            ],
            generation_name="answer-general-question",
            generation_role="general",
            metadata={"feature": "general-chat", "data_accessed": False},
            config=config,
        )
        return {
            "answer": result.content,
            "answer_mode": "llm",
            **_usage_fields(state, result, role="general"),
        }

    def _explain_metric(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("semantic", "正在读取销售指标口径")
        explanation = self._semantics.metric_explanation(state["question"])
        if explanation is None:
            explanation = "当前销售语义层已定义销售额、含税销售额、订单数、平均订单额、销售数量、交付数量和开票数量。"
        return {"answer": explanation, "answer_mode": "semantic", "data_accessed": False}

    async def _retrieve_sales_context(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("database-check", "正在检查 Odoo 只读连接")
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

        _emit_stage("semantic-retrieval", "正在检索销售语义与字段")
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
        _emit_stage("sql-generation", "正在生成查询计划和 SQL")
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
            generation_role="sql",
            metadata={"feature": "text2sql", "semantic_version": self._semantics.version},
            json_mode=True,
            config=self._routing.sql,
        )
        try:
            payload = parse_sql_generation_payload(
                result.content,
                allowed_metric_ids=state.get("metric_ids", []),
            )
            sql = payload.sql
            plan = payload.plan.model_dump(mode="json")
            metric_ids = payload.plan.metric_ids or state.get("metric_ids", [])
            parse_errors: list[str] = []
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            sql = ""
            plan = None
            metric_ids = state.get("metric_ids", [])
            parse_errors = [f"查询计划格式无效：{type(exc).__name__}。"]
        return {
            "sql": sql,
            "query_plan": plan,
            "metric_ids": metric_ids,
            "sql_errors": parse_errors,
            **_usage_fields(state, result, role="sql"),
        }

    def _route_after_planning(self, state: AgentState) -> str:
        if state.get("query_plan"):
            plan = QueryPlan.model_validate(state["query_plan"])
            if plan.requires_clarification:
                return "clarify"
        return "validate"

    def _clarify_query_plan(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("clarification", "需要你补充一个关键条件")
        plan = QueryPlan.model_validate(state["query_plan"])
        response = interrupt(
            {
                "type": "clarification",
                "question": plan.clarification_question,
                "ambiguities": plan.ambiguities,
            }
        )
        answer = str(response.get("answer", "")) if isinstance(response, dict) else str(response)
        updated_plan = plan.model_copy(
            update={
                "requires_clarification": False,
                "clarification_question": None,
            }
        )
        return {
            "question": f"{state['display_question']}\n用户补充条件：{answer}",
            "clarification_answer": answer,
            "query_plan": updated_plan.model_dump(mode="json"),
            "sql": "",
            "safe_sql": "",
            "sql_errors": [],
            "repair_count": 0,
        }

    def _validate_sales_sql(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("sql-validation", "正在执行只读 SQL 安全校验")
        sql = state.get("sql", "")
        with trace_tool(
            name="validate-readonly-sales-sql",
            input_data={"sql": sql, "company_id": self._database_config.company_id},
        ) as observation:
            if not sql:
                errors = state.get("sql_errors", []) or ["模型没有返回可用 SQL。"]
                safe_sql = ""
                tables: list[str] = []
                safe = False
            else:
                validation = self._guard.validate(sql)
                errors = validation.errors
                safe_sql = validation.sql or ""
                tables = validation.tables
                safe = validation.safe
            update_observation(observation, output={"safe": safe, "errors": errors, "tables": tables})
        return {"safe_sql": safe_sql, "sql_errors": errors, "tables": tables}

    def _route_after_validation(self, state: AgentState) -> str:
        if state.get("safe_sql") and not state.get("sql_errors"):
            return "execute"
        return "repair" if state.get("repair_count", 0) < 2 else "fail"

    async def _repair_sales_sql(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("sql-repair", "SQL 未通过校验，正在安全修复")
        result = await self._gateway.complete(
            messages=[
                {
                    "role": "user",
                    "content": sql_repair_prompt(
                        question=state["question"],
                        semantic_context=state["semantic_context"],
                        previous_sql=state.get("sql", ""),
                        previous_plan=state.get("query_plan") or {},
                        errors=state.get("sql_errors", []),
                    ),
                }
            ],
            generation_name="repair-sales-sql",
            generation_role="sql-repair",
            metadata={
                "feature": "text2sql-repair",
                "repair_number": state.get("repair_count", 0) + 1,
            },
            json_mode=True,
            config=self._routing.sql,
        )
        try:
            payload = parse_sql_generation_payload(
                result.content,
                allowed_metric_ids=state.get("metric_ids", []),
            )
            sql = payload.sql
            plan = payload.plan.model_dump(mode="json")
            errors: list[str] = []
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            sql = ""
            plan = state.get("query_plan")
            errors = [f"修复后的查询计划格式无效：{type(exc).__name__}。"]
        return {
            "sql": sql,
            "query_plan": plan,
            "safe_sql": "",
            "sql_errors": errors,
            "repair_count": state.get("repair_count", 0) + 1,
            **_usage_fields(state, result, role="sql-repair"),
        }

    async def _execute_sales_sql(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("sql-execution", "正在查询 Odoo 实时销售数据")
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
            plan = None
            if state.get("query_plan"):
                plan = QueryPlan.model_validate(state["query_plan"])
            if can_answer_deterministically(
                plan,
                state.get("columns", []),
                state.get("rows", []),
                truncated=state.get("truncated", False),
            ):
                return "deterministic"
            return "answer"
        return "repair" if state.get("repair_count", 0) < 2 else "fail"

    def _format_simple_answer(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("deterministic-answer", "正在生成确定性结果摘要")
        plan = QueryPlan.model_validate(state["query_plan"])
        answer = build_deterministic_answer(
            plan=plan,
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            currency=state.get("currency"),
        )
        chart = build_chart_spec(state["question"], state.get("columns", []), state.get("rows", []))
        return {"answer": answer, "answer_mode": "deterministic", "chart": chart}

    async def _synthesize_sales_answer(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("answer-synthesis", "正在整理销售分析结论")
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
            generation_role="answer",
            metadata={
                "feature": "chatbi-answer",
                "row_count": len(state.get("rows", [])),
                "data_accessed": True,
            },
            config=self._routing.answer,
        )
        chart = build_chart_spec(state["question"], state.get("columns", []), state.get("rows", []))
        return {
            "answer": result.content,
            "answer_mode": "llm",
            "chart": chart,
            **_usage_fields(state, result, role="answer"),
        }

    def _safe_failure_answer(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("safe-failure", "查询未通过安全检查")
        warnings = state.get("warnings", [])
        if not state.get("database_ready", True):
            answer = "Odoo 只读数据库当前没有连通。我没有查询或编造业务数据；请先在设置页检查数据库连接。"
        else:
            answer = "这次查询没有通过只读安全校验或数据库执行失败，因此没有返回业务数据。你可以换一种更明确的销售问题再试。"
            warnings = warnings + state.get("sql_errors", [])
        return {
            "answer": answer,
            "answer_mode": "failure",
            "data_accessed": False,
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _finalize_turn(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("complete", "回答已完成")
        conversation = list(state.get("conversation", state.get("history", [])))
        question = state.get("display_question", state.get("question", ""))
        if question and (not conversation or conversation[-1] != {"role": "user", "content": question}):
            conversation.append({"role": "user", "content": question})
        clarification = state.get("clarification_answer", "")
        if clarification:
            conversation.append({"role": "user", "content": clarification})
        conversation.append({"role": "assistant", "content": state.get("answer", "")})
        return {"conversation": conversation[-20:], "history": conversation[-20:]}
