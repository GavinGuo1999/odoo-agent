"""Top-N 识别：说了"N 个"就必须声明 row_limit，没说就不许声明。

这条规则是双向的，两边都会出事：
  - 该识别没识别 → 模型声明了 row_limit=3，契约校验判它"未指定 Top N 却写了 LIMIT"，
    修复循环耗尽后整轮失败（实测缺陷：「下降最大的三个月」）；
  - 不该识别却识别 → 完整排名被悄悄截断，用户看到的榜单是不全的。
"""

from __future__ import annotations

import unittest

from app.schemas.query_plan import ranking_requires_row_limit


class ExplicitTopNTests(unittest.TestCase):
    def assert_bounded(self, question: str) -> None:
        self.assertTrue(ranking_requires_row_limit(question), question)

    def assert_unbounded(self, question: str) -> None:
        self.assertFalse(ranking_requires_row_limit(question), question)

    def test_superlatives_other_than_high_low(self) -> None:
        # 实测缺陷就出在这一组：以前只认"最高/最低"。
        for question in (
            "帮我统计 2025 年各月份销售额，并找出同比下降最大的三个月，分析原因。",
            "环比降幅最大的三个月是哪几个",
            "增长最快的五个产品",
            "销量最多的10个商品",
            "毛利最小的三个客户",
        ):
            with self.subTest(question=question):
                self.assert_bounded(question)

    def test_classic_top_n_forms_still_work(self) -> None:
        for question in ("Top 5 客户", "销售额前三的客户", "销售额最高的十个客户是谁？"):
            with self.subTest(question=question):
                self.assert_bounded(question)

    def test_month_unit_is_recognised(self) -> None:
        # "三个月"里的量词是"个月"，不能只认"个"就漏掉。
        self.assert_bounded("下降最大的三个月")

    def test_full_ranking_stays_unbounded(self) -> None:
        for question in (
            "各客户销售额排名",
            "所有产品的销售额",
            "今年销售额是多少",
            "按月列出今年销售额",
        ):
            with self.subTest(question=question):
                self.assert_unbounded(question)

    def test_empty_question_is_treated_as_bounded(self) -> None:
        # 问题为空时无从判断，保守地要求声明上限，避免拉全表。
        self.assert_bounded("")


if __name__ == "__main__":
    unittest.main()
