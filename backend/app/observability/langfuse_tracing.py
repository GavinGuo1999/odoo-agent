"""Langfuse tracing primitives used by the future FastAPI/LangGraph backend.

The helpers deliberately degrade to no-ops when Langfuse is disabled or its
credentials are missing. Observability must never stop the user-facing flow.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _langfuse_client() -> Any:
    # Langfuse 4.14 returns a new facade from get_client() on each call. A
    # process-level client retains the project ID needed for immediate URLs.
    configure_langfuse_environment()
    return get_client()


def configure_langfuse_environment() -> str:
    """Map the application environment to the Langfuse SDK before client init."""

    environment = (
        os.getenv("ODOO_AGENT_ENVIRONMENT", "development").strip().casefold()
        or "development"
    )
    os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", environment)
    return os.environ["LANGFUSE_TRACING_ENVIRONMENT"]


def _is_enabled() -> bool:
    custom_enabled = (
        os.getenv("LANGFUSE_ENABLED", "true").strip().lower() in _TRUE_VALUES
    )
    sdk_enabled = (
        os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower()
        in _TRUE_VALUES
    )
    return custom_enabled and sdk_enabled


def langfuse_is_configured() -> bool:
    """Return whether tracing can be safely initialized in this process."""

    return _is_enabled() and bool(
        os.getenv("LANGFUSE_PUBLIC_KEY")
        and os.getenv("LANGFUSE_SECRET_KEY")
        and os.getenv("LANGFUSE_BASE_URL")
    )


def get_current_trace_id() -> str | None:
    """Return the active trace ID without initializing an unconfigured client."""

    if not langfuse_is_configured():
        return None
    return _langfuse_client().get_current_trace_id()


def get_current_trace_url(trace_id: str | None = None) -> str | None:
    """Return the direct Langfuse URL for the active trace when configured."""

    if not langfuse_is_configured():
        return None
    try:
        return _langfuse_client().get_trace_url(trace_id=trace_id)
    except Exception:
        return None


def warm_langfuse_client() -> bool:
    """Authenticate once and cache the project ID used to build trace URLs."""

    if not langfuse_is_configured():
        return False
    try:
        client = _langfuse_client()
        if not client.auth_check():
            return False
        return bool(client.get_trace_url(trace_id="0" * 32))
    except Exception:
        return False


def record_user_feedback(
    *,
    trace_id: str,
    positive: bool,
    reason: str | None = None,
    comment: str | None = None,
) -> bool:
    """Record explicit chat feedback as a BOOLEAN trace score."""

    if not langfuse_is_configured():
        return False
    client = _langfuse_client()
    client.create_score(
        trace_id=trace_id,
        name="user-thumbs",
        value=1.0 if positive else 0.0,
        data_type="BOOLEAN",
        comment=comment,
        metadata={"source": "odoo-agent-chat"},
    )
    if reason:
        client.create_score(
            trace_id=trace_id,
            name="user-feedback-reason",
            value=reason,
            data_type="CATEGORICAL",
            comment=comment,
            metadata={"source": "odoo-agent-chat"},
        )
    client.flush()
    return True


def flush_langfuse() -> None:
    if langfuse_is_configured():
        _langfuse_client().flush()


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
    prompt: Any | None = None,
) -> Iterator[Any | None]:
    if not langfuse_is_configured():
        yield None
        return

    client = _langfuse_client()
    kwargs: dict[str, Any] = {
        "as_type": as_type,
        "name": name,
        "input": redact_for_trace(input_data),
    }
    if model:
        kwargs["model"] = model
    if prompt is not None:
        kwargs["prompt"] = prompt

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
    """Create one root trace for one assistant turn."""

    with _observation(
        as_type="agent",
        name="answer-user-question",
        input_data={"question": question},
    ) as observation:
        if observation is None:
            yield None
            return

        with propagate_attributes(
            trace_name="odoo-chat-turn",
            session_id=session_id,
            tags=["odoo-agent", "chatbi", "sales"],
            metadata={"provider": provider, "access_mode": "read-only"},
            version="0.2.0",
        ):
            yield observation


@contextmanager
def trace_agent(*, name: str, input_data: Any) -> Iterator[Any | None]:
    """Trace an agent or graph subtree with a stable semantic name."""

    with _observation(
        as_type="agent",
        name=name,
        input_data=input_data,
    ) as observation:
        yield observation


@contextmanager
def trace_generation(
    *,
    name: str,
    model: str,
    input_data: Any,
    prompt: Any | None = None,
) -> Iterator[Any | None]:
    """Trace an LLM generation, e.g. question-to-SQL or answer synthesis."""

    with _observation(
        as_type="generation",
        name=name,
        input_data=input_data,
        model=model,
        prompt=prompt,
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
