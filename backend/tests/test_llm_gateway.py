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
        client = Mock()
        client.chat.completions.create = AsyncMock(return_value=completion)
        client.close = AsyncMock()

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
            patch("app.llm.gateway.AsyncOpenAI", return_value=client),
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
        call = client.chat.completions.create.await_args.kwargs
        self.assertEqual(call["model"], "deepseek-v4-pro")
        self.assertNotIn("name", call)
        trace.assert_called_once_with(
            name="generate-model-response",
            model="deepseek-v4-pro",
            input_data={"messages": [{"role": "user", "content": "你好"}]},
        )
        client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
