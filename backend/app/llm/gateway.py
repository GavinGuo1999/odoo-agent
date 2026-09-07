"""LiteLLM-backed gateway for the application's configured providers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import litellm

from app.config import ProviderConfig
from app.observability import trace_generation, update_observation


logger = logging.getLogger(__name__)


_SILICONFLOW_THINKING_MODEL_MARKERS = (
    "deepseek-v3.1",
    "deepseek-v3.2",
    "deepseek-v4",
    "qwen3-",
    "glm-4.6",
    "glm-4.7",
    "glm-5",
    "hunyuan-a13b",
)


def _siliconflow_supports_thinking_switch(model: str) -> bool:
    normalized = model.casefold()
    return any(marker in normalized for marker in _SILICONFLOW_THINKING_MODEL_MARKERS)


class ProviderNotConfiguredError(RuntimeError):
    pass


class EmptyModelResponseError(RuntimeError):
    pass


def _is_retryable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429, 500, 502, 503, 504}:
        return True
    normalized = type(exc).__name__.casefold()
    return any(
        marker in normalized
        for marker in (
            "timeout",
            "apiconnection",
            "ratelimit",
            "serviceunavailable",
            "internalserver",
        )
    )


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

        request_options: dict[str, Any] = {}
        if json_mode:
            request_options["response_format"] = {"type": "json_object"}
        if active_config.thinking_mode != "auto":
            if active_config.name == "siliconflow" and _siliconflow_supports_thinking_switch(
                active_config.model
            ):
                # SiliconFlow exposes thinking control as an OpenAI-compatible
                # extension on an explicit model allowlist. Passing LiteLLM's
                # generic `thinking` parameter to the openai adapter is rejected,
                # while unsupported SiliconFlow models reject enable_thinking.
                request_options["extra_body"] = {
                    "enable_thinking": active_config.thinking_mode == "enabled"
                }
            elif active_config.name != "siliconflow":
                request_options["thinking"] = {"type": active_config.thinking_mode}

        # LiteLLM requires an explicit provider prefix. DeepSeek has a native
        # adapter; SiliconFlow exposes an OpenAI-compatible endpoint.
        litellm_model = (
            f"deepseek/{active_config.model}"
            if active_config.name == "deepseek"
            else f"openai/{active_config.model}"
        )

        trace_metadata = {
            "provider": active_config.name,
            "llm_gateway": "litellm-sdk",
            "gateway_model": litellm_model,
            "generation_role": generation_role or generation_name,
            "pricing_currency": active_config.pricing_currency,
            "pricing_is_estimate": True,
            "thinking_mode": active_config.thinking_mode,
            "max_retries": active_config.max_retries,
            **(metadata or {}),
        }
        langfuse_prompt = next(
            (
                getattr(message.get("content"), "langfuse_prompt", None)
                for message in messages
                if getattr(message.get("content"), "langfuse_prompt", None) is not None
            ),
            None,
        )
        trace_kwargs: dict[str, Any] = {
            "name": generation_name,
            "model": active_config.model,
            "input_data": {"messages": messages},
        }
        if langfuse_prompt is not None:
            trace_kwargs["prompt"] = langfuse_prompt
        with trace_generation(
            **trace_kwargs,
        ) as observation:
            update_observation(observation, metadata=trace_metadata)
            retry_count = 0
            while True:
                try:
                    completion = await litellm.acompletion(
                        model=litellm_model,
                        messages=messages,
                        api_key=active_config.api_key,
                        api_base=active_config.base_url,
                        timeout=active_config.timeout_seconds,
                        temperature=0,
                        stream=False,
                        **request_options,
                    )
                    break
                except Exception as exc:
                    if (
                        retry_count >= active_config.max_retries
                        or not _is_retryable_error(exc)
                    ):
                        logger.warning(
                            "LLM request failed provider=%s model=%s error=%s status=%s code=%s param=%s retries=%s",
                            active_config.name,
                            active_config.model,
                            type(exc).__name__,
                            getattr(exc, "status_code", None),
                            getattr(exc, "code", None),
                            getattr(exc, "param", None),
                            retry_count,
                        )
                        update_observation(
                            observation,
                            level="ERROR",
                            status_message=type(exc).__name__,
                            metadata={**trace_metadata, "retry_count": retry_count},
                        )
                        raise
                    retry_count += 1
                    logger.info(
                        "Retrying LLM request provider=%s model=%s attempt=%s error=%s",
                        active_config.name,
                        active_config.model,
                        retry_count,
                        type(exc).__name__,
                    )
                    delay = active_config.retry_backoff_seconds * (2 ** (retry_count - 1))
                    if delay:
                        await asyncio.sleep(delay)

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
                metadata={**trace_metadata, "retry_count": retry_count},
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
