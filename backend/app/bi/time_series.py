from __future__ import annotations

from datetime import date, datetime
from numbers import Number
from typing import Any


_MONTH_QUESTIONS = ("每个月", "每月", "月度")
_CURRENT_YEAR_WORDS = ("今年", "本年")
_LAST_YEAR_WORDS = ("去年", "上年")
_NUMERIC_FIELD_HINTS = (
    "amount",
    "count",
    "percentage",
    "price",
    "qty",
    "quantity",
    "rate",
    "ratio",
    "revenue",
    "subtotal",
    "total",
)


def _target_year_and_month(question: str, today: date) -> tuple[int, int] | None:
    if not any(word in question for word in _MONTH_QUESTIONS):
        return None
    if any(word in question for word in _CURRENT_YEAR_WORDS):
        return today.year, today.month
    if any(word in question for word in _LAST_YEAR_WORDS):
        return today.year - 1, 12
    return None


def _month_field(columns: list[str]) -> str | None:
    for column in columns:
        normalized = column.lower()
        if normalized == "month" or normalized.endswith("_month") or "月份" in column:
            return column
    return None


def _as_month(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)
    if isinstance(value, str):
        candidate = value.strip()[:10]
        try:
            return date.fromisoformat(candidate).replace(day=1)
        except ValueError:
            return None
    return None


def _numeric_fields(columns: list[str], rows: list[dict[str, Any]], month_field: str) -> set[str]:
    numeric: set[str] = set()
    for column in columns:
        if column == month_field:
            continue
        values = [row.get(column) for row in rows if row.get(column) is not None]
        if any(isinstance(value, Number) and not isinstance(value, bool) for value in values):
            numeric.add(column)
        elif any(hint in column.lower() for hint in _NUMERIC_FIELD_HINTS):
            numeric.add(column)
    return numeric


def complete_year_months(
    *,
    question: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    today: date | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Fill missing month buckets with zero for explicit current/last-year trends.

    This changes presentation rows only. It never invents business transactions:
    existing result rows are preserved, and absent time buckets are marked as zero.
    """

    current_date = today or date.today()
    target = _target_year_and_month(question, current_date)
    month_field = _month_field(columns)
    if target is None or month_field is None:
        return rows, 0

    target_year, final_month = target
    by_month: dict[date, dict[str, Any]] = {}
    outside_target: list[dict[str, Any]] = []
    for row in rows:
        parsed = _as_month(row.get(month_field))
        if parsed is None or parsed.year != target_year:
            outside_target.append(row)
            continue
        by_month[parsed] = row

    numeric_fields = _numeric_fields(columns, rows, month_field)
    completed: list[dict[str, Any]] = []
    added = 0
    for month_number in range(1, final_month + 1):
        bucket = date(target_year, month_number, 1)
        if bucket in by_month:
            completed.append(by_month[bucket])
            continue
        added += 1
        row: dict[str, Any] = {column: None for column in columns}
        row[month_field] = bucket.isoformat()
        for column in numeric_fields:
            row[column] = 0
        completed.append(row)

    # A correctly generated year query should not return outside-year rows. Keep
    # them visible if it does so that post-processing never hides real results.
    return completed + outside_target, added
