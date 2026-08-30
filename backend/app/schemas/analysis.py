from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SqlErrorStage = Literal["planning", "semantic_compilation", "validation", "execution"]
SqlErrorCategory = Literal[
    "invalid_plan",
    "semantic_compilation",
    "syntax",
    "contract_violation",
    "unknown_column",
    "unknown_table",
    "type_mismatch",
    "timeout",
    "permission",
    "connection",
    "unsafe_operation",
    "ambiguous_request",
    "repair_loop",
    "unknown",
]


class SqlErrorAnalysis(BaseModel):
    """Safe, serializable routing decision for a failed SQL stage."""

    model_config = ConfigDict(extra="forbid")

    stage: SqlErrorStage
    category: SqlErrorCategory
    repairable: bool
    needs_user_input: bool = False
    summary: str = Field(min_length=1, max_length=500)
    repair_hint: str | None = Field(default=None, max_length=500)
    clarification_question: str | None = Field(default=None, max_length=500)
    sql_fingerprint: str | None = Field(default=None, max_length=64)


ColumnKind = Literal["number", "time", "category", "boolean", "empty", "mixed"]


class DataColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: ColumnKind
    non_null_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    min_value: float | None = None
    max_value: float | None = None


class DataProfile(BaseModel):
    """Small deterministic summary used by analysis and chart planning."""

    model_config = ConfigDict(extra="forbid")

    row_count: int = Field(ge=0)
    columns: list[DataColumnProfile] = Field(default_factory=list, max_length=100)
    numeric_fields: list[str] = Field(default_factory=list, max_length=100)
    time_fields: list[str] = Field(default_factory=list, max_length=100)
    dimension_fields: list[str] = Field(default_factory=list, max_length=100)


ChartType = Literal["none", "table", "kpi", "line", "bar", "pie", "scatter"]


class ChartSeriesPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=120)


class ChartPlan(BaseModel):
    """Non-executable chart contract; the browser compiles it to ECharts options."""

    model_config = ConfigDict(extra="forbid")

    type: ChartType = "none"
    title: str = Field(default="", max_length=120)
    x_field: str | None = Field(default=None, max_length=200)
    series: list[ChartSeriesPlan] = Field(default_factory=list, max_length=4)
    # Retained as the stable API field used by the current frontend.
    y_fields: list[str] = Field(default_factory=list, max_length=4)
    sort_by: str | None = Field(default=None, max_length=200)
    sort_order: Literal["asc", "desc"] | None = None
    top_n: int | None = Field(default=None, ge=1, le=50)
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def normalize_series_fields(self) -> "ChartPlan":
        series_fields = [item.field for item in self.series]
        if series_fields and self.y_fields and series_fields != self.y_fields:
            raise ValueError("series fields and y_fields must match")
        if series_fields:
            self.y_fields = series_fields
        elif self.y_fields:
            self.series = [ChartSeriesPlan(field=field) for field in self.y_fields]

        if self.type in {"none", "table"}:
            if self.x_field or self.series:
                raise ValueError("none/table plans must not declare chart axes")
        elif self.type == "kpi":
            if self.x_field or not self.series:
                raise ValueError("kpi plans require series and no x_field")
        elif not self.x_field or not self.series:
            raise ValueError("chart plans require x_field and at least one series")

        if self.sort_order and not self.sort_by:
            raise ValueError("sort_order requires sort_by")
        if self.type == "pie" and len(self.series) != 1:
            raise ValueError("pie charts require exactly one series")
        return self
