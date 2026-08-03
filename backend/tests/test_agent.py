from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi import SalesAgent, classify_intent  # noqa: E402
from app.config import DatabaseConfig, ProviderConfig  # noqa: E402
from app.database import DatabaseHealth, QueryResult  # noqa: E402
from app.llm import LLMResult  # noqa: E402


def llm_result(content: str, total: int = 10) -> LLMResult:
    return LLMResult(
        content=content,
        provider="deepseek",
        model="deepseek-chat",
        input_tokens=total - 2,
        output_tokens=2,
        total_tokens=total,
    )


class SalesAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.provider = ProviderConfig(
            name="deepseek",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            timeout_seconds=30,
        )
        self.database = DatabaseConfig(
            host="127.0.0.1",
            port=55432,
            database="odoo19_dev",
            user="codex_readonly",
            password=None,
            company_id=1,
            statement_timeout_ms=15000,
            max_rows=500,
        )
        self.environment = {
            **os.environ,
            "LANGFUSE_ENABLED": "false",
            "LANGFUSE_TRACING_ENABLED": "false",
        }

    def test_intent_routes_general_semantic_and_data(self) -> None:
        self.assertEqual(classify_intent("你好，你是什么模型？", []), "general")
        self.assertEqual(classify_intent("销售额口径是什么？", []), "semantic")
        self.assertEqual(classify_intent("今年每个月销售趋势怎么样？", []), "data")

    async def test_general_question_does_not_touch_database(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database)
            agent._gateway.complete = AsyncMock(return_value=llm_result("我是当前配置的模型。"))
            agent._database.healthcheck = AsyncMock()
            result = await agent.run(question="你是什么模型？", history=[])

        self.assertEqual(result.intent, "general")
        self.assertFalse(result.data_accessed)
        agent._database.healthcheck.assert_not_awaited()

    async def test_data_question_executes_safe_sql_and_builds_chart(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database)
            agent._gateway.complete = AsyncMock(
                side_effect=[
                    llm_result(
                        '{"sql":"SELECT date_trunc(\'month\', so.date_order)::date AS month, '
                        'SUM(so.amount_untaxed) AS sales_amount FROM sale_order so '
                        "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
                        'GROUP BY 1 ORDER BY 1","metric_ids":["sales_amount"]}'
                    ),
                    llm_result("今年 6 月销售额 100，7 月销售额 200，呈上升趋势。", 12),
                ]
            )
            agent._database.healthcheck = AsyncMock(
                return_value=DatabaseHealth(
                    connected=True,
                    user="codex_readonly",
                    database="odoo19_dev",
                    read_only=True,
                    company_id=1,
                    company_name="My Company",
                    currency="USD",
                    order_count=23,
                    response_ms=2.0,
                )
            )
            agent._database.discover_columns = AsyncMock(
                return_value={
                    table: [{"name": column, "type": "text"} for column in columns]
                    for table, columns in agent._semantics.table_columns.items()
                }
            )
            agent._database.execute_readonly = AsyncMock(
                return_value=QueryResult(
                    columns=["month", "sales_amount"],
                    rows=[
                        {"month": "2026-06-01", "sales_amount": 100.0},
                        {"month": "2026-07-01", "sales_amount": 200.0},
                    ],
                    row_count=2,
                    truncated=False,
                    duration_ms=3.5,
                )
            )
            result = await agent.run(question="今年每个月销售趋势怎么样？", history=[])

        self.assertTrue(result.data_accessed)
        self.assertEqual(result.intent, "data")
        self.assertEqual(result.chart["type"], "line")
        self.assertIn("LIMIT 500", result.sql or "")
        self.assertEqual(result.total_tokens, 22)

    async def test_invalid_sql_is_repaired_before_execution(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database)
            agent._gateway.complete = AsyncMock(
                side_effect=[
                    llm_result('{"sql":"SELECT * FROM sale_order","metric_ids":[]}'),
                    llm_result(
                        '{"sql":"SELECT COUNT(so.id) AS order_count FROM sale_order so '
                        "WHERE so.company_id = 1 AND so.state IN ('sale','done')\","
                        '"metric_ids":["order_count"]}'
                    ),
                    llm_result("共有 23 张已确认销售订单。"),
                ]
            )
            agent._database.healthcheck = AsyncMock(
                return_value=DatabaseHealth(
                    connected=True,
                    user="codex_readonly",
                    database="odoo19_dev",
                    read_only=True,
                    company_id=1,
                    company_name="My Company",
                    currency="USD",
                    order_count=23,
                    response_ms=2.0,
                )
            )
            agent._database.discover_columns = AsyncMock(
                return_value={
                    table: [{"name": column, "type": "text"} for column in columns]
                    for table, columns in agent._semantics.table_columns.items()
                }
            )
            agent._database.execute_readonly = AsyncMock(
                return_value=QueryResult(
                    columns=["order_count"],
                    rows=[{"order_count": 23}],
                    row_count=1,
                    truncated=False,
                    duration_ms=1.2,
                )
            )
            result = await agent.run(question="订单数是多少？", history=[])

        self.assertTrue(result.data_accessed)
        self.assertEqual(agent._gateway.complete.await_count, 3)
        agent._database.execute_readonly.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
