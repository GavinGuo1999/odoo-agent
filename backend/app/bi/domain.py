"""业务域包（DomainPack）。

设计依据：`docs/23-m2-crm-multiagent-waza-design.md` §2.5 / §3.3。

M1 的 Text2SQL 链路（检索语义 → 生成 QueryPlan+SQL → 编译 → 守卫 → 执行 → 画像 →
补充查询 → 展示）与"销售"这件事其实无关。它只依赖四样东西：语义上下文、SQL 守卫、
Prompt、验证过的示例。把这四样打成一个包注入，子图就是域无关的——之后加 CRM、采购、
库存都是"加一个包"，不是"加一条链路"。

这里刻意**不**做的事：

- 不把多个域的表合成一个白名单。每个域一份独立的 `ReadOnlySqlGuard`，
  所以 CRM 查询在结构上碰不到 `sale_order_line`——这是
  [M1 §2.2](../../../docs/22-milestone-2026-09.md)"安全边界靠结构保证，不靠提示词"
  的直接延伸。合并白名单等于开一个万能后门。
- 不做域之间的继承。共享表（res_partner / res_users / res_company / res_currency）
  在每个域包里各自声明。重复的代价是一次性的；继承的代价是"CRM 到底能看客户的哪些
  字段"这个问题的答案分散在两个文件里，而这正是审计时最需要一眼看清的东西。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.bi.semantic import SemanticLayer
from app.bi.semantic_provider import (
    NativeSemanticProvider,
    SemanticContextProvider,
    build_semantic_provider,
)
from app.config import DatabaseConfig, SemanticConfig
from app.database.sql_guard import ReadOnlySqlGuard


DEFAULT_DOMAIN = "sales"
CRM_DOMAIN = "crm"


class UnknownDomainError(KeyError):
    """请求了一个没有注册的业务域。"""


@dataclass(frozen=True, slots=True)
class DomainPack:
    """一个业务域运行 Text2SQL 所需的全部东西。

    `guard` 是**这个域自己的**实例，不是全局共享的。守卫的白名单来自
    `semantics.table_columns`，所以域包一旦确定，可触达的表和字段就确定了。
    """

    domain: str
    semantics: SemanticContextProvider
    guard: ReadOnlySqlGuard
    routing_keywords: tuple[str, ...] = ()

    @property
    def table_columns(self) -> dict[str, list[str]]:
        return self.semantics.table_columns


class DomainRegistry:
    """已注册的业务域。

    `pack()` 对未知域**回落到默认域**而不是抛异常：域路由是确定性关键词判定
    （见设计 §3.4），它会认错；认错时给出销售域的答案比让整轮 500 更可用。
    真正需要严格的地方是守卫，而守卫在每个包里各自把关。
    """

    def __init__(
        self,
        packs: Iterable[DomainPack],
        *,
        default_domain: str = DEFAULT_DOMAIN,
    ) -> None:
        self._packs = {pack.domain: pack for pack in packs}
        if not self._packs:
            raise ValueError("DomainRegistry requires at least one domain pack.")
        if default_domain not in self._packs:
            raise UnknownDomainError(
                f"default domain {default_domain!r} is not registered; "
                f"registered: {sorted(self._packs)}"
            )
        self._default_domain = default_domain

    @property
    def domains(self) -> list[str]:
        return sorted(self._packs)

    @property
    def packs(self) -> list[DomainPack]:
        return [self._packs[domain] for domain in self.domains]

    @property
    def default(self) -> DomainPack:
        return self._packs[self._default_domain]

    def pack(self, domain: str | None) -> DomainPack:
        if not domain:
            return self.default
        return self._packs.get(domain, self.default)

    def require(self, domain: str) -> DomainPack:
        """严格取包，未注册就抛异常。给测试和审计用，不给请求路径用。"""
        try:
            return self._packs[domain]
        except KeyError as exc:
            raise UnknownDomainError(
                f"domain {domain!r} is not registered; registered: {sorted(self._packs)}"
            ) from exc


def classify_domain(question: str, registry: DomainRegistry) -> tuple[str, dict[str, int]]:
    """确定性域路由：数每个域的路由关键词命中数，取最高。

    不调模型，理由和 `classify_intent` 一样（见
    [M1 §2.3](../../../docs/22-milestone-2026-09.md)"确定性优先"）：可测、可回归、
    改一个词修一个 case。代价是脆。

    **零命中和并列都回落到默认域**，这一点是刻意的：M1 的 76 题黄金集里有大量
    不含任何域关键词的问题（"哪个客户买得最多"），严格路由会把它们全部打断。
    设计 §3.4 里"域完全无命中就问用户"是 Supervisor 的行为，带自己的门禁，
    放在 P4；在那之前，回落保证 M1 的行为一个字不变。

    返回 (域名, 各域命中数)。命中数要进 trace——线上排查"这题为什么走错了域"时，
    光知道结论没用，得知道是哪个词把它带过去的。
    """
    normalized = question.lower()
    scores = {
        pack.domain: sum(
            1 for keyword in pack.routing_keywords if keyword.lower() in normalized
        )
        for pack in registry.packs
    }
    best = max(scores.values(), default=0)
    if best == 0:
        return registry.default.domain, scores
    winners = sorted(domain for domain, score in scores.items() if score == best)
    if len(winners) > 1:
        return registry.default.domain, scores
    return winners[0], scores


def single_domain_registry(
    *,
    semantics: SemanticContextProvider,
    guard: ReadOnlySqlGuard,
    domain: str = DEFAULT_DOMAIN,
) -> DomainRegistry:
    """用现成的语义 provider 和守卫组一个单域注册表。

    给两类调用者：需要显式控制这两样东西的测试，以及 P4 里按域分别装配 analyst 的
    编排代码。它不读配置，所以不会在单测里意外去连数据库或起 Wren。
    """
    return DomainRegistry(
        [DomainPack(domain=domain, semantics=semantics, guard=guard)],
        default_domain=domain,
    )


def build_sales_pack(
    *,
    database: DatabaseConfig,
    semantic_config: SemanticConfig | None = None,
    semantic_provider: SemanticContextProvider | None = None,
) -> DomainPack:
    """销售域包。

    语义 provider 仍然由 `build_semantic_provider` 决定（native 或 Wren），
    守卫的白名单仍然取自 provider 的 `table_columns`——和 M1 里 `SalesAgent.__init__`
    直接做的那两步完全一样，只是换了个地方放。
    """
    layer = SemanticLayer.load(DEFAULT_DOMAIN)
    semantics = semantic_provider or build_semantic_provider(semantic_config)
    guard = ReadOnlySqlGuard(
        table_columns=semantics.table_columns,
        company_id=database.company_id,
        max_rows=database.max_rows,
        profile=layer.sql_profile,
    )
    return DomainPack(
        domain=DEFAULT_DOMAIN,
        semantics=semantics,
        guard=guard,
        routing_keywords=tuple(layer.routing_keywords),
    )


def build_crm_pack(*, database: DatabaseConfig) -> DomainPack:
    """CRM 域包。

    这里**不**走 `build_semantic_provider`：Wren MDL 项目当前只建模了销售域，
    让 CRM 去问 Wren 只会拿到一个空 schema。CRM 固定用 native provider，
    等 MDL 覆盖到 CRM 再说。

    守卫是**独立实例**，白名单只有 CRM 域声明的那 14 张表——所以 CRM 查询
    在结构上碰不到 `sale_order_line`，反之亦然。
    """
    layer = SemanticLayer.load(CRM_DOMAIN)
    semantics = NativeSemanticProvider(layer)
    guard = ReadOnlySqlGuard(
        table_columns=semantics.table_columns,
        company_id=database.company_id,
        max_rows=database.max_rows,
        profile=layer.sql_profile,
    )
    return DomainPack(
        domain=CRM_DOMAIN,
        semantics=semantics,
        guard=guard,
        routing_keywords=tuple(layer.routing_keywords),
    )


def build_domain_registry(
    *,
    database: DatabaseConfig,
    semantic_config: SemanticConfig | None = None,
    semantic_provider: SemanticContextProvider | None = None,
) -> DomainRegistry:
    """注册销售域与 CRM 域。

    P4 在这里追加跨域包。注册表是唯一需要改的地方——这就是域包重构要买到的东西：
    加一个业务域不需要动 LangGraph 的任何节点或边。
    """
    return DomainRegistry(
        [
            build_sales_pack(
                database=database,
                semantic_config=semantic_config,
                semantic_provider=semantic_provider,
            ),
            build_crm_pack(database=database),
        ],
        default_domain=DEFAULT_DOMAIN,
    )
