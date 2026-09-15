from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi import SalesAgent, classify_intent  # noqa: E402
from app.bi.agent import normalize_query_text  # noqa: E402
from app.config import DatabaseConfig, ProviderConfig  # noqa: E402
from app.database import DatabaseConnectionError, DatabaseHealth, QueryResult  # noqa: E402
from app.llm import LLMResult  # noqa: E402
from app.schemas.query_plan import QueryPlan  # noqa: E402
from app.services.wiki_knowledge import WikiHit, WikiSearchResult  # noqa: E402


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
    time_range: dict[str, object] | None = None,
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
                "time_range": time_range or {
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


def chart_payload(
    *,
    chart_type: str,
    x_field: str | None,
    series: list[str],
    title: str = "查询结果",
) -> str:
    import json

    return json.dumps(
        {
            "type": chart_type,
            "title": title,
            "x_field": x_field,
            "series": [{"field": field, "label": None} for field in series],
            "sort_by": x_field if chart_type == "line" else None,
            "sort_order": "asc" if chart_type == "line" else None,
            "top_n": None,
            "reason": "根据结果字段类型选择",
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
        self.assertEqual(classify_intent("销售额是什么？", []), "semantic")
        self.assertEqual(classify_intent("今年每个月销售趋势怎么样？", []), "data")
        self.assertEqual(classify_intent("本月销售额最高的五个产品是什么？", []), "data")
        self.assertEqual(classify_intent("sale.order 有哪些字段？", []), "knowledge")
        self.assertEqual(classify_intent("qty_to_invoice 怎么计算？", []), "knowledge")
        self.assertEqual(
            classify_intent("查看 sale.order._action_confirm 的源码", []),
            "source",
        )
        self.assertEqual(
            classify_intent("本月哪些订单已交付但不能开票，为什么？", []),
            "hybrid",
        )

    @staticmethod
    def wiki_result(question: str) -> WikiSearchResult:
        hit = WikiHit(
            note_id="note-1",
            title="Sale 源码主链路",
            heading="qty_to_invoice 的计算",
            excerpt="qty_to_invoice 由订单行的开票策略、交付数量与已开票数量共同决定。",
            content="qty_to_invoice 由订单行的开票策略、交付数量与已开票数量共同决定。",
            relative_path="01_Odoo/03_源码/Sale 源码主链路.md",
            absolute_path="D:/odoo19e/learn_odoo/01_Odoo/03_源码/Sale 源码主链路.md",
            obsidian_uri="obsidian://open?vault=learn_odoo&file=01_Odoo/03_源码/Sale%20源码主链路",
            status="reviewed",
            note_type="source",
            module="sale",
            topic="invoice",
            updated="2026-08-20",
            score=12.0,
        )
        return WikiSearchResult(query=question, index_fingerprint="wiki-v1", hits=[hit])

    async def test_knowledge_question_uses_wiki_without_database(self) -> None:
        wiki = Mock()
        wiki.search.return_value = self.wiki_result("qty_to_invoice 怎么计算？")
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database, wiki_service=wiki)
            agent._gateway.complete = AsyncMock(
                return_value=llm_result("它由开票策略、交付数量和已开票数量决定。[知识来源 1]")
            )
            agent._database.healthcheck = AsyncMock()
            result = await agent.run(question="qty_to_invoice 怎么计算？", history=[])

        self.assertEqual(result.intent, "knowledge")
        self.assertEqual(result.phase, "knowledge-base")
        self.assertEqual(result.answer_mode, "knowledge")
        self.assertEqual(len(result.citations), 1)
        self.assertFalse(result.data_accessed)
        agent._database.healthcheck.assert_not_awaited()
        prompt = agent._gateway.complete.await_args.kwargs["messages"][0]["content"]
        self.assertIn("[知识来源 1]", prompt)

    async def test_hybrid_question_keeps_wiki_out_of_sql_prompt(self) -> None:
        wiki = Mock()
        wiki.search.return_value = self.wiki_result("本月哪些订单已交付但不能开票，为什么？")
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database, wiki_service=wiki)
            agent._gateway.complete = AsyncMock(
                side_effect=[
                    llm_result(
                        sql_payload(
                            "SELECT so.name AS order_name, so.invoice_status FROM sale_order so "
                            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
                            "AND so.date_order >= DATE '2026-09-01' "
                            "AND so.date_order < DATE '2026-10-01'",
                            query_type="detail",
                            metrics=[],
                            dimensions=["order_name", "invoice_status"],
                            select_columns=["order_name", "invoice_status"],
                            filters=[
                                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                                {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                                {"field": "date_order", "operator": "gte", "value": "2026-09-01", "source": "user"},
                                {"field": "date_order", "operator": "lt", "value": "2026-10-01", "source": "user"},
                            ],
                            time_range={
                                "label": "本月",
                                "start": "2026-09-01",
                                "end": "2026-09-30",
                                "grain": "day",
                            },
                        )
                    ),
                    llm_result("数据事实：SO001 尚未开票。Wiki 业务解释：需继续核查订单行。[知识来源 1]"),
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
                    columns=["order_name", "invoice_status"],
                    rows=[{"order_name": "SO001", "invoice_status": "to invoice"}],
                    row_count=1,
                    truncated=False,
                    duration_ms=1.0,
                )
            )
            result = await agent.run(
                question="本月哪些订单已交付但不能开票，为什么？",
                history=[],
            )

        self.assertEqual(result.intent, "hybrid")
        self.assertTrue(result.data_accessed)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(agent._gateway.complete.await_count, 2)
        sql_prompt = agent._gateway.complete.await_args_list[0].kwargs["messages"][0]["content"]
        answer_prompt = agent._gateway.complete.await_args_list[1].kwargs["messages"][0]["content"]
        self.assertNotIn("qty_to_invoice 由订单行", sql_prompt)
        self.assertIn("qty_to_invoice 由订单行", answer_prompt)

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
                        sql_payload(
                            "SELECT date_trunc('month', so.date_order)::date AS month, "
                            "SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
                            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
                            "GROUP BY 1 ORDER BY 1",
                            query_type="trend",
                            metrics=["sales_amount"],
                            dimensions=["month"],
                        )
                    ),
                    llm_result(
                        chart_payload(
                            chart_type="line",
                            x_field="month",
                            series=["sales_amount"],
                            title="今年月度销售趋势",
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
        self.assertEqual(result.total_tokens, 20)
        self.assertEqual(result.answer_mode, "deterministic")
        self.assertEqual(agent._gateway.complete.await_count, 2)
        self.assertIn("chart-planner", result.model_roles)

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

    async def test_unsafe_sql_is_rejected_without_model_repair(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database)
            agent._gateway.complete = AsyncMock(
                return_value=llm_result(
                    sql_payload(
                        "DELETE FROM sale_order WHERE company_id = 1",
                        query_type="kpi",
                        metrics=["order_count"],
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
            agent._database.execute_readonly = AsyncMock()
            result = await agent.run(question="删除全部销售订单", history=[])

        self.assertEqual(result.answer_mode, "failure")
        self.assertEqual(agent._gateway.complete.await_count, 1)
        agent._database.execute_readonly.assert_not_awaited()

    async def test_execution_error_is_analyzed_repaired_and_retried(self) -> None:
        initial_sql = (
            "SELECT SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done')"
        )
        repaired_sql = (
            "SELECT COALESCE(SUM(so.amount_untaxed), 0) AS sales_amount FROM sale_order so "
            "WHERE so.company_id = 1 AND so.state IN ('sale','done')"
        )
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database)
            agent._gateway.complete = AsyncMock(
                side_effect=[
                    llm_result(
                        sql_payload(
                            initial_sql,
                            query_type="kpi",
                            metrics=["sales_amount"],
                        )
                    ),
                    llm_result(
                        sql_payload(
                            repaired_sql,
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
                side_effect=[
                    DatabaseConnectionError("UndefinedColumn"),
                    QueryResult(
                        columns=["sales_amount"],
                        rows=[{"sales_amount": 8768.0}],
                        row_count=1,
                        truncated=False,
                        duration_ms=1.2,
                    ),
                ]
            )
            result = await agent.run(question="销售额是多少？", history=[])

        self.assertTrue(result.data_accessed)
        self.assertEqual(result.answer_mode, "deterministic")
        self.assertEqual(agent._gateway.complete.await_count, 2)
        self.assertEqual(agent._database.execute_readonly.await_count, 2)

    async def test_repeated_repair_sql_stops_the_loop(self) -> None:
        repeated = sql_payload(
            "SELECT * FROM sale_order",
            query_type="kpi",
            metrics=["order_count"],
        )
        with patch.dict(os.environ, self.environment, clear=True):
            agent = SalesAgent(self.provider, self.database)
            agent._gateway.complete = AsyncMock(
                side_effect=[llm_result(repeated), llm_result(repeated)]
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
            agent._database.execute_readonly = AsyncMock()
            result = await agent.run(question="订单数是多少？", history=[])

        self.assertEqual(result.answer_mode, "failure")
        self.assertEqual(agent._gateway.complete.await_count, 2)
        self.assertTrue(any("重复查询" in warning for warning in result.warnings))
        agent._database.execute_readonly.assert_not_awaited()

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
                            "SELECT SUM(so.amount_untaxed) AS sales_amount FROM sale_order so "
                            "JOIN res_partner rp ON rp.id = so.partner_id "
                            "WHERE so.company_id = 1 AND so.state IN ('sale','done') "
                            "AND rp.name = 'CODEX Website Customer 20260627'",
                            query_type="kpi",
                            metrics=["sales_amount"],
                            filters=[
                                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                                {"field": "state", "operator": "in", "value": ["sale", "done"], "source": "metric_rule"},
                                {"field": "partner_id", "operator": "eq", "value": "CODEX Website Customer 20260627", "source": "user"},
                            ],
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
            agent._gateway.complete.assert_not_awaited()
            agent._database.healthcheck.assert_not_awaited()
            resumed = await agent.resume(
                session_id="interrupt-test",
                answer="CODEX Website Customer 20260627",
            )

        self.assertTrue(interrupted.interrupted)
        self.assertEqual(
            interrupted.interrupt_payload["question"],
            "请提供要查询的客户名称或 ID。",
        )
        self.assertFalse(resumed.interrupted)
        self.assertTrue(resumed.data_accessed)
        self.assertEqual(resumed.answer_mode, "deterministic")
        self.assertEqual(agent._gateway.complete.await_count, 1)

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


class UsageAttributionTests(unittest.TestCase):
    """docs/20 P1-1: generation_role 已经打在 Langfuse 上，本地也要能按 role 归因成本。"""

    def test_usage_fields_accumulate_tokens_and_cost_per_role(self) -> None:
        from app.bi.agent import _usage_fields

        state: dict[str, object] = {}
        sql_call = LLMResult(
            content="SELECT 1",
            provider="deepseek",
            model="deepseek-chat",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            total_cost_usd=0.006,
        )
        answer_call = LLMResult(
            content="结论",
            provider="deepseek",
            model="deepseek-chat",
            input_tokens=40,
            output_tokens=10,
            total_tokens=50,
            total_cost_usd=0.002,
        )

        state.update(_usage_fields(state, sql_call, role="sql"))
        state.update(_usage_fields(state, sql_call, role="sql"))
        state.update(_usage_fields(state, answer_call, role="answer"))

        role_usage = state["role_usage"]
        self.assertEqual(role_usage["sql"]["calls"], 2)
        self.assertEqual(role_usage["sql"]["total_tokens"], 240)
        self.assertEqual(role_usage["sql"]["input_tokens"], 200)
        self.assertAlmostEqual(role_usage["sql"]["estimated_cost_usd"], 0.012, places=6)
        self.assertEqual(role_usage["answer"]["calls"], 1)
        self.assertAlmostEqual(role_usage["answer"]["estimated_cost_usd"], 0.002, places=6)
        # 分角色之和必须等于总数，否则归因报表会对不上账。
        self.assertEqual(state["total_tokens"], 290)
        self.assertAlmostEqual(
            sum(entry["estimated_cost_usd"] for entry in role_usage.values()),
            state["estimated_cost_usd"],
            places=8,
        )


if __name__ == "__main__":
    unittest.main()


class ConversationArtifactTests(unittest.TestCase):
    """切到别的页面再回来，图表和明细表必须还在。

    实测缺陷：会话历史只存了 role/content，重绘时图表、明细表、SQL 全部丢失。
    产物必须与 conversation 等长对齐，且**不能**混进发给模型的消息里。
    """

    def _data_state(self, answer: str = "结论") -> dict:
        return {
            "answer": answer,
            "question": "按月统计销售额",
            "display_question": "按月统计销售额",
            "intent": "data",
            "answer_mode": "deterministic",
            "data_accessed": True,
            "sql": "SELECT 1",
            "columns": ["month", "sales_amount"],
            "rows": [{"month": "2026-01-01", "sales_amount": 1.0}],
            "chart": {"type": "line", "title": "趋势"},
            "metric_ids": ["sales_amount"],
            "currency": "USD",
            "query_ms": 12.5,
        }

    def test_data_turn_keeps_chart_and_rows(self) -> None:
        from app.bi.agent import _turn_artifact

        artifact = _turn_artifact(self._data_state())

        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["chart"]["type"], "line")
        self.assertEqual(artifact["columns"], ["month", "sales_amount"])
        self.assertEqual(artifact["row_count"], 1)
        self.assertEqual(artifact["sql"], "SELECT 1")

    def test_plain_answer_stores_nothing(self) -> None:
        from app.bi.agent import _turn_artifact

        # 普通问答没有任何可视化产物，不该在检查点里留垃圾。
        self.assertIsNone(_turn_artifact({"answer": "你好", "intent": "general"}))

    def test_chart_type_none_is_not_an_artifact(self) -> None:
        from app.bi.agent import _turn_artifact

        # 规划器对闲聊返回 {"type": "none"}，那是"明确不画图"，不是产物；
        # 当成产物会让每条闲聊后面都挂一张空的结果卡片。
        self.assertIsNone(
            _turn_artifact({"answer": "你好", "intent": "general", "chart": {"type": "none"}})
        )

    def test_artifacts_stay_aligned_with_conversation(self) -> None:
        agent = SalesAgent.__new__(SalesAgent)
        state = self._data_state()

        first = SalesAgent._finalize_turn(agent, state)
        self.assertEqual(len(first["conversation"]), len(first["conversation_artifacts"]))
        self.assertIsNone(first["conversation_artifacts"][0])          # 用户提问
        self.assertIsNotNone(first["conversation_artifacts"][1])       # 助手回答带图

        second_state = {
            **self._data_state("第二轮"),
            "question": "那上个月呢",
            "display_question": "那上个月呢",
            "conversation": first["conversation"],
            "conversation_artifacts": first["conversation_artifacts"],
        }
        second = SalesAgent._finalize_turn(agent, second_state)

        self.assertEqual(len(second["conversation"]), 4)
        self.assertEqual(len(second["conversation_artifacts"]), 4)
        roles = [message["role"] for message in second["conversation"]]
        has_artifact = [item is not None for item in second["conversation_artifacts"]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])
        self.assertEqual(has_artifact, [False, True, False, True])

    def test_old_session_without_artifacts_recovers_alignment(self) -> None:
        agent = SalesAgent.__new__(SalesAgent)
        # 改动之前存下的检查点里没有 conversation_artifacts 这一列。
        legacy = [
            {"role": "user", "content": "历史提问"},
            {"role": "assistant", "content": "历史回答"},
        ]
        result = SalesAgent._finalize_turn(
            agent, {**self._data_state(), "conversation": legacy}
        )

        self.assertEqual(len(result["conversation"]), len(result["conversation_artifacts"]))
        self.assertEqual(result["conversation_artifacts"][:2], [None, None])
        self.assertIsNotNone(result["conversation_artifacts"][-1])

    def test_history_sent_to_model_has_no_artifact_key(self) -> None:
        agent = SalesAgent.__new__(SalesAgent)
        result = SalesAgent._finalize_turn(agent, self._data_state())

        # history 会原样作为 messages 发给 provider，多一个键就是一次 400。
        for message in result["history"]:
            self.assertEqual(set(message), {"role", "content"})

    def test_only_recent_turns_keep_detail_rows(self) -> None:
        from app.bi.agent import _ARTIFACT_TURNS_WITH_ROWS, _trim_artifacts

        artifacts = [{"rows": [{"n": i}], "row_count": 1} for i in range(10)]
        trimmed = _trim_artifacts(artifacts)

        kept = [item for item in trimmed if item["rows"]]
        self.assertEqual(len(kept), _ARTIFACT_TURNS_WITH_ROWS)
        # 早期轮次的骨架仍在，只是明细被裁掉并标记出来。
        self.assertTrue(trimmed[0]["rows_trimmed"])
        self.assertEqual(trimmed[0]["row_count"], 1)


class IntentRoutingTests(unittest.TestCase):
    """领域概念问题必须走检索，不能落到"闲聊"由模型自由发挥。

    实测缺陷：「成本怎么计算的」被判为 general，RAG 完全没跑，答案来自模型记忆
    而非 learn_odoo 笔记——看起来权威、实则无依据也无法核实，且不会附引用链接。
    根因是 `成本` 不在任何关键词表里。
    """

    def test_cost_concept_questions_route_to_knowledge(self) -> None:
        from app.bi import classify_intent

        for question in ("成本怎么计算的", "成本是怎么算的", "库存成本怎么核算"):
            with self.subTest(question=question):
                self.assertIn(
                    classify_intent(question, []),
                    {"knowledge", "source"},
                    f"{question!r} 应走检索，落到 general 会给出无引用的答案",
                )

    def test_cost_data_questions_still_route_to_data(self) -> None:
        from app.bi import classify_intent

        # 加了关键词后不能把"查数"问题也拽去检索。
        self.assertEqual(classify_intent("本月成本是多少？", []), "data")


class QueryNormalizationTests(unittest.IsolatedAsyncioTestCase):
    def test_sales_amount_typo_is_corrected_with_exact_character_details(self) -> None:
        normalized, corrections = normalize_query_text("今年销售呃是多少？")

        self.assertEqual(normalized, "今年销售额是多少？")
        self.assertEqual(
            corrections,
            [{
                "original": "销售呃",
                "corrected": "销售额",
                "wrong_fragment": "呃",
                "replacement": "额",
            }],
        )

    async def test_initial_state_preserves_original_and_warns_about_correction(self) -> None:
        agent = SalesAgent.__new__(SalesAgent)
        agent._persisted_history = AsyncMock(return_value=[])
        agent._routing = Mock(
            general=Mock(name="deepseek", model="deepseek-chat")
        )
        agent._semantics = Mock(name="native", version="test-v1")

        state = await SalesAgent._initial_state(
            agent,
            question="今年销售呃是多少？",
            history=[],
            session_id="typo-test",
        )

        self.assertEqual(state["question"], "今年销售额是多少？")
        self.assertEqual(state["display_question"], "今年销售呃是多少？")
        self.assertIn("‘呃’纠正为‘额’", state["warnings"][0])

    def test_known_macro_region_without_semantic_field_requires_clarification(self) -> None:
        agent = SalesAgent.__new__(SalesAgent)
        agent._semantics = Mock(
            table_columns={"res_partner": ["id", "name"]}
        )

        result = SalesAgent._detect_data_ambiguity(
            agent,
            {
                "question": "统计今年华东区每个月销售额",
                "history": [],
            },
        )

        plan = QueryPlan.model_validate(result["query_plan"])
        self.assertTrue(plan.requires_clarification)
        self.assertIn("华东区", plan.clarification_question)
        self.assertEqual(result["logical_sql"], "")
