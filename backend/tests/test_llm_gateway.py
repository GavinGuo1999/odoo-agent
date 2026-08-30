from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import ProviderConfig  # noqa: E402
from app.llm.gateway import LLMGateway  # noqa: E402


class LLMGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_uses_stable_generation_name_and_returns_usage(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="连接正常"))],
            model="deepseek-v4-pro",
            usage=SimpleNamespace(
                prompt_tokens=8,
                completion_tokens=4,
                total_tokens=12,
            ),
        )
        config = ProviderConfig(
            name="deepseek",
            api_key="sensitive-value",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            timeout_seconds=30,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
        )

        observation = Mock()

        @contextmanager
        def fake_generation(**_kwargs):
            yield observation

        with (
            patch(
                "app.llm.gateway.litellm.acompletion",
                new=AsyncMock(return_value=completion),
            ) as complete,
            patch("app.llm.gateway.trace_generation", side_effect=fake_generation) as trace,
        ):
            result = await LLMGateway(config).complete(
                messages=[{"role": "user", "content": "你好"}],
                generation_name="generate-model-response",
                metadata={"feature": "test"},
            )

        self.assertEqual(result.content, "连接正常")
        self.assertEqual(result.total_tokens, 12)
        self.assertAlmostEqual(result.total_cost_usd, 0.000016)
        call = complete.await_args.kwargs
        self.assertEqual(call["model"], "deepseek/deepseek-v4-pro")
        self.assertEqual(call["api_base"], "https://api.deepseek.com")
        self.assertEqual(call["api_key"], "sensitive-value")
        self.assertNotIn("name", call)
        self.assertNotIn("thinking", call)
        trace.assert_called_once_with(
            name="generate-model-response",
            model="deepseek-v4-pro",
            input_data={"messages": [{"role": "user", "content": "你好"}]},
        )
        metadata = observation.update.call_args_list[0].kwargs["metadata"]
        self.assertEqual(metadata["llm_gateway"], "litellm-sdk")
        self.assertEqual(metadata["gateway_model"], "deepseek/deepseek-v4-pro")

    async def test_siliconflow_uses_openai_compatible_litellm_adapter(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            model="deepseek-ai/DeepSeek-V3.1-Terminus",
            usage=None,
        )
        config = ProviderConfig(
            name="siliconflow",
            api_key="sensitive-value",
            base_url="https://api.siliconflow.cn/v1",
            model="deepseek-ai/DeepSeek-V3.1-Terminus",
            timeout_seconds=30,
        )

        @contextmanager
        def fake_generation(**_kwargs):
            yield Mock()

        with (
            patch(
                "app.llm.gateway.litellm.acompletion",
                new=AsyncMock(return_value=completion),
            ) as complete,
            patch(
                "app.llm.gateway.trace_generation",
                side_effect=fake_generation,
            ),
        ):
            result = await LLMGateway(config).complete(
                messages=[{"role": "user", "content": "你好"}],
                generation_name="generate-model-response",
                json_mode=True,
            )

        call = complete.await_args.kwargs
        self.assertEqual(
            call["model"],
            "openai/deepseek-ai/DeepSeek-V3.1-Terminus",
        )
        self.assertEqual(call["api_base"], "https://api.siliconflow.cn/v1")
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertEqual(result.provider, "siliconflow")

    async def test_sql_role_can_disable_deepseek_thinking(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            model="deepseek-v4-pro",
            usage=None,
        )
        config = ProviderConfig(
            name="deepseek",
            api_key="sensitive-value",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            timeout_seconds=30,
            thinking_mode="disabled",
        )

        @contextmanager
        def fake_generation(**_kwargs):
            yield Mock()

        with (
            patch(
                "app.llm.gateway.litellm.acompletion",
                new=AsyncMock(return_value=completion),
            ) as complete,
            patch("app.llm.gateway.trace_generation", side_effect=fake_generation),
        ):
            await LLMGateway(config).complete(
                messages=[{"role": "user", "content": "SQL"}],
                generation_name="generate-sales-sql",
                generation_role="sql",
                json_mode=True,
            )

        self.assertEqual(
            complete.await_args.kwargs["thinking"],
            {"type": "disabled"},
        )

    async def test_siliconflow_omits_thinking_switch_for_unlisted_model(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            model="deepseek-ai/DeepSeek-V4-Pro",
            usage=None,
        )
        config = ProviderConfig(
            name="siliconflow",
            api_key="sensitive-value",
            base_url="https://api.siliconflow.cn/v1",
            model="deepseek-ai/DeepSeek-V4-Pro",
            timeout_seconds=30,
            thinking_mode="disabled",
        )

        @contextmanager
        def fake_generation(**_kwargs):
            yield Mock()

        with (
            patch(
                "app.llm.gateway.litellm.acompletion",
                new=AsyncMock(return_value=completion),
            ) as complete,
            patch("app.llm.gateway.trace_generation", side_effect=fake_generation),
        ):
            await LLMGateway(config).complete(
                messages=[{"role": "user", "content": "SQL"}],
                generation_name="generate-sales-sql",
                generation_role="sql",
                json_mode=True,
            )

        call = complete.await_args.kwargs
        self.assertNotIn("thinking", call)
        self.assertNotIn("extra_body", call)

    async def test_siliconflow_uses_native_thinking_switch_for_supported_model(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            model="deepseek-ai/DeepSeek-V3.2",
            usage=None,
        )
        config = ProviderConfig(
            name="siliconflow",
            api_key="sensitive-value",
            base_url="https://api.siliconflow.cn/v1",
            model="deepseek-ai/DeepSeek-V3.2",
            timeout_seconds=30,
            thinking_mode="disabled",
        )

        @contextmanager
        def fake_generation(**_kwargs):
            yield Mock()

        with (
            patch(
                "app.llm.gateway.litellm.acompletion",
                new=AsyncMock(return_value=completion),
            ) as complete,
            patch("app.llm.gateway.trace_generation", side_effect=fake_generation),
        ):
            await LLMGateway(config).complete(
                messages=[{"role": "user", "content": "SQL"}],
                generation_name="generate-sales-sql",
                generation_role="sql",
                json_mode=True,
            )

        call = complete.await_args.kwargs
        self.assertNotIn("thinking", call)
        self.assertEqual(call["extra_body"], {"enable_thinking": False})


if __name__ == "__main__":
    unittest.main()
