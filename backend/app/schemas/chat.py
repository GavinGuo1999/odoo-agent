from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import ProviderName


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    provider: ProviderName | None = None


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    provider: str
    model: str
    usage: TokenUsage
    trace_id: str | None = None
    data_accessed: bool = False
    phase: str = "model-connectivity"
