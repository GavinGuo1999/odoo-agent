"""CRM 域的守卫规则 R1～R3 与译名回退。

设计依据：`docs/23-m2-crm-multiagent-waza-design.md` §2.4、验收矩阵 P3。

这四条规则挡的都是**不报错的错**：SQL 跑得通、结果有数字、只是口径悄悄变了。
所以每条规则都要有一个"不加规则就会静默通过"的反例，否则测试证明不了什么。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PROVIDER_API_KEY", "test-key")

from app.bi.semantic import SemanticLayer  # noqa: E402
from app.database.sql_guard import ReadOnlySqlGuard  # noqa: E402
from app.schemas.query_plan import QueryPlan  # noqa: E402


CRM_LAYER = SemanticLayer.load("crm")


def _guard() -> ReadOnlySqlGuard:
    return ReadOnlySqlGuard(
        table_columns=CRM_LAYER.table_columns,
        company_id=1,
        max_rows=500,
        profile=CRM_LAYER.sql_profile,
    )


def _plan(**overrides) -> QueryPlan:
    base = {
        "query_type": "kpi",
        "metric_ids": ["crm_won_count"],
        "dimensions": [],
        "filters": [
            {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
            {"field": "won_status", "operator": "eq", "value": "won", "source": "metric_rule"},
        ],
        "time_range": {"label": None, "start": None, "end": None, "grain": "none"},
        "result_shape": "scalar",
        "select_columns": ["crm_won_count"],
        "sort": [],
        "row_limit": None,
    }
    base.update(overrides)
    return QueryPlan.model_validate(base)


class R1ScopeDeclarationTests(unittest.TestCase):
    """R1：指标声明了口径字段，QueryPlan 里就必须真的声明它。

    挡的是"问赢率却不声明 won_status"——算出来的东西不是赢率，但不会报错。
    """

    def setUp(self) -> None:
        self.guard = _guard()

    def test_won_count_without_won_status_is_rejected(self) -> None:
        sql = "SELECT COUNT(l.id) AS crm_won_count FROM crm_lead l WHERE l.company_id = 1"
        plan = _plan(
            filters=[
                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"}
            ]
        )
        result = self.guard.validate(sql, plan=plan, question="赢单数是多少")
        self.assertFalse(result.safe)
        self.assertIn("missing_scope_declaration", result.error_codes)
        self.assertTrue(any("crm_won_count" in error for error in result.errors), result.errors)

    def test_won_count_with_won_status_is_accepted(self) -> None:
        sql = (
            "SELECT COUNT(l.id) AS crm_won_count FROM crm_lead l "
            "WHERE l.company_id = 1 AND l.won_status = 'won'"
        )
        result = self.guard.validate(sql, plan=_plan(), question="赢单数是多少")
        self.assertTrue(result.safe, result.errors)

    def test_opportunity_count_requires_the_type_declaration(self) -> None:
        """线索和商机同表，不声明 type 就说不清在数哪一种。"""
        sql = (
            "SELECT COUNT(l.id) AS crm_opportunity_count FROM crm_lead l WHERE l.company_id = 1"
        )
        plan = _plan(
            metric_ids=["crm_opportunity_count"],
            select_columns=["crm_opportunity_count"],
            filters=[
                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"}
            ],
        )
        result = self.guard.validate(sql, plan=plan, question="有多少商机")
        self.assertFalse(result.safe)
        self.assertIn("missing_scope_declaration", result.error_codes)

    def test_conversion_rate_needs_no_scope_declaration(self) -> None:
        """反向用例：转化率的口径就是"不限 type"，不能被 R1 误伤。

        这正是 [M1 §2.1](../../docs/22-milestone-2026-09.md) 说的那类误伤。
        R1 的必填字段从每个指标自己的 scope 推导，所以不限范围的指标天然不受约束。
        """
        sql = (
            "SELECT COUNT(*) FILTER (WHERE l.date_conversion IS NOT NULL)::numeric "
            "/ NULLIF(COUNT(*), 0) AS crm_conversion_rate "
            "FROM crm_lead l WHERE l.company_id = 1"
        )
        plan = _plan(
            metric_ids=["crm_conversion_rate"],
            select_columns=["crm_conversion_rate"],
            filters=[
                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"}
            ],
        )
        result = self.guard.validate(sql, plan=plan, question="线索转化率是多少")
        self.assertTrue(result.safe, result.errors)

    def test_sales_domain_is_not_subject_to_r1(self) -> None:
        """销售域没有 scope_required_metrics，76 题黄金集的既有行为不能被改。"""
        profile = SemanticLayer.load("sales").sql_profile
        self.assertEqual(dict(profile.scope_required_metrics), {})


class R2ForbiddenActiveFilterTests(unittest.TestCase):
    """R2：`active` 在 CRM 域不得作为过滤条件，声明了也不行。

    输单记录是 `active = false`（crm_lead.py:1119 的 Lost semantic）。
    顺手写 `active = true` 会把全部输单抹掉，赢率变成 100%——不报错、不缺列、
    只是数字错了。这是本域最该被结构挡住的一件事。
    """

    def setUp(self) -> None:
        self.guard = _guard()

    def test_undeclared_active_filter_is_rejected(self) -> None:
        sql = (
            "SELECT COUNT(l.id) AS crm_won_count FROM crm_lead l "
            "WHERE l.company_id = 1 AND l.won_status = 'won' AND l.active = true"
        )
        result = self.guard.validate(sql, plan=_plan(), question="赢单数是多少")
        self.assertFalse(result.safe)
        self.assertIn("filter_undeclared", result.error_codes)

    def test_declared_active_filter_is_still_rejected(self) -> None:
        """关键的一条：R2 不是"需要授权"，是"这个域里用它就是错的"。

        如果只靠"未声明的过滤字段"那条，模型只要把 active 写进 plan.filters
        就放行了，而修复节点恰恰最擅长干这个——它读到"未声明"就会去补声明。
        """
        sql = (
            "SELECT COUNT(l.id) AS crm_won_count FROM crm_lead l "
            "WHERE l.company_id = 1 AND l.won_status = 'won' AND l.active = true"
        )
        plan = _plan(
            filters=[
                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                {"field": "won_status", "operator": "eq", "value": "won", "source": "metric_rule"},
                {"field": "active", "operator": "eq", "value": True, "source": "metric_rule"},
            ]
        )
        result = self.guard.validate(sql, plan=plan, question="赢单数是多少")
        self.assertFalse(result.safe)
        self.assertIn("forbidden_scope_filter", result.error_codes)
        self.assertTrue(any("active" in error for error in result.errors), result.errors)

    def test_the_rejection_survives_the_evidence_waiver(self) -> None:
        """补充查询放宽的是展示层契约，不该顺带放宽 R2。"""
        sql = (
            "SELECT COUNT(l.id) AS crm_won_count FROM crm_lead l "
            "WHERE l.company_id = 1 AND l.won_status = 'won' AND l.active = true"
        )
        plan = _plan(
            filters=[
                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                {"field": "won_status", "operator": "eq", "value": "won", "source": "metric_rule"},
                {"field": "active", "operator": "eq", "value": True, "source": "metric_rule"},
            ]
        )
        result = self.guard.validate(
            sql, plan=plan, question="赢单证据", enforce_presentation_contract=False
        )
        self.assertFalse(result.safe)
        self.assertIn("forbidden_scope_filter", result.error_codes)

    def test_sales_domain_may_still_filter_on_active(self) -> None:
        """销售域的 forbidden 列表是空的——R2 不能外溢到别的域。"""
        self.assertEqual(SemanticLayer.load("sales").sql_profile.forbidden_filter_fields, frozenset())


class R3TimeFieldTests(unittest.TestCase):
    """R3：声明了时间范围之后，WHERE 里允许出现的时间列按域给定。

    销售域写死 `date_order`，CRM 的三个时间列会全部被判成"未声明的过滤字段"。
    """

    def setUp(self) -> None:
        self.guard = _guard()

    def test_crm_time_columns_are_allowed_when_a_range_is_declared(self) -> None:
        for column in ("create_date", "date_closed", "date_deadline"):
            with self.subTest(column=column):
                sql = (
                    f"SELECT COUNT(l.id) AS crm_won_count FROM crm_lead l "
                    f"WHERE l.company_id = 1 AND l.won_status = 'won' "
                    f"AND l.{column} >= '2026-01-01' AND l.{column} < '2027-01-01'"
                )
                plan = _plan(
                    time_range={
                        "label": "2026年",
                        "start": "2026-01-01",
                        "end": "2027-01-01",
                        "grain": "year",
                    }
                )
                result = self.guard.validate(sql, plan=plan, question="2026年赢单数")
                self.assertTrue(result.safe, result.errors)

    def test_sales_time_column_is_not_allowed_in_the_crm_domain(self) -> None:
        """`date_order` 不在 CRM 的时间列里，而且 sale_order 也不在白名单里。"""
        self.assertNotIn("date_order", CRM_LAYER.sql_profile.time_fields)

    def test_time_columns_still_need_a_declared_range(self) -> None:
        """没声明时间范围就用时间列过滤，仍然要拒——R3 放宽的是列名，不是"要声明"。"""
        sql = (
            "SELECT COUNT(l.id) AS crm_won_count FROM crm_lead l "
            "WHERE l.company_id = 1 AND l.won_status = 'won' AND l.date_closed >= '2026-01-01'"
        )
        result = self.guard.validate(sql, plan=_plan(), question="赢单数")
        self.assertFalse(result.safe)
        self.assertIn("filter_undeclared", result.error_codes)


class TranslatedNameFallbackTests(unittest.TestCase):
    """译名回退：CRM 的阶段/输单原因/团队/标签和销售的产品名同为 JSONB 译名列。"""

    def setUp(self) -> None:
        self.guard = _guard()

    def _stage_plan(self) -> QueryPlan:
        return _plan(
            query_type="ranking",
            metric_ids=["crm_opportunity_count"],
            dimensions=["stage"],
            select_columns=["stage", "crm_opportunity_count"],
            sort=[{"field": "crm_opportunity_count", "direction": "desc"}],
            filters=[
                {"field": "company_id", "operator": "eq", "value": 1, "source": "system_required"},
                {"field": "type", "operator": "eq", "value": "opportunity", "source": "metric_rule"},
            ],
        )

    def test_stage_name_without_locale_fallback_is_rejected(self) -> None:
        sql = (
            "SELECT s.name AS stage, COUNT(l.id) AS crm_opportunity_count "
            "FROM crm_lead l JOIN crm_stage s ON s.id = l.stage_id "
            "WHERE l.company_id = 1 AND l.type = 'opportunity' "
            "GROUP BY s.name ORDER BY crm_opportunity_count DESC"
        )
        result = self.guard.validate(sql, plan=self._stage_plan(), question="各阶段商机数")
        self.assertFalse(result.safe)
        self.assertIn("translated_name_locale", result.error_codes)

    def test_stage_name_with_locale_fallback_is_accepted(self) -> None:
        sql = (
            "SELECT COALESCE(s.name->>'zh_CN', s.name->>'en_US') AS stage, "
            "COUNT(l.id) AS crm_opportunity_count "
            "FROM crm_lead l JOIN crm_stage s ON s.id = l.stage_id "
            "WHERE l.company_id = 1 AND l.type = 'opportunity' "
            "GROUP BY s.name ORDER BY crm_opportunity_count DESC"
        )
        result = self.guard.validate(sql, plan=self._stage_plan(), question="各阶段商机数")
        self.assertTrue(result.safe, result.errors)

    def test_every_translated_dimension_is_registered(self) -> None:
        self.assertEqual(
            CRM_LAYER.sql_profile.translated_name_dimensions,
            frozenset({"stage", "lost_reason", "team", "tag"}),
        )

    def test_sales_product_contract_is_unchanged(self) -> None:
        """M1 的产品名契约必须原样保留，消息文案也不能变——错误码表依赖它。"""
        self.assertEqual(
            SemanticLayer.load("sales").sql_profile.translated_name_dimensions,
            frozenset({"product"}),
        )


class CrmSecurityBoundaryTests(unittest.TestCase):
    """新规则不能顺带放宽任何 M1 的安全检查。"""

    def setUp(self) -> None:
        self.guard = _guard()

    def test_core_boundaries_still_hold_in_the_crm_domain(self) -> None:
        for sql, expected_code in (
            ("DROP TABLE crm_lead", "not_a_select"),
            ("SELECT * FROM crm_lead WHERE company_id = 1", "select_star"),
            ("SELECT COUNT(id) AS c FROM crm_lead", "missing_company_filter"),
            ("SELECT id AS c FROM ir_config_parameter WHERE company_id = 1", "unlisted_table"),
            (
                "SELECT l.email_from AS c FROM crm_lead l WHERE l.company_id = 1",
                "unlisted_column",
            ),
        ):
            with self.subTest(sql=sql[:44]):
                result = self.guard.validate(sql)
                self.assertFalse(result.safe)
                self.assertIn(expected_code, result.error_codes)


if __name__ == "__main__":
    unittest.main()
