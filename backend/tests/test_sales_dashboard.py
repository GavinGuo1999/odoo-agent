from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import DatabaseConfig, get_settings  # noqa: E402
from app.database import QueryResult  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.sales_dashboard import (  # noqa: E402
    SalesDashboardService,
    _change,
    _period_bounds,
)


def query_result(rows: list[dict[str, object]]) -> QueryResult:
    return QueryResult(
        columns=list(rows[0]) if rows else [],
        rows=rows,
        row_count=len(rows),
        truncated=False,
        duration_ms=1.0,
    )


class SalesDashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = DatabaseConfig(
            host="127.0.0.1",
            port=55432,
            database="odoo19_dev",
            user="codex_readonly",
            password=None,
            company_id=1,
            statement_timeout_ms=15000,
            max_rows=500,
        )

    def test_period_boundaries_and_change_calculation(self) -> None:
        self.assertEqual(
            _period_bounds("month", date(2026, 8, 6)),
            (date(2026, 8, 1), date(2026, 9, 1), date(2026, 7, 1)),
        )
        self.assertEqual(
            _period_bounds("quarter", date(2026, 8, 6)),
            (date(2026, 7, 1), date(2026, 10, 1), date(2026, 4, 1)),
        )
        self.assertEqual(_change(150, 100), 50.0)
        self.assertIsNone(_change(100, 0))

    async def test_build_uses_real_result_shapes_without_fallback_numbers(self) -> None:
        service = SalesDashboardService(self.config)
        service._database.execute_readonly = AsyncMock(
            return_value=query_result(
                [{"company_name": "My Company", "currency_code": "USD", "symbol": "$", "position": "before"}]
            )
        )
        service._metrics = AsyncMock(
            side_effect=[
                query_result([{"sales_amount": 200.0, "order_count": 2, "average_order_value": 100.0, "active_customers": 2}]),
                query_result([{"sales_amount": 100.0, "order_count": 1, "average_order_value": 100.0, "active_customers": 1}]),
            ]
        )
        service._trend = AsyncMock(return_value=[])
        service._monthly_trend = AsyncMock(return_value=[])
        service._statuses = AsyncMock(return_value=[])
        service._customers = AsyncMock(return_value=[])
        service._products = AsyncMock(return_value=[])
        service._attention = AsyncMock(return_value=[])
        service._salespeople = AsyncMock(return_value=[])
        service._recent_orders = AsyncMock(return_value=[])

        result = await service.build(period="month", salesperson_id=None)

        self.assertEqual(result.company_name, "My Company")
        self.assertEqual(result.currency.symbol, "$")
        self.assertEqual(result.kpis.sales_amount.current, 200.0)
        self.assertEqual(result.kpis.sales_amount.change_pct, 100.0)
        self.assertEqual(result.products, [])

    async def test_metrics_endpoint_reads_semantic_layer(self) -> None:
        environment = {
            **os.environ,
            "LANGFUSE_ENABLED": "false",
            "LANGFUSE_TRACING_ENABLED": "false",
        }
        with patch.dict(os.environ, environment, clear=True):
            get_settings.cache_clear()
            async with AsyncClient(
                transport=ASGITransport(app=create_app()),
                base_url="http://test",
            ) as client:
                response = await client.get("/api/sales/metrics")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(len(payload["metrics"]), 7)
        self.assertIn("sales_amount", {item["id"] for item in payload["metrics"]})


if __name__ == "__main__":
    unittest.main()
