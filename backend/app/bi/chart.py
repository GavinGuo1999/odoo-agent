from __future__ import annotations

from numbers import Real
from typing import Any


_TIME_HINTS = ("date", "day", "week", "month", "quarter", "year", "time", "日期", "月份")
_PIE_HINTS = ("占比", "构成", "比例", "份额")


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def build_chart_spec(
    question: str,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select a safe chart shape; the model never emits executable chart code."""

    if not rows or not columns:
        return {"type": "none", "title": "", "x_field": None, "y_fields": []}

    numeric_columns = [
        column
        for column in columns
        if any(_is_number(row.get(column)) for row in rows)
    ]
    if not numeric_columns:
        return {"type": "none", "title": "", "x_field": None, "y_fields": []}

    if len(rows) == 1:
        return {
            "type": "kpi",
            "title": "查询结果",
            "x_field": None,
            "y_fields": numeric_columns[:4],
        }

    dimension_columns = [column for column in columns if column not in numeric_columns]
    if not dimension_columns:
        return {"type": "none", "title": "", "x_field": None, "y_fields": []}

    x_field = dimension_columns[0]
    normalized_x = x_field.lower()
    chart_type = "bar"
    if any(hint in normalized_x for hint in _TIME_HINTS):
        chart_type = "line"
    elif any(hint in question for hint in _PIE_HINTS) and len(numeric_columns) == 1:
        chart_type = "pie"

    return {
        "type": chart_type,
        "title": "查询结果",
        "x_field": x_field,
        "y_fields": numeric_columns[:4],
    }
