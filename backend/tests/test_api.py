from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import DatabaseHealth  # noqa: E402
from app.llm import LLMResult  # noqa: E402
from app.main import create_app  # noqa: E402


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY"}
        }
        self.environment["LLM_PROVIDER"] = "deepseek"
        self.environment["LANGFUSE_ENABLED"] = "false"
        self.environment["LANGFUSE_TRACING_ENABLED"] = "false"

    def tearDown(self) -> None:
        get_settings.cache_clear()

    async def test_health_reports_unconfigured_model_provider(self) -> None:
        disconnected = DatabaseHealth(
            connected=False,
            user="codex_readonly",
            database="odoo19_dev",
            read_only=False,
            company_id=1,
            company_name=None,
            currency=None,
            order_count=0,
            response_ms=1.0,
            error_type="ConnectionError",
        )
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch(
                "app.api.routes.health.OdooDatabase.healthcheck",
                new=AsyncMock(return_value=disconnected),
            ),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["ready_for_model_calls"])
        self.assertEqual(
            payload["services"]["odoo_database"]["access_mode"],
            "read-only",
        )

    async def test_chat_returns_503_without_provider_key(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/chat",
                    json={"question": "本月销售额是多少？"},
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "deepseek API key is not configured.",
        )

    async def test_chat_returns_model_result_without_claiming_data_access(self) -> None:
        environment = dict(self.environment)
        environment["DEEPSEEK_API_KEY"] = "sensitive-value"
        result = LLMResult(
            content="模型连接正常；当前没有查询 Odoo 数据。",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=12,
            output_tokens=10,
            total_tokens=22,
        )

        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "app.bi.agent.LLMGateway.complete",
                new=AsyncMock(return_value=result),
            ),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/chat",
                    json={"question": "你好，请简单介绍你自己。"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data_accessed"])
        self.assertEqual(payload["phase"], "general-chat")
        self.assertEqual(payload["intent"], "general")
        self.assertEqual(payload["usage"]["total_tokens"], 22)
        self.assertIsNone(payload["trace_id"])
        self.assertIsNone(payload["trace_url"])
        self.assertEqual(payload["column_labels"], {})

    async def test_chat_masks_provider_error_details(self) -> None:
        environment = dict(self.environment)
        environment["DEEPSEEK_API_KEY"] = "sensitive-value"
        session_id = "api-failed-turn-persistence-test"

        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "app.bi.agent.LLMGateway.complete",
                new=AsyncMock(side_effect=RuntimeError("sensitive provider detail")),
            ),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/chat",
                    json={
                        "question": "测试错误处理",
                        "session_id": session_id,
                    },
                )
                restored = await client.get(f"/api/chat/sessions/{session_id}")
                await client.delete(f"/api/chat/conversations/{session_id}")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"],
            "Agent request failed: RuntimeError",
        )
        self.assertNotIn("sensitive provider detail", response.text)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["run_status"], "failed")
        self.assertEqual(
            restored.json()["history"][-1],
            {"role": "user", "content": "测试错误处理"},
        )

    async def test_chat_supports_general_questions_and_conversation_history(
        self,
    ) -> None:
        environment = dict(self.environment)
        environment["DEEPSEEK_API_KEY"] = "sensitive-value"
        result = LLMResult(
            content="今天是 8 月 2 日。",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=24,
            output_tokens=8,
            total_tokens=32,
        )
        completion = AsyncMock(return_value=result)

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("app.bi.agent.LLMGateway.complete", new=completion),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/chat",
                    json={
                        "question": "今天呢？",
                        "history": [
                            {"role": "user", "content": "你知道日期吗？"},
                            {"role": "assistant", "content": "知道。"},
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        messages = completion.await_args.kwargs["messages"]
        self.assertIn("普通对话", messages[0]["content"])
        self.assertIn("不要生成 SQL", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "user", "content": "你知道日期吗？"})
        self.assertEqual(messages[2], {"role": "assistant", "content": "知道。"})
        self.assertEqual(messages[3], {"role": "user", "content": "今天呢？"})

    async def test_chat_stream_returns_progress_and_result_events(self) -> None:
        environment = dict(self.environment)
        environment["DEEPSEEK_API_KEY"] = "sensitive-value"
        result = LLMResult(
            content="流式回答完成。",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=8,
            output_tokens=4,
            total_tokens=12,
        )

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("app.bi.agent.LLMGateway.complete", new=AsyncMock(return_value=result)),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/chat/stream",
                    json={"question": "你好", "session_id": "api-stream-test"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: progress", response.text)
        self.assertIn("event: result", response.text)
        self.assertIn("流式回答完成", response.text)

    async def test_chat_conversations_can_be_listed_restored_and_deleted(self) -> None:
        environment = dict(self.environment)
        environment["DEEPSEEK_API_KEY"] = "sensitive-value"
        session_id = "api-conversation-history-test"
        result = LLMResult(
            content="本月销售情况正常。",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=8,
            output_tokens=4,
            total_tokens=12,
        )

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("app.bi.agent.LLMGateway.complete", new=AsyncMock(return_value=result)),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/chat",
                    json={
                        "question": "  请看一下\n本月销售  ",
                        "session_id": session_id,
                    },
                )
                conversations = await client.get("/api/chat/conversations")
                restored = await client.get(f"/api/chat/sessions/{session_id}")
                detached = await client.post(
                    f"/api/chat/conversations/{session_id}/detach"
                )
                deleted = await client.delete(f"/api/chat/conversations/{session_id}")
                after_delete = await client.get("/api/chat/conversations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(conversations.status_code, 200)
        matching = [
            item
            for item in conversations.json()["conversations"]
            if item["session_id"] == session_id
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["title"], "请看一下 本月销售")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["run_status"], "completed")
        self.assertEqual(detached.json()["run_status"], "completed")
        self.assertEqual(
            [message["role"] for message in restored.json()["history"]],
            ["user", "assistant"],
        )
        self.assertEqual(deleted.json(), {"session_id": session_id, "deleted": True})
        self.assertNotIn(
            session_id,
            [item["session_id"] for item in after_delete.json()["conversations"]],
        )

    async def test_chat_feedback_records_boolean_langfuse_score(self) -> None:
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch(
                "app.api.routes.chat.record_user_feedback",
                return_value=True,
            ) as record,
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/chat/feedback",
                    json={"trace_id": "a" * 32, "positive": False},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["recorded"])
        record.assert_called_once_with(
            trace_id="a" * 32,
            positive=False,
            comment=None,
        )

    async def test_settings_never_returns_saved_secrets(self) -> None:
        environment = dict(self.environment)
        environment.update(
            {
                "DEEPSEEK_API_KEY": "deepseek-secret-marker",
                "LANGFUSE_PUBLIC_KEY": "test-public-marker",
                "LANGFUSE_SECRET_KEY": "test-private-marker",
                "LANGFUSE_BASE_URL": "https://cloud.langfuse.com",
            }
        )

        with patch.dict(os.environ, environment, clear=True):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/api/settings")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["providers"]["deepseek"]["configured"])
        self.assertTrue(response.json()["langfuse"]["configured"])
        self.assertNotIn("deepseek-secret-marker", response.text)
        self.assertNotIn("public-secret-marker", response.text)
        self.assertNotIn("private-secret-marker", response.text)
        self.assertNotIn("api_key", response.text)
        self.assertNotIn("secret_key", response.text)

    async def test_settings_reads_provider_model_catalog_without_exposing_key(
        self,
    ) -> None:
        environment = dict(self.environment)
        environment["SILICONFLOW_API_KEY"] = "siliconflow-secret-marker"
        model_client = MagicMock()
        model_client.models.list = AsyncMock(
            return_value=SimpleNamespace(
                data=[
                    SimpleNamespace(id="vendor/model-b"),
                    SimpleNamespace(id="vendor/model-a"),
                ]
            )
        )
        model_client.close = AsyncMock()

        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "app.api.routes.settings.AsyncOpenAI",
                return_value=model_client,
            ),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/api/settings/models/siliconflow")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "siliconflow")
        self.assertIn("vendor/model-a", payload["models"])
        self.assertIn(payload["current_model"], payload["models"])
        self.assertNotIn("siliconflow-secret-marker", response.text)
        model_client.models.list.assert_awaited_once()
        model_client.close.assert_awaited_once()

    async def test_settings_update_persists_allowlisted_values_without_echoing_keys(
        self,
    ) -> None:
        saved: dict[str, str] = {}

        def fake_store(updates: dict[str, str]) -> None:
            saved.update(updates)
            os.environ.update(updates)

        request = {
            "selected_provider": "siliconflow",
            "deepseek": {
                "api_key": "deepseek-new-secret-marker",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
                "input_price_per_million": 0.435,
                "output_price_per_million": 0.87,
            },
            "siliconflow": {
                "api_key": "siliconflow-new-secret-marker",
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "deepseek-ai/DeepSeek-V3.1-Terminus",
                "input_price_per_million": 4,
                "output_price_per_million": 12,
            },
            "routing": {
                "sql": {"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V3.1-Terminus"},
                "answer": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                "general": {"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V3.1-Terminus"},
            },
            "langfuse": {
                "public_key": "pk-lf-new-public-marker",
                "secret_key": "sk-lf-new-secret-marker",
                "base_url": "https://cloud.langfuse.com",
                "enabled": True,
            },
            "database": {
                "host": "127.0.0.1",
                "port": 55432,
                "database": "odoo19_dev",
                "user": "codex_readonly",
                "password": None,
                "company_id": 1,
                "statement_timeout_ms": 15000,
                "max_rows": 500,
            },
            "state_database": {
                "enabled": False,
                "host": "127.0.0.1",
                "port": 55432,
                "database": "odoo_agent_state",
                "user": "odoo_agent_state",
                "password": None,
            },
        }

        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch(
                "app.api.routes.settings.set_user_environment",
                side_effect=fake_store,
            ),
        ):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.put("/api/settings", json=request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(saved["LLM_PROVIDER"], "siliconflow")
        self.assertEqual(saved["SILICONFLOW_API_KEY"], "siliconflow-new-secret-marker")
        self.assertEqual(saved["ODOO_DB_USER"], "codex_readonly")
        self.assertEqual(saved["ODOO_COMPANY_ID"], "1")
        self.assertEqual(saved["SQL_LLM_PROVIDER"], "siliconflow")
        self.assertEqual(saved["ANSWER_LLM_PROVIDER"], "deepseek")
        self.assertTrue(response.json()["providers"]["siliconflow"]["configured"])
        self.assertNotIn("new-secret-marker", response.text)
        self.assertNotIn("new-public-marker", response.text)

    async def test_settings_rejects_odoo_database_as_checkpoint_database(self) -> None:
        request = {
            "selected_provider": "deepseek",
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
                "input_price_per_million": 0.435,
                "output_price_per_million": 0.87,
            },
            "siliconflow": {
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "deepseek-ai/DeepSeek-V3.1-Terminus",
                "input_price_per_million": 4,
                "output_price_per_million": 12,
            },
            "routing": {
                "sql": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                "answer": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                "general": {"provider": "deepseek", "model": "deepseek-v4-pro"},
            },
            "langfuse": {
                "base_url": "https://cloud.langfuse.com",
                "enabled": False,
            },
            "database": {
                "host": "127.0.0.1",
                "port": 55432,
                "database": "odoo19_dev",
                "user": "codex_readonly",
                "company_id": 1,
                "statement_timeout_ms": 15000,
                "max_rows": 500,
            },
            "state_database": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 55432,
                "database": "odoo19_dev",
                "user": "writer",
            },
        }

        with patch.dict(os.environ, self.environment, clear=True):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.put("/api/settings", json=request)

        self.assertEqual(response.status_code, 422)
        self.assertIn("必须与 Odoo 业务数据库分离", response.json()["detail"])

    async def test_fastapi_serves_only_allowlisted_ui_files(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                settings_page = await client.get("/ui/settings.html")
                chart_library = await client.get("/ui/echarts.min.js")
                private_path = await client.get("/ui/.git/config")

        self.assertEqual(settings_page.status_code, 200)
        self.assertIn("Langfuse 可观测性", settings_page.text)
        self.assertEqual(chart_library.status_code, 200)
        self.assertGreater(len(chart_library.content), 1_000_000)
        self.assertEqual(private_path.status_code, 404)

    async def test_root_redirects_to_the_application(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
            get_settings.cache_clear()
            transport = ASGITransport(app=create_app())
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                root = await client.get("/")
                favicon = await client.get("/favicon.ico")

        self.assertEqual(root.status_code, 307)
        self.assertEqual(root.headers["location"], "/ui/index.html")
        self.assertEqual(favicon.status_code, 204)




if __name__ == "__main__":
    unittest.main()
