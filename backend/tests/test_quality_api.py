from __future__ import annotations

import sys
import unittest
from pathlib import Path

from httpx import ASGITransport, AsyncClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import create_app  # noqa: E402


PROJECT_DIR = BACKEND_DIR.parent


class QualityApiTests(unittest.IsolatedAsyncioTestCase):
    async def _get(self, path: str) -> tuple[int, dict]:
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(path)
        return response.status_code, response.json()

    async def test_summary_is_readable_even_with_no_reports(self) -> None:
        status_code, payload = await self._get("/api/quality/summary")

        # 没跑过评测是正常状态，不是错误：必须 200 并自报 available。
        self.assertEqual(status_code, 200)
        for key in (
            "available",
            "semantic_benchmark",
            "semantic_history",
            "wiki_rag",
            "wiki_history",
            "skipped",
        ):
            self.assertIn(key, payload)
        self.assertIsInstance(payload["available"], bool)
        self.assertIsInstance(payload["semantic_history"], list)

    async def test_summary_does_not_leak_host_paths(self) -> None:
        _, payload = await self._get("/api/quality/summary")

        # reports_dir 是主机目录结构，页面用不到，不该发给浏览器。
        self.assertNotIn("reports_dir", payload)


class QualityPageWiringTests(unittest.TestCase):
    def test_page_and_script_are_allowlisted(self) -> None:
        main_py = (PROJECT_DIR / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"quality.html"', main_py)
        self.assertIn('"quality.js"', main_py)

    def test_every_page_links_to_the_quality_page(self) -> None:
        for page in ("index.html", "chat.html", "dashboard.html", "wiki.html", "settings.html", "quality.html"):
            markup = (PROJECT_DIR / page).read_text(encoding="utf-8")
            self.assertIn("quality.html", markup, f"{page} 缺少评测与质量页的导航入口")

    def test_quality_script_loads_after_app(self) -> None:
        markup = (PROJECT_DIR / "quality.html").read_text(encoding="utf-8")
        self.assertLess(
            markup.index("app.js"),
            markup.index("quality.js"),
            "quality.js 依赖 app.js 建立的页面骨架，必须后加载",
        )


if __name__ == "__main__":
    unittest.main()
