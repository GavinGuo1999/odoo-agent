"""OpenAI-compatible gateway for DeepSeek and SiliconFlow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The Langfuse wrapper is a drop-in replacement and automatically records the
# generation model, latency, token usage, cost, responses, and provider errors.
from langfuse.openai import AsyncOpenAI

from app.config import ProviderConfig


class ProviderNotConfiguredError(RuntimeError):
    pass


class EmptyModelResponseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LLMResult:
    content: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class LLMGateway:
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        generation_name: str,
        metadata: dict[str, Any] | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        if not self._config.configured:
            raise ProviderNotConfiguredError(
                f"Provider {self._config.name} is not configured."
            )

        client = AsyncOpenAI(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
        )

        request_options: dict[str, Any] = {}
        if json_mode:
            request_options["response_format"] = {"type": "json_object"}

        try:
            completion = await client.chat.completions.create(
                model=self._config.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0,
                stream=False,
                name=generation_name,
                metadata={
                    "provider": self._config.name,
                    **(metadata or {}),
                },
                **request_options,
            )
        finally:
            await client.close()

        content = completion.choices[0].message.content
        if not content:
            raise EmptyModelResponseError("The model returned an empty response.")

        usage = completion.usage
        return LLMResult(
            content=content,
            provider=self._config.name,
            model=completion.model or self._config.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
        )
