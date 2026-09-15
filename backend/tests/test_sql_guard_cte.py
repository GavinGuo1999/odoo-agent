"""CTE 列校验：同比这类自连接必须放行，但 CTE 不能成为绕过白名单的后门。

实测缺陷：模型为"2025 各月销售额同比"写了两个 CTE 再自连接，守卫把外层别名
`s25` 映射回物理表 `sale_order`，然后拿 CTE 的计算列 `sales_amount_2025` 去
`sale_order` 的字段白名单里找，四列全部报"字段未开放"。凡是带计算列的 CTE
都会被误杀——也就是所有同比/环比查询。
"""

from __future__ import annotations

import unittest

from app.database.sql_guard import ReadOnlySqlGuard
from app.schemas.query_plan import QueryPlan


_COLUMNS = {
    "sale_order": [
        "id", "name", "state", "company_id", "partner_id",
        "amount_untaxed", "date_order",
    ],
    "res_partner": ["id", "name"],
    "res_users": ["id", "partner_id", "company_id", "active", "share"],
}

_YOY = """
WITH sales_2025 AS (
    SELECT date_trunc('month', o.date_order)::date AS month,
           SUM(o.amount_untaxed) AS sales_amount_2025
    FROM sale_order o
    WHERE o.company_id = 1 AND o.state IN ('sale','done')
      AND o.date_order >= '2025-01-01' AND o.date_order < '2026-01-01'
    GROUP BY 1
), sales_2024 AS (
    SELECT date_trunc('month', o.date_order)::date AS month,
           SUM(o.amount_untaxed) AS sales_amount_2024
    FROM sale_order o
    WHERE o.company_id = 1 AND o.state IN ('sale','done')
      AND o.date_order >= '2024-01-01' AND o.date_order < '2025-01-01'
    GROUP BY 1
)
SELECT s25.month AS month,
       s25.sales_amount_2025 AS sales_amount,
       s24.sales_amount_2024 AS sales_amount_last_year
FROM sales_2025 s25
LEFT JOIN sales_2024 s24 ON s24.month = s25.month - INTERVAL '1 year'
ORDER BY month
"""


class CteColumnValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = ReadOnlySqlGuard(
            table_columns=_COLUMNS, company_id=1, max_rows=500
        )

    def check(self, sql: str):
        return self.guard.validate(sql, plan=None, question="各月销售额同比")

    def test_year_over_year_self_join_is_allowed(self) -> None:
        result = self.check(_YOY)
        self.assertTrue(result.safe, result.errors)
        self.assertEqual(result.errors, [])

    def test_cte_reference_without_alias_is_allowed(self) -> None:
        sql = """
        WITH monthly AS (
            SELECT date_trunc('month', o.date_order)::date AS month,
                   SUM(o.amount_untaxed) AS sales_amount
            FROM sale_order o WHERE o.company_id = 1 GROUP BY 1
        )
        SELECT monthly.month AS month, monthly.sales_amount AS sales_amount FROM monthly
        """
        self.assertTrue(self.check(sql).safe)

    def test_unknown_cte_column_is_still_rejected(self) -> None:
        """CTE 没有导出的列必须拒——否则外层可以引用任意名字。"""

        sql = """
        WITH leak AS (SELECT o.id AS oid FROM sale_order o WHERE o.company_id = 1)
        SELECT l.oid AS a, l.nonexistent AS b FROM leak l
        """
        result = self.check(sql)
        self.assertFalse(result.safe)
        self.assertTrue(any("l.nonexistent" in error for error in result.errors))

    def test_cte_cannot_launder_an_unlisted_column(self) -> None:
        """最重要的一条：CTE 内部读未开放字段，不能因为套了层 CTE 就放行。"""

        sql = """
        WITH sneaky AS (
            SELECT u.login AS who FROM res_users u WHERE u.company_id = 1
        )
        SELECT s.who AS who FROM sneaky s
        """
        result = self.check(sql)
        self.assertFalse(result.safe)
        self.assertTrue(any("login" in error for error in result.errors))

    def test_cte_cannot_launder_an_unlisted_table(self) -> None:
        sql = """
        WITH sneaky AS (SELECT p.value AS v FROM ir_config_parameter p)
        SELECT s.v AS v FROM sneaky s
        """
        result = self.check(sql)
        self.assertFalse(result.safe)
        self.assertTrue(any("ir_config_parameter" in error for error in result.errors))

    def test_explicit_cte_column_list_is_honoured(self) -> None:
        sql = """
        WITH monthly(m, amount) AS (
            SELECT date_trunc('month', o.date_order)::date, SUM(o.amount_untaxed)
            FROM sale_order o WHERE o.company_id = 1 GROUP BY 1
        )
        SELECT monthly.m AS month, monthly.amount AS sales_amount FROM monthly
        """
        self.assertTrue(self.check(sql).safe, self.check(sql).errors)

    def test_month_over_month_top_three_from_trace_is_allowed(self) -> None:
        """回归真实失败：CTE 计算出的 mom_change 不是隐藏的物理字段过滤。"""

        plan = QueryPlan.model_validate({
            "query_type": "ranking",
            "metric_ids": ["sales_amount"],
            "dimensions": ["month"],
            "filters": [
                {"field": "company_id", "operator": "eq", "value": 1,
                 "source": "system_required"},
                {"field": "state", "operator": "in", "value": ["sale", "done"],
                 "source": "metric_rule"},
                {"field": "date_order", "operator": "gte", "value": "2026-01-01",
                 "source": "user"},
                {"field": "date_order", "operator": "lt", "value": "2027-01-01",
                 "source": "user"},
            ],
            "time_range": {
                "label": "今年", "start": "2026-01-01", "end": "2026-12-31",
                "grain": "month",
            },
            "result_shape": "ranking",
            "select_columns": ["month", "sales_amount", "mom_change"],
            "sort": [{"field": "mom_change", "direction": "asc"}],
            "row_limit": 3,
        })
        sql = """
        WITH monthly_sales AS (
            SELECT date_trunc('month', so.date_order)::date AS month,
                   SUM(so.amount_untaxed) AS sales_amount
            FROM sale_order AS so
            WHERE so.company_id = 1 AND so.state IN ('sale', 'done')
              AND so.date_order >= '2026-01-01' AND so.date_order < '2027-01-01'
            GROUP BY 1
        ), with_mom AS (
            SELECT month, sales_amount,
                   LAG(sales_amount) OVER (ORDER BY month) AS prev_sales,
                   CASE
                     WHEN LAG(sales_amount) OVER (ORDER BY month) IS NULL
                       OR LAG(sales_amount) OVER (ORDER BY month) = 0 THEN NULL
                     ELSE (sales_amount - LAG(sales_amount) OVER (ORDER BY month))
                          / LAG(sales_amount) OVER (ORDER BY month)
                   END AS mom_change
            FROM monthly_sales
        )
        SELECT month, sales_amount, mom_change
        FROM with_mom
        WHERE mom_change IS NOT NULL
        ORDER BY mom_change ASC
        LIMIT 3
        """

        result = self.guard.validate(
            sql,
            plan=plan,
            question="今年各月销售额，环比降幅最大的三个月是哪几个，为什么",
        )
        self.assertTrue(result.safe, result.errors)
        self.assertEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
