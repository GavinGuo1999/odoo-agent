from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


DashboardPeriod = Literal["week", "month", "quarter"]


class CurrencyResponse(BaseModel):
    code: str
    symbol: str
    position: str


class DateRangeResponse(BaseModel):
    start: date
    end: date
    label: str
    comparison_label: str


class ComparisonValueResponse(BaseModel):
    current: float
    previous: float
    change_pct: float | None


class DashboardKpisResponse(BaseModel):
    sales_amount: ComparisonValueResponse
    order_count: ComparisonValueResponse
    average_order_value: ComparisonValueResponse
    active_customers: ComparisonValueResponse


class TrendPointResponse(BaseModel):
    period_start: date
    label: str
    sales_amount: float
    order_count: int


class OrderStatusResponse(BaseModel):
    state: str
    label: str
    order_count: int
    sales_amount: float
    percentage: float


class CustomerRankingResponse(BaseModel):
    customer_id: int
    customer: str
    order_count: int
    sales_amount: float
    percentage: float


class ProductRankingResponse(BaseModel):
    product_id: int
    product: str
    quantity: float
    sales_amount: float
    percentage: float


class AttentionItemResponse(BaseModel):
    key: str
    label: str
    order_count: int
    sales_amount: float
    question: str


class SalespersonResponse(BaseModel):
    user_id: int
    name: str


class RecentOrderResponse(BaseModel):
    order_name: str
    customer: str
    salesperson: str
    order_date: datetime
    state: str
    state_label: str
    amount_untaxed: float


class SalesDashboardResponse(BaseModel):
    generated_at: datetime
    period: DashboardPeriod
    selected_salesperson_id: int | None
    company_id: int
    company_name: str
    currency: CurrencyResponse
    date_range: DateRangeResponse
    kpis: DashboardKpisResponse
    trend: list[TrendPointResponse]
    monthly_trend: list[TrendPointResponse]
    order_statuses: list[OrderStatusResponse]
    customers: list[CustomerRankingResponse]
    products: list[ProductRankingResponse]
    attention: list[AttentionItemResponse]
    salespeople: list[SalespersonResponse]
    recent_orders: list[RecentOrderResponse]


class MetricDefinitionResponse(BaseModel):
    id: str
    name: str
    description: str
    expression: str
    states: list[str] = Field(default_factory=list)
    date_field: str


class SalesMetricsResponse(BaseModel):
    version: str
    metrics: list[MetricDefinitionResponse]
