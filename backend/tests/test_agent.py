from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver

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


def sql_payload(
    sql: str,
    *,
    query_type: str,
    metrics: list[str],
    dimensions: list[str] | None = None,
    requires_clarification: bool = False,
    clarification_question: str | None = None,
    filters: list[dict[str, object]] | None = None,
    select_columns: list[str] | None = None,
    row_limit: int | None = None,
) -> str:
    import json

    return json.dumps(
        {
            "plan": {
                "query_type": query_type,
                "metric_ids": metrics,
                "dimensions": dimensions or [],
                "filters": filters
                or [
                    {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                    {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                ],
                "time_range": {
                    "label": None,
                    "start": None,
                    "end": None,
                    "grain": "month" if query_type == "trend" else "none",
                },
                "result_shape": {
                    "kpi": "scalar",
                    "trend": "time_series",
                    "ranking": "ranking",
                }.get(query_type, "table"),
                "select_columns": select_columns or [*(dimensions or []), *metrics],
                "sort": (
                    [{"field": (dimensions or ["month"])[0], "direction": "asc"}]
                    if query_type == "trend"
                    else []
                ),
                "row_limit": row_limit,
                "assumptions": [],
                "ambiguities": ["客户名称不明确"] if requires_clarification else [],
                "requires_clarification": requires_clarification,
                "clarification_question": clarification_question,
            },
            "sql": sql,
        },
        ensure_ascii=False,
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
                return_value=llm_result(
                    sql_payload(
                        "SELECT date_trunc('month', so.date_order)::date AS month, "
                        "SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
                        "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
                        "GROUP BY 1 ORDER BY 1",
                        query_type="trend",
                        metrics=["sales_amount"],
                        dimensions=["month"],
                    )
                )
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
        self.assertEqual(result.total_tokens, 10)
        self.assertEqual(result.answer_mode, "deterministic")
        self.assertEqual(agent._gateway.complete.await_count, 1)

    async def test_invalid_sql_is_repaired_before_execution(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database)
            agent._gateway.complete = AsyncMock(
                side_effect=[
                    llm_result(
                        sql_payload(
                            "SELECT * FROM sale_order",
                            query_type="kpi",
                            metrics=["order_count"],
                        )
                    ),
                    llm_result(
                        sql_payload(
                            "SELECT COUNT(so.id) AS order_count FROM sale_order so "
                            "WHERE so.company_id = 1 AND so.state IN ('sale','done')",
                            query_type="kpi",
                            metrics=["order_count"],
                        )
                    ),
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
        self.assertEqual(agent._gateway.complete.await_count, 2)
        agent._database.execute_readonly.assert_awaited_once()

    async def test_query_plan_interrupt_can_resume_with_same_thread(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(
                self.provider,
                self.database,
                checkpointer=InMemorySaver(),
            )
            agent._gateway.complete = AsyncMock(
                side_effect=[
                    llm_result(
                        sql_payload(
                            "",
                            query_type="kpi",
                            metrics=["sales_amount"],
                            requires_clarification=True,
                            clarification_question="你指的是哪一个客户？",
                        )
                    ),
                    llm_result(
                        sql_payload(
                            "SELECT SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
                            "WHERE so.company_id = 1 AND so.state IN ('sale','done')",
                            query_type="kpi",
                            metrics=["sales_amount"],
                        )
                    ),
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
                    columns=["sales_amount"],
                    rows=[{"sales_amount": 8768.0}],
                    row_count=1,
                    truncated=False,
                    duration_ms=1.2,
                )
            )

            interrupted = await agent.run(
                question="那个客户今年的销售额是多少？",
                history=[],
                session_id="interrupt-test",
            )
            resumed = await agent.resume(
                session_id="interrupt-test",
                answer="CODEX Website Customer 20260627",
            )

        self.assertTrue(interrupted.interrupted)
        self.assertEqual(interrupted.interrupt_payload["question"], "你指的是哪一个客户？")
        self.assertFalse(resumed.interrupted)
        self.assertTrue(resumed.data_accessed)
        self.assertEqual(resumed.answer_mode, "deterministic")

    async def test_stream_emits_progress_and_final_outcome(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(
                self.provider,
                self.database,
                checkpointer=InMemorySaver(),
            )
            agent._gateway.complete = AsyncMock(return_value=llm_result("你好。"))
            events = [
                event
                async for event in agent.stream(
                    question="你好",
                    history=[],
                    session_id="stream-test",
                )
            ]

        self.assertTrue(any(event.get("type") == "progress" for event in events))
        self.assertEqual(events[-1]["type"], "outcome")
        self.assertEqual(events[-1]["outcome"].answer, "你好。")


if __name__ == "__main__":
    unittest.main()
