from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _isolation import isolate_ambient_environment  # noqa: E402

# 开发者本机若配了演示门禁的口令，这里不剥掉的话整个模块都会 401。
_ambient = isolate_ambient_environment()


def setUpModule() -> None:
    _ambient.start()


def tearDownModule() -> None:
    _ambient.stop()



def _result() -> dict[str, object]:
    return {
        "audit_id": "20260814-120000-000000-12345678",
        "generated_at": "2026-08-14T12:00:00+08:00",
        "status": "clean",
        "approval_status": "manual_review_required",
        "semantic_version": "1.0",
        "formal_wren_version": "1.0",
        "model_count": 9,
        "field_count": 70,
        "error_count": 0,
        "warning_count": 0,
        "info_count": 2,
        "database_read_only": True,
        "python_files_seen": 100,
        "candidate_files_parsed": 10,
        "source_files_matched": 8,
        "source_path": "D:\\odoo19e\\odoo-source",
        "report_path": "D:\\audit\\report.md",
        "snapshot_path": "D:\\audit\\semantic-snapshot.json",
        "draft_path": "D:\\audit\\wren-draft",
        "issues": [
            {
                "severity": "info",
                "code": "source_definition_not_found",
                "table": "sale_order",
                "field": "id",
                "message": "仅测试",
            }
        ],
    }


class SemanticAuditApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.environment = dict(os.environ)
        self.environment["LANGFUSE_ENABLED"] = "false"
        self.environment["LANGFUSE_TRACING_ENABLED"] = "false"

    def tearDown(self) -> None:
        get_settings.cache_clear()

    async def test_latest_returns_404_before_first_audit(self) -> None:
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch("app.api.routes.semantic_audit.SemanticSyncService.latest", return_value=None),
        ):
            get_settings.cache_clear()
            async with AsyncClient(
                transport=ASGITransport(app=create_app()),
                base_url="http://test",
            ) as client:
                response = await client.get("/api/semantic-audit/latest")

        self.assertEqual(response.status_code, 404)

    async def test_run_returns_structured_audit_summary(self) -> None:
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch(
                "app.api.routes.semantic_audit.SemanticSyncService.audit",
                new=AsyncMock(return_value=_result()),
            ),
        ):
            get_settings.cache_clear()
            async with AsyncClient(
                transport=ASGITransport(app=create_app()),
                base_url="http://test",
            ) as client:
                response = await client.post("/api/semantic-audit")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "clean")
        self.assertTrue(payload["database_read_only"])
        self.assertEqual(payload["approval_status"], "manual_review_required")


if __name__ == "__main__":
    unittest.main()
