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

    async def test_chat_masks_provider_error_details(self) -> None:
        environment = dict(self.environment)
        environment["DEEPSEEK_API_KEY"] = "sensitive-value"

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
                    json={"question": "测试错误处理"},
                )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"],
            "Agent request failed: RuntimeError",
        )
        self.assertNotIn("sensitive provider detail", response.text)

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

    async def test_settings_never_returns_saved_secrets(self) -> None:
        environment = dict(self.environment)
        environment.update(
            {
                "DEEPSEEK_API_KEY": "deepseek-secret-marker",
                "LANGFUSE_PUBLIC_KEY": "pk-lf-public-secret-marker",
                "LANGFUSE_SECRET_KEY": "sk-lf-private-secret-marker",
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
            },
            "siliconflow": {
                "api_key": "siliconflow-new-secret-marker",
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "deepseek-ai/DeepSeek-V3.1-Terminus",
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
        self.assertTrue(response.json()["providers"]["siliconflow"]["configured"])
        self.assertNotIn("new-secret-marker", response.text)
        self.assertNotIn("new-public-marker", response.text)

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
