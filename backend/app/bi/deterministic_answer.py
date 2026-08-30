from __future__ import annotations

from numbers import Real
from typing import Any

from app.schemas.query_plan import QueryPlan


_COLUMN_LABELS = {
    "sales_amount": "销售额",
    "sales_amount_taxed": "含税销售额",
    "order_count": "订单数",
    "average_order_amount": "平均订单额",
    "average_order_value": "平均订单额",
    "sales_quantity": "销售数量",
    "delivered_quantity": "交付数量",
    "invoiced_quantity": "开票数量",
    "customer": "客户",
    "product": "产品",
    "salesperson": "销售员",
    "month": "月份",
    "day": "日期",
    "year": "年份",
}
_CURRENCY_FIELDS = {
    "sales_amount",
    "sales_amount_taxed",
    "average_order_amount",
    "average_order_value",
}


def _numeric_columns(columns: list[str], rows: list[dict[str, Any]]) -> list[str]:
    return [
        column
        for column in columns
        if any(
            isinstance(row.get(column), Real) and not isinstance(row.get(column), bool)
            for row in rows
        )
    ]


def can_answer_deterministically(
    plan: QueryPlan | None,
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    truncated: bool,
) -> bool:
    if plan is None or truncated or len(rows) > 24:
        return False
    if not rows:
        return True
    numeric = _numeric_columns(columns, rows)
    dimensions = [column for column in columns if column not in numeric]
    if plan.query_type == "kpi":
        return len(rows) == 1 and bool(numeric) and len(numeric) <= 4
    if plan.query_type in {"ranking", "trend"}:
        return len(numeric) == 1 and len(dimensions) >= 1
    return False


def _label(field: str) -> str:
    return _COLUMN_LABELS.get(field, field.replace("_", " "))


def _format_value(value: Any, field: str, currency: str | None) -> str:
    if isinstance(value, Real) and not isinstance(value, bool):
        digits = 0 if float(value).is_integer() else 2
        text = f"{float(value):,.{digits}f}"
        if field in _CURRENCY_FIELDS and currency:
            return f"{text} {currency}"
        return text
    return str(value)


def build_deterministic_answer(
    *,
    plan: QueryPlan,
    columns: list[str],
    rows: list[dict[str, Any]],
    currency: str | None,
) -> str:
    if not rows:
        return "按当前筛选条件没有查到符合口径的销售数据。"

    numeric = _numeric_columns(columns, rows)
    dimensions = [column for column in columns if column not in numeric]
    if plan.query_type == "kpi":
        parts = [
            f"{_label(field)}为 {_format_value(rows[0].get(field), field, currency)}"
            for field in numeric
        ]
        return "查询结果：" + "，".join(parts) + "。"

    dimension = dimensions[0]
    metric = numeric[0]
    if plan.query_type == "ranking":
        top = rows[:3]
        details = "；".join(
            f"{index + 1}. {_format_value(row.get(dimension), dimension, currency)}："
            f"{_format_value(row.get(metric), metric, currency)}"
            for index, row in enumerate(top)
        )
        return f"按{_label(metric)}排序，前 {len(top)} 名为：{details}。"

    first, last = rows[0], rows[-1]
    first_value = first.get(metric)
    last_value = last.get(metric)
    direction = "持平"
    change = ""
    if isinstance(first_value, Real) and isinstance(last_value, Real):
        if last_value > first_value:
            direction = "上升"
        elif last_value < first_value:
            direction = "下降"
        if first_value:
            percent = (float(last_value) - float(first_value)) / abs(float(first_value)) * 100
            change = f"，变动 {percent:+.1f}%"
    return (
        f"{_label(metric)}从 {_format_value(first_value, metric, currency)}"
        f"变为 {_format_value(last_value, metric, currency)}，整体{direction}{change}。"
    )
