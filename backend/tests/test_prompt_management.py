from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.observability.prompt_management import ManagedPrompt, render_managed_prompt  # noqa: E402


class PromptManagementTests(unittest.TestCase):
    def test_unconfigured_prompt_uses_code_template(self) -> None:
        with patch(
            "app.observability.prompt_management.langfuse_is_configured",
            return_value=False,
        ):
            prompt = render_managed_prompt(
                name="test-prompt",
                rendered="你好，小王",
                variables={"name": "小王"},
            )

        self.assertIsInstance(prompt, ManagedPrompt)
        self.assertEqual(prompt, "你好，小王")
        self.assertEqual(prompt.fallback_template, "你好，{{name}}")
        self.assertIsNone(prompt.langfuse_prompt)

    def test_remote_prompt_is_compiled_and_linked(self) -> None:
        remote = Mock()
        remote.compile.return_value = "远端版本：小王"
        client = Mock()
        client.get_prompt.return_value = remote
        with (
            patch.dict(os.environ, {"LANGFUSE_PROMPTS_FETCH_ENABLED": "true"}),
            patch(
                "app.observability.prompt_management.langfuse_is_configured",
                return_value=True,
            ),
            patch(
                "app.observability.prompt_management._langfuse_client",
                return_value=client,
            ),
        ):
            prompt = render_managed_prompt(
                name="test-prompt",
                rendered="你好，小王",
                variables={"name": "小王"},
            )

        self.assertEqual(prompt, "远端版本：小王")
        self.assertIs(prompt.langfuse_prompt, remote)
        remote.compile.assert_called_once_with(name="小王")


if __name__ == "__main__":
    unittest.main()
