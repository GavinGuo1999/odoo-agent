from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
RENDERER = PROJECT_DIR / "markdown.js"
JS_TEST = PROJECT_DIR / "tests" / "markdown_render_test.js"


class MarkdownRendererTests(unittest.TestCase):
    """把前端渲染器的 Node 测试纳入统一门禁。

    仓库刻意没有前端构建链，所以渲染器用一个最小 DOM 桩在 Node 里直接测；
    这里只负责把它接进 `unittest discover`，让一条命令仍能覆盖全部检查。
    """

    def test_renderer_is_served_and_loaded_before_app(self) -> None:
        self.assertTrue(RENDERER.exists())
        main_py = (PROJECT_DIR / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"markdown.js"', main_py, "markdown.js 必须在静态文件白名单里，否则前端 404")

        chat_html = (PROJECT_DIR / "chat.html").read_text(encoding="utf-8")
        self.assertIn("markdown.js", chat_html)
        self.assertLess(
            chat_html.index("markdown.js"),
            chat_html.index("app.js"),
            "渲染器必须在 app.js 之前加载",
        )

    def test_renderer_never_uses_innerhtml(self) -> None:
        source = RENDERER.read_text(encoding="utf-8")
        # 注释里可以讨论这些 API，代码里不行，所以先剥掉注释再断言。
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", "", code)
        # 渲染器只构造 DOM 节点。一旦出现这些写法，模型输出就可能变成可执行标记。
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(forbidden, code, f"渲染器不得使用 {forbidden}")

    def test_node_regression_suite_passes(self) -> None:
        node = shutil.which("node")
        if node is None:  # pragma: no cover - node 是既有门禁依赖
            self.skipTest("node is not available")
        completed = subprocess.run(
            [node, str(JS_TEST)],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"markdown 渲染器测试失败：\n{completed.stdout}\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
