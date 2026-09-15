"""user 来源的过滤条件何时算"用户授权过"。

这条规则防的是模型偷偷加过滤条件让结果变小。原来只看关键词表（问题里要出现
"客户/产品/销售员"），实测缺陷：「莱茵重工的销售额是多少？」整轮失败——直接报
公司名是最自然的问法，却因为没说"客户"两个字被拒。

新增的判据是"过滤值逐字出现在问题里"，它比关键词**更严**：关键词只能证明用户
提到了这个维度，值命中能证明用户说出了这个具体的值。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.database.sql_guard import ReadOnlySqlGuard
from app.schemas.query_plan import QueryPlan


_SEMANTICS = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "bi" / "sales_semantics.json")
    .read_text(encoding="utf-8")
)
_COLUMNS = {name: table["columns"] for name, table in _SEMANTICS["tables"].items()}

_CUSTOMER_SQL = (
    "SELECT SUM(o.amount_untaxed) AS sales_amount FROM sale_order o "
    "JOIN res_partner p ON p.id = o.partner_id "
    "WHERE o.company_id = 1 AND o.state IN ('sale','done') AND p.name = '莱茵重工'"
)


def _plan(field: str, value: object) -> QueryPlan:
    return QueryPlan.model_validate({
        "query_type": "kpi",
        "metric_ids": ["sales_amount"],
        "dimensions": [],
        "filters": [
            {"field": "company_id", "operator": "eq", "value": 1,
             "source": "system_required"},
            {"field": "state", "operator": "in", "value": ["sale", "done"],
             "source": "metric_rule"},
            {"field": field, "operator": "eq", "value": value, "source": "user"},
        ],
        "time_range": {"label": None, "start": None, "end": None, "grain": "none"},
        "result_shape": "scalar",
        "select_columns": ["sales_amount"],
        "sort": [],
        "row_limit": None,
    })


class FilterAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = ReadOnlySqlGuard(
            table_columns=_COLUMNS, company_id=1, max_rows=500
        )

    def check(self, question: str, *, field: str = "name", value: object = "莱茵重工",
              sql: str = _CUSTOMER_SQL):
        return self.guard.validate(sql, plan=_plan(field, value), question=question)

    def test_naming_the_customer_directly_is_authorization(self) -> None:
        # 实测缺陷就是这一条。
        result = self.check("莱茵重工的销售额是多少？")
        self.assertTrue(result.safe, result.errors)

    def test_keyword_route_still_works(self) -> None:
        self.assertTrue(self.check("客户莱茵重工的销售额").safe)

    def test_value_absent_from_the_question_is_still_rejected(self) -> None:
        """模型自己编一个客户名去过滤，必须拒——这正是这条规则存在的理由。"""

        result = self.check("今年销售额是多少？")
        self.assertFalse(result.safe)
        self.assertTrue(any("没有授权过滤字段 name" in e for e in result.errors))

    def test_bare_numbers_never_authorize(self) -> None:
        """问题里的 2025 是年份，不能拿来授权 partner_id = 2025。"""

        sql = (
            "SELECT SUM(o.amount_untaxed) AS sales_amount FROM sale_order o "
            "WHERE o.company_id = 1 AND o.partner_id = 2025"
        )
        result = self.guard.validate(
            sql,
            plan=QueryPlan.model_validate({
                **_plan("partner_id", 2025).model_dump(mode="json"),
                "filters": [
                    {"field": "company_id", "operator": "eq", "value": 1,
                     "source": "system_required"},
                    {"field": "partner_id", "operator": "eq", "value": 2025,
                     "source": "user"},
                ],
            }),
            question="2025 年销售额是多少？",
        )
        self.assertFalse(result.safe)

    def test_single_character_values_never_authorize(self) -> None:
        # 一个字太容易碰巧命中。
        self.assertFalse(self.check("A 公司的销售额", value="A").safe)

    def test_every_value_in_a_list_must_be_mentioned(self) -> None:
        """IN ('甲','乙') 里只要有一个用户没说过，就不算授权。"""

        sql = (
            "SELECT SUM(o.amount_untaxed) AS sales_amount FROM sale_order o "
            "JOIN res_partner p ON p.id = o.partner_id "
            "WHERE o.company_id = 1 AND p.name IN ('莱茵重工', '北海能源')"
        )
        self.assertFalse(
            self.guard.validate(
                sql, plan=_plan("name", ["莱茵重工", "北海能源"]),
                question="莱茵重工的销售额是多少？",
            ).safe
        )
        self.assertTrue(
            self.guard.validate(
                sql, plan=_plan("name", ["莱茵重工", "北海能源"]),
                question="莱茵重工和北海能源的销售额分别是多少？",
            ).safe
        )


if __name__ == "__main__":
    unittest.main()
