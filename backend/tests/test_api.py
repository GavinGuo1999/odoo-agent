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




if __name__ == "__main__":
    unittest.main()
