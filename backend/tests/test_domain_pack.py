"""域包重构的等价性与隔离契约。

设计依据：`docs/23-m2-crm-multiagent-waza-design.md` §2.5 / §3.3、验收矩阵 P2。

P2 是"改很多、行为不能变"的一步，所以这里的重点不是域包好不好用，而是
**它和 M1 直接构造出来的那一套完全等价**。等价性一旦证明，后面加 CRM 包
才谈得上可归因——否则 P3 出问题时分不清是新域的毛病还是重构漏了东西。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PROVIDER_API_KEY", "test-key")

from app.bi.domain import (  # noqa: E402
    CRM_DOMAIN,
    DEFAULT_DOMAIN,
    DomainPack,
    DomainRegistry,
    UnknownDomainError,
    build_crm_pack,
    build_domain_registry,
    build_sales_pack,
    classify_domain,
    single_domain_registry,
)
from app.bi.semantic import SalesSemanticLayer  # noqa: E402
from app.bi.semantic_provider import build_semantic_provider  # noqa: E402
from app.config import DatabaseConfig  # noqa: E402
from app.database.sql_guard import ReadOnlySqlGuard  # noqa: E402


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


class SalesPackEquivalenceTests(unittest.TestCase):
    """销售域包必须和 M1 的 `SalesAgent.__init__` 那两步产出同样的东西。"""

    def setUp(self) -> None:
        self.database = _database_config()
        self.pack = build_sales_pack(database=self.database)

    def test_sales_pack_matches_m1_semantics(self) -> None:
        expected = build_semantic_provider(None)
        self.assertEqual(self.pack.semantics.name, expected.name)
        self.assertEqual(self.pack.semantics.version, expected.version)
        self.assertEqual(self.pack.table_columns, expected.table_columns)

    def test_sales_pack_whitelist_matches_the_semantic_layer(self) -> None:
        """守卫白名单必须严格等于语义层开放的表和字段。

        这条是安全边界的根：白名单如果比语义层宽，模型就能碰到语义层没声明过的东西。
        """
        layer = SalesSemanticLayer.load()
        self.assertEqual(set(self.pack.table_columns), layer.allowed_tables)
        self.assertEqual(self.pack.table_columns, layer.table_columns)

    def test_sales_pack_guard_enforces_the_same_boundaries(self) -> None:
        reference = ReadOnlySqlGuard(
            table_columns=SalesSemanticLayer.load().table_columns,
            company_id=self.database.company_id,
            max_rows=self.database.max_rows,
        )
        cases = (
            "DROP TABLE sale_order",
            "SELECT * FROM sale_order WHERE company_id = 1",
            "SELECT id FROM ir_config_parameter WHERE company_id = 1",
            "SELECT SUM(amount_untaxed) AS sales_amount FROM sale_order",
        )
        for sql in cases:
            with self.subTest(sql=sql[:40]):
                self.assertEqual(
                    self.pack.guard.validate(sql).safe,
                    reference.validate(sql).safe,
                )

    def test_registry_registers_sales_and_crm(self) -> None:
        """P3 结束时应有销售与 CRM 两个域，且默认域仍是销售。

        默认域必须保持销售：M1 的 76 题黄金集里大量问题不含任何域关键词
        （"哪个客户买得最多"），它们全靠回落到默认域才维持原行为。
        跨域包是 P4 的事，这里出现说明阶段混了。
        """
        registry = build_domain_registry(database=self.database)
        self.assertEqual(registry.domains, [CRM_DOMAIN, DEFAULT_DOMAIN])
        self.assertEqual(registry.default.domain, DEFAULT_DOMAIN)


class DomainIsolationTests(unittest.TestCase):
    """跨域隔离：每个域一份守卫，白名单不共享。"""

    def _pack(self, domain: str, tables: dict[str, list[str]]) -> DomainPack:
        return DomainPack(
            domain=domain,
            semantics=Mock(name=domain, version="t", table_columns=tables),
            guard=ReadOnlySqlGuard(table_columns=tables, company_id=1, max_rows=500),
        )

    def setUp(self) -> None:
        self.sales = self._pack(
            "sales",
            {"sale_order": ["id", "company_id", "amount_untaxed", "date_order"]},
        )
        self.crm = self._pack(
            "crm",
            {"crm_lead": ["id", "company_id", "expected_revenue", "won_status"]},
        )
        self.registry = DomainRegistry([self.sales, self.crm], default_domain="sales")

    def test_a_domain_guard_rejects_another_domains_table(self) -> None:
        """这是多域架构里最该被结构挡住的一件事。

        CRM 的守卫里根本没有 `sale_order` 这张表，所以不是"提示词让它别查"，
        而是查了就被拒。反向同理。
        """
        crm_reaching_sales = (
            "SELECT SUM(amount_untaxed) AS sales_amount FROM sale_order WHERE company_id = 1"
        )
        sales_reaching_crm = (
            "SELECT SUM(expected_revenue) AS revenue FROM crm_lead WHERE company_id = 1"
        )
        self.assertFalse(self.registry.require("crm").guard.validate(crm_reaching_sales).safe)
        self.assertFalse(self.registry.require("sales").guard.validate(sales_reaching_crm).safe)

    def test_each_pack_has_its_own_guard_instance(self) -> None:
        self.assertIsNot(self.sales.guard, self.crm.guard)

    def test_unknown_domain_falls_back_to_default(self) -> None:
        """域路由是确定性关键词判定，会认错；认错时给默认域的答案比整轮 500 更可用。"""
        for domain in (None, "", "purchase", "inventory"):
            with self.subTest(domain=domain):
                self.assertEqual(self.registry.pack(domain).domain, "sales")

    def test_require_is_strict(self) -> None:
        """审计和测试要的是严格语义，不能被回落掩盖。"""
        with self.assertRaises(UnknownDomainError):
            self.registry.require("purchase")

    def test_registry_rejects_an_unregistered_default(self) -> None:
        with self.assertRaises(UnknownDomainError):
            DomainRegistry([self.crm], default_domain="sales")

    def test_registry_rejects_an_empty_pack_list(self) -> None:
        with self.assertRaises(ValueError):
            DomainRegistry([])


class SingleDomainRegistryTests(unittest.TestCase):
    def test_single_domain_registry_does_not_read_configuration(self) -> None:
        """它必须能在不碰数据库、不起 Wren 的情况下装配出来，否则单测会被外部依赖污染。"""
        semantics = Mock(name="native", version="t", table_columns={"t": ["id"]})
        guard = Mock()
        registry = single_domain_registry(semantics=semantics, guard=guard)
        self.assertEqual(registry.domains, [DEFAULT_DOMAIN])
        self.assertIs(registry.default.semantics, semantics)
        self.assertIs(registry.default.guard, guard)


if __name__ == "__main__":
    unittest.main()
