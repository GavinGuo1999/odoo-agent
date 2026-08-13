from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.semantic import SalesSemanticLayer  # noqa: E402
from app.bi.semantic_provider import (  # noqa: E402
    NativeSemanticProvider,
    WrenSemanticProvider,
    _prepare_wren_context,
)
from app.config import SemanticConfig  # noqa: E402


class SemanticProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_provider_is_a_passthrough(self) -> None:
        provider = NativeSemanticProvider(SalesSemanticLayer.load())
        context = await provider.retrieve("本月销售额", company_id=1)
        self.assertEqual(context.provider, "native")
        self.assertEqual(await provider.plan_sql(" SELECT 1 "), "SELECT 1")

    async def test_wren_provider_uses_compiled_context_and_dry_plan(self) -> None:
        _prepare_wren_context.cache_clear()
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "wren_project.yml").write_text("schema_version: 5\n", encoding="utf-8")
            config = SemanticConfig(
                provider="wren",
                wren_project_path=project,
                wren_executable="fake-wren",
                timeout_seconds=5,
            )
            provider = WrenSemanticProvider(
                SalesSemanticLayer.load(),
                config,
                executable="fake-wren",
            )

            responses = ["built", "MODEL sale_order", "company rule", "SELECT physical"]
            with patch("app.bi.semantic_provider._run_wren", side_effect=responses) as run_wren:
                context = await provider.retrieve("销售额", company_id=1)
                sql = await provider.plan_sql("SELECT amount_untaxed FROM sale_order")

            self.assertEqual(context.provider, "wren")
            self.assertIn("MODEL sale_order", context.as_prompt())
            self.assertEqual(sql, "SELECT physical")
            self.assertEqual(run_wren.call_count, 4)


if __name__ == "__main__":
    unittest.main()
