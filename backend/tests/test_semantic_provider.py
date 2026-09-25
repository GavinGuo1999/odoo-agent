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
    CubeSemanticProvider,
    NativeSemanticProvider,
    SemanticProviderError,
    WrenSemanticProvider,
    _prepare_cube_context,
    _prepare_wren_context,
    build_semantic_provider,
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


CUBE_META = {
    "cubes": [
        {"name": "sale_order", "type": "cube", "measures": [], "dimensions": []},
        {
            "name": "sales_analysis",
            "type": "view",
            "description": "订单级销售分析",
            "measures": [{"name": "sales_analysis.sales_amount", "title": "销售额"}],
            "dimensions": [{"name": "sales_analysis.res_partner_name", "title": "客户"}],
        },
    ]
}


def _cube_config(**overrides: object) -> SemanticConfig:
    base = {
        "provider": "cube",
        "wren_project_path": Path("."),
        "wren_executable": None,
        "timeout_seconds": 5.0,
        "cube_base_url": "http://cube.test/cubejs-api/v1",
        "cube_api_token": None,
    }
    base.update(overrides)
    return SemanticConfig(**base)  # type: ignore[arg-type]


class CubeProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _prepare_cube_context.cache_clear()

    async def test_retrieve_uses_cube_meta_and_plan_sql_compiles(self) -> None:
        provider = CubeSemanticProvider(SalesSemanticLayer.load(), _cube_config())

        with patch(
            "app.bi.semantic_provider.fetch_cube_meta", return_value=CUBE_META
        ) as fetch, patch(
            "app.bi.semantic_provider.compile_cube_sql",
            return_value="SELECT sum(amount_untaxed) FROM public.sale_order",
        ) as compile_sql:
            context = await provider.retrieve("销售额", company_id=1)
            sql = await provider.plan_sql("SELECT MEASURE(sales_amount) FROM sales_analysis")

        self.assertEqual(context.provider, "cube")
        # 模型看到的是视图这一层，不是底层 cube。
        self.assertIn("sales_analysis", context.as_prompt())
        self.assertIn("销售额", context.as_prompt())
        self.assertEqual(sql, "SELECT sum(amount_untaxed) FROM public.sale_order")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(compile_sql.call_count, 1)

    async def test_prompt_tells_the_model_to_write_cube_sql(self) -> None:
        """Cube 的 SQL 语法与物理 SQL 不同，提示词不说清楚就产不出能编译的查询。"""
        provider = CubeSemanticProvider(SalesSemanticLayer.load(), _cube_config())
        with patch("app.bi.semantic_provider.fetch_cube_meta", return_value=CUBE_META):
            prompt = (await provider.retrieve("销售额", company_id=1)).as_prompt()

        self.assertIn("MEASURE(", prompt)
        self.assertIn("cube_schema", prompt)
        # 不能串台到 Wren 的命名空间说明。
        self.assertNotIn("Wren MDL", prompt)
        self.assertNotIn("wren_mdl_schema", prompt)
        # 语义层接管 schema 后，物理表清单不应再出现。
        self.assertNotIn('"tables"', prompt)

    async def test_meta_is_fetched_once_across_calls(self) -> None:
        """模型定义在进程外且启动后不变，不该每次问答都打一次 Cube。"""
        provider = CubeSemanticProvider(SalesSemanticLayer.load(), _cube_config())
        with patch(
            "app.bi.semantic_provider.fetch_cube_meta", return_value=CUBE_META
        ) as fetch:
            await provider.retrieve("销售额", company_id=1)
            await provider.retrieve("订单数", company_id=1)
        self.assertEqual(fetch.call_count, 1)

    async def test_cube_failure_surfaces_as_semantic_provider_error(self) -> None:
        """Cube 不可用时必须报成 SemanticProviderError，而不是漏出底层异常。"""
        from app.bi.cube_client import CubeClientError

        provider = CubeSemanticProvider(SalesSemanticLayer.load(), _cube_config())
        with patch(
            "app.bi.semantic_provider.compile_cube_sql",
            side_effect=CubeClientError("connection refused"),
        ):
            with self.assertRaises(SemanticProviderError):
                await provider.plan_sql("SELECT 1")

        with patch(
            "app.bi.semantic_provider.fetch_cube_meta",
            side_effect=CubeClientError("connection refused"),
        ):
            with self.assertRaises(SemanticProviderError):
                await provider.retrieve("销售额", company_id=1)

    def test_build_semantic_provider_routes_to_cube(self) -> None:
        provider = build_semantic_provider(_cube_config())
        self.assertIsInstance(provider, CubeSemanticProvider)
        self.assertEqual(provider.name, "cube")


if __name__ == "__main__":
    unittest.main()
