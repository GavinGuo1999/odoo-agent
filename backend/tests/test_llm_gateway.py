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
    async def test_retryable_504_is_retried_once(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            model="deepseek-v4-pro",
            usage=None,
        )
        error = RuntimeError("gateway timeout")
        error.status_code = 504
        config = ProviderConfig(
            name="deepseek",
            api_key="sensitive-value",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            timeout_seconds=30,
            max_retries=1,
            retry_backoff_seconds=0,
        )

        @contextmanager
        def fake_generation(**_kwargs):
            yield Mock()

        with (
            patch(
                "app.llm.gateway.litellm.acompletion",
                new=AsyncMock(side_effect=[error, completion]),
            ) as complete,
            patch("app.llm.gateway.trace_generation", side_effect=fake_generation),
        ):
            result = await LLMGateway(config).complete(
                messages=[{"role": "user", "content": "重试"}],
                generation_name="retry-test",
            )

        self.assertEqual(result.content, "ok")
        self.assertEqual(complete.await_count, 2)

    async def test_non_retryable_402_is_not_retried(self) -> None:
        error = RuntimeError("payment required")
        error.status_code = 402
        config = ProviderConfig(
            name="siliconflow",
            api_key="sensitive-value",
            base_url="https://api.siliconflow.cn/v1",
            model="model",
            timeout_seconds=30,
            max_retries=1,
            retry_backoff_seconds=0,
        )

        @contextmanager
        def fake_generation(**_kwargs):
            yield Mock()

        with (
            patch(
                "app.llm.gateway.litellm.acompletion",
                new=AsyncMock(side_effect=error),
            ) as complete,
            patch("app.llm.gateway.trace_generation", side_effect=fake_generation),
            self.assertRaises(RuntimeError),
        ):
            await LLMGateway(config).complete(
                messages=[{"role": "user", "content": "额度"}],
                generation_name="no-retry-test",
            )

        self.assertEqual(complete.await_count, 1)

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
        # 不在 _SILICONFLOW_THINKING_MODEL_MARKERS 里的模型不发任何思考开关：
        # 发了会被拒绝。此前这里用 DeepSeek-V4-Pro 举例，等于把“V4 收不到开关”
        # 这个缺陷写成了预期行为。
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            model="moonshotai/Kimi-K2",
            usage=None,
        )
        config = ProviderConfig(
            name="siliconflow",
            api_key="sensitive-value",
            base_url="https://api.siliconflow.cn/v1",
            model="moonshotai/Kimi-K2",
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

    async def test_deepseek_v4_uses_the_native_thinking_switch(self) -> None:
        # 实测：`enable_thinking=False` 让同一个提问从均值 14.6s 降到 4.4s、
        # completion_tokens 从 ~500 降到 ~30。此前 V4 不在支持列表里，配置写的
        # `thinking_mode="disabled"` 发出去的是别家厂商的 `thinking` 参数格式，
        # SiliconFlow 直接忽略，等于从未生效。
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
        self.assertEqual(call["extra_body"], {"enable_thinking": False})


if __name__ == "__main__":
    unittest.main()
