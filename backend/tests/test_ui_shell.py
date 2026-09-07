from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from httpx import ASGITransport, AsyncClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import create_app  # noqa: E402


PROJECT_DIR = BACKEND_DIR.parent
PAGES = ("index.html", "chat.html", "dashboard.html", "wiki.html", "settings.html", "quality.html")


class NavigationSingleSourceTests(unittest.TestCase):
    """导航必须只有一处定义。

    加一个导航项曾经要改 6 个 HTML —— 本轮加"评测与质量"时就漏改过 index.html。
    """

    def test_pages_do_not_hardcode_navigation_links(self) -> None:
        for page in PAGES:
            markup = (PROJECT_DIR / page).read_text(encoding="utf-8")
            self.assertNotIn(
                'class="nav-link"',
                markup,
                f"{page} 仍在硬编码导航项；导航应由 sidebar.js 单点生成",
            )

    def test_every_page_hosts_the_shared_sidebar_placeholder(self) -> None:
        for page in PAGES:
            markup = (PROJECT_DIR / page).read_text(encoding="utf-8")
            self.assertIn("data-sidebar", markup, f"{page} 缺少侧边栏挂载点")
            self.assertLess(
                markup.index("sidebar.js"),
                markup.index("app.js"),
                f"{page} 必须先加载 sidebar.js，app.js 才能找到侧边栏里的元素",
            )

    def test_navigation_is_defined_once_in_the_shell(self) -> None:
        source = (PROJECT_DIR / "sidebar.js").read_text(encoding="utf-8")
        for target in ("index.html", "chat.html", "dashboard.html", "wiki.html", "quality.html", "settings.html"):
            self.assertIn(target, source, f"sidebar.js 缺少 {target} 的导航项")

    def test_shell_builds_dom_without_innerhtml(self) -> None:
        source = (PROJECT_DIR / "sidebar.js").read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", "", code)
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(forbidden, code)


class StaticAssetCachingTests(unittest.IsolatedAsyncioTestCase):
    """去掉手工版本号后，缓存正确性必须由服务端保证。"""

    async def _get(self, path: str, headers: dict[str, str] | None = None):
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers=headers or {})

    def test_pages_no_longer_carry_manual_version_query_strings(self) -> None:
        for page in PAGES:
            markup = (PROJECT_DIR / page).read_text(encoding="utf-8")
            self.assertNotRegex(
                markup,
                r'(styles\.css|app\.js|sidebar\.js|markdown\.js|quality\.js)\?v=',
                f"{page} 仍在用手工版本号；漏改一次就会产生“改了没生效”的假缺陷",
            )

    async def test_ui_assets_are_revalidated_rather_than_cached_blindly(self) -> None:
        response = await self._get("/ui/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-cache", response.headers.get("cache-control", ""))
        self.assertTrue(response.headers.get("etag"), "缺少 ETag 就无法做条件请求")

    async def test_unchanged_asset_answers_304_for_a_matching_etag(self) -> None:
        first = await self._get("/ui/styles.css")
        etag = first.headers["etag"]

        second = await self._get("/ui/styles.css", headers={"If-None-Match": etag})

        # 每次都重新下载整份资源是浪费；ETag 命中时应当返回 304。
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.content, b"")

    async def test_changed_asset_gets_a_different_etag(self) -> None:
        app_js = await self._get("/ui/app.js")
        styles = await self._get("/ui/styles.css")

        self.assertNotEqual(app_js.headers["etag"], styles.headers["etag"])


if __name__ == "__main__":
    unittest.main()
