from __future__ import annotations

import json
import re
from numbers import Real
from typing import Any

from app.schemas.analysis import ChartPlan, ChartSeriesPlan, DataColumnProfile, DataProfile
from app.schemas.query_plan import QueryPlan


_TIME_HINTS = ("date", "day", "week", "month", "quarter", "year", "time", "日期", "月份")
_PIE_HINTS = ("占比", "构成", "比例", "份额")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[T ][0-9:.+-]+)?$")


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _unique_count(values: list[Any]) -> int:
    normalized = {json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value in values}
    return len(normalized)


def build_data_profile(columns: list[str], rows: list[dict[str, Any]]) -> DataProfile:
    profiles: list[DataColumnProfile] = []
    numeric_fields: list[str] = []
    time_fields: list[str] = []
    dimension_fields: list[str] = []

    for column in columns:
        values = [row.get(column) for row in rows if row.get(column) is not None]
        normalized_name = column.casefold()
        if not values:
            kind = "empty"
            minimum = maximum = None
        elif all(isinstance(value, bool) for value in values):
            kind = "boolean"
            minimum = maximum = None
        elif all(_is_number(value) for value in values):
            kind = "number"
            minimum = float(min(values))
            maximum = float(max(values))
        elif any(hint in normalized_name for hint in _TIME_HINTS) or all(
            isinstance(value, str) and _ISO_DATE.match(value) for value in values
        ):
            kind = "time"
            minimum = maximum = None
        elif all(isinstance(value, (str, bool)) for value in values):
            kind = "category"
            minimum = maximum = None
        else:
            kind = "mixed"
            minimum = maximum = None

        profile = DataColumnProfile(
            name=column,
            kind=kind,
            non_null_count=len(values),
            unique_count=_unique_count(values),
            min_value=minimum,
            max_value=maximum,
        )
        profiles.append(profile)
        if kind == "number":
            numeric_fields.append(column)
        elif kind == "time":
            time_fields.append(column)
            dimension_fields.append(column)
        elif kind in {"category", "boolean", "mixed"}:
            dimension_fields.append(column)

    return DataProfile(
        row_count=len(rows),
        columns=profiles,
        numeric_fields=numeric_fields,
        time_fields=time_fields,
        dimension_fields=dimension_fields,
    )


def _json_object(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip(), flags=re.I)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("ChartPlanMissingJsonObject")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("ChartPlanIsNotObject")
    return parsed


def validate_chart_plan(plan: ChartPlan, *, profile: DataProfile) -> ChartPlan:
    available = {column.name: column.kind for column in profile.columns}
    referenced = [
        field
        for field in [plan.x_field, plan.sort_by, *plan.y_fields]
        if field is not None
    ]
    unknown = sorted(set(referenced) - set(available))
    if unknown:
        raise ValueError("ChartPlanUnknownFields:" + ",".join(unknown))

    if plan.type == "kpi":
        if profile.row_count != 1 or any(available[field] != "number" for field in plan.y_fields):
            raise ValueError("ChartPlanInvalidKpi")
    elif plan.type in {"line", "bar", "pie"}:
        if any(available[field] != "number" for field in plan.y_fields):
            raise ValueError("ChartPlanSeriesMustBeNumeric")
        if plan.type == "line" and available.get(plan.x_field or "") != "time":
            raise ValueError("ChartPlanLineRequiresTimeAxis")
        if plan.type == "pie" and profile.row_count > 12 and not (
            plan.top_n and plan.top_n <= 12
        ):
            raise ValueError("ChartPlanPieHasTooManyCategories")
    elif plan.type == "scatter":
        if available.get(plan.x_field or "") != "number" or any(
            available[field] != "number" for field in plan.y_fields
        ):
            raise ValueError("ChartPlanScatterRequiresNumericAxes")
    return plan


def parse_chart_plan(
    content: str,
    *,
    profile: DataProfile,
    query_plan: QueryPlan | None = None,
) -> ChartPlan:
    payload = _json_object(content)
    if payload.get("type") not in {"none", "table"} and "series" not in payload:
        raise ValueError("ChartPlanSeriesMissing")
    plan = ChartPlan.model_validate(payload)
    if (
        query_plan
        and query_plan.query_type == "ranking"
        and query_plan.row_limit is None
        and plan.type in {"bar", "pie"}
        and plan.top_n is None
    ):
        plan = plan.model_copy(update={"top_n": 10})
    return validate_chart_plan(plan, profile=profile)


def should_call_chart_planner(profile: DataProfile) -> bool:
    return (
        profile.row_count > 1
        and bool(profile.numeric_fields)
        and bool(profile.dimension_fields or len(profile.numeric_fields) > 1)
    )


def build_chart_spec(
    question: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    query_plan: QueryPlan | None = None,
) -> dict[str, Any]:
    """Build a safe deterministic fallback when model planning is unnecessary or invalid."""

    profile = build_data_profile(columns, rows)
    if not rows or not columns:
        return ChartPlan(type="none", reason="没有可展示的数据").model_dump(mode="json")
    if not profile.numeric_fields:
        return ChartPlan(type="table", reason="结果没有可绘制的数值字段").model_dump(mode="json")
    if len(rows) == 1:
        plan = ChartPlan(
            type="kpi",
            title="查询结果",
            series=[ChartSeriesPlan(field=field) for field in profile.numeric_fields[:4]],
            reason="单行聚合结果适合 KPI",
        )
        return plan.model_dump(mode="json")

    dimensions = profile.dimension_fields
    if not dimensions:
        return ChartPlan(type="table", reason="结果缺少可用的维度字段").model_dump(mode="json")

    x_field = profile.time_fields[0] if profile.time_fields else dimensions[0]
    chart_type = "line" if x_field in profile.time_fields else "bar"
    if any(hint in question for hint in _PIE_HINTS) and len(profile.numeric_fields) == 1:
        chart_type = "pie"
    top_n = None
    if query_plan and query_plan.query_type == "ranking":
        top_n = query_plan.row_limit or 10
    elif len(rows) > 20:
        top_n = 10 if chart_type in {"bar", "pie"} else 20

    plan = ChartPlan(
        type=chart_type,
        title="查询结果",
        x_field=x_field,
        series=[ChartSeriesPlan(field=field) for field in profile.numeric_fields[:4]],
        sort_by=x_field if chart_type == "line" else None,
        sort_order="asc" if chart_type == "line" else None,
        top_n=top_n,
        reason="由确定性规则根据字段类型生成",
    )
    return validate_chart_plan(plan, profile=profile).model_dump(mode="json")
