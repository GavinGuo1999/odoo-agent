from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.chart import build_data_profile, parse_chart_plan  # noqa: E402
from app.bi.error_analysis import (  # noqa: E402
    analyze_sql_errors,
    query_fingerprint,
    sql_fingerprint,
)


class SqlErrorAnalyzerTests(unittest.TestCase):
    def test_contract_violation_is_repairable(self) -> None:
        analysis = analyze_sql_errors(
            stage="validation",
            errors=["销售查询必须包含 company_id = 1。"],
            sql="SELECT COUNT(id) FROM sale_order",
        )

        self.assertEqual(analysis.category, "contract_violation")
        self.assertTrue(analysis.repairable)
        self.assertFalse(analysis.needs_user_input)

    def test_undeclared_user_filter_is_a_repairable_contract_violation(self) -> None:
        analysis = analyze_sql_errors(
            stage="validation",
            errors=["用户问题没有授权过滤字段 date_order。"],
            sql="SELECT 1 FROM sale_order WHERE date_order >= '2099-01-01'",
        )

        self.assertEqual(analysis.category, "contract_violation")
        self.assertTrue(analysis.repairable)

    def test_unsafe_operation_is_never_repaired(self) -> None:
        analysis = analyze_sql_errors(
            stage="validation",
            errors=["查询包含写操作、锁或不允许的命令。"],
            sql="DELETE FROM sale_order",
        )

        self.assertEqual(analysis.category, "unsafe_operation")
        self.assertFalse(analysis.repairable)
        self.assertFalse(analysis.needs_user_input)

    def test_timeout_is_not_sent_to_blind_sql_repair(self) -> None:
        analysis = analyze_sql_errors(
            stage="execution",
            errors=["数据库执行失败：QueryCanceled。"],
            sql="SELECT 1",
        )

        self.assertEqual(analysis.category, "timeout")
        self.assertFalse(analysis.repairable)

    def test_sql_fingerprint_ignores_whitespace_and_case(self) -> None:
        self.assertEqual(
            sql_fingerprint("SELECT  id\nFROM sale_order"),
            sql_fingerprint(" select ID from SALE_ORDER "),
        )

    def test_query_fingerprint_includes_plan_contract(self) -> None:
        sql = "SELECT amount FROM sale_order WHERE company_id = 1"
        first = query_fingerprint(sql, {"filters": [{"field": "partner_id"}]})
        second = query_fingerprint(sql, {"filters": [{"field": "name"}]})

        self.assertNotEqual(first, second)


class ChartPlannerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            {"month": "2026-06-01", "sales_amount": 100.0},
            {"month": "2026-07-01", "sales_amount": 200.0},
        ]
        self.profile = build_data_profile(["month", "sales_amount"], self.rows)

    def test_profile_identifies_time_and_numeric_fields(self) -> None:
        self.assertEqual(self.profile.row_count, 2)
        self.assertEqual(self.profile.time_fields, ["month"])
        self.assertEqual(self.profile.numeric_fields, ["sales_amount"])

    def test_chart_plan_is_pydantic_validated_against_result_fields(self) -> None:
        plan = parse_chart_plan(
            """{
              "type": "line",
              "title": "月度销售趋势",
              "x_field": "month",
              "series": [{"field": "sales_amount", "label": "销售额"}],
              "sort_by": "month",
              "sort_order": "asc",
              "top_n": null,
              "reason": "时间序列适合折线图"
            }""",
            profile=self.profile,
        )

        self.assertEqual(plan.type, "line")
        self.assertEqual(plan.y_fields, ["sales_amount"])
        self.assertEqual(plan.sort_by, "month")

    def test_chart_plan_rejects_invented_columns(self) -> None:
        with self.assertRaises(ValueError):
            parse_chart_plan(
                """{
                  "type": "bar",
                  "title": "错误图表",
                  "x_field": "customer",
                  "series": [{"field": "invented_amount", "label": null}],
                  "sort_by": null,
                  "sort_order": null,
                  "top_n": 10,
                  "reason": "测试非法字段"
                }""",
                profile=self.profile,
            )


if __name__ == "__main__":
    unittest.main()
