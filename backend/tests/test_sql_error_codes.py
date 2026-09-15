"""拒绝理由的分类码：每一类失败都要能被聚合统计。

错误消息是给用户看的中文；要回答"哪类问题在失败、占多少"，需要的是稳定的标签。
本文件把每一类拒绝场景都跑一遍并断言分类码——**改了 sql_guard 里的消息而忘记
同步 _ERROR_CODES，这里就会红**，不会悄悄退化成 other。
"""

from __future__ import annotations

import unittest

from app.database.sql_guard import ReadOnlySqlGuard, classify_sql_error
from app.schemas.query_plan import QueryPlan


_COLUMNS = {
    "sale_order": ["id", "state", "company_id", "partner_id", "amount_untaxed", "date_order"],
    "res_partner": ["id", "name"],
    "res_users": ["id", "partner_id", "company_id", "active", "share"],
}


def _plan(**overrides) -> QueryPlan:
    base = {
        "query_type": "kpi",
        "metric_ids": ["sales_amount"],
        "dimensions": [],
        "filters": [{"field": "company_id", "operator": "eq", "value": 1,
                     "source": "system_required"}],
        "time_range": {"label": None, "start": None, "end": None, "grain": "none"},
        "result_shape": "scalar",
        "select_columns": ["sales_amount"],
        "sort": [],
        "row_limit": None,
    }
    base.update(overrides)
    return QueryPlan.model_validate(base)


class ErrorCodeCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = ReadOnlySqlGuard(
            table_columns=_COLUMNS, company_id=1, max_rows=500
        )

    def assert_code(self, sql: str, expected: str, *, plan=None, question="") -> None:
        result = self.guard.validate(sql, plan=plan, question=question)
        self.assertFalse(result.safe, f"这条本该被拒：{sql}")
        self.assertIn(
            expected, result.error_codes,
            f"期望分类码 {expected}，实际 {result.error_codes}（消息：{result.errors}）",
        )
        self.assertNotIn(
            "other", result.error_codes,
            f"出现未分类的理由，请同步 _ERROR_CODES：{result.errors}",
        )

    def test_structural_rejections(self) -> None:
        cases = [
            ("DROP TABLE sale_order", "not_a_select"),
            ("SELECT 1; SELECT 2", "multiple_statements"),
            ("SELECT FROM WHERE", "unparsable"),
            ("SELECT * FROM sale_order WHERE company_id = 1", "select_star"),
        ]
        for sql, code in cases:
            with self.subTest(code=code):
                self.assert_code(sql, code)

    def test_surface_rejections(self) -> None:
        cases = [
            ("SELECT id AS a FROM ir_config_parameter WHERE company_id = 1",
             "unlisted_table"),
            ("SELECT u.login AS a FROM res_users u WHERE u.company_id = 1",
             "unlisted_column"),
            ("SELECT pg_sleep(1) AS a FROM sale_order WHERE company_id = 1",
             "forbidden_function"),
            ("SELECT amount_untaxed AS a FROM sale_order", "missing_company_filter"),
        ]
        for sql, code in cases:
            with self.subTest(code=code):
                self.assert_code(sql, code)

    def test_contract_rejections(self) -> None:
        sql = "SELECT amount_untaxed AS wrong_name FROM sale_order WHERE company_id = 1"
        self.assert_code(sql, "select_columns_mismatch", plan=_plan(), question="销售额")

    def test_filter_authorization_rejection(self) -> None:
        sql = (
            "SELECT SUM(o.amount_untaxed) AS sales_amount FROM sale_order o "
            "JOIN res_partner p ON p.id = o.partner_id "
            "WHERE o.company_id = 1 AND p.name = '某公司'"
        )
        plan = _plan(filters=[
            {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
            {"field": "name", "operator": "eq", "value": "某公司", "source": "user"},
        ])
        self.assert_code(sql, "filter_unauthorized", plan=plan, question="今年销售额是多少")

    def test_codes_are_deduplicated_and_ordered(self) -> None:
        result = self.guard.validate("SELECT x FROM nope")
        self.assertEqual(len(result.error_codes), len(set(result.error_codes)))
        self.assertEqual(result.error_codes[0], classify_sql_error(result.errors[0]))

    def test_a_clean_query_has_no_codes(self) -> None:
        result = self.guard.validate(
            "SELECT SUM(amount_untaxed) AS sales_amount FROM sale_order WHERE company_id = 1"
        )
        self.assertTrue(result.safe, result.errors)
        self.assertEqual(result.error_codes, [])

    def test_unknown_message_falls_back_to_other(self) -> None:
        # 兜底存在，但上面的断言保证它不会被真实理由命中。
        self.assertEqual(classify_sql_error("某条没登记的理由"), "other")


if __name__ == "__main__":
    unittest.main()
