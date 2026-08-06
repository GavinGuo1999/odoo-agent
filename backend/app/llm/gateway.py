"""OpenAI-compatible gateway for DeepSeek and SiliconFlow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.config import ProviderConfig
from app.observability import trace_generation, update_observation


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
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0


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
        config: ProviderConfig | None = None,
        generation_role: str | None = None,
    ) -> LLMResult:
        active_config = config or self._config
        if not active_config.configured:
            raise ProviderNotConfiguredError(
                f"Provider {active_config.name} is not configured."
            )

        client = AsyncOpenAI(
            api_key=active_config.api_key,
            base_url=active_config.base_url,
            timeout=active_config.timeout_seconds,
        )

        request_options: dict[str, Any] = {}
        if json_mode:
            request_options["response_format"] = {"type": "json_object"}

        trace_metadata = {
            "provider": active_config.name,
            "generation_role": generation_role or generation_name,
            "pricing_currency": active_config.pricing_currency,
            "pricing_is_estimate": True,
            **(metadata or {}),
        }
        with trace_generation(
            name=generation_name,
            model=active_config.model,
            input_data={"messages": messages},
        ) as observation:
            update_observation(observation, metadata=trace_metadata)
            try:
                completion = await client.chat.completions.create(
                    model=active_config.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0,
                    stream=False,
                    **request_options,
                )
            except Exception as exc:
                update_observation(
                    observation,
                    level="ERROR",
                    status_message=type(exc).__name__,
                )
                raise
            finally:
                await client.close()

            content = completion.choices[0].message.content
            if not content:
                raise EmptyModelResponseError("The model returned an empty response.")

            usage = completion.usage
            input_tokens = usage.prompt_tokens if usage else None
            output_tokens = usage.completion_tokens if usage else None
            total_tokens = usage.total_tokens if usage else None
            input_cost, output_cost = active_config.estimated_cost_usd(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            update_observation(
                observation,
                output=content,
                usage_details={
                    "input": input_tokens or 0,
                    "output": output_tokens or 0,
                    "total": total_tokens or 0,
                },
                cost_details={
                    "input": input_cost,
                    "output": output_cost,
                    "total": input_cost + output_cost,
                },
            )

        return LLMResult(
            content=content,
            provider=active_config.name,
            model=completion.model or active_config.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            total_cost_usd=input_cost + output_cost,
        )
