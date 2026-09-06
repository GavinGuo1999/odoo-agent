from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.observability import (  # noqa: E402
    redact_for_trace,
    record_user_feedback,
    trace_chat_turn,
    update_observation,
)
from app.observability.langfuse_tracing import _langfuse_client  # noqa: E402


class ObservabilityTests(unittest.TestCase):
    # `_langfuse_client` 带 lru_cache：任何在 patch 生效期间触发它的用例都会把 Mock
    # 永久缓存下来，泄漏到同一进程的后续测试（曾导致 test_query_plan 拿到 Mock 版
    # prompt 而失败）。每个用例前后各清一次，保证隔离。
    def setUp(self) -> None:
        _langfuse_client.cache_clear()

    def tearDown(self) -> None:
        _langfuse_client.cache_clear()

    def test_redacts_sensitive_nested_fields(self) -> None:
        value = {
            "question": "本月销售额是多少？",
            "headers": {"authorization": "sensitive-value"},
            "database_password": "sensitive-value",
            "public_key": "sensitive-value",
        }

        redacted = redact_for_trace(value)

        self.assertEqual(redacted["question"], "本月销售额是多少？")
        self.assertEqual(redacted["headers"]["authorization"], "[REDACTED]")
        self.assertEqual(redacted["database_password"], "[REDACTED]")
        self.assertEqual(redacted["public_key"], "[REDACTED]")

    def test_missing_credentials_degrades_to_noop(self) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "LANGFUSE_PUBLIC_KEY",
                "LANGFUSE_SECRET_KEY",
                "LANGFUSE_BASE_URL",
            }
        }
        with patch.dict(os.environ, environment, clear=True):
            with trace_chat_turn(
                session_id="test-session",
                question="测试问题",
                provider="test",
            ) as observation:
                self.assertIsNone(observation)

    def test_safe_update_redacts_output(self) -> None:
        observation = Mock()

        update_observation(
            observation,
            output={"answer": "ok", "access_token": "sensitive-value"},
        )

        observation.update.assert_called_once_with(
            output={"answer": "ok", "access_token": "[REDACTED]"}
        )

    def test_chat_turn_uses_stable_trace_attributes(self) -> None:
        observation = Mock()

        @contextmanager
        def fake_observation(**_kwargs):
            yield observation

        with (
            patch(
                "app.observability.langfuse_tracing._observation",
                side_effect=fake_observation,
            ),
            patch(
                "app.observability.langfuse_tracing.propagate_attributes"
            ) as propagate_attributes,
        ):
            with trace_chat_turn(
                session_id="test-session",
                question="测试问题",
                provider="siliconflow",
            ) as active_observation:
                self.assertIs(active_observation, observation)

        propagate_attributes.assert_called_once_with(
            trace_name="odoo-chat-turn",
            session_id="test-session",
            tags=["odoo-agent", "chatbi", "sales"],
            metadata={"provider": "siliconflow", "access_mode": "read-only"},
            version="0.2.0",
        )

    def test_user_feedback_uses_boolean_trace_score(self) -> None:
        client = Mock()
        environment = {
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_TRACING_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "test-public",
            "LANGFUSE_SECRET_KEY": "test-secret",
            "LANGFUSE_BASE_URL": "https://cloud.langfuse.com",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("app.observability.langfuse_tracing.get_client", return_value=client),
        ):
            recorded = record_user_feedback(
                trace_id="a" * 32,
                positive=False,
                reason=None,
                comment="结果不对",
            )

        self.assertTrue(recorded)
        client.create_score.assert_called_once_with(
            trace_id="a" * 32,
            name="user-thumbs",
            value=0.0,
            data_type="BOOLEAN",
            comment="结果不对",
            metadata={"source": "odoo-agent-chat"},
        )

    def test_negative_feedback_reason_uses_categorical_score(self) -> None:
        client = Mock()
        with (
            patch.dict(
                os.environ,
                {
                    "LANGFUSE_PUBLIC_KEY": "public",
                    "LANGFUSE_SECRET_KEY": "secret",
                    "LANGFUSE_BASE_URL": "https://cloud.langfuse.com",
                },
                clear=True,
            ),
            patch("app.observability.langfuse_tracing._langfuse_client", return_value=client),
        ):
            recorded = record_user_feedback(
                trace_id="b" * 32,
                positive=False,
                reason="sql-wrong",
            )

        self.assertTrue(recorded)
        self.assertEqual(client.create_score.call_count, 2)
        reason_call = client.create_score.call_args_list[1].kwargs
        self.assertEqual(reason_call["name"], "user-feedback-reason")
        self.assertEqual(reason_call["value"], "sql-wrong")
        self.assertEqual(reason_call["data_type"], "CATEGORICAL")
        client.flush.assert_called_once_with()

    def test_client_maps_application_environment_before_initialization(self) -> None:
        environment = {
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_TRACING_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "test-public",
            "LANGFUSE_SECRET_KEY": "test-secret",
            "LANGFUSE_BASE_URL": "https://cloud.langfuse.com",
            "ODOO_AGENT_ENVIRONMENT": "test",
        }
        client = Mock()
        _langfuse_client.cache_clear()
        try:
            with (
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "app.observability.langfuse_tracing.get_client",
                    return_value=client,
                ) as get_client,
            ):
                self.assertIs(_langfuse_client(), client)
                self.assertEqual(os.environ["LANGFUSE_TRACING_ENVIRONMENT"], "test")
            get_client.assert_called_once_with()
        finally:
            _langfuse_client.cache_clear()


if __name__ == "__main__":
    unittest.main()
