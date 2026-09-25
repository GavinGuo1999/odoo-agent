"""Cube 客户端契约测试，重点在参数回填器。

回填器进入安全路径：守卫靠 SQL 里的字面值判断公司隔离，而 Cube 返回的是
参数化语句，所以这个函数的输出决定守卫看到什么。下面每条都对应一种能让
它悄悄出错的方式。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.cube_client import (  # noqa: E402
    CubeClientError,
    describe_cube_meta,
    inline_cube_params,
    restore_cube_aliases,
)


class InlineCubeParamsTests(unittest.TestCase):
    def test_integer_string_is_restored_to_bare_number(self) -> None:
        """Cube 对整数列也用字符串传参，不还原就过不了守卫的公司隔离检查。"""
        self.assertEqual(
            inline_cube_params('WHERE "sale_order".company_id = $1', ["1"]),
            'WHERE "sale_order".company_id = 1',
        )

    def test_timestamp_string_stays_quoted(self) -> None:
        """时间参数裸化会变成非法 SQL——这是数字还原最容易误伤的地方。"""
        self.assertEqual(
            inline_cube_params("WHERE date_order >= $1::timestamptz", ["2026-01-01T00:00:00.000Z"]),
            "WHERE date_order >= '2026-01-01T00:00:00.000Z'::timestamptz",
        )

    def test_decimal_string_stays_quoted(self) -> None:
        """只还原整数。浮点字符串保持引号，交给 Postgres 自己转型。"""
        self.assertEqual(
            inline_cube_params("WHERE amount > $1", ["1000.5"]),
            "WHERE amount > '1000.5'",
        )

    def test_quote_in_value_is_escaped(self) -> None:
        self.assertEqual(
            inline_cube_params("WHERE name = $1", ["O'Brien"]),
            "WHERE name = 'O''Brien'",
        )

    def test_value_containing_placeholder_text_is_not_substituted_again(self) -> None:
        """单趟替换的理由：分多趟会把参数值里的 `$1` 当占位符二次替换。

        这是一个真实的注入面——如果客户名恰好是 `$1`，二次替换会把它换成
        另一个参数的值，产出一条语义完全不同、但仍能通过守卫的 SQL。
        """
        result = inline_cube_params(
            "WHERE name = $1 AND company_id = $2",
            ["$2", "1"],
        )
        self.assertEqual(result, "WHERE name = '$2' AND company_id = 1")

    def test_double_digit_placeholders(self) -> None:
        """$1 不能吃掉 $10 的前缀。"""
        sql = " ".join(f"c{i} = ${i}" for i in range(1, 12))
        result = inline_cube_params(sql, [str(i) for i in range(1, 12)])
        self.assertIn("c10 = 10", result)
        self.assertIn("c11 = 11", result)
        self.assertNotIn("$", result)

    def test_out_of_range_placeholder_raises(self) -> None:
        with self.assertRaises(CubeClientError):
            inline_cube_params("WHERE a = $2", ["1"])

    def test_unused_param_raises(self) -> None:
        """宁可报错，也不能静默产出一条与 Cube 本意不同的 SQL。"""
        with self.assertRaises(CubeClientError):
            inline_cube_params("WHERE a = $1", ["1", "2"])

    def test_none_and_bool(self) -> None:
        self.assertEqual(inline_cube_params("a = $1 AND b = $2", [None, True]), "a = NULL AND b = TRUE")

    def test_negative_integer_string(self) -> None:
        self.assertEqual(inline_cube_params("a = $1", ["-3"]), "a = -3")

    def test_no_params_is_passthrough(self) -> None:
        self.assertEqual(inline_cube_params("SELECT 1", []), "SELECT 1")


class DescribeCubeMetaTests(unittest.TestCase):
    META = {
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

    def test_only_views_are_exposed(self) -> None:
        """模型看到的应当是视图这一层，cube 是实现细节。"""
        text = describe_cube_meta(self.META)
        self.assertIn("sales_analysis", text)
        self.assertNotIn("视图 sale_order", text)
        self.assertIn("销售额", text)
        self.assertIn("客户", text)

    def test_missing_views_raises(self) -> None:
        with self.assertRaises(CubeClientError):
            describe_cube_meta({"cubes": [{"name": "x", "type": "cube"}]})

    def test_malformed_meta_raises(self) -> None:
        with self.assertRaises(CubeClientError):
            describe_cube_meta({})


class RestoreCubeAliasesTests(unittest.TestCase):
    """别名还原也在安全路径上：守卫按名字校验输出列，改错名字就等于改了语义。

    设计上只认「按位置 + 前缀截断」这一种情形，其余一律原样返回，
    让守卫去拒绝——宁可失败，也不要悄悄改出一条不同的 SQL。
    """

    REQUESTED = (
        "SELECT product_template_name AS product, "
        "MEASURE(sales_quantity) AS sales_quantity, "
        "MEASURE(delivered_quantity) AS delivered_quantity "
        "FROM sales_line_analysis WHERE company_id = 1 GROUP BY 1"
    )

    def test_truncated_alias_is_restored(self) -> None:
        compiled = (
            'SELECT a.name "product", sum(b.qty) "sales_quantity", '
            'sum(b.delivered) "delivered_quanti" FROM t AS a'
        )
        out = restore_cube_aliases(compiled, self.REQUESTED)
        self.assertIn("delivered_quantity", out)
        self.assertNotIn("delivered_quanti\"", out.replace("delivered_quantity", ""))

    def test_untruncated_aliases_are_left_alone(self) -> None:
        compiled = (
            'SELECT a.name "product", sum(b.qty) "sales_quantity", '
            'sum(b.delivered) "delivered_quantity" FROM t AS a'
        )
        self.assertEqual(
            restore_cube_aliases(compiled, self.REQUESTED).count("delivered_quantity"), 1
        )

    def test_order_by_reference_follows_the_rename(self) -> None:
        """只改 SELECT 不改 ORDER BY，会留下一个悬空引用。"""
        compiled = (
            'SELECT a.name "product", sum(b.qty) "sales_quantity", '
            'sum(b.delivered) "delivered_quanti" FROM t AS a '
            'ORDER BY "delivered_quanti" DESC'
        )
        out = restore_cube_aliases(compiled, self.REQUESTED)
        self.assertIn("ORDER BY", out.upper())
        self.assertNotIn("delivered_quanti ", out)
        self.assertEqual(out.count("delivered_quantity"), 2)

    def test_column_count_mismatch_returns_input_unchanged(self) -> None:
        compiled = 'SELECT a.name "product" FROM t AS a'
        self.assertEqual(restore_cube_aliases(compiled, self.REQUESTED), compiled)

    def test_non_prefix_difference_is_refused(self) -> None:
        """名字对不上前缀关系时不能改——那不是截断，是另一个列。"""
        compiled = (
            'SELECT a.name "product", sum(b.qty) "sales_quantity", '
            'sum(b.delivered) "something_else12" FROM t AS a'
        )
        self.assertEqual(restore_cube_aliases(compiled, self.REQUESTED), compiled)

    def test_short_alias_difference_is_refused(self) -> None:
        """不足 16 字符就不是 Cube 的截断，不能当截断处理。"""
        compiled = (
            'SELECT a.name "product", sum(b.qty) "sales_quantity", '
            'sum(b.delivered) "deliv" FROM t AS a'
        )
        self.assertEqual(restore_cube_aliases(compiled, self.REQUESTED), compiled)

    def test_duplicate_requested_aliases_are_refused(self) -> None:
        requested = "SELECT x AS dup, y AS dup FROM t"
        compiled = 'SELECT a "dup", b "dup" FROM t'
        self.assertEqual(restore_cube_aliases(compiled, requested), compiled)

    def test_unparsable_input_returns_unchanged(self) -> None:
        self.assertEqual(restore_cube_aliases("not sql at all {{", self.REQUESTED),
                         "not sql at all {{")


class OrderByRealignmentTests(unittest.TestCase):
    """SELECT 里出现表达式时，Cube 会包一层子查询，ORDER BY 引用内层生成名。

    守卫按名字比对 ORDER BY 与 QueryPlan.sort，所以必须改写回输出列的别名。
    判据是表达式完全相等——排序键改错就是结果顺序改错，不做模糊匹配。
    """

    REQUESTED = (
        "SELECT date_trunc('month', date_order) AS month, "
        "MEASURE(sales_amount) AS sales_amount FROM sales_analysis "
        "WHERE company_id = 1 GROUP BY 1 ORDER BY 1"
    )

    def test_inner_alias_in_order_by_is_rewritten_to_output_alias(self) -> None:
        compiled = (
            'SELECT "v"."cast_datetrunc_u" AS "month", "v"."measure_sales_an" AS "sales_amount" '
            'FROM (SELECT 1 AS "cast_datetrunc_u", 2 AS "measure_sales_an") AS "v" '
            'ORDER BY "v"."cast_datetrunc_u" ASC'
        )
        out = restore_cube_aliases(compiled, self.REQUESTED)
        order_clause = out.upper().split("ORDER BY", 1)[1]
        self.assertIn("month", order_clause.lower())
        self.assertNotIn("cast_datetrunc_u", order_clause.lower())

    def test_order_by_on_an_unrelated_expression_is_left_alone(self) -> None:
        """ORDER BY 的表达式不等于任何输出列时不能碰——那不是同一个东西。"""
        compiled = (
            'SELECT "v"."cast_datetrunc_u" AS "month", "v"."measure_sales_an" AS "sales_amount" '
            'FROM (SELECT 1 AS "cast_datetrunc_u", 2 AS "measure_sales_an") AS "v" '
            'ORDER BY "v"."something_else" ASC'
        )
        out = restore_cube_aliases(compiled, self.REQUESTED)
        self.assertIn("something_else", out)

    def test_no_order_by_is_a_noop(self) -> None:
        compiled = (
            'SELECT "v"."cast_datetrunc_u" AS "month", "v"."measure_sales_an" AS "sales_amount" '
            'FROM (SELECT 1 AS "cast_datetrunc_u", 2 AS "measure_sales_an") AS "v"'
        )
        self.assertEqual(restore_cube_aliases(compiled, self.REQUESTED), compiled)


class PhysicalColumnHintTests(unittest.TestCase):
    """Cube 把连接维度扁平化成 res_partner_name，编译出的 SQL 却是物理列 name。

    守卫按 QueryPlan.filters 声明的字段名比对 WHERE 里的列名，所以 schema 说明
    必须带上物理列名，否则过滤类查询一律被判"未声明的过滤字段"。
    """

    META = {
        "cubes": [
            {
                "name": "sales_analysis",
                "type": "view",
                "description": "订单级销售分析",
                "measures": [{"name": "sales_analysis.sales_amount", "shortTitle": "销售额"}],
                "dimensions": [
                    {
                        "name": "sales_analysis.res_partner_name",
                        "shortTitle": "客户",
                        "aliasMember": "res_partner.name",
                    },
                    {
                        "name": "sales_analysis.date_order",
                        "shortTitle": "下单时间",
                        "aliasMember": "sale_order.date_order",
                    },
                ],
            }
        ]
    }

    def test_flattened_dimension_exposes_its_physical_column(self) -> None:
        text = describe_cube_meta(self.META)
        self.assertIn("res_partner_name", text)
        self.assertIn("物理列 name", text)

    def test_unflattened_dimension_has_no_redundant_hint(self) -> None:
        """名字本来就等于物理列时不该加噪音。"""
        text = describe_cube_meta(self.META)
        self.assertNotIn("物理列 date_order", text)


if __name__ == "__main__":
    unittest.main()
