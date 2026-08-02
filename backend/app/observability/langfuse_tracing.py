"""Langfuse tracing primitives used by the future FastAPI/LangGraph backend.

The helpers deliberately degrade to no-ops when Langfuse is disabled or its
credentials are missing. Observability must never stop the user-facing flow.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from langfuse import get_client, propagate_attributes


_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "public_key",
    "secret",
    "token",
    "api_key",
    "apikey",
)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk)-lf-[A-Za-z0-9-]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
)
_TRUE_VALUES = {"1", "true", "yes", "on"}
_REDACTED = "[REDACTED]"


def _is_enabled() -> bool:
    return os.getenv("LANGFUSE_ENABLED", "true").strip().lower() in _TRUE_VALUES


def langfuse_is_configured() -> bool:
    """Return whether tracing can be safely initialized in this process."""

    return _is_enabled() and bool(
        os.getenv("LANGFUSE_PUBLIC_KEY")
        and os.getenv("LANGFUSE_SECRET_KEY")
        and os.getenv("LANGFUSE_BASE_URL")
    )


def _redact_string(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def redact_for_trace(value: Any) -> Any:
    """Recursively remove credentials while retaining useful trace context."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized_key = key.lower().replace("-", "_")
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                result[key] = _REDACTED
            else:
                result[key] = redact_for_trace(raw_value)
        return result

    if isinstance(value, str):
        return _redact_string(value)

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact_for_trace(item) for item in value]

    return value


def update_observation(observation: Any | None, **fields: Any) -> None:
    """Safely update an observation without exporting credentials in payloads."""

    if observation is None:
        return

    sanitized = dict(fields)
    for field in ("input", "output", "metadata"):
        if field in sanitized:
            sanitized[field] = redact_for_trace(sanitized[field])
    observation.update(**sanitized)


@contextmanager
def _observation(
    *,
    as_type: str,
    name: str,
    input_data: Any = None,
    model: str | None = None,
) -> Iterator[Any | None]:
    if not langfuse_is_configured():
        yield None
        return

    client = get_client()
    kwargs: dict[str, Any] = {
        "as_type": as_type,
        "name": name,
        "input": redact_for_trace(input_data),
    }
    if model:
        kwargs["model"] = model

    with client.start_as_current_observation(**kwargs) as observation:
        try:
            yield observation
        except Exception as exc:
            update_observation(
                observation,
                level="ERROR",
                status_message=type(exc).__name__,
            )
            raise


@contextmanager
def trace_chat_turn(
    *,
    session_id: str,
    question: str,
    provider: str,
) -> Iterator[Any | None]:
    """Create one root trace for one ChatBI user turn."""

    with _observation(
        as_type="agent",
        name="answer-sales-question",
        input_data={"question": question},
    ) as observation:
        if observation is None:
            yield None
            return

        with propagate_attributes(
            session_id=session_id,
            tags=["odoo-agent", "chatbi", "sales"],
            metadata={"provider": provider, "access_mode": "read-only"},
            version="0.1.0",
        ):
            yield observation


@contextmanager
def trace_generation(
    *,
    name: str,
    model: str,
    input_data: Any,
) -> Iterator[Any | None]:
    """Trace an LLM generation, e.g. question-to-SQL or answer synthesis."""

    with _observation(
        as_type="generation",
        name=name,
        input_data=input_data,
        model=model,
    ) as observation:
        yield observation


@contextmanager
def trace_retrieval(*, name: str, input_data: Any) -> Iterator[Any | None]:
    """Trace schema, metric-definition, or example retrieval."""

    with _observation(
        as_type="retriever",
        name=name,
        input_data=input_data,
    ) as observation:
        yield observation


@contextmanager
def trace_tool(*, name: str, input_data: Any) -> Iterator[Any | None]:
    """Trace a deterministic tool call such as read-only SQL execution."""

    with _observation(
        as_type="tool",
        name=name,
        input_data=input_data,
    ) as observation:
        yield observation
