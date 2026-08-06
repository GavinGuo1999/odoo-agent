from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import DatabaseConfig
from app.database import OdooDatabase, QueryResult
from app.schemas.sales import (
    AttentionItemResponse,
    ComparisonValueResponse,
    CurrencyResponse,
    CustomerRankingResponse,
    DashboardKpisResponse,
    DashboardPeriod,
    DateRangeResponse,
    OrderStatusResponse,
    ProductRankingResponse,
    RecentOrderResponse,
    SalesDashboardResponse,
    SalespersonResponse,
    TrendPointResponse,
)


_TIMEZONE = ZoneInfo("Asia/Shanghai")
_STATE_LABELS = {
    "draft": "报价草稿",
    "sent": "报价已发送",
    "sale": "销售订单",
    "done": "已锁定",
    "cancel": "已取消",
}
_PERIOD_LABELS: dict[DashboardPeriod, tuple[str, str]] = {
    "week": ("本周", "较上周"),
    "month": ("本月", "较上月"),
    "quarter": ("本季度", "较上季度"),
}


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _period_bounds(period: DashboardPeriod, today: date) -> tuple[date, date, date]:
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
        previous_start = start - timedelta(days=7)
    elif period == "month":
        start = today.replace(day=1)
        end = _add_months(start, 1)
        previous_start = _add_months(start, -1)
    else:
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start = date(today.year, quarter_month, 1)
        end = _add_months(start, 3)
        previous_start = _add_months(start, -3)
    return start, end, previous_start


def _utc_boundary(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=_TIMEZONE).astimezone(UTC).replace(tzinfo=None)


def _float(value: object) -> float:
    return float(value or 0)


def _int(value: object) -> int:
    return int(value or 0)


def _change(current: float, previous: float) -> float | None:
    if previous == 0:
        return 0.0 if current == 0 else None
    return round((current - previous) / previous * 100, 2)


def _comparison(current: float, previous: float) -> ComparisonValueResponse:
    return ComparisonValueResponse(
        current=round(current, 2),
        previous=round(previous, 2),
        change_pct=_change(current, previous),
    )


def _first(result: QueryResult) -> dict[str, object]:
    return result.rows[0] if result.rows else {}


class SalesDashboardService:
    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._database = OdooDatabase(config)

    async def build(
        self,
        *,
        period: DashboardPeriod,
        salesperson_id: int | None,
    ) -> SalesDashboardResponse:
        now = datetime.now(_TIMEZONE)
        start, end, previous_start = _period_bounds(period, now.date())
        start_utc = _utc_boundary(start)
        end_utc = _utc_boundary(end)
        previous_start_utc = _utc_boundary(previous_start)

        company_task = self._database.execute_readonly(
            """
            SELECT company.name AS company_name,
                   currency.name AS currency_code,
                   currency.symbol,
                   currency.position
            FROM res_company AS company
            JOIN res_currency AS currency ON currency.id = company.currency_id
            WHERE company.id = %s
            """,
            (self._config.company_id,),
        )
        current_task = self._metrics(start_utc, end_utc, salesperson_id)
        previous_task = self._metrics(previous_start_utc, start_utc, salesperson_id)
        trend_task = self._trend(period, start, end, salesperson_id)
        monthly_task = self._monthly_trend(now.date(), salesperson_id)
        statuses_task = self._statuses(start_utc, end_utc, salesperson_id)
        customers_task = self._customers(start_utc, end_utc, salesperson_id)
        products_task = self._products(start_utc, end_utc, salesperson_id)
        attention_task = self._attention(now, salesperson_id)
        salespeople_task = self._salespeople()
        recent_task = self._recent_orders(salesperson_id)

        (
            company_result,
            current_result,
            previous_result,
            trend,
            monthly_trend,
            statuses,
            customers,
            products,
            attention,
            salespeople,
            recent_orders,
        ) = await asyncio.gather(
            company_task,
            current_task,
            previous_task,
            trend_task,
            monthly_task,
            statuses_task,
            customers_task,
            products_task,
            attention_task,
            salespeople_task,
            recent_task,
        )

        company = _first(company_result)
        current = _first(current_result)
        previous = _first(previous_result)
        period_label, comparison_label = _PERIOD_LABELS[period]

        return SalesDashboardResponse(
            generated_at=now,
            period=period,
            selected_salesperson_id=salesperson_id,
            company_id=self._config.company_id,
            company_name=str(company.get("company_name") or f"公司 {self._config.company_id}"),
            currency=CurrencyResponse(
                code=str(company.get("currency_code") or ""),
                symbol=str(company.get("symbol") or company.get("currency_code") or ""),
                position=str(company.get("position") or "before"),
            ),
            date_range=DateRangeResponse(
                start=start,
                end=end - timedelta(days=1),
                label=period_label,
                comparison_label=comparison_label,
            ),
            kpis=DashboardKpisResponse(
                sales_amount=_comparison(
                    _float(current.get("sales_amount")),
                    _float(previous.get("sales_amount")),
                ),
                order_count=_comparison(
                    _float(current.get("order_count")),
                    _float(previous.get("order_count")),
                ),
                average_order_value=_comparison(
                    _float(current.get("average_order_value")),
                    _float(previous.get("average_order_value")),
                ),
                active_customers=_comparison(
                    _float(current.get("active_customers")),
                    _float(previous.get("active_customers")),
                ),
            ),
            trend=trend,
            monthly_trend=monthly_trend,
            order_statuses=statuses,
            customers=customers,
            products=products,
            attention=attention,
            salespeople=salespeople,
            recent_orders=recent_orders,
        )

    async def _metrics(
        self,
        start: datetime,
        end: datetime,
        salesperson_id: int | None,
    ) -> QueryResult:
        return await self._database.execute_readonly(
            """
            SELECT COALESCE(SUM(so.amount_untaxed), 0) AS sales_amount,
                   COUNT(DISTINCT so.id) AS order_count,
                   COALESCE(AVG(so.amount_untaxed), 0) AS average_order_value,
                   COUNT(DISTINCT so.partner_id) AS active_customers
            FROM sale_order AS so
            WHERE so.company_id = %s
              AND so.state IN ('sale', 'done')
              AND so.date_order >= %s AND so.date_order < %s
              AND (%s::integer IS NULL OR so.user_id = %s)
            """,
            (self._config.company_id, start, end, salesperson_id, salesperson_id),
        )

    async def _trend(
        self,
        period: DashboardPeriod,
        start: date,
        end: date,
        salesperson_id: int | None,
    ) -> list[TrendPointResponse]:
        unit = "month" if period == "quarter" else "day"
        result = await self._database.execute_readonly(
            f"""
            SELECT date_trunc('{unit}',
                       so.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai'
                   )::date AS bucket,
                   COALESCE(SUM(so.amount_untaxed), 0) AS sales_amount,
                   COUNT(DISTINCT so.id) AS order_count
            FROM sale_order AS so
            WHERE so.company_id = %s
              AND so.state IN ('sale', 'done')
              AND so.date_order >= %s AND so.date_order < %s
              AND (%s::integer IS NULL OR so.user_id = %s)
            GROUP BY 1 ORDER BY 1
            """,
            (
                self._config.company_id,
                _utc_boundary(start),
                _utc_boundary(end),
                salesperson_id,
                salesperson_id,
            ),
        )
        by_date = {date.fromisoformat(str(row["bucket"])): row for row in result.rows}
        points: list[TrendPointResponse] = []
        cursor = start
        while cursor < end:
            row = by_date.get(cursor, {})
            points.append(
                TrendPointResponse(
                    period_start=cursor,
                    label=cursor.strftime("%Y-%m") if unit == "month" else cursor.strftime("%m-%d"),
                    sales_amount=_float(row.get("sales_amount")),
                    order_count=_int(row.get("order_count")),
                )
            )
            cursor = _add_months(cursor, 1) if unit == "month" else cursor + timedelta(days=1)
        return points

    async def _monthly_trend(
        self,
        today: date,
        salesperson_id: int | None,
    ) -> list[TrendPointResponse]:
        end = _add_months(today.replace(day=1), 1)
        start = _add_months(end, -6)
        result = await self._database.execute_readonly(
            """
            SELECT date_trunc('month',
                       so.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai'
                   )::date AS bucket,
                   COALESCE(SUM(so.amount_untaxed), 0) AS sales_amount,
                   COUNT(DISTINCT so.id) AS order_count
            FROM sale_order AS so
            WHERE so.company_id = %s
              AND so.state IN ('sale', 'done')
              AND so.date_order >= %s AND so.date_order < %s
              AND (%s::integer IS NULL OR so.user_id = %s)
            GROUP BY 1 ORDER BY 1
            """,
            (
                self._config.company_id,
                _utc_boundary(start),
                _utc_boundary(end),
                salesperson_id,
                salesperson_id,
            ),
        )
        by_date = {date.fromisoformat(str(row["bucket"])): row for row in result.rows}
        points = []
        cursor = start
        while cursor < end:
            row = by_date.get(cursor, {})
            points.append(
                TrendPointResponse(
                    period_start=cursor,
                    label=cursor.strftime("%Y-%m"),
                    sales_amount=_float(row.get("sales_amount")),
                    order_count=_int(row.get("order_count")),
                )
            )
            cursor = _add_months(cursor, 1)
        return points

    async def _statuses(
        self,
        start: datetime,
        end: datetime,
        salesperson_id: int | None,
    ) -> list[OrderStatusResponse]:
        result = await self._database.execute_readonly(
            """
            SELECT so.state, COUNT(*) AS order_count,
                   COALESCE(SUM(so.amount_untaxed), 0) AS sales_amount
            FROM sale_order AS so
            WHERE so.company_id = %s
              AND so.date_order >= %s AND so.date_order < %s
              AND (%s::integer IS NULL OR so.user_id = %s)
            GROUP BY so.state ORDER BY sales_amount DESC
            """,
            (self._config.company_id, start, end, salesperson_id, salesperson_id),
        )
        total = sum(_float(row.get("sales_amount")) for row in result.rows)
        return [
            OrderStatusResponse(
                state=str(row["state"]),
                label=_STATE_LABELS.get(str(row["state"]), str(row["state"])),
                order_count=_int(row.get("order_count")),
                sales_amount=_float(row.get("sales_amount")),
                percentage=round(_float(row.get("sales_amount")) / total * 100, 2) if total else 0,
            )
            for row in result.rows
        ]

    async def _customers(
        self,
        start: datetime,
        end: datetime,
        salesperson_id: int | None,
    ) -> list[CustomerRankingResponse]:
        result = await self._database.execute_readonly(
            """
            SELECT rp.id AS customer_id, rp.name AS customer,
                   COUNT(DISTINCT so.id) AS order_count,
                   COALESCE(SUM(so.amount_untaxed), 0) AS sales_amount
            FROM sale_order AS so
            JOIN res_partner AS rp ON rp.id = so.partner_id
            WHERE so.company_id = %s
              AND so.state IN ('sale', 'done')
              AND so.date_order >= %s AND so.date_order < %s
              AND (%s::integer IS NULL OR so.user_id = %s)
            GROUP BY rp.id, rp.name ORDER BY sales_amount DESC LIMIT 10
            """,
            (self._config.company_id, start, end, salesperson_id, salesperson_id),
        )
        total = sum(_float(row.get("sales_amount")) for row in result.rows)
        return [
            CustomerRankingResponse(
                customer_id=_int(row.get("customer_id")),
                customer=str(row.get("customer") or "未命名客户"),
                order_count=_int(row.get("order_count")),
                sales_amount=_float(row.get("sales_amount")),
                percentage=round(_float(row.get("sales_amount")) / total * 100, 2) if total else 0,
            )
            for row in result.rows
        ]

    async def _products(
        self,
        start: datetime,
        end: datetime,
        salesperson_id: int | None,
    ) -> list[ProductRankingResponse]:
        result = await self._database.execute_readonly(
            """
            SELECT pp.id AS product_id,
                   COALESCE(pt.name->>'zh_CN', pt.name->>'en_US', pp.default_code,
                            '#' || pp.id::text) AS product,
                   COALESCE(SUM(sol.product_uom_qty), 0) AS quantity,
                   COALESCE(SUM(sol.price_subtotal), 0) AS sales_amount
            FROM sale_order_line AS sol
            JOIN sale_order AS so ON so.id = sol.order_id
            JOIN product_product AS pp ON pp.id = sol.product_id
            JOIN product_template AS pt ON pt.id = pp.product_tmpl_id
            WHERE so.company_id = %s
              AND so.state IN ('sale', 'done')
              AND so.date_order >= %s AND so.date_order < %s
              AND sol.display_type IS NULL
              AND (%s::integer IS NULL OR so.user_id = %s)
            GROUP BY pp.id, pt.name, pp.default_code
            ORDER BY sales_amount DESC LIMIT 10
            """,
            (self._config.company_id, start, end, salesperson_id, salesperson_id),
        )
        total = sum(_float(row.get("sales_amount")) for row in result.rows)
        return [
            ProductRankingResponse(
                product_id=_int(row.get("product_id")),
                product=str(row.get("product") or "未命名产品"),
                quantity=_float(row.get("quantity")),
                sales_amount=_float(row.get("sales_amount")),
                percentage=round(_float(row.get("sales_amount")) / total * 100, 2) if total else 0,
            )
            for row in result.rows
        ]

    async def _attention(
        self,
        now: datetime,
        salesperson_id: int | None,
    ) -> list[AttentionItemResponse]:
        result = await self._database.execute_readonly(
            """
            SELECT key, order_count, sales_amount
            FROM (
                SELECT 'pending_delivery' AS key, COUNT(*) AS order_count,
                       COALESCE(SUM(so.amount_untaxed), 0) AS sales_amount
                FROM sale_order AS so
                WHERE so.company_id = %s AND so.state IN ('sale', 'done')
                  AND COALESCE(so.delivery_status, 'pending') <> 'full'
                  AND (%s::integer IS NULL OR so.user_id = %s)
                UNION ALL
                SELECT 'to_invoice', COUNT(*), COALESCE(SUM(so.amount_untaxed), 0)
                FROM sale_order AS so
                WHERE so.company_id = %s AND so.state IN ('sale', 'done')
                  AND so.invoice_status = 'to invoice'
                  AND (%s::integer IS NULL OR so.user_id = %s)
                UNION ALL
                SELECT 'stale_quotation', COUNT(*), COALESCE(SUM(so.amount_untaxed), 0)
                FROM sale_order AS so
                WHERE so.company_id = %s AND so.state IN ('draft', 'sent')
                  AND so.date_order < %s
                  AND (%s::integer IS NULL OR so.user_id = %s)
            ) AS attention
            """,
            (
                self._config.company_id, salesperson_id, salesperson_id,
                self._config.company_id, salesperson_id, salesperson_id,
                self._config.company_id,
                now.astimezone(UTC).replace(tzinfo=None) - timedelta(days=7),
                salesperson_id,
                salesperson_id,
            ),
        )
        definitions = {
            "pending_delivery": (
                "待完成交付",
                "哪些订单已经确认但还没有完全交付？",
            ),
            "to_invoice": ("待开票订单", "哪些已确认订单还需要开票？"),
            "stale_quotation": ("长期未确认报价", "哪些报价单超过七天没有确认？"),
        }
        return [
            AttentionItemResponse(
                key=str(row["key"]),
                label=definitions[str(row["key"])][0],
                order_count=_int(row.get("order_count")),
                sales_amount=_float(row.get("sales_amount")),
                question=definitions[str(row["key"])][1],
            )
            for row in result.rows
        ]

    async def _salespeople(self) -> list[SalespersonResponse]:
        result = await self._database.execute_readonly(
            """
            SELECT ru.id AS user_id, rp.name
            FROM res_users AS ru
            JOIN res_partner AS rp ON rp.id = ru.partner_id
            WHERE EXISTS (
                SELECT 1 FROM sale_order AS so
                WHERE so.company_id = %s AND so.user_id = ru.id
            )
            ORDER BY rp.name
            """,
            (self._config.company_id,),
        )
        return [
            SalespersonResponse(user_id=_int(row.get("user_id")), name=str(row.get("name") or "未命名"))
            for row in result.rows
        ]

    async def _recent_orders(
        self,
        salesperson_id: int | None,
    ) -> list[RecentOrderResponse]:
        result = await self._database.execute_readonly(
            """
            SELECT so.name AS order_name, customer.name AS customer,
                   salesperson.name AS salesperson, so.date_order AS order_date,
                   so.state, so.amount_untaxed
            FROM sale_order AS so
            JOIN res_partner AS customer ON customer.id = so.partner_id
            LEFT JOIN res_users AS ru ON ru.id = so.user_id
            LEFT JOIN res_partner AS salesperson ON salesperson.id = ru.partner_id
            WHERE so.company_id = %s
              AND (%s::integer IS NULL OR so.user_id = %s)
            ORDER BY so.date_order DESC, so.id DESC LIMIT 8
            """,
            (self._config.company_id, salesperson_id, salesperson_id),
        )
        return [
            RecentOrderResponse(
                order_name=str(row.get("order_name") or ""),
                customer=str(row.get("customer") or "未命名客户"),
                salesperson=str(row.get("salesperson") or "未分配"),
                order_date=str(row.get("order_date")),
                state=str(row.get("state") or ""),
                state_label=_STATE_LABELS.get(str(row.get("state") or ""), str(row.get("state") or "")),
                amount_untaxed=_float(row.get("amount_untaxed")),
            )
            for row in result.rows
        ]
