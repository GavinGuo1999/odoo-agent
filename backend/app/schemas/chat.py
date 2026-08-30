from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import ProviderName
from app.schemas.analysis import ChartPlan
from app.schemas.query_plan import QueryPlan
from app.schemas.wiki import WikiCitationResponse


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    provider: ProviderName | None = None
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=20)


class ChatResumeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=4_000)
    provider: ProviderName | None = None


class ChatFeedbackRequest(BaseModel):
    trace_id: str = Field(pattern=r"^[a-fA-F0-9]{32}$")
    positive: bool
    comment: str | None = Field(default=None, max_length=500)


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float = 0.0


class InterruptInfo(BaseModel):
    type: Literal["clarification"] = "clarification"
    question: str
    ambiguities: list[str] = Field(default_factory=list)


class ModelExecution(BaseModel):
    provider: str
    model: str


class ChartSpec(ChartPlan):
    """Public API name for the validated, non-executable ChartPlan."""


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    provider: str
    model: str
    usage: TokenUsage
    trace_id: str | None = None
    trace_url: str | None = None
    status: Literal["completed", "interrupted"] = "completed"
    interrupt: InterruptInfo | None = None
    data_accessed: bool = False
    phase: Literal["general-chat", "knowledge-base", "semantic-layer", "text2sql"] = "general-chat"
    intent: Literal["general", "knowledge", "source", "semantic", "data", "hybrid"] = "general"
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    chart: ChartSpec | None = None
    metrics: list[str] = Field(default_factory=list)
    currency: str | None = None
    column_labels: dict[str, str] = Field(default_factory=dict)
    column_formats: dict[str, str] = Field(default_factory=dict)
    metric_labels: dict[str, str] = Field(default_factory=dict)
    query_ms: float | None = None
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    query_plan: QueryPlan | None = None
    answer_mode: Literal["llm", "deterministic", "knowledge", "semantic", "failure"] = "llm"
    model_roles: dict[str, ModelExecution] = Field(default_factory=dict)
    citations: list[WikiCitationResponse] = Field(default_factory=list)


class ChatSessionView(BaseModel):
    session_id: str
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    pending_interrupt: InterruptInfo | None = None
    persistence_mode: Literal["memory", "postgres"]
    run_status: Literal[
        "new",
        "running",
        "completed",
        "interrupted",
        "failed",
        "cancelled",
    ] = "completed"


class ChatConversationSummary(BaseModel):
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatConversationList(BaseModel):
    conversations: list[ChatConversationSummary] = Field(default_factory=list)
    persistence_mode: Literal["memory", "postgres"]


class ChatConversationDeleteResponse(BaseModel):
    session_id: str
    deleted: bool


class ChatConversationStatusResponse(BaseModel):
    session_id: str
    run_status: Literal[
        "new",
        "running",
        "completed",
        "interrupted",
        "failed",
        "cancelled",
    ]


class ChatFeedbackResponse(BaseModel):
    recorded: bool
    score_name: Literal["user-thumbs"] = "user-thumbs"
