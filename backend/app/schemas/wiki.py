from __future__ import annotations

from pydantic import BaseModel, Field


class WikiStatusResponse(BaseModel):
    available: bool
    root_path: str
    index_path: str
    note_count: int
    chunk_count: int
    indexed_at: str | None = None
    source_fingerprint: str | None = None
    tokenizer: str | None = None
    allowed_statuses: list[str] = Field(default_factory=list)
    error_type: str | None = None


class WikiCitationResponse(BaseModel):
    note_id: str
    title: str
    heading: str
    excerpt: str
    relative_path: str
    absolute_path: str
    obsidian_uri: str
    status: str
    note_type: str
    module: str | None = None
    topic: str | None = None
    updated: str | None = None
    score: float
    via_wikilink: bool = False


class WikiSearchResponse(BaseModel):
    query: str
    index_fingerprint: str
    hits: list[WikiCitationResponse] = Field(default_factory=list)
