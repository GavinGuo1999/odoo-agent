from __future__ import annotations

import asyncio
import json
import logging
import time
from contextvars import ContextVar
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from app.bi.chart import (
    build_chart_spec,
    build_data_profile,
    parse_chart_plan,
    should_call_chart_planner,
)
from app.bi.deterministic_answer import (
    build_deterministic_answer,
    can_answer_deterministically,
)
from app.bi.error_analysis import (
    MAX_SQL_REPAIR_ATTEMPTS,
    analyze_sql_errors,
    query_fingerprint,
    sql_fingerprint,
)
from app.bi.prompts import (
    answer_synthesis_prompt,
    chart_planning_prompt,
    general_system_prompt,
    knowledge_answer_prompt,
    sql_generation_prompt,
    sql_repair_prompt,
)
from app.bi.semantic_provider import SemanticContextProvider, build_semantic_provider
from app.bi.time_series import complete_year_months
from app.config import (
    DatabaseConfig,
    ModelRoutingConfig,
    ProviderConfig,
    SemanticConfig,
    WikiConfig,
)
from app.database import (
    DatabaseConnectionError,
    OdooDatabase,
    ReadOnlySqlGuard,
    classify_sql_error,
)
from app.llm import LLMGateway, LLMResult
from app.observability import trace_agent, trace_retrieval, trace_tool, update_observation
from app.schemas.analysis import DataProfile, SqlErrorAnalysis
from app.schemas.query_plan import (
    FollowupQuery,
    QueryPlan,
    SqlGenerationPayload,
    ranking_requires_row_limit,
)
from app.services.wiki_knowledge import WikiKnowledgeService, get_wiki_service
from app.state import get_state_store


Intent = Literal["general", "knowledge", "source", "semantic", "data", "hybrid"]
AnswerMode = Literal["llm", "deterministic", "knowledge", "semantic", "failure"]


logger = logging.getLogger(__name__)

class AgentState(TypedDict, total=False):
    # 产物要按会话+消息下标落库，节点必须能拿到它。
    session_id: str
    question: str
    display_question: str
    query_corrections: list[dict[str, str]]
    clarification_answer: str
    history: list[dict[str, str]]
    conversation: list[dict[str, str]]
    intent: Intent
    answer: str
    answer_mode: AnswerMode
    provider: str
    model: str
    model_roles: dict[str, dict[str, str]]
    role_usage: dict[str, dict[str, Any]]
    semantic_context: str
    semantic_provider: str
    semantic_version: str
    knowledge_context: str
    knowledge_citations: list[dict[str, Any]]
    knowledge_index_fingerprint: str
    metric_ids: list[str]
    query_plan: dict[str, Any] | None
    logical_sql: str
    sql: str
    safe_sql: str
    sql_errors: list[str]
    sql_error_stage: str
    sql_error_analysis: dict[str, Any] | None
    sql_fingerprints: list[str]
    tables: list[str]
    database_ready: bool
    currency: str | None
    columns: list[str]
    rows: list[dict[str, Any]]
    query_ms: float
    truncated: bool
    data_accessed: bool
    chart: dict[str, Any]
    # 补充查询的结果。只喂给答案合成，不参与画图——图表必须和主查询一一对应，
    # 否则用户看不出图上画的是哪一份数据。
    followup_queries: list[dict[str, Any]]
    auxiliary_results: list[dict[str, Any]]
    data_profile: dict[str, Any]
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
    role_usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    semantic_provider: str = "native"
    repair_count: int = 0
    interrupted: bool = False
    interrupt_payload: dict[str, Any] | None = None
    conversation: list[dict[str, str]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    trace_steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def phase(self) -> str:
        return {
            "general": "general-chat",
            "knowledge": "knowledge-base",
            "source": "knowledge-base",
            "semantic": "semantic-layer",
            "data": "text2sql",
            "hybrid": "text2sql",
        }[self.intent]


_DATA_WORDS = (
    "销售", "订单", "客户", "产品", "商品", "销量", "业绩", "收入", "交付",
    "发货", "开票", "报价", "成交", "销售员",
    "成本",
    "毛利",
    "费用",
    "库存价值", "业务员", "排行榜", "趋势", "同比",
    "环比", "本月", "上月", "本年", "今年", "去年", "采购", "库存", "odoo", "sql",
)
_SEMANTIC_WORDS = (
    "口径", "定义", "怎么算", "怎么计算", "什么意思", "包括什么", "是什么", "含义",    "怎么核算",
    "如何核算",
    "核算方法",
)
_METRIC_WORDS = (
    "销售额", "含税销售额", "订单数", "平均订单额", "销量", "销售数量", "交付数量", "开票数量",
)
_TECHNICAL_WORDS = (
    "qty_to_invoice", "qty_delivered", "qty_invoiced", "sale.order", "sale.order.line",
    "stock.move", "stock.picking", "account.move", "procurement", "invoice_status",    "成本",
    "毛利",
    "费用",
    "库存价值",
    "计价",
)
_KNOWLEDGE_WORDS = (
    "为什么", "什么是", "是什么", "流程", "区别", "关系", "原理", "机制", "含义",
    "怎么来的", "怎么生成", "怎么产生", "怎么工作", "如何工作", "如何生成", "如何产生",
)
_SOURCE_WORDS = ("源码", "代码", "调用链", "哪个方法", "哪个类", "在哪里定义", "字段在哪")
_KNOWLEDGE_STRUCTURE_WORDS = (
    "有哪些模块", "有哪些模型", "有哪些字段", "包括哪些字段", "包含哪些字段",
)
_HYBRID_EXPLANATION_WORDS = (
    "为什么", "原因", "怎么来的", "怎么生成", "怎么产生", "如何工作", "如何生成", "如何产生",
)
_DATA_REQUEST_WORDS = (
    "多少", "最高", "最低", "排名", "趋势", "同比", "环比", "本月", "上月", "今年", "去年",
    "哪些", "列出", "明细", "每月", "每天", "每周", "汇总", "统计",
)
_FOLLOW_UP_WORDS = ("那", "再", "呢", "上个月", "去年", "同比", "环比", "换成")
_UNRESOLVED_CUSTOMER_MARKERS = ("那个客户", "这个客户", "该客户", "那位客户")
_NAMED_BUSINESS_REGIONS = ("华东", "华南", "华北", "华中", "西南", "西北", "东北")
_SUPPORTED_REGION_FIELDS = {
    "region",
    "region_id",
    "sales_region",
    "sales_region_id",
    "business_region",
    "business_region_id",
}
_PRONOUN_TOKENS = ("那个", "这个", "该", "那位", "这位", "所有", "每个", "各个")
# 匹配"客户/公司 + 名字"或"名字 + 公司/集团"，用于判断历史里是否点过具体客户。
_CUSTOMER_NAME_HINT = re.compile(
    r"(?:客户|公司)\s*[:：]?\s*(?P<name>[\w一-鿿][\w一-鿿\s.-]{1,40}?)(?=[的\s，。,、?？]|$)"
    r"|(?P<name2>[\w一-鿿]{2,20})(?:公司|集团|实业|重工|科技|工业)"
)


def classify_intent(question: str, history: list[dict[str, str]]) -> Intent:
    normalized = question.lower().strip()
    has_business_term = any(word in normalized for word in _DATA_WORDS)
    has_technical_term = any(word in normalized for word in _TECHNICAL_WORDS)
    has_knowledge_cue = any(word in normalized for word in _KNOWLEDGE_WORDS) or (
        has_technical_term and any(word in normalized for word in _SEMANTIC_WORDS)
    )
    has_data_request = any(word in normalized for word in _DATA_REQUEST_WORDS)
    if (has_business_term or has_technical_term) and any(
        word in normalized for word in _SOURCE_WORDS
    ):
        return "source"
    if not has_data_request and any(word in normalized for word in _METRIC_WORDS) and any(
        word in normalized for word in _SEMANTIC_WORDS
    ):
        return "semantic"
    if (has_business_term or has_technical_term) and any(
        word in normalized for word in _KNOWLEDGE_STRUCTURE_WORDS
    ):
        return "knowledge"
    if (has_business_term or has_technical_term) and has_data_request and any(
        word in normalized for word in _HYBRID_EXPLANATION_WORDS
    ):
        return "hybrid"
    if (has_business_term or has_technical_term) and has_knowledge_cue and not has_data_request:
        return "knowledge"
    if has_business_term or has_technical_term:
        return "data"

    previous_context = " ".join(item.get("content", "") for item in history[-4:]).lower()
    if any(word in normalized for word in _FOLLOW_UP_WORDS) and any(
        word in previous_context for word in (*_DATA_WORDS, *_TECHNICAL_WORDS)
    ):
        if has_knowledge_cue:
            return "knowledge"
        return "data"
    return "general"


_QUERY_TYPO_RULES = (
    {
        "original": "销售呃",
        "corrected": "销售额",
        "wrong_fragment": "呃",
        "replacement": "额",
    },
)


def normalize_query_text(question: str) -> tuple[str, list[dict[str, str]]]:
    """Apply only high-confidence, domain-specific typo corrections.

    Entity names, regions, dates and values must never be guessed here. Structured
    corrections are persisted so the result can tell the user exactly what changed.
    """

    normalized = question
    corrections: list[dict[str, str]] = []
    for rule in _QUERY_TYPO_RULES:
        original = rule["original"]
        if original not in normalized:
            continue
        normalized = normalized.replace(original, rule["corrected"])
        corrections.append(dict(rule))
    return normalized, corrections


def _query_correction_warnings(corrections: list[dict[str, str]]) -> list[str]:
    return [
        (
            f"检测到可能的业务错字：‘{item['original']}’中的‘{item['wrong_fragment']}’"
            f"可能误写，已将‘{item['wrong_fragment']}’纠正为‘{item['replacement']}’，"
            f"本次按‘{item['corrected']}’理解。"
        )
        for item in corrections
    ]


def _unsupported_business_region(
    question: str,
    table_columns: dict[str, list[str]],
) -> str | None:
    """Return a requested macro-region when the semantic model has no region field."""

    requested = next((item for item in _NAMED_BUSINESS_REGIONS if item in question), None)
    if requested is None:
        return None
    exposed = {
        column.casefold()
        for columns in table_columns.values()
        for column in columns
    }
    return None if exposed.intersection(_SUPPORTED_REGION_FIELDS) else requested


def _turn_decisions(state: AgentState) -> dict[str, Any]:
    """本轮确定性决策的汇总，挂在轮次根节点上。

    Langfuse 里原本只看得到模型调用，所以"这类问题在哪一步失败、占多少"答不上来。
    这些字段是可聚合的：按 `guard_error_codes` 分组就能回答"守卫拒绝里哪一类最多"，
    按 `failed_stage` 分组就能回答"失败集中在规划、编译还是执行"。
    """

    errors = list(state.get("sql_errors", []) or [])
    steps = _TURN_TRACE.get() or []
    return {
        "guard_error_codes": [
            classify_sql_error(error) for error in errors
        ],
        "failed_stage": state.get("sql_error_stage") if errors else None,
        "followup_count": len(state.get("auxiliary_results", []) or []),
        "followup_declared": len(state.get("followup_queries", []) or []),
        "warning_count": len(state.get("warnings", []) or []),
        "node_path": [step["stage"] for step in steps],
        "elapsed_ms": round(steps[-1]["at_ms"], 1) if steps else 0.0,
    }


def _classification_evidence(question: str) -> dict[str, list[str]]:
    """列出问题命中了哪些词表。路由判错时，这是唯一能直接指向原因的东西。"""

    normalized = question.lower().strip()
    groups = {
        "business": _DATA_WORDS,
        "technical": _TECHNICAL_WORDS,
        "data_request": _DATA_REQUEST_WORDS,
        "knowledge": _KNOWLEDGE_WORDS,
        "hybrid_explanation": _HYBRID_EXPLANATION_WORDS,
        "semantic": _SEMANTIC_WORDS,
        "source": _SOURCE_WORDS,
    }
    return {
        name: [word for word in words if word in normalized]
        for name, words in groups.items()
        if any(word in normalized for word in words)
    }


def _history_names_a_customer(history: list[dict[str, str]] | None) -> bool:
    """判断历史里是否真的点过某个具体客户。

    曾经这里只判断"有没有历史"，导致只要不是第一轮提问，"那个客户"就永远不触发
    澄清——哪怕前面聊的是完全无关的话题。实测中"帮我查一下那个客户今年的销售额"
    因此返回了全部客户的合计，并被当成某一个客户的答案呈现。

    历史里出现过客户名的典型形态：用户说了"客户 X"，或助手在回答里提到过
    "X 的销售额"。这里只做保守判断：宁可多问一次，也不要凭空替用户选一个客户。
    """

    for message in history or []:
        content = str(message.get("content") or "")
        if not content:
            continue
        # 命中"客户/公司 + 具体名字"，且名字本身不是代词。
        for match in _CUSTOMER_NAME_HINT.finditer(content):
            name = match.group("name").strip()
            if name and not any(marker in name for marker in _PRONOUN_TOKENS):
                return True
    return False


def _has_unresolved_customer_reference(
    question: str,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = question.casefold()
    return (
        "用户补充条件：" not in question
        and any(marker in normalized for marker in _UNRESOLVED_CUSTOMER_MARKERS)
        and not _history_names_a_customer(history)
    )


def _deterministic_clarification_plan(question: str) -> QueryPlan:
    normalized = question.casefold()
    metric_ids: list[str] = []
    if any(word in normalized for word in ("销售额", "销售收入", "业绩", "成交金额")):
        metric_ids.append("sales_amount")
    if any(word in normalized for word in ("订单数", "订单数量", "多少张订单")):
        metric_ids.append("order_count")

    query_type = "kpi"
    result_shape = "scalar"
    dimensions: list[str] = []
    if any(word in normalized for word in ("明细", "列表", "哪些订单")):
        query_type, result_shape = "detail", "table"
    elif any(word in normalized for word in ("趋势", "每月", "每个月", "月度", "按月")):
        query_type, result_shape = "trend", "time_series"
        dimensions = ["month"]
    elif any(word in normalized for word in ("最高", "最低", "排名", "排行", "top ")):
        query_type, result_shape = "ranking", "ranking"
        dimensions = ["customer"]
    elif any(word in normalized for word in ("分别", "差额", "相比", "比较", "对比")):
        query_type, result_shape = "comparison", "table"

    return QueryPlan(
        query_type=query_type,
        metric_ids=metric_ids,
        dimensions=dimensions,
        result_shape=result_shape,
        select_columns=[*dimensions, *metric_ids],
        ambiguities=["客户指代不明确"],
        requires_clarification=True,
        clarification_question="请提供要查询的客户名称或 ID。",
    )


def _json_object(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip(), flags=re.I)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("NoJsonObject")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("NotJsonObject")
    return parsed


def _normalize_time_range_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop dynamic SQL expressions from display-only date metadata.

    The executable SQL and declared filters remain unchanged and continue through
    the read-only guard. QueryTimeRange deliberately remains typed as ISO dates.
    """

    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return payload
    time_range = plan.get("time_range")
    if not isinstance(time_range, dict):
        return payload
    for field in ("start", "end"):
        value = time_range.get(field)
        if not isinstance(value, str):
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            time_range[field] = None
    return payload


def format_plan_validation_error(exc: Exception) -> str:
    """Return repair-useful validation locations without echoing model input."""

    if isinstance(exc, ValidationError):
        issues: list[str] = []
        for item in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:8]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "plan"
            issues.append(f"{location}:{item.get('type', 'validation_error')}")
        return "ValidationError[" + "; ".join(issues) + "]"
    message = str(exc)
    if re.fullmatch(r"(?:NoJsonObject|NotJsonObject|QueryPlan[A-Za-z:,]+)", message):
        return message
    return type(exc).__name__


_CANONICAL_DIMENSIONS = {
    "月份": "month",
    "月": "month",
    "年份": "year",
    "年": "year",
    "客户": "customer",
    "客户名称": "customer",
    "伙伴": "customer",
    "产品": "product",
    "产品名称": "product",
    "商品": "product",
    "销售员": "salesperson",
    "销售员名称": "salesperson",
    "业务员": "salesperson",
}
_INVOICE_DIFFERENCE_METRICS = (
    "sales_quantity",
    "invoiced_quantity",
    "uninvoiced_quantity",
)


def _requires_invoice_difference_breakdown(question: str) -> bool:
    normalized = question.casefold()
    return (
        "差额" in normalized
        and any(word in normalized for word in ("销售数量", "订购数量", "销量"))
        and any(word in normalized for word in ("已开票", "开票数量"))
    )


def _normalize_plan_semantics(
    plan: QueryPlan,
    question: str,
    history: list[dict[str, str]] | None,
) -> QueryPlan:
    dimensions = [
        _CANONICAL_DIMENSIONS.get(item.strip().casefold(), item)
        for item in plan.dimensions
    ]
    if not dimensions:
        stable_dimensions = {
            "day",
            "week",
            "month",
            "quarter",
            "year",
            "customer",
            "product",
            "salesperson",
        }
        for column in plan.select_columns:
            canonical = _CANONICAL_DIMENSIONS.get(column.strip().casefold(), column)
            if canonical in stable_dimensions and canonical not in dimensions:
                dimensions.append(canonical)
    normalized_question = question.casefold()
    ranking_markers = ("最高", "最低", "排名", "排行", "前十", "前五", "top ")
    comparison_markers = ("分别", "差额", "相比", "比较", "对比")
    trend_markers = ("趋势", "每月", "每个月", "月度", "按月")
    requires_clarification = plan.requires_clarification
    clarification_question = plan.clarification_question
    ambiguities = list(plan.ambiguities)
    if (
        _has_unresolved_customer_reference(question, history)
    ):
        requires_clarification = True
        clarification_question = (
            clarification_question or "请提供要查询的客户名称或 ID。"
        )
        if "客户指代不明确" not in ambiguities:
            ambiguities.append("客户指代不明确")

    query_type = plan.query_type
    result_shape = plan.result_shape
    if any(marker in normalized_question for marker in ranking_markers):
        query_type, result_shape = "ranking", "ranking"
    elif any(marker in normalized_question for marker in comparison_markers):
        query_type, result_shape = "comparison", "table"
    elif any(marker in normalized_question for marker in trend_markers):
        query_type, result_shape = "trend", "time_series"
    elif (
        requires_clarification
        and plan.metric_ids
        and plan.query_type == "detail"
        and not any(marker in normalized_question for marker in ("明细", "列表", "哪些订单"))
    ):
        query_type, result_shape = "kpi", "scalar"
        if not any(marker in normalized_question for marker in ("各", "每个", "按")):
            dimensions = []

    return plan.model_copy(
        update={
            "query_type": query_type,
            "result_shape": result_shape,
            "dimensions": dimensions,
            "ambiguities": ambiguities,
            "requires_clarification": requires_clarification,
            "clarification_question": clarification_question,
        }
    )


def _align_entity_filters_with_sql(
    plan: QueryPlan,
    *,
    sql: str,
    question: str,
) -> QueryPlan:
    if not re.search(r"\b(?:[a-z_]\w*\.)?name\s*(?:=|ilike\b|like\b)", sql, re.I):
        return plan

    changed = False
    filters = []
    for item in plan.filters:
        field = item.field.rsplit(".", 1)[-1].casefold()
        if (
            item.source == "user"
            and field in {
                "partner_id",
                "partner_name",
                "customer",
                "customer_name",
                "客户",
                "客户名称",
            }
            and isinstance(item.value, str)
        ):
            filters.append(item.model_copy(update={"field": "name"}))
            changed = True
        else:
            filters.append(item)
    return plan.model_copy(update={"filters": filters}) if changed else plan


def parse_sql_generation_payload(
    content: str,
    *,
    allowed_metric_ids: list[str],
    question: str = "",
    history: list[dict[str, str]] | None = None,
) -> SqlGenerationPayload:
    payload_dict = _normalize_time_range_metadata(_json_object(content))
    payload = SqlGenerationPayload.model_validate(payload_dict)
    allowed = set(allowed_metric_ids)
    filtered_metrics = [item for item in payload.plan.metric_ids if item in allowed]
    plan = payload.plan.model_copy(update={"metric_ids": filtered_metrics})
    plan = _normalize_plan_semantics(plan, question, history)
    normalized_sql = "" if plan.requires_clarification else payload.sql.strip()
    plan = _align_entity_filters_with_sql(
        plan,
        sql=normalized_sql,
        question=question,
    )
    payload = payload.model_copy(update={"plan": plan, "sql": normalized_sql})
    required_contract_fields = {"result_shape", "select_columns", "sort", "row_limit"}
    missing_fields = required_contract_fields - payload.plan.model_fields_set
    if missing_fields:
        raise ValueError(
            "QueryPlanMissingContractFields:" + ",".join(sorted(missing_fields))
        )
    if any("source" not in item.model_fields_set for item in payload.plan.filters):
        raise ValueError("QueryPlanFilterSourceMissing")
    if payload.sql.strip() and not payload.plan.select_columns:
        raise ValueError("QueryPlanSelectColumnsMissing")
    if (
        payload.sql.strip()
        and not payload.plan.requires_clarification
        and _requires_invoice_difference_breakdown(question)
    ):
        expected_columns = ["product", *_INVOICE_DIFFERENCE_METRICS]
        if (
            not set(_INVOICE_DIFFERENCE_METRICS).issubset(payload.plan.metric_ids)
            or payload.plan.select_columns != expected_columns
        ):
            raise ValueError("QueryPlanInvoiceDifferenceColumnsMissing")
    # query_type 描述分析目标，result_shape 描述展示形态，两者不是严格一一对应。
    # 环比/同比本质是 comparison，但最自然的结果仍然是 time_series；带 Top-N 的
    # 比较又可能是 ranking。只对语义上确实单一的类型做严格限制。
    expected_shapes = {
        "trend": {"time_series"},
        "ranking": {"ranking"},
        "detail": {"table"},
        "comparison": {"table", "time_series", "ranking"},
    }.get(payload.plan.query_type)
    if expected_shapes and payload.plan.result_shape not in expected_shapes:
        raise ValueError("QueryPlanResultShapeMismatch")
    if (
        ("今年" in question or "本年" in question)
        and "环比" in question
        and payload.plan.time_range.grain == "month"
    ):
        # 今年 1 月的环比基准是上年 12 月。只从 1 月开始扫描会让 SQL 合法、
        # 结果却静默漏掉 1 月——这是业务正确性错误，不能交给答案模型猜。
        baseline_start = date(date.today().year - 1, 12, 1)
        if (
            payload.plan.time_range.start is None
            or payload.plan.time_range.start > baseline_start
        ):
            raise ValueError("QueryPlanPreviousPeriodBaselineMissing")
    if (
        payload.plan.result_shape == "ranking"
        and payload.plan.row_limit is None
        and ranking_requires_row_limit(question)
    ):
        raise ValueError("QueryPlanRankingLimitMissing")
    if (
        payload.plan.result_shape == "ranking"
        and payload.plan.row_limit is not None
        and not ranking_requires_row_limit(question)
    ):
        raise ValueError("QueryPlanUnexpectedRankingLimit")
    if payload.sql.strip() and not payload.plan.requires_clarification:
        filter_sources = {
            (item.field.rsplit(".", 1)[-1].casefold(), item.source)
            for item in payload.plan.filters
        }
        if ("company_id", "system_required") not in filter_sources:
            raise ValueError("QueryPlanCompanyFilterMissing")
        if any(metric_id in payload.plan.metric_ids for metric_id in allowed_metric_ids) and (
            "state",
            "metric_rule",
        ) not in filter_sources:
            raise ValueError("QueryPlanStateFilterMissing")
    return payload


def _usage_fields(state: AgentState, result: LLMResult, *, role: str) -> dict[str, Any]:
    model_roles = dict(state.get("model_roles", {}))
    model_roles[role] = {"provider": result.provider, "model": result.model}
    # `role` 与推给 Langfuse 的 generation_role 同名，这里按同一维度在本地累加，
    # 使成本归因不必回查 Langfuse API（docs/20 P1-1）。
    role_usage = {key: dict(value) for key, value in state.get("role_usage", {}).items()}
    bucket = role_usage.setdefault(
        role,
        {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        },
    )
    bucket["calls"] += 1
    bucket["input_tokens"] += result.input_tokens or 0
    bucket["output_tokens"] += result.output_tokens or 0
    bucket["total_tokens"] += result.total_tokens or 0
    bucket["estimated_cost_usd"] = round(
        bucket["estimated_cost_usd"] + result.total_cost_usd, 10
    )
    return {
        "provider": result.provider,
        "model": result.model,
        "model_roles": model_roles,
        "role_usage": role_usage,
        "input_tokens": state.get("input_tokens", 0) + (result.input_tokens or 0),
        "output_tokens": state.get("output_tokens", 0) + (result.output_tokens or 0),
        "total_tokens": state.get("total_tokens", 0) + (result.total_tokens or 0),
        "estimated_cost_usd": state.get("estimated_cost_usd", 0.0)
        + result.total_cost_usd,
    }


# 本轮走过的节点。用 ContextVar 而不是塞进 AgentState：_emit_stage 分散在二十多个
# 节点里，让每个节点都往返回值里追加一条既啰嗦又容易漏。
_TURN_TRACE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "odoo_agent_turn_trace", default=None
)


def _begin_turn_trace() -> None:
    _TURN_TRACE.set([])


def _collect_turn_trace() -> list[dict[str, Any]]:
    steps = _TURN_TRACE.get() or []
    return [dict(step) for step in steps]


def _emit_stage(stage: str, label: str) -> None:
    steps = _TURN_TRACE.get()
    if steps is not None:
        now = time.monotonic()
        # 每一步记录它距上一步的耗时——看链路时真正有用的是"卡在哪一步"。
        previous = steps[-1]["at_ms"] if steps else 0.0
        at_ms = 0.0 if not steps else (now - steps[-1]["_t"]) * 1000 + previous
        steps.append({"stage": stage, "label": label, "at_ms": round(at_ms, 1), "_t": now})
    try:
        writer = get_stream_writer()
        writer({"type": "progress", "stage": stage, "label": label})
    except Exception:
        # Nodes are also invoked by non-streaming API/tests, where no writer exists.
        return


# 检查点是每轮整体写入的，历史越长写放大越明显。所以只给最近几轮保留明细行，
# 更早的轮次降级成"图表和 SQL 还在、表格行数被裁掉"。
# 补充查询的两个上限：条数决定额外的数据库往返，行数决定塞进答案 prompt 的体积。
_MAX_FOLLOWUP_QUERIES = 2
_MAX_FOLLOWUP_ROWS = 50

# 产物现在写在独立表里，一轮一行、互不包含，存储是线性的，不再需要
# "只给最近 N 轮留明细"这种限制。保留单轮行数上限的理由变了：它决定
# 一次 /chat/sessions 响应要传多少数据，跟检查点写放大无关。
_ARTIFACT_MAX_ROWS = 500


def _current_trace_id() -> str | None:
    """取当前 Langfuse trace id；没配置或出错一律返回 None，绝不影响本轮回答。"""

    try:
        from app.observability import get_current_trace_id

        return get_current_trace_id()
    except Exception:
        return None


def _current_trace_url() -> str | None:
    try:
        from app.observability import get_current_trace_url

        return get_current_trace_url()
    except Exception:
        return None


def _turn_artifact(state: AgentState) -> dict[str, Any] | None:
    """把这一轮的可视化产物摘出来，供切换会话后重绘。

    只存原始数据；列标签、货币格式这类展示层的东西在读取时再算，避免同一份
    信息在检查点里存两遍、而且改了展示规则老会话不跟着变。
    """

    rows = list(state.get("rows", []) or [])
    chart = state.get("chart") or None
    # 规划器对普通问答会返回 {"type": "none"}，那是"明确不画图"，不是产物。
    if chart and chart.get("type") in (None, "none"):
        chart = None
    citations = list(state.get("knowledge_citations", []) or [])
    sql = state.get("sql") or None
    warnings = list(state.get("warnings", []) or [])

    # 出处信息（哪个模型答的、花了多少、Langfuse 在哪）对**每一条**回答都要留，
    # 包括没有图没有表的纯文字回答——否则刷新一次就再也追不回这次调用的成本。
    provenance: dict[str, Any] = {
        "provider": state.get("provider") or None,
        "model": state.get("model") or None,
        "model_roles": dict(state.get("model_roles", {}) or {}),
        "usage": {
            "input_tokens": int(state.get("input_tokens", 0) or 0),
            "output_tokens": int(state.get("output_tokens", 0) or 0),
            "total_tokens": int(state.get("total_tokens", 0) or 0),
            "estimated_cost_usd": float(state.get("estimated_cost_usd", 0.0) or 0.0),
        },
        "role_usage": dict(state.get("role_usage", {}) or {}),
        "trace_id": _current_trace_id(),
        "trace_url": _current_trace_url(),
    }
    has_render_payload = bool(rows or chart or citations or sql or warnings)
    has_provenance = bool(
        provenance["provider"] or provenance["trace_id"]
        or provenance["usage"]["total_tokens"]
        or _TURN_TRACE.get()
    )
    if not (has_render_payload or has_provenance):
        return None

    # 执行链路：走过哪些节点、每步多久。以前这些事件推给前端当提示文字用完就丢，
    # 事后没法回答"这一轮到底怎么走的、修过几次 SQL、慢在哪"。
    steps = [
        {k: v for k, v in step.items() if k != "_t"}
        for step in _collect_turn_trace()
    ]

    artifact: dict[str, Any] = {
        **provenance,
        "trace_steps": steps,
        "repair_count": int(state.get("repair_count", 0) or 0),
        "intent": state.get("intent"),
        "answer_mode": state.get("answer_mode", "llm"),
        "data_accessed": bool(state.get("data_accessed", False)),
        "sql": sql,
        "columns": list(state.get("columns", []) or []),
        "rows": rows[:_ARTIFACT_MAX_ROWS],
        "row_count": len(rows),
        "chart": chart,
        "metrics": list(state.get("metric_ids", []) or []),
        "currency": state.get("currency"),
        "query_ms": state.get("query_ms"),
        "truncated": bool(state.get("truncated", False)),
        "warnings": warnings,
        "citations": citations,
    }
    if len(rows) > _ARTIFACT_MAX_ROWS:
        artifact["rows_trimmed"] = True
    return artifact




class SalesAgent:
    """LangGraph orchestration for general chat, metric help, and safe Text2SQL."""

    def __init__(
        self,
        provider: ProviderConfig,
        database: DatabaseConfig,
        *,
        routing: ModelRoutingConfig | None = None,
        checkpointer: Any | None = None,
        state_store: Any | None = None,
        semantic_config: SemanticConfig | None = None,
        semantic_provider: SemanticContextProvider | None = None,
        wiki_config: WikiConfig | None = None,
        wiki_service: WikiKnowledgeService | None = None,
    ) -> None:
        self._provider = provider
        self._routing = routing or ModelRoutingConfig(
            sql=provider,
            answer=provider,
            general=provider,
        )
        self._database_config = database
        # 渲染产物写在这里，而不是塞进检查点。传 None 表示不持久化（单测用）。
        self._state_store = state_store
        self._gateway = LLMGateway(provider)
        self._database = OdooDatabase(database)
        self._semantics = semantic_provider or build_semantic_provider(semantic_config)
        self._wiki = wiki_service or (get_wiki_service(wiki_config) if wiki_config else None)
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
        builder.add_node("retrieve_wiki_context", self._retrieve_wiki_context)
        builder.add_node("answer_knowledge", self._answer_knowledge)
        builder.add_node("explain_metric", self._explain_metric)
        builder.add_node("detect_data_ambiguity", self._detect_data_ambiguity)
        builder.add_node("retrieve_sales_context", self._retrieve_sales_context)
        builder.add_node("generate_sales_sql", self._generate_sales_sql)
        builder.add_node("clarify_query_plan", self._clarify_query_plan)
        builder.add_node("compile_semantic_sql", self._compile_semantic_sql)
        builder.add_node("validate_sales_sql", self._validate_sales_sql)
        builder.add_node("analyze_sql_error", self._analyze_sql_error)
        builder.add_node("clarify_sql_error", self._clarify_sql_error)
        builder.add_node("repair_sales_sql", self._repair_sales_sql)
        builder.add_node("execute_sales_sql", self._execute_sales_sql)
        builder.add_node("profile_sales_result", self._profile_sales_result)
        builder.add_node("plan_chart", self._plan_chart)
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
                "knowledge": "retrieve_wiki_context",
                "source": "retrieve_wiki_context",
                "semantic": "explain_metric",
                "data": "detect_data_ambiguity",
                "hybrid": "retrieve_wiki_context",
            },
        )
        builder.add_edge("answer_general", "finalize_turn")
        builder.add_conditional_edges(
            "retrieve_wiki_context",
            lambda state: "data" if state["intent"] == "hybrid" else "answer",
            {"data": "detect_data_ambiguity", "answer": "answer_knowledge"},
        )
        builder.add_edge("answer_knowledge", "finalize_turn")
        builder.add_edge("explain_metric", "finalize_turn")
        builder.add_conditional_edges(
            "detect_data_ambiguity",
            self._route_after_ambiguity_detection,
            {"clarify": "clarify_query_plan", "ready": "retrieve_sales_context"},
        )
        builder.add_conditional_edges(
            "retrieve_sales_context",
            lambda state: "ready" if state.get("database_ready") else "unavailable",
            {"ready": "generate_sales_sql", "unavailable": "safe_failure_answer"},
        )
        builder.add_conditional_edges(
            "generate_sales_sql",
            self._route_after_planning,
            {"clarify": "clarify_query_plan", "compile": "compile_semantic_sql"},
        )
        builder.add_edge("clarify_query_plan", "retrieve_sales_context")
        builder.add_conditional_edges(
            "compile_semantic_sql",
            self._route_after_semantic_compilation,
            {
                "validate": "validate_sales_sql",
                "error": "analyze_sql_error",
            },
        )
        builder.add_conditional_edges(
            "validate_sales_sql",
            self._route_after_validation,
            {
                "execute": "execute_sales_sql",
                "error": "analyze_sql_error",
            },
        )
        builder.add_conditional_edges(
            "analyze_sql_error",
            self._route_after_error_analysis,
            {
                "repair": "repair_sales_sql",
                "clarify": "clarify_sql_error",
                "fail": "safe_failure_answer",
            },
        )
        builder.add_edge("clarify_sql_error", "retrieve_sales_context")
        builder.add_edge("repair_sales_sql", "compile_semantic_sql")
        builder.add_conditional_edges(
            "execute_sales_sql",
            self._route_after_execution,
            {
                "profile": "profile_sales_result",
                "error": "analyze_sql_error",
            },
        )
        builder.add_node("run_followup_queries", self._run_followup_queries)
        builder.add_edge("profile_sales_result", "run_followup_queries")
        builder.add_edge("run_followup_queries", "plan_chart")
        builder.add_conditional_edges(
            "plan_chart",
            self._route_after_chart_planning,
            {
                "deterministic": "format_simple_answer",
                "answer": "synthesize_sales_answer",
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
        normalized_question, corrections = normalize_query_text(question)
        return {
            "session_id": session_id,
            "question": normalized_question,
            "display_question": question,
            "query_corrections": corrections,
            "clarification_answer": "",
            "history": persisted_history,
            "conversation": persisted_history,
            "intent": "general",
            "answer": "",
            "answer_mode": "llm",
            "provider": self._routing.general.name,
            "model": self._routing.general.model,
            "model_roles": {},
            "role_usage": {},
            "semantic_context": "",
            "semantic_provider": self._semantics.name,
            "semantic_version": self._semantics.version,
            "knowledge_context": "",
            "knowledge_citations": [],
            "knowledge_index_fingerprint": "",
            "metric_ids": [],
            "query_plan": None,
            "logical_sql": "",
            "sql": "",
            "safe_sql": "",
            "sql_errors": [],
            "sql_error_stage": "planning",
            "sql_error_analysis": None,
            "sql_fingerprints": [],
            "tables": [],
            "database_ready": False,
            "currency": None,
            "columns": [],
            "rows": [],
            "query_ms": 0.0,
            "truncated": False,
            "data_accessed": False,
            "chart": build_chart_spec("", [], []),
            "followup_queries": [],
            "auxiliary_results": [],
            "data_profile": {},
            "warnings": _query_correction_warnings(corrections),
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
            role_usage=state.get("role_usage", {}),
            semantic_provider=state.get("semantic_provider", self._semantics.name),
            repair_count=state.get("repair_count", 0),
            interrupted=interrupted,
            interrupt_payload=interrupt_payload,
            conversation=state.get("conversation", []),
            citations=state.get("knowledge_citations", []),
            trace_steps=[
                {k: v for k, v in step.items() if k != "_t"}
                for step in _collect_turn_trace()
            ],
        )

    async def _invoke(
        self,
        value: AgentState | Command,
        *,
        session_id: str,
    ) -> AgentOutcome:
        config = self._config(session_id)
        _begin_turn_trace()
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
                    "sql_error_category": (
                        (state.get("sql_error_analysis") or {}).get("category")
                    ),
                    "interrupted": bool(interrupt_payload),
                    "answer_mode": state.get("answer_mode"),
                    "citation_count": len(state.get("knowledge_citations", [])),
                    "chart_type": (state.get("chart") or {}).get("type"),
                    **_turn_decisions(state),
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
        _begin_turn_trace()
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
                    "citation_count": len(outcome.citations),
                },
            )
        yield {"type": "outcome", "outcome": outcome}

    async def session_state(
        self,
        session_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """返回历史消息（assistant 消息可能带 artifact）与待回答的澄清问题。

        artifact 就地挂在消息上，只用于前端重绘；发给模型的历史仍然只有
        role/content 两个键。
        """

        snapshot = await self._graph.aget_state(self._config(session_id))
        values = dict(snapshot.values or {})
        conversation = [dict(message) for message in values.get("conversation", [])]

        stored: dict[int, dict[str, Any]] = {}
        if self._state_store is not None:
            try:
                stored = await self._state_store.load_message_artifacts(session_id)
            except Exception as exc:  # pragma: no cover - 取决于数据库可用性
                logger.warning(
                    "Failed to load message artifacts: %s", type(exc).__name__
                )
        # 搬家之前的会话把产物存在检查点的一个 channel 里，按下标对齐。
        # 这里做兼容回退，让老会话翻回去仍然看得到图。
        legacy = list(values.get("conversation_artifacts", []))
        for index, message in enumerate(conversation):
            artifact = stored.get(index)
            if artifact is None and index < len(legacy):
                artifact = legacy[index]
            if artifact:
                message["artifact"] = artifact
        pending = self._interrupt_from_snapshot(snapshot)
        if pending and values.get("display_question"):
            question = str(values["display_question"])
            if not conversation or conversation[-1] != {"role": "user", "content": question}:
                conversation.append({"role": "user", "content": question})
        return conversation, pending

    def _classify(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("classify", "正在判断问题类型")
        question = state["question"]
        intent = classify_intent(question, state.get("history", []))
        # 路由不调模型，所以它本来不会出现在任何 trace 里——可线上排查时
        # "这题为什么没走取数"恰恰是最常问的。把判定和命中的词一起记下来。
        with trace_tool(
            name="classify-question-intent",
            input_data={"question": question},
        ) as observation:
            update_observation(
                observation,
                output={
                    "intent": intent,
                    "matched": _classification_evidence(question),
                },
            )
        return {"intent": intent}

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

    async def _retrieve_wiki_context(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("knowledge-retrieval", "正在检索 Odoo Wiki")
        if self._wiki is None:
            return {
                "knowledge_context": "",
                "knowledge_citations": [],
                "knowledge_index_fingerprint": "",
                "warnings": state.get("warnings", []) + ["Odoo Wiki 当前未配置。"],
            }

        with trace_retrieval(
            name="retrieve-odoo-wiki-context",
            input_data={"question": state["question"], "intent": state["intent"]},
        ) as observation:
            result = await asyncio.to_thread(self._wiki.search, state["question"])
            update_observation(
                observation,
                output={
                    "hit_count": len(result.hits),
                    "titles": [hit.title for hit in result.hits],
                    "headings": [hit.heading for hit in result.hits],
                    "index_fingerprint": result.index_fingerprint,
                    "retrieval_mode": result.retrieval_mode,
                    "reranked": result.reranked,
                    "fallback_reason": result.fallback_reason,
                },
            )
        return {
            "knowledge_context": result.context(),
            "knowledge_citations": result.citations,
            "knowledge_index_fingerprint": result.index_fingerprint,
        }

    async def _answer_knowledge(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("knowledge-answer", "正在根据 Odoo Wiki 组织回答")
        context = state.get("knowledge_context", "")
        if not context:
            return {
                "answer": (
                    "我没有在已审核的 learn_odoo 笔记中找到足够依据，所以这次不凭记忆猜测。"
                    "你可以换一个更具体的模型、字段或业务流程名称再问。"
                ),
                "answer_mode": "failure",
                "data_accessed": False,
                "warnings": state.get("warnings", []) + ["Odoo Wiki 未命中可引用内容。"],
            }

        result = await self._gateway.complete(
            messages=[
                {
                    "role": "user",
                    "content": knowledge_answer_prompt(
                        question=state["question"],
                        history=state.get("history", []),
                        knowledge_context=context,
                        source_mode=state["intent"] == "source",
                    ),
                }
            ],
            generation_name="answer-odoo-knowledge-question",
            generation_role="answer",
            metadata={
                "feature": "wiki-knowledge",
                "intent": state["intent"],
                "citation_count": len(state.get("knowledge_citations", [])),
                "knowledge_index_fingerprint": state.get("knowledge_index_fingerprint", ""),
                "data_accessed": False,
            },
            config=self._routing.answer,
        )
        return {
            "answer": result.content,
            "answer_mode": "knowledge",
            "data_accessed": False,
            **_usage_fields(state, result, role="answer"),
        }

    def _explain_metric(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("semantic", "正在读取销售指标口径")
        explanation = self._semantics.metric_explanation(state["question"])
        if explanation is None:
            explanation = "当前销售语义层已定义销售额、含税销售额、订单数、平均订单额、销售数量、交付数量和开票数量。"
        return {"answer": explanation, "answer_mode": "semantic", "data_accessed": False}

    def _detect_data_ambiguity(self, state: AgentState) -> dict[str, Any]:
        unsupported_region = _unsupported_business_region(
            state["question"],
            self._semantics.table_columns,
        )
        if unsupported_region:
            _emit_stage("ambiguity-detection", "检测到未配置的业务区域口径")
            plan = _deterministic_clarification_plan(state["question"]).model_copy(
                update={
                    "ambiguities": [f"语义模型未配置‘{unsupported_region}区’对应的区域字段"],
                    "requires_clarification": True,
                    "clarification_question": (
                        f"当前数据模型没有可查询的‘{unsupported_region}区’字段。"
                        f"请去掉‘{unsupported_region}区’条件，或先在数据与模型中配置客户区域字段。"
                    ),
                }
            )
            return {
                "query_plan": plan.model_dump(mode="json"),
                "metric_ids": plan.metric_ids,
                "logical_sql": "",
                "sql": "",
                "safe_sql": "",
                "sql_errors": [],
                "sql_error_stage": "planning",
                "sql_error_analysis": None,
            }
        if not _has_unresolved_customer_reference(
            state["question"],
            state.get("history", []),
        ):
            return {}
        _emit_stage("ambiguity-detection", "检测到客户指代不明确")
        plan = _deterministic_clarification_plan(state["question"])
        return {
            "query_plan": plan.model_dump(mode="json"),
            "metric_ids": plan.metric_ids,
            "logical_sql": "",
            "sql": "",
            "safe_sql": "",
            "sql_errors": [],
            "sql_error_stage": "planning",
            "sql_error_analysis": None,
        }

    @staticmethod
    def _route_after_ambiguity_detection(state: AgentState) -> str:
        plan_data = state.get("query_plan")
        if plan_data and QueryPlan.model_validate(plan_data).requires_clarification:
            return "clarify"
        return "ready"

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
                "semantic_provider": self._semantics.name,
                "company_id": self._database_config.company_id,
            },
        ) as observation:
            discovered = await self._database.discover_columns(self._semantics.table_columns)
            context = await self._semantics.retrieve(
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
                    "semantic_provider": context.provider,
                },
            )
        return {
            "database_ready": True,
            "currency": health.currency,
            "semantic_context": context.as_prompt(),
            "semantic_provider": context.provider,
            "semantic_version": context.version,
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
            metadata={
                "feature": "text2sql",
                "semantic_version": state.get("semantic_version", self._semantics.version),
                "semantic_provider": state.get("semantic_provider", self._semantics.name),
            },
            json_mode=True,
            config=self._routing.sql,
        )
        try:
            payload = parse_sql_generation_payload(
                result.content,
                allowed_metric_ids=state.get("metric_ids", []),
                question=state["question"],
                history=state.get("history", []),
            )
            logical_sql = payload.sql
            plan = payload.plan.model_dump(mode="json")
            metric_ids = payload.plan.metric_ids or state.get("metric_ids", [])
            followups = [item.model_dump(mode="json") for item in payload.followups]
            parse_errors: list[str] = []
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            logical_sql = ""
            plan = None
            metric_ids = state.get("metric_ids", [])
            followups = []
            parse_errors = [f"查询计划格式无效：{format_plan_validation_error(exc)}。"]
        return {
            "logical_sql": logical_sql,
            "sql": "",
            "query_plan": plan,
            "metric_ids": metric_ids,
            "followup_queries": followups,
            "sql_errors": parse_errors,
            "sql_error_stage": "planning",
            "sql_error_analysis": None,
            "sql_fingerprints": (
                [fingerprint]
                if (fingerprint := query_fingerprint(logical_sql, plan))
                else []
            ),
            **_usage_fields(state, result, role="sql"),
        }

    def _route_after_planning(self, state: AgentState) -> str:
        if state.get("query_plan"):
            plan = QueryPlan.model_validate(state["query_plan"])
            if plan.requires_clarification:
                return "clarify"
        return "compile"

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
            # 下游继续使用已规范化的问题；display_question 只负责向用户展示原文。
            "question": f"{state['question']}\n用户补充条件：{answer}",
            "clarification_answer": answer,
            "query_plan": updated_plan.model_dump(mode="json"),
            "logical_sql": "",
            "sql": "",
            "safe_sql": "",
            "sql_errors": [],
            "sql_error_stage": "planning",
            "sql_error_analysis": None,
            "sql_fingerprints": [],
            "repair_count": 0,
        }

    async def _compile_semantic_sql(self, state: AgentState) -> dict[str, Any]:
        provider_name = state.get("semantic_provider", self._semantics.name)
        _emit_stage(
            "semantic-compile",
            "正在用 Wren MDL 编译语义 SQL"
            if provider_name == "wren"
            else "正在准备物理 SQL",
        )
        logical_sql = state.get("logical_sql", "").strip()
        if not logical_sql:
            return {
                "sql": "",
                "sql_errors": state.get("sql_errors", []) or ["模型没有返回可用 SQL。"],
                "sql_error_stage": (
                    "planning" if not state.get("query_plan") else "semantic_compilation"
                ),
            }

        with trace_tool(
            name=(
                "compile-wren-semantic-sql"
                if provider_name == "wren"
                else "prepare-native-sql"
            ),
            input_data={
                "semantic_provider": provider_name,
                "semantic_version": state.get("semantic_version"),
                "logical_sql": logical_sql,
            },
        ) as observation:
            try:
                planned_sql = await self._semantics.plan_sql(logical_sql)
                errors: list[str] = []
            except Exception as exc:
                planned_sql = ""
                errors = [f"语义 SQL 编译失败：{type(exc).__name__}。"]
            update_observation(
                observation,
                output={
                    "compiled": bool(planned_sql),
                    "semantic_provider": provider_name,
                    "errors": errors,
                },
            )
        return {
            "sql": planned_sql,
            "safe_sql": "",
            "sql_errors": errors,
            "sql_error_stage": "semantic_compilation",
            "sql_error_analysis": None,
        }

    @staticmethod
    def _route_after_semantic_compilation(state: AgentState) -> str:
        if state.get("sql") and not state.get("sql_errors"):
            return "validate"
        return "error"

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
                plan = (
                    QueryPlan.model_validate(state["query_plan"])
                    if state.get("query_plan")
                    else None
                )
                validation = self._guard.validate(
                    sql,
                    plan=plan,
                    question=state.get("question", ""),
                )
                errors = validation.errors
                safe_sql = validation.sql or ""
                tables = validation.tables
                safe = validation.safe
            update_observation(
                observation,
                # 分类码才是能聚合的那一列；中文消息是给用户看的。
                output={
                    "safe": safe,
                    "errors": errors,
                    "error_codes": (
                        validation.error_codes if sql and not safe else []
                    ),
                    "tables": tables,
                },
            )
        return {
            "safe_sql": safe_sql,
            "sql_errors": errors,
            "tables": tables,
            "sql_error_stage": "validation",
            "sql_error_analysis": None,
        }

    def _route_after_validation(self, state: AgentState) -> str:
        if state.get("safe_sql") and not state.get("sql_errors"):
            return "execute"
        return "error"

    def _analyze_sql_error(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("sql-error-analysis", "正在分析 SQL 失败原因")
        stage = state.get("sql_error_stage", "execution")
        if stage not in {"planning", "semantic_compilation", "validation", "execution"}:
            stage = "execution"
        analysis = analyze_sql_errors(
            stage=stage,
            errors=state.get("sql_errors", []),
            sql=state.get("logical_sql") or state.get("sql", ""),
        )
        with trace_tool(
            name="analyze-sql-error",
            input_data={
                "stage": stage,
                "repair_count": state.get("repair_count", 0),
                "sql_fingerprint": analysis.sql_fingerprint,
            },
        ) as observation:
            update_observation(
                observation,
                output={
                    "category": analysis.category,
                    "repairable": analysis.repairable,
                    "needs_user_input": analysis.needs_user_input,
                },
            )
        return {"sql_error_analysis": analysis.model_dump(mode="json")}

    @staticmethod
    def _route_after_error_analysis(state: AgentState) -> str:
        raw = state.get("sql_error_analysis")
        if not raw:
            return "fail"
        analysis = SqlErrorAnalysis.model_validate(raw)
        if analysis.needs_user_input:
            return "clarify"
        if analysis.repairable and state.get("repair_count", 0) < MAX_SQL_REPAIR_ATTEMPTS:
            return "repair"
        return "fail"

    def _clarify_sql_error(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("clarification", "SQL 失败原因需要你补充业务条件")
        analysis = SqlErrorAnalysis.model_validate(state["sql_error_analysis"])
        response = interrupt(
            {
                "type": "clarification",
                "question": analysis.clarification_question,
                "ambiguities": [analysis.summary],
            }
        )
        answer = str(response.get("answer", "")) if isinstance(response, dict) else str(response)
        return {
            "question": f"{state['question']}\n用户补充条件：{answer}",
            "clarification_answer": answer,
            "query_plan": None,
            "logical_sql": "",
            "sql": "",
            "safe_sql": "",
            "sql_errors": [],
            "sql_error_stage": "planning",
            "sql_error_analysis": None,
            "sql_fingerprints": [],
            "repair_count": 0,
        }

    async def _repair_sales_sql(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("sql-repair", "SQL 未通过校验，正在安全修复")
        result = await self._gateway.complete(
            messages=[
                {
                    "role": "user",
                    "content": sql_repair_prompt(
                        question=state["question"],
                        semantic_context=state["semantic_context"],
                        previous_sql=state.get("logical_sql") or state.get("sql", ""),
                        previous_plan=state.get("query_plan") or {},
                        errors=state.get("sql_errors", []),
                        error_analysis=state.get("sql_error_analysis"),
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
                question=state["question"],
                history=state.get("history", []),
            )
            logical_sql = payload.sql
            plan = payload.plan.model_dump(mode="json")
            followups = [item.model_dump(mode="json") for item in payload.followups]
            errors: list[str] = []
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            logical_sql = ""
            plan = state.get("query_plan")
            followups = []
            errors = [
                "修复后的查询计划格式无效："
                f"{format_plan_validation_error(exc)}。"
            ]
        fingerprints = list(state.get("sql_fingerprints", []))
        fingerprint = query_fingerprint(logical_sql, plan)
        if fingerprint and fingerprint in fingerprints:
            logical_sql = ""
            errors = ["SQL 修复产生了重复查询，已停止循环。"]
        elif fingerprint:
            fingerprints.append(fingerprint)
        return {
            "logical_sql": logical_sql,
            "sql": "",
            "query_plan": plan,
            "followup_queries": followups,
            "safe_sql": "",
            "sql_errors": errors,
            "sql_error_stage": "planning",
            "sql_error_analysis": None,
            "sql_fingerprints": fingerprints,
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
                safe_detail = type(exc).__name__
                if isinstance(exc, DatabaseConnectionError) and re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_]{0,100}", str(exc)
                ):
                    safe_detail = str(exc)
                errors = [f"数据库执行失败：{safe_detail}。"]
                update_observation(
                    observation,
                    level="ERROR",
                    status_message=type(exc).__name__,
                    output={"status": "error", "error_type": type(exc).__name__},
                )
                return {
                    "sql_errors": errors,
                    "sql_error_stage": "execution",
                    "sql_error_analysis": None,
                    "data_accessed": False,
                }

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
                    "estimated_plan_cost": result.estimated_plan_cost,
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
            "sql_error_stage": "execution",
            "sql_error_analysis": None,
            "filled_time_buckets": filled_time_buckets,
            "warnings": warnings,
        }

    def _route_after_execution(self, state: AgentState) -> str:
        return "profile" if state.get("data_accessed") else "error"

    def _profile_sales_result(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("data-profile", "正在分析结果字段和数据结构")
        profile = build_data_profile(state.get("columns", []), state.get("rows", []))
        return {"data_profile": profile.model_dump(mode="json")}

    async def _run_followup_queries(self, state: AgentState) -> dict[str, Any]:
        """执行模型声明的补充查询，给"为什么"类问题提供下钻证据。

        三条硬规则：
          1. **同一套守卫**。每条补充查询带自己的 QueryPlan，走 self._guard.validate，
             和主查询一字不差。没有任何"补充查询所以放宽一点"的余地。
          2. **失败不影响主答案**。主查询已经成功了，补充数据只是锦上添花；这里出错
             记一条 warning 继续走，绝不把整轮拖进修复循环。
          3. **不参与画图**。图表只反映主查询，否则用户看不出图上是哪份数据。
        """

        followups = list(state.get("followup_queries", []) or [])
        if not followups:
            return {}

        _emit_stage("followup-queries", f"正在执行 {len(followups)} 条补充查询")
        results: list[dict[str, Any]] = []
        warnings = list(state.get("warnings", []) or [])

        for raw in followups[:_MAX_FOLLOWUP_QUERIES]:
            try:
                followup = FollowupQuery.model_validate(raw)
            except ValidationError:
                warnings.append("一条补充查询的结构无效，已跳过。")
                continue

            # 契约校验里的 Top-N 判据要看"这条查询是干什么的"，不能看主问题：
            # 主问题问"下降最大的三个月"，但补充查询要的是那几个月的**完整**客户构成，
            # 拿主问题去判会要求它声明 row_limit，直接把它挡在门外。
            # 表/字段白名单、company_id、禁写这些安全检查与主查询完全一致，不受影响。
            validation = self._guard.validate(
                followup.sql,
                plan=followup.plan,
                question=followup.purpose,
                # 补充查询不展示给用户，展示层的契约（Top-N 声明、产品名 COALESCE、
                # select_columns 对齐）对它没有意义。安全检查一条不少。
                enforce_presentation_contract=False,
            )
            if not validation.safe or not validation.sql:
                # 把守卫的理由带出来，评测时才知道模型在哪类补充查询上踩坑。
                reason = validation.errors[0] if validation.errors else "未通过安全校验"
                warnings.append(f"补充查询「{followup.name}」未执行：{reason}")
                continue

            with trace_tool(
                name="execute-followup-sales-sql",
                input_data={"name": followup.name, "sql": validation.sql},
            ) as observation:
                try:
                    result = await self._database.execute_readonly(validation.sql)
                except Exception as exc:
                    update_observation(
                        observation,
                        level="ERROR",
                        status_message=type(exc).__name__,
                        output={"status": "error"},
                    )
                    warnings.append(
                        f"补充查询「{followup.name}」执行失败：{type(exc).__name__}"
                    )
                    continue
                rows = list(result.rows)
                update_observation(
                    observation,
                    output={"status": "ok", "row_count": len(rows)},
                )

            results.append({
                "name": followup.name,
                "purpose": followup.purpose,
                "sql": validation.sql,
                "columns": list(result.columns),
                "rows": rows[:_MAX_FOLLOWUP_ROWS],
                "row_count": len(rows),
            })

        return {
            "auxiliary_results": results,
            "warnings": list(dict.fromkeys(warnings)),
        }

    async def _plan_chart(self, state: AgentState) -> dict[str, Any]:
        profile = DataProfile.model_validate(state.get("data_profile", {}))
        query_plan = (
            QueryPlan.model_validate(state["query_plan"])
            if state.get("query_plan")
            else None
        )
        fallback = build_chart_spec(
            state["question"],
            state.get("columns", []),
            state.get("rows", []),
            query_plan=query_plan,
        )
        if not should_call_chart_planner(profile):
            return {"chart": fallback}

        _emit_stage("chart-planning", "正在规划安全图表")
        try:
            result = await self._gateway.complete(
                messages=[
                    {
                        "role": "user",
                        "content": chart_planning_prompt(
                            question=state["question"],
                            query_plan=state.get("query_plan") or {},
                            data_profile=state["data_profile"],
                        ),
                    }
                ],
                generation_name="plan-sales-chart",
                generation_role="chart-planner",
                metadata={
                    "feature": "genbi-chart-planning",
                    "row_count": profile.row_count,
                    "column_count": len(profile.columns),
                },
                json_mode=True,
                config=self._routing.answer,
            )
            chart = parse_chart_plan(
                result.content,
                profile=profile,
                query_plan=query_plan,
            ).model_dump(mode="json")
        except Exception as exc:
            warnings = state.get("warnings", []) + [
                f"图表规划未通过安全 Schema，已使用确定性回退：{type(exc).__name__}。"
            ]
            return {"chart": fallback, "warnings": list(dict.fromkeys(warnings))}
        return {
            "chart": chart,
            **_usage_fields(state, result, role="chart-planner"),
        }

    @staticmethod
    def _route_after_chart_planning(state: AgentState) -> str:
        if state.get("intent") == "hybrid":
            return "answer"
        plan = (
            QueryPlan.model_validate(state["query_plan"])
            if state.get("query_plan")
            else None
        )
        if can_answer_deterministically(
            plan,
            state.get("columns", []),
            state.get("rows", []),
            truncated=state.get("truncated", False),
        ):
            return "deterministic"
        return "answer"

    def _format_simple_answer(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("deterministic-answer", "正在生成确定性结果摘要")
        plan = QueryPlan.model_validate(state["query_plan"])
        answer = build_deterministic_answer(
            plan=plan,
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            currency=state.get("currency"),
        )
        return {"answer": answer, "answer_mode": "deterministic"}

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
                        data_profile=state.get("data_profile"),
                        knowledge_context=state.get("knowledge_context", ""),
                        auxiliary_results=state.get("auxiliary_results", []),
                        hybrid=state.get("intent") == "hybrid",
                    ),
                }
            ],
            generation_name="explain-sales-result",
            generation_role="answer",
            metadata={
                "feature": "chatbi-answer",
                "row_count": len(state.get("rows", [])),
                "data_accessed": True,
                "hybrid": state.get("intent") == "hybrid",
                "citation_count": len(state.get("knowledge_citations", [])),
            },
            config=self._routing.answer,
        )
        return {
            "answer": result.content,
            "answer_mode": "llm",
            **_usage_fields(state, result, role="answer"),
        }

    def _safe_failure_answer(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("safe-failure", "查询未通过安全检查")
        warnings = state.get("warnings", [])
        if not state.get("database_ready", True):
            answer = "Odoo 只读数据库当前没有连通。我没有查询或编造业务数据；请先在设置页检查数据库连接。"
        else:
            raw_analysis = state.get("sql_error_analysis")
            category = (
                SqlErrorAnalysis.model_validate(raw_analysis).category
                if raw_analysis
                else "unknown"
            )
            answer = {
                "unsafe_operation": "这次 SQL 触发了只读安全策略，因此已拒绝执行，也不会尝试自动绕过限制。",
                "permission": "数据库拒绝了当前查询权限，因此没有执行或返回业务数据。",
                "timeout": "这次查询超过了只读数据库的时间限制。为避免持续占用资源，系统没有盲目重试。",
                "connection": "查询过程中数据库连接不可用，因此没有返回或编造业务数据。",
                "repair_loop": "SQL 修复产生了重复查询，系统已经终止循环，没有执行不可靠的结果。",
            }.get(
                category,
                "这次查询在有限修复次数内仍未通过安全校验或执行，因此没有返回业务数据。你可以换一种更明确的销售问题再试。",
            )
            warnings = warnings + state.get("sql_errors", [])
        return {
            "answer": answer,
            "answer_mode": "failure",
            "data_accessed": False,
            "warnings": list(dict.fromkeys(warnings)),
        }

    async def _finalize_turn(self, state: AgentState) -> dict[str, Any]:
        _emit_stage("complete", "回答已完成")
        conversation = list(state.get("conversation", state.get("history", [])))

        question = state.get("display_question", state.get("question", ""))
        if question and (not conversation or conversation[-1] != {"role": "user", "content": question}):
            conversation.append({"role": "user", "content": question})
        clarification = state.get("clarification_answer", "")
        if clarification:
            conversation.append({"role": "user", "content": clarification})
        conversation.append({"role": "assistant", "content": state.get("answer", "")})

        # 产物落独立表，按消息下标定位。写在裁剪**之前**取下标会对不上，
        # 所以先裁剪再算：存下来的下标必须和 session_state 读回的 conversation 一致。
        trimmed = conversation[-20:]
        await self._persist_turn_artifact(state, len(trimmed) - 1)

        return {"conversation": trimmed, "history": trimmed}

    async def _persist_turn_artifact(self, state: AgentState, message_index: int) -> None:
        """把这一轮的渲染产物写进独立表。

        **失败不致命**：产物只决定"翻回历史时还能不能看到图和明细"，
        丢一条不该让已经算好的回答失败。
        """

        artifact = _turn_artifact(state)
        if not artifact or self._state_store is None:
            return
        session_id = state.get("session_id") or ""
        if not session_id:
            return
        try:
            await self._state_store.save_message_artifact(
                session_id, message_index, artifact
            )
        except Exception as exc:  # pragma: no cover - 取决于数据库可用性
            logger.warning(
                "Failed to persist message artifact: %s", type(exc).__name__
            )
