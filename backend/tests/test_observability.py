from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.observability import (  # noqa: E402
    redact_for_trace,
    trace_chat_turn,
    update_observation,
)


class ObservabilityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

