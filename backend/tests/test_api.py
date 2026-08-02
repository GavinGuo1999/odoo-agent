from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm import LLMResult  # noqa: E402
from app.main import create_app  # noqa: E402


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY"}
        }
        self.environment["LANGFUSE_ENABLED"] = "false"
        self.environment["LANGFUSE_TRACING_ENABLED"] = "false"

    def tearDown(self) -> None:
        get_settings.cache_clear()

    async def test_health_reports_unconfigured_model_provider(self) -> None:
        with patch.dict(os.environ, self.environment, clear=True):
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
                "app.api.routes.chat.LLMGateway.complete",
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
                    json={"question": "本月销售额是多少？"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["data_accessed"])
        self.assertEqual(payload["phase"], "model-connectivity")
        self.assertEqual(payload["usage"]["total_tokens"], 22)
        self.assertIsNone(payload["trace_id"])

    async def test_chat_masks_provider_error_details(self) -> None:
        environment = dict(self.environment)
        environment["DEEPSEEK_API_KEY"] = "sensitive-value"

        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "app.api.routes.chat.LLMGateway.complete",
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
            "Model request failed: RuntimeError",
        )
        self.assertNotIn("sensitive provider detail", response.text)

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
                private_path = await client.get("/ui/.git/config")

        self.assertEqual(settings_page.status_code, 200)
        self.assertIn("Langfuse 可观测性", settings_page.text)
        self.assertEqual(private_path.status_code, 404)




if __name__ == "__main__":
    unittest.main()
