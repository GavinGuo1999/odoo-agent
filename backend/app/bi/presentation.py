from __future__ import annotations

from collections.abc import Iterable


_COLUMN_LABELS = {
    "active_customers": "活跃客户数",
    "amount": "金额",
    "amount_total": "含税金额",
    "amount_untaxed": "未税金额",
    "average_order_value": "平均订单额",
    "avg_order_value": "平均订单额",
    "category": "产品类别",
    "customer": "客户",
    "customer_count": "客户数",
    "date": "日期",
    "day": "日期",
    "delivered_quantity": "已交付数量",
    "invoice_count": "发票数",
    "month": "月份",
    "order_count": "订单数",
    "order_date": "订单日期",
    "order_name": "订单",
    "percentage": "占比",
    "product": "产品",
    "quantity": "销售数量",
    "quarter": "季度",
    "sales_amount": "销售额",
    "salesperson": "销售员",
    "team": "销售团队",
    "week": "周",
    "year": "年份",
}

_CURRENCY_HINTS = ("amount", "price", "revenue", "subtotal")
_INTEGER_HINTS = ("count", "quantity", "qty")
_PERCENT_HINTS = ("percent", "percentage", "ratio", "rate")
_DATE_HINTS = ("date", "day", "week", "month", "quarter", "year")


def display_label(field: str) -> str:
    """Return a stable Chinese presentation label for a SQL result field."""

    normalized = field.strip().lower()
    if normalized in _COLUMN_LABELS:
        return _COLUMN_LABELS[normalized]
    return field.replace("_", " ").strip() or field


def display_format(field: str) -> str:
    """Describe how the browser should format a SQL result field."""

    normalized = field.strip().lower()
    if any(hint in normalized for hint in _CURRENCY_HINTS):
        return "currency"
    if any(hint in normalized for hint in _PERCENT_HINTS):
        return "percent"
    if any(hint in normalized for hint in _INTEGER_HINTS):
        return "integer"
    if normalized in _DATE_HINTS or normalized.endswith("_date"):
        return "date"
    return "auto"


def presentation_metadata(
    columns: Iterable[str],
    metric_ids: Iterable[str],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    column_list = list(columns)
    metric_list = list(metric_ids)
    return (
        {column: display_label(column) for column in column_list},
        {column: display_format(column) for column in column_list},
        {metric: display_label(metric) for metric in metric_list},
    )
