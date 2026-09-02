from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


QueryType = Literal["kpi", "trend", "ranking", "detail", "comparison"]
TimeGrain = Literal["none", "day", "week", "month", "quarter", "year"]
FilterOperator = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
]
FilterSource = Literal["user", "metric_rule", "system_required"]
ResultShape = Literal["scalar", "time_series", "ranking", "table"]
SortDirection = Literal["asc", "desc"]


_RANKING_COUNT = r"(?:[1-9]\d{0,3}|[一二两三四五六七八九十百千]+)"
_EXPLICIT_RANKING_LIMIT_PATTERNS = (
    re.compile(rf"\btop\s*{_RANKING_COUNT}\b", re.IGNORECASE),
    re.compile(rf"前\s*{_RANKING_COUNT}\s*(?:个|名|位|条|款|种|家|件|张)?"),
    re.compile(
        rf"(?:最高|最低)(?:的)?\s*{_RANKING_COUNT}\s*"
        r"(?:个|名|位|条|款|种|家|件|张)?"
    ),
    re.compile(
        r"(?:最高|最低)(?:的)?\s*"
        r"(?:客户|产品|商品|销售员|业务员)(?:是|为|有|谁|哪)"
    ),
)


def ranking_requires_row_limit(question: str) -> bool:
    """Return whether the user explicitly requested a bounded Top-N ranking."""

    normalized = question.strip()
    if not normalized:
        return True
    return any(pattern.search(normalized) for pattern in _EXPLICIT_RANKING_LIMIT_PATTERNS)


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=120)
    operator: FilterOperator = "eq"
    value: Any
    source: FilterSource = "user"


class QuerySort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=120)
    direction: SortDirection = "asc"


class QueryTimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=120)
    start: date | None = None
    end: date | None = None
    grain: TimeGrain = "none"

    @model_validator(mode="after")
    def end_must_not_precede_start(self) -> "QueryTimeRange":
        if self.start and self.end and self.end < self.start:
            raise ValueError("time range end must not precede start")
        return self


class QueryPlan(BaseModel):
    """Typed, JSON-serializable contract between the LLM and SQL pipeline."""

    model_config = ConfigDict(extra="forbid")

    query_type: QueryType
    metric_ids: list[str] = Field(default_factory=list, max_length=12)
    dimensions: list[str] = Field(default_factory=list, max_length=12)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=20)
    time_range: QueryTimeRange = Field(default_factory=QueryTimeRange)
    result_shape: ResultShape = "table"
    select_columns: list[str] = Field(default_factory=list, max_length=20)
    sort: list[QuerySort] = Field(default_factory=list, max_length=6)
    row_limit: int | None = Field(default=None, ge=1, le=5_000)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    ambiguities: list[str] = Field(default_factory=list, max_length=10)
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def clarification_must_be_actionable(self) -> "QueryPlan":
        if self.requires_clarification and not (
            self.clarification_question and self.clarification_question.strip()
        ):
            raise ValueError(
                "clarification_question is required when clarification is requested"
            )
        return self


class SqlGenerationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: QueryPlan
    sql: str = Field(default="", max_length=20_000)
