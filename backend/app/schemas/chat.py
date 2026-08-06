from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import ProviderName


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    provider: ProviderName | None = None
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=20)


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ChartSpec(BaseModel):
    type: Literal["none", "kpi", "line", "bar", "pie"] = "none"
    title: str = ""
    x_field: str | None = None
    y_fields: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    provider: str
    model: str
    usage: TokenUsage
    trace_id: str | None = None
    trace_url: str | None = None
    data_accessed: bool = False
    phase: Literal["general-chat", "semantic-layer", "text2sql"] = "general-chat"
    intent: Literal["general", "semantic", "data"] = "general"
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
