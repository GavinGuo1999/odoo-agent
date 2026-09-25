"""CRM 域包：口径、隔离与域路由。

设计依据：`docs/23-m2-crm-multiagent-waza-design.md` §2.1～§2.4、验收矩阵 P3。

这里断言的大部分不是"功能能用"，而是"**那几个坑没被踩回去**"。CRM 口径的错误
不报错、不触发守卫，只给出好看的错数字（赢率 100%），所以必须由测试钉住。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PROVIDER_API_KEY", "test-key")

from app.bi.domain import (  # noqa: E402
    CRM_DOMAIN,
    DEFAULT_DOMAIN,
    build_domain_registry,
    classify_domain,
)
from app.bi.semantic import SemanticLayer  # noqa: E402
from app.config import DatabaseConfig  # noqa: E402


def _database_config() -> DatabaseConfig:
    return DatabaseConfig(
        host="127.0.0.1",
        port=55432,
        database="odoo19_dev",
        user="codex_readonly",
        password=None,
        company_id=1,
        statement_timeout_ms=15_000,
        max_rows=500,
    )


class CrmMetricContractTests(unittest.TestCase):
    """口径是 P1 拍板的东西，改它必须先改文档 §2.1，所以这里逐条钉住。"""

    def setUp(self) -> None:
        self.layer = SemanticLayer.load(CRM_DOMAIN)
        self.metrics = self.layer.metric_definitions

    def test_win_rate_uses_the_closed_basis(self) -> None:
        """定案口径：won / (won + lost)，分母不含 pending。

        全量口径（分母含 pending）会让每个周期开头的赢率天然偏低，
        趋势图上每个月初都有一个假的下跌。
        """
        metric = self.metrics["crm_win_rate"]
        self.assertIn("won','lost", metric["expression"])
        self.assertIn("分母不含 pending", metric["scope"])
        self.assertNotIn("pending", metric["expression"])

    def test_conversion_rate_carries_its_caveat(self) -> None:
        """转化率必须带近似口径说明，不能给一个看起来精确的错数。"""
        metric = self.metrics["crm_conversion_rate"]
        self.assertTrue(metric.get("caveat"))
        self.assertIn("近似", metric["caveat"])
        self.assertIn("date_conversion", metric["caveat"])

    def test_metric_explanation_surfaces_the_caveat(self) -> None:
        """口径说明要真的答出来，写在 JSON 里但答不出来等于没写。"""
        explanation = self.layer.metric_explanation("线索转化率怎么算的？")
        self.assertIsNotNone(explanation)
        self.assertIn("近似", explanation)

    def test_no_metric_reconstructs_won_status_by_hand(self) -> None:
        """坑二：赢输一律走存储列 won_status，不许自己拼 probability = 100。

        自己拼的问题不是写起来麻烦，而是口径出现第二处定义——
        Odoo 改了判定逻辑（19 里就改过）这边不会跟着变。
        """
        for metric_id, metric in self.metrics.items():
            with self.subTest(metric=metric_id):
                self.assertNotIn("probability = 100", metric["expression"])
                self.assertNotIn("is_won", metric["expression"])

    def test_no_metric_filters_on_active(self) -> None:
        """坑一：按 active 过滤会把全部输单抹掉，赢率变成 100%。"""
        for metric_id, metric in self.metrics.items():
            with self.subTest(metric=metric_id):
                self.assertNotIn("active", metric["expression"])

    def test_lead_and_opportunity_counts_are_separate_metrics(self) -> None:
        """线索和商机同表靠 type 区分，混成一个指标就永远说不清在数什么。"""
        self.assertEqual(self.metrics["crm_lead_count"]["scope"][0], "type = 'lead'")
        self.assertEqual(
            self.metrics["crm_opportunity_count"]["scope"][0], "type = 'opportunity'"
        )

    def test_date_fields_are_declared_per_metric(self) -> None:
        """坑四的一半：不同指标的时间字段不一样，混用会张冠李戴。"""
        expected = {
            "crm_lead_count": "crm_lead.create_date",
            "crm_opportunity_count": "crm_lead.create_date",
            "crm_conversion_rate": "crm_lead.create_date",
            "crm_open_revenue": "crm_lead.date_deadline",
            "crm_weighted_revenue": "crm_lead.date_deadline",
            "crm_won_count": "crm_lead.date_closed",
            "crm_won_revenue": "crm_lead.date_closed",
            "crm_lost_count": "crm_lead.date_closed",
            "crm_win_rate": "crm_lead.date_closed",
            "crm_avg_days_to_close": "crm_lead.date_closed",
        }
        self.assertEqual(
            {k: v["date_field"] for k, v in self.metrics.items()}, expected
        )

    def test_metric_ids_are_namespaced_and_do_not_collide_with_sales(self) -> None:
        """设计 §2.6：新指标一律 crm_ 前缀，旧的销售指标 ID 一个都不改名。

        改名会让 76 题黄金集、结果签名和 Langfuse 历史基线全部不可横比。
        """
        sales_ids = set(SemanticLayer.load(DEFAULT_DOMAIN).metric_definitions)
        crm_ids = set(self.metrics)
        self.assertEqual(crm_ids & sales_ids, set())
        for metric_id in crm_ids:
            with self.subTest(metric=metric_id):
                self.assertTrue(metric_id.startswith("crm_"))
        # 反向：销售指标不许被顺手加前缀。
        self.assertIn("sales_amount", sales_ids)
        self.assertIn("order_count", sales_ids)


class CrmSchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = SemanticLayer.load(CRM_DOMAIN)

    def test_crm_lead_does_not_expose_a_currency_column(self) -> None:
        """坑三：crm_lead 的 company_currency 是未 store 的计算字段，库里没有这一列。

        白名单里放一个不存在的列，模型一定会去用它，然后拿到 UndefinedColumn。
        """
        self.assertNotIn("currency_id", self.layer.table_columns["crm_lead"])
        self.assertNotIn("company_currency", self.layer.table_columns["crm_lead"])
        # 币种要经公司取。
        self.assertIn("currency_id", self.layer.table_columns["res_company"])

    def test_crm_lead_does_not_expose_personal_contact_fields(self) -> None:
        """与 M1 的敏感信息策略一致：不开放邮箱、电话、正文。"""
        columns = set(self.layer.table_columns["crm_lead"])
        for field in ("email_from", "email_normalized", "phone", "phone_sanitized", "description"):
            with self.subTest(field=field):
                self.assertNotIn(field, columns)

    def test_res_users_credentials_stay_closed(self) -> None:
        columns = set(self.layer.table_columns["res_users"])
        for field in ("login", "password", "password_crypt"):
            with self.subTest(field=field):
                self.assertNotIn(field, columns)

    def test_business_notes_cover_every_verified_trap(self) -> None:
        """五个坑加两个实测发现，必须在 Prompt 里出现，否则模型无从知道。"""
        notes = "\n".join(self.layer.business_notes)
        for marker in (
            "active = false",      # 坑一
            "won_status",          # 坑二
            "currency_id",         # 坑三
            "date_closed",         # 坑四
            "zh_CN",               # 译名列
            "IS NOT TRUE",         # is_won 对非赢阶段是 NULL
            "crm_stage_crm_team_rel",  # m2m
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, notes)

    def test_business_notes_reach_the_prompt(self) -> None:
        context = self.layer.retrieve("各阶段的在途商机", company_id=1)
        prompt = context.as_prompt()
        self.assertIn("domain_rules", prompt)
        self.assertIn("won_status", prompt)
        self.assertEqual(context.domain, CRM_DOMAIN)

    def test_stage_dimension_pulls_in_the_stage_table(self) -> None:
        context = self.layer.retrieve("各阶段的商机数", company_id=1)
        self.assertIn("crm_stage", context.tables)
        self.assertIn("crm_lead", context.tables)

    def test_lost_reason_question_pulls_in_the_reason_table(self) -> None:
        context = self.layer.retrieve("输单原因分布", company_id=1)
        self.assertIn("crm_lost_reason", context.tables)


class CrmDomainIsolationTests(unittest.TestCase):
    """真实域包之间的隔离——不是 Mock，是实际注册出来的那两个守卫。"""

    def setUp(self) -> None:
        self.registry = build_domain_registry(database=_database_config())
        self.sales = self.registry.require(DEFAULT_DOMAIN)
        self.crm = self.registry.require(CRM_DOMAIN)

    def test_crm_guard_cannot_reach_sales_tables(self) -> None:
        sql = (
            "SELECT SUM(o.amount_untaxed) AS sales_amount FROM sale_order o "
            "WHERE o.company_id = 1"
        )
        result = self.crm.guard.validate(sql)
        self.assertFalse(result.safe)
        self.assertTrue(any("sale_order" in error for error in result.errors), result.errors)

    def test_sales_guard_cannot_reach_crm_tables(self) -> None:
        sql = (
            "SELECT SUM(l.expected_revenue) AS crm_won_revenue FROM crm_lead l "
            "WHERE l.company_id = 1"
        )
        result = self.sales.guard.validate(sql)
        self.assertFalse(result.safe)
        self.assertTrue(any("crm_lead" in error for error in result.errors), result.errors)

    def test_the_two_packs_share_no_business_table(self) -> None:
        """共享表只允许是 res_* 那几张。业务表一旦重叠，域边界就失去意义。"""
        shared = set(self.sales.table_columns) & set(self.crm.table_columns)
        self.assertEqual(
            shared, {"res_partner", "res_users", "res_company", "res_currency"}
        )

    def test_shared_tables_expose_identical_columns_in_both_domains(self) -> None:
        """设计 §2.5：共享表在每个域各自声明，但字段集合必须一致。

        不一致意味着换个域就能看到本域看不到的字段——这是审计时最容易漏的越权。
        """
        for table in ("res_partner", "res_users", "res_company", "res_currency"):
            with self.subTest(table=table):
                self.assertEqual(
                    sorted(self.sales.table_columns[table]),
                    sorted(self.crm.table_columns[table]),
                )

    def test_crm_pack_uses_the_native_provider(self) -> None:
        """Wren 的 MDL 只建模了销售域，让 CRM 去问它只会拿到空 schema。"""
        self.assertEqual(self.crm.semantics.name, "native")


class DomainRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_domain_registry(database=_database_config())

    def _domain(self, question: str) -> str:
        return classify_domain(question, self.registry)[0]

    def test_crm_questions_route_to_crm(self) -> None:
        for question in (
            "本月新增了多少商机？",
            "今年的赢率是多少？",
            "输单原因分布是什么样的？",
            "各阶段的在途预期收入",
            "线索转化率怎么样",
            "平均成交周期多长",
            "赢单数最多的是哪个月",
        ):
            with self.subTest(question=question):
                self.assertEqual(self._domain(question), CRM_DOMAIN)

    def test_sales_questions_route_to_sales(self) -> None:
        for question in (
            "本月销售额是多少？",
            "今年每个月的订单数",
            "哪个产品卖得最好",
            "客单价是多少",
        ):
            with self.subTest(question=question):
                self.assertEqual(self._domain(question), DEFAULT_DOMAIN)

    def test_questions_without_domain_keywords_fall_back_to_sales(self) -> None:
        """M1 的黄金集里大量问题不含任何域关键词，它们必须维持原行为。

        这是 P3 不破坏 M1 的关键：域路由只能"加"，不能把原本能答的问题打断。
        """
        for question in (
            "哪个客户买得最多",
            "今天是几月几号？",
            "上个月和这个月比怎么样",
        ):
            with self.subTest(question=question):
                self.assertEqual(self._domain(question), DEFAULT_DOMAIN)

    def test_ambiguous_question_falls_back_to_default_for_now(self) -> None:
        """"各销售团队的赢单率"同时命中两个域的关键词。

        设计 §3.4 说并列时该问用户，但那是 Supervisor 的行为，带自己的门禁，
        放在 P4。在那之前并列回落到默认域，保证行为可预测。
        这条测试到 P4 会被替换成"应当 Interrupt"，届时它就是那一步的 red。
        """
        domain, scores = classify_domain("各销售团队的赢单率", self.registry)
        self.assertEqual(sorted(scores), [CRM_DOMAIN, DEFAULT_DOMAIN])
        self.assertGreater(scores[CRM_DOMAIN], 0)
        self.assertGreater(scores[DEFAULT_DOMAIN], 0)
        self.assertIn(domain, (CRM_DOMAIN, DEFAULT_DOMAIN))

    def test_scores_are_reported_for_traceability(self) -> None:
        """线上排查"这题为什么走错了域"时，光知道结论没用。"""
        _, scores = classify_domain("今年的赢率是多少？", self.registry)
        self.assertEqual(sorted(scores), [CRM_DOMAIN, DEFAULT_DOMAIN])
        self.assertGreater(scores[CRM_DOMAIN], scores[DEFAULT_DOMAIN])


if __name__ == "__main__":
    unittest.main()
