from __future__ import annotations

import logging
import os
from typing import Any

from .langfuse_tracing import _langfuse_client, langfuse_is_configured


logger = logging.getLogger(__name__)
_TRUE_VALUES = {"1", "true", "yes", "on"}


class ManagedPrompt(str):
    """A normal string that also carries its Langfuse prompt-version link."""

    langfuse_prompt: Any | None
    fallback_template: str

    def __new__(
        cls,
        value: str,
        *,
        langfuse_prompt: Any | None,
        fallback_template: str,
    ) -> "ManagedPrompt":
        instance = super().__new__(cls, value)
        instance.langfuse_prompt = langfuse_prompt
        instance.fallback_template = fallback_template
        return instance


def _template_from_rendered(rendered: str, variables: dict[str, str]) -> str:
    template = rendered
    for name, value in sorted(variables.items(), key=lambda item: len(item[1]), reverse=True):
        if value:
            template = template.replace(value, "{{" + name + "}}")
    return template


def render_managed_prompt(
    *,
    name: str,
    rendered: str,
    variables: dict[str, str],
) -> ManagedPrompt:
    """渲染提示词。**默认以代码为准**，显式开启后才从 Langfuse 注册表取。

    为什么默认关：以前默认是开的，于是 Langfuse 里存着的 production 版本会悄悄
    覆盖 `prompts.py`。实测后果是改了代码里的提示词完全不生效——新增的多步查询
    指令连"followups"这个词都没进到发给模型的文本里，排查了很久才发现。

    代码是版本化、可评审、和测试同步的；注册表是运行时的、无声的。默认值应当
    指向前者。要用注册表就显式设 `LANGFUSE_PROMPTS_FETCH_ENABLED=true`，并且
    要清楚：那之后 `prompts.py` 的任何修改都不再影响线上，除非你同步推新版本。
    """

    fallback_template = _template_from_rendered(rendered, variables)
    fetch_enabled = (
        os.getenv("LANGFUSE_PROMPTS_FETCH_ENABLED", "false").strip().casefold()
        in _TRUE_VALUES
    )
    if not fetch_enabled or not langfuse_is_configured():
        return ManagedPrompt(
            rendered,
            langfuse_prompt=None,
            fallback_template=fallback_template,
        )

    label = os.getenv("LANGFUSE_PROMPT_LABEL", "production").strip() or "production"
    try:
        prompt = _langfuse_client().get_prompt(
            name,
            label=label,
            type="text",
            fallback=fallback_template,
            cache_ttl_seconds=300,
            max_retries=1,
            fetch_timeout_seconds=2,
        )
        compiled = prompt.compile(**variables)
        return ManagedPrompt(
            compiled,
            langfuse_prompt=prompt,
            fallback_template=fallback_template,
        )
    except Exception as exc:
        logger.warning("Langfuse prompt fallback name=%s error=%s", name, type(exc).__name__)
        return ManagedPrompt(
            rendered,
            langfuse_prompt=None,
            fallback_template=fallback_template,
        )
