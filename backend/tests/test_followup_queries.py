"""补充查询：安全性与降级行为。

这个特性最容易出的错不是"跑不通"，而是"为了让补充查询能跑通，悄悄放宽了守卫"。
所以这里的重点不是happy path，而是：不安全的补充查询必须被拒，且拒绝之后主答案
仍然完整返回。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock

from app.bi.agent import SalesAgent, _MAX_FOLLOWUP_QUERIES
from app.database.sql_guard import ReadOnlySqlGuard
from app.schemas.query_plan import FollowupQuery, QueryPlan, SqlGenerationPayload


_COLUMNS = {
    "sale_order": ["id", "state", "company_id", "partner_id", "amount_untaxed", "date_order"],
    "res_partner": ["id", "name"],
}


def _plan(**overrides) -> QueryPlan:
    base = {
        "query_type": "ranking",
        "metric_ids": ["sales_amount"],
        "dimensions": ["customer"],
        "filters": [{"field": "company_id", "operator": "eq", "value": 1,
                     "source": "system_required"}],
        "time_range": {"label": None, "start": None, "end": None, "grain": "none"},
        "result_shape": "ranking",
        "select_columns": ["customer", "sales_amount"],
        "sort": [{"field": "sales_amount", "direction": "desc"}],
        "row_limit": None,
    }
    base.update(overrides)
    return QueryPlan.model_validate(base)


class FollowupSchemaTests(unittest.TestCase):
    def test_followups_default_to_empty(self) -> None:
        payload = SqlGenerationPayload.model_validate({"plan": _plan().model_dump(), "sql": "SELECT 1"})
        self.assertEqual(payload.followups, [])

    def test_at_most_two_followups(self) -> None:
        one = {"name": "a", "purpose": "p", "plan": _plan().model_dump(), "sql": "SELECT 1"}
        with self.assertRaises(Exception):
            SqlGenerationPayload.model_validate({
                "plan": _plan().model_dump(), "sql": "SELECT 1",
                "followups": [one, one, one],
            })

    def test_followup_requires_its_own_plan(self) -> None:
        # 没有 plan 就没有契约可校验，等于绕过了契约层。
        with self.assertRaises(Exception):
            FollowupQuery.model_validate({"name": "a", "purpose": "p", "sql": "SELECT 1"})


class FollowupGuardTests(unittest.TestCase):
    """补充查询必须过和主查询一模一样的守卫。"""

    def setUp(self) -> None:
        self.guard = ReadOnlySqlGuard(table_columns=_COLUMNS, company_id=1, max_rows=500)

    def _validate(self, sql: str, plan: QueryPlan | None = None):
        return self.guard.validate(sql, plan=plan or _plan(), question="各客户销售额")

    def test_write_statement_is_rejected(self) -> None:
        self.assertFalse(self._validate("DROP TABLE sale_order").safe)

    def test_unlisted_table_is_rejected(self) -> None:
        result = self.guard.validate(
            "SELECT id AS customer FROM ir_config_parameter WHERE company_id = 1",
            plan=_plan(), question="x",
        )
        self.assertFalse(result.safe)

    def test_missing_company_filter_is_rejected(self) -> None:
        result = self._validate(
            "SELECT p.name AS customer, SUM(o.amount_untaxed) AS sales_amount "
            "FROM sale_order o JOIN res_partner p ON p.id = o.partner_id "
            "GROUP BY p.name ORDER BY sales_amount DESC"
        )
        self.assertFalse(result.safe)
        self.assertTrue(any("company_id" in error for error in result.errors))

    def test_select_star_is_rejected(self) -> None:
        self.assertFalse(self._validate("SELECT * FROM sale_order WHERE company_id = 1").safe)

    def test_presentation_contract_can_be_waived_for_evidence(self) -> None:
        """补充查询不展示，展示层契约不该卡它。

        实测缺陷：主问题里有"三个月"，补充查询要的是那几个月的完整客户构成，
        被"排名查询必须声明 Top N"拒掉；产品构成那条被"产品名必须 COALESCE"拒掉。
        两条都是给用户看的表格才需要的规则。
        """

        sql = (
            "SELECT p.name AS customer, SUM(o.amount_untaxed) AS sales_amount "
            "FROM sale_order o JOIN res_partner p ON p.id = o.partner_id "
            "WHERE o.company_id = 1 AND o.state IN ('sale','done') "
            "GROUP BY p.name ORDER BY sales_amount DESC"
        )
        question = "找出下降最大的三个月的客户构成"

        strict = self.guard.validate(sql, plan=_plan(), question=question)
        self.assertFalse(strict.safe)

        waived = self.guard.validate(
            sql, plan=_plan(), question=question,
            enforce_presentation_contract=False,
        )
        self.assertTrue(waived.safe, waived.errors)

    def test_evidence_may_filter_on_fields_the_purpose_never_names(self) -> None:
        """补充查询的 purpose 不可能列出字段名，不能拿它当授权依据。

        实测缺陷：补充查询按 date_order 圈定那几个月，被判"用户问题没有授权过滤
        字段 date_order"；但"必须在 plan.filters 里声明"这条仍然要守住。
        """

        plan = _plan(
            filters=[
                {"field": "company_id", "operator": "eq", "value": 1,
                 "source": "system_required"},
                {"field": "date_order", "operator": "gte", "value": "2026-03-01",
                 "source": "user"},
            ],
            time_range={"label": "2026年3月", "start": "2026-03-01",
                        "end": "2026-04-01", "grain": "month"},
        )
        sql = (
            "SELECT p.name AS customer, SUM(o.amount_untaxed) AS sales_amount "
            "FROM sale_order o JOIN res_partner p ON p.id = o.partner_id "
            "WHERE o.company_id = 1 AND o.date_order >= '2026-03-01' "
            "GROUP BY p.name ORDER BY sales_amount DESC"
        )
        result = self.guard.validate(
            sql, plan=plan, question="那几个月的客户构成",
            enforce_presentation_contract=False,
        )
        self.assertTrue(result.safe, result.errors)

    def test_undeclared_filter_is_still_rejected_for_evidence(self) -> None:
        """放宽的只是"用户授权"，"必须声明"这条一步不让。"""

        sql = (
            "SELECT p.name AS customer, SUM(o.amount_untaxed) AS sales_amount "
            "FROM sale_order o JOIN res_partner p ON p.id = o.partner_id "
            "WHERE o.company_id = 1 AND o.partner_id = 42 GROUP BY p.name"
        )
        result = self.guard.validate(
            sql, plan=_plan(), question="客户构成",
            enforce_presentation_contract=False,
        )
        self.assertFalse(result.safe)
        self.assertTrue(any("partner_id" in error for error in result.errors))

    def test_filtering_on_an_aggregate_alias_is_allowed(self) -> None:
        """HAVING sales_amount > 0 筛的是算出来的值，不是隐藏的物理列过滤。"""

        sql = (
            "SELECT p.name AS customer, SUM(o.amount_untaxed) AS sales_amount "
            "FROM sale_order o JOIN res_partner p ON p.id = o.partner_id "
            "WHERE o.company_id = 1 GROUP BY p.name"
        )
        result = self.guard.validate(
            sql, plan=_plan(), question="客户构成",
            enforce_presentation_contract=False,
        )
        self.assertTrue(result.safe, result.errors)

    def test_waiving_presentation_does_not_waive_security(self) -> None:
        """最关键的一条：放宽展示契约不能顺带放宽任何安全检查。"""

        for sql, expected in (
            ("SELECT u.id AS a FROM res_users u WHERE u.company_id = 1 AND u.login = 'x'", "login"),
            ("SELECT * FROM sale_order WHERE company_id = 1", "SELECT *"),
            # DROP 在更早的语句类型检查就被拦了，消息是"只允许 SELECT 查询"，
            # 不是后面那条"包含写操作"。
            ("DROP TABLE sale_order", "只允许 SELECT"),
            ("SELECT p.name AS customer FROM sale_order o JOIN res_partner p ON p.id = o.partner_id",
             "company_id"),
        ):
            with self.subTest(sql=sql[:40]):
                result = self.guard.validate(
                    sql, plan=_plan(), question="证据",
                    enforce_presentation_contract=False,
                )
                self.assertFalse(result.safe)
                self.assertTrue(
                    any(expected in error for error in result.errors),
                    f"{expected} 未被拦下：{result.errors}",
                )


class FollowupExecutionTests(unittest.IsolatedAsyncioTestCase):
    """降级行为：补充查询出问题，主答案不能受影响。"""

    def _agent(self, *, guard_safe: bool, execute: AsyncMock | None = None) -> SalesAgent:
        agent = SalesAgent.__new__(SalesAgent)
        validation = Mock(
            safe=guard_safe,
            sql="SELECT 1" if guard_safe else None,
            errors=[] if guard_safe else ["不允许访问数据表：res_users。"],
            tables=["sale_order"],
        )
        agent._guard = Mock(validate=Mock(return_value=validation))
        agent._database = Mock(execute_readonly=execute or AsyncMock(
            return_value=Mock(columns=["customer"], rows=[{"customer": "A"}])
        ))
        agent._database_config = Mock(company_id=1)
        return agent

    def _state(self, count: int = 1) -> dict:
        followup = {
            "name": "客户构成", "purpose": "支撑下降原因",
            "plan": _plan().model_dump(mode="json"), "sql": "SELECT 1",
        }
        return {
            "question": "为什么下降", "warnings": [],
            "followup_queries": [followup] * count,
        }

    async def test_no_followups_is_a_noop(self) -> None:
        agent = self._agent(guard_safe=True)
        self.assertEqual(await agent._run_followup_queries({"followup_queries": []}), {})

    async def test_successful_followup_is_returned(self) -> None:
        agent = self._agent(guard_safe=True)
        result = await agent._run_followup_queries(self._state())
        self.assertEqual(len(result["auxiliary_results"]), 1)
        self.assertEqual(result["auxiliary_results"][0]["name"], "客户构成")
        self.assertEqual(result["auxiliary_results"][0]["row_count"], 1)

    async def test_unsafe_followup_is_skipped_with_a_warning(self) -> None:
        agent = self._agent(guard_safe=False)
        result = await agent._run_followup_queries(self._state())

        # 关键：没有结果，但也没有抛异常——主答案照常返回。
        self.assertEqual(result["auxiliary_results"], [])
        self.assertTrue(any("未执行" in w for w in result["warnings"]))
        # 守卫的具体理由要带出来，否则评测时不知道模型踩了哪类坑。
        self.assertTrue(any("res_users" in w for w in result["warnings"]))

    async def test_database_failure_is_contained(self) -> None:
        agent = self._agent(guard_safe=True, execute=AsyncMock(side_effect=TimeoutError()))
        result = await agent._run_followup_queries(self._state())

        self.assertEqual(result["auxiliary_results"], [])
        self.assertTrue(any("TimeoutError" in w for w in result["warnings"]))

    async def test_contract_is_judged_by_purpose_not_the_main_question(self) -> None:
        """补充查询的 Top-N 判据必须看它自己的用途。

        实测缺陷：主问题是"下降最大的三个月"，补充查询要的是那几个月的完整客户构成，
        结果被要求"排名查询必须声明 Top N"而拒执行。
        """

        agent = self._agent(guard_safe=True)
        state = self._state()
        state["question"] = "找出同比下降最大的三个月"
        state["followup_queries"][0]["purpose"] = "列出这几个月的全部客户销售额构成"

        await agent._run_followup_queries(state)

        _, kwargs = agent._guard.validate.call_args
        self.assertEqual(kwargs["question"], "列出这几个月的全部客户销售额构成")
        self.assertNotIn("三个月", kwargs["question"])

    async def test_followup_count_is_capped(self) -> None:
        agent = self._agent(guard_safe=True)
        # 即使模型硬塞进来更多，执行层也只跑上限那么多次。
        result = await agent._run_followup_queries(self._state(count=5))
        self.assertEqual(len(result["auxiliary_results"]), _MAX_FOLLOWUP_QUERIES)
        self.assertEqual(
            agent._database.execute_readonly.await_count, _MAX_FOLLOWUP_QUERIES
        )


if __name__ == "__main__":
    unittest.main()
