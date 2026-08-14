from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SemanticAuditIssueResponse(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    table: str
    field: str | None = None
    message: str


class SemanticAuditResponse(BaseModel):
    audit_id: str
    generated_at: str
    status: Literal["clean", "drift"]
    approval_status: Literal["manual_review_required"]
    semantic_version: str
    formal_wren_version: str
    model_count: int
    field_count: int
    error_count: int
    warning_count: int
    info_count: int
    database_read_only: bool
    python_files_seen: int
    candidate_files_parsed: int
    source_files_matched: int
    source_path: str
    report_path: str
    snapshot_path: str
    draft_path: str
    issues: list[SemanticAuditIssueResponse]
