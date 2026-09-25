# 里程碑 M2 设计：CRM 业务域、多 Agent 编排与 Waza 门禁

> 文档版本：1.1
>
> 日期：2026-09-15（设计） / 2026-09-16（实施记录）
>
> 状态：**P0 / P2 / P3 / W 已实现并通过门禁；P4、P5 未开始。**
> 实现结果与设计的偏差见 [§10 实施记录](#10-实施记录2026-09-15)，已实现部分的
> 运行时说明见 [域包与域路由](24-domain-packs-and-routing.md)。
> 本文其余部分保持设计稿原貌，**不要**把 P4 的内容当成已有能力。
>
> 基线：[M1 里程碑](22-milestone-2026-09.md)（提交 `4e25751`）——只读销售 ChatBI，9 表 / 8 指标 / 253 用例全绿。

## 0. 先说三条判断

### 0.1 这不是三个并列功能，是一条依赖链

| 顺序 | 事项 | 为什么不能提前 |
| --- | --- | --- |
| 1 | CRM 业务域 | 它是**第二个**域。第一次出现"两个域"这件事本身，才暴露出单域架构里哪些东西是硬编码的 |
| 2 | 多 Agent | 多 Agent 的唯一正当理由是"承载多域"。只有一个域时，Supervisor 就是一层没有信息量的转发 |
| 3 | Waza | 它固化的是**已经做出的决策**。口径没定、拓扑没定，Skill 写出来就是废话，还要反复改 |

先做 2 再做 1，会得到一个为单域设计的 Supervisor；先做 3，会得到两个必须推倒重写的 Skill。

### 0.2 M1 的建议是"先做方向 A"，本设计部分违背它

[M1 §5](22-milestone-2026-09.md) 建议先做工程收口（方向 A）而不是扩域（方向 B）。你选择了 B 并且加量。我按你的选择设计，但把 A 里**唯一阻塞 CRM 的那一项并入 P0**：

- **并入**：补历史时间数据。CRM 的漏斗、周期、赢率全是时间序列，只有 2025-10 起的数据做不出任何同比，和销售域同一个病根。
- **留在 M2 之外**：HTTPS、A/B 在 deepseek 下复测、延迟优化。这三项和 CRM 无依赖关系，不要塞进来。

一个诚实的预判：M1 暴露的缺陷里，相当一部分是真实提问下才现形的（CTE 同比、点名客户、Top-N 措辞）。扩域会让这类问题**更晚**被发现。接受这个代价，是选 B 的成本。

### 0.3 多 Agent 必须兑现两条具体收益，否则不做

不是"更智能"、不是"更像人类团队"。是：

1. **并行降延迟。** 跨域问题把两个域的取数并发执行，p50 接近单域而不是两倍。
2. **每个域一份独立守卫白名单。** CRM 问题在结构上碰不到 `sale_order_line`，销售问题碰不到 `crm_lead`。这是 [M1 §2.2](22-milestone-2026-09.md)"安全边界靠结构保证"的直接延伸。

如果实现下来这两条都没兑现，多 Agent 就是纯成本，应当回退到单图加节点。第 3.9 节给出了判定这件事的具体数字。

---

## 1. 阶段与门禁

每个阶段的出口门禁必须全绿才进下一阶段。门禁定义见第 5 节。

| 阶段 | 内容 | 阻塞关系 | 出口门禁 |
| --- | --- | --- | --- |
| **P0** | CRM 模块安装确认 + CRM 演示数据 + 历史时间数据回填 | 阻塞一切 | 数据形态自检（§2.7）通过 |
| **P1** | CRM 口径确认（需用户签字） | 阻塞 P3 | §2.1 争议列全部有结论 |
| **P2** | DomainPack 重构（纯重构，只有 sales 一个域） | 阻塞 P3、P4 | 253 用例 + 76 题黄金集**零变化**全绿 |
| **P3** | CRM 域包（语义层、守卫规则、审计范围、黄金集、Wiki 笔记） | 阻塞 P4 跨域 | CRM 黄金集 ≥20 题全绿，四条新守卫规则各有红转绿测试 |
| **P4** | Supervisor + 并行分发 + 预算 + 跨域 | — | 单域 p50 不退化，跨域 p50 ≤ 25s，预算耗尽路径有测试 |
| **P5** | 归因 Agent（可选，预算允许才做） | — | 下钻步数受限，失败不污染主答案 |
| **W** | Waza：随 P1/P2/P4 各交付一个 Skill | 跟随，不独立排期 | §4.3 门禁阶梯 |

**P2 是整个 M2 风险最高的一步**，因为它是"改了很多、行为不能变"。把它单独隔一个阶段、门禁是"零变化全绿"，就是为了让这一步可归因。

---

## 2. CRM 业务域

### 2.1 口径先行（P1，需用户确认）

[M1 §5 方向 B](22-milestone-2026-09.md) 的前置条件写得很清楚：**必须先和业务确认口径**，否则重演"退款算不算"。下面是待确认表，`争议` 列非空的必须由你拍板。

| 指标 ID | 名称 | 表达式 | 默认范围 | 时间字段 | 争议 |
| --- | --- | --- | --- | --- | --- |
| `crm_lead_count` | 线索数 | `COUNT(crm_lead.id)` | `type='lead'` | `create_date` | 是否含已归档（`active=false`）线索 |
| `crm_opportunity_count` | 商机数 | `COUNT(crm_lead.id)` | `type='opportunity'` | `create_date` | 同上 |
| `crm_open_revenue` | 在途预期收入 | `SUM(expected_revenue)` | `type='opportunity' AND won_status='pending'` | `date_deadline` | 在途按预计成交日还是创建日切分 |
| `crm_weighted_revenue` | 加权预期收入 | `SUM(prorated_revenue)` | 同上 | `date_deadline` | — |
| `crm_won_count` | 赢单数 | `COUNT(crm_lead.id)` | `won_status='won'` | `date_closed` | — |
| `crm_won_revenue` | 赢单金额 | `SUM(expected_revenue)` | `won_status='won'` | `date_closed` | 赢单金额用商机预期收入，还是用落地订单金额（见 `crm_won_order_amount`） |
| `crm_lost_count` | 输单数 | `COUNT(crm_lead.id)` | `won_status='lost'` | `date_closed` | — |
| `crm_win_rate` | 赢率 | `won / (won + lost)` | `won_status IN ('won','lost')` | `date_closed` | **分母是否含 pending**。建议已关闭口径（不含），否则月初赢率天然低 |
| `crm_avg_days_to_close` | 平均成交周期 | `AVG(day_close)` | `won_status='won'` | `date_closed` | `day_close` 是 Odoo 存储计算字段，单位为天（float） |
| `crm_conversion_rate` | 线索转化率 | `COUNT(date_conversion IS NOT NULL) / COUNT(*)` | 全量 | `create_date` | **见 §2.2 坑四：这个口径在 Odoo 里天然有偏差** |
| `crm_won_order_amount` | 赢单落地金额（跨域） | `SUM(sale_order.amount_untaxed)` via `sale_order.opportunity_id` | `sale_order.state IN ('sale','done')` | `sale_order.date_order` | 这是第一个跨域指标，需要 `sale_crm` 已安装 |

维度：`stage`、`team`、`salesperson`、`lost_reason`、`source` / `medium` / `campaign`、`priority`、`type`、`customer`、`month`。

### 2.2 Odoo 19 的五个坑（已对源码核实）

这一节是本设计里信息密度最高的部分。前四条如果不处理，做出来的 CRM 报表会**看起来正常但数字是错的**——正是 [M1 §2.1](22-milestone-2026-09.md) 说的那类最危险的错误。

#### 坑一：输单记录 `active = false`，会被本能写法悄悄抹掉

`crm_lead.py:1119` 的注释是原话：`Lost semantic: probability = 0 AND active = False`。

后果：任何模仿 ORM 习惯、顺手加 `active = true` 的 SQL，会把**全部输单**排除掉。于是：

- 赢率 = 100%
- 输单原因分布 = 空
- 漏斗底部凭空消失

这不会报错，不会触发守卫，只会给出一个好看的错数字。**它是 M2 最该被结构挡住的一件事**，对应守卫规则 R1/R2（§2.4）。

#### 坑二：`won_status` 是存储计算字段，可以直接在 SQL 里用

`crm_lead.py:611-618`：

```python
if lead.probability == 100 and lead.stage_id.is_won:
    lead.won_status = 'won'
elif not lead.active and lead.probability == 0:
    lead.won_status = 'lost'
else:
    lead.won_status = 'pending'
```

`store=True`，所以 `crm_lead.won_status` 是**物理列**。这是 Odoo 19 相对旧版本的一个实质简化：赢/输/在途的三态判定不需要在 SQL 里复刻 `probability` + `stage_id.is_won` + `active` 的组合逻辑，一个列就够。

设计上因此采纳：**所有赢输相关口径统一走 `won_status`，不允许自己拼 `probability = 100`**。理由是口径只有一处定义，且与 Odoo 界面显示的值一致。

#### 坑三：`expected_revenue` 是公司币种，但币种列不落库

`crm_lead.py:141` 的 `currency_field='company_currency'`，而 `company_currency`（`crm_lead.py:150`）是 `compute` 且**未 store**——数据库里没有这一列。

后果：金额已经是公司币种（不需要汇率换算，比预想的简单），但**取币种符号必须 join `res_company.currency_id → res_currency`**，不能像销售域那样用 `sale_order.currency_id`。CRM 域包的开放表必须包含 `res_company` 和 `res_currency`，且 `crm_lead` 的字段白名单里**不存在** `currency_id`。

#### 坑四：线索转化率在 Odoo 里天然有偏差

Odoo 的线索转商机是**同一条记录把 `type` 从 `lead` 改成 `opportunity`**，不产生新记录、不保留原线索副本。所以：

- 已转化的线索，现在 `type = 'opportunity'`，从"线索"里消失了；
- 唯一痕迹是 `date_conversion IS NOT NULL`；
- 因此"线索转化率"的分母不是"曾经的线索总数"，而是"现在还是线索的 + 有转化时间戳的"。

这个偏差无法用 SQL 消除（真相在 `mail_message` 跟踪记录里，代价过高）。设计决定：**保留这个指标但在语义层的 `description` 里写明近似口径**，并让 `explain_metric` 节点能把这句话原样答出来。宁可让用户知道它是近似的，也不要给一个看起来精确的错数。

#### 坑五：`crm_stage.team_ids` 在 Odoo 19 是 many2many

`crm_stage.py:31`：`team_ids = fields.Many2many('crm.team', ...)`。旧版本这里是 `team_id`（many2one）。

后果："某团队的漏斗阶段"是 m2m join，关系表是 Odoo 自动生成的名字（按表名排序推断为 `crm_stage_crm_team_rel`，列 `crm_stage_id` / `crm_team_id`）。**这个名字必须以实际库为准**，不要照抄本文——见 §2.7 的自检查询。

同时注意 `team_ids` 为空表示"该阶段对所有团队可用"，不是"不属于任何团队"。按团队筛漏斗时，条件是 `team_ids 为空 OR 含该团队`。

### 2.3 开放表

| 表 | 用途 | 备注 |
| --- | --- | --- |
| `crm_lead` | 线索与商机主表 | 同表双实体，靠 `type` 区分 |
| `crm_stage` | 阶段 | `sequence` 排序、`is_won` 判赢 |
| `crm_team` | 销售团队 | 来自 `sales_team` 模块 |
| `crm_lost_reason` | 输单原因 | — |
| `crm_tag` / `crm_tag_rel` | 标签 | 关系表列名 `lead_id` / `tag_id`（显式声明，可靠） |
| `utm_source` / `utm_medium` / `utm_campaign` | 来源渠道 | 渠道分析的维度来源 |
| `crm_stage_crm_team_rel` | 阶段↔团队 | 自动生成名，需实测确认 |
| `res_partner` / `res_users` / `res_company` / `res_currency` | 共享 | 与销售域共用，见 §2.5 |
| `sale_order`（仅跨域） | 跨域落地金额 | 只开放 `opportunity_id` 连接路径 |

`crm_lead` 字段白名单（初版）：`id, name, type, active, won_status, probability, stage_id, team_id, user_id, partner_id, company_id, lost_reason_id, source_id, medium_id, campaign_id, priority, expected_revenue, prorated_revenue, recurring_revenue, create_date, date_open, date_conversion, date_closed, date_deadline, day_open, day_close`。

**不开放** `description`（Html 正文）、`email_from` / `phone`（个人信息，M1 的敏感信息策略见 [06](06-data-and-security.md)）。

### 2.4 四条新守卫规则（结构性，不靠提示词）

沿用 [M1 §2.2](22-milestone-2026-09.md)：安全与口径边界由 `ReadOnlySqlGuard` 的 AST 检查保证，不由 Prompt 保证。在 [sql_guard.py](../backend/app/database/sql_guard.py) 上新增：

| 规则 | 内容 | 错误码 | 挡住的具体错误 |
| --- | --- | --- | --- |
| **R1** | 任何命中 `crm_lead` 的查询，QueryPlan 必须显式声明 `won_status` 范围或 `include_lost` 布尔 | `missing_won_status_scope` | 坑一：默默排除输单，赢率变 100% |
| **R2** | SQL 里出现 `active` 过滤而计划未声明它，直接拒（与现有"WHERE 里不得出现计划未声明的字段"同款判据，只是把 `active` 从系统字段豁免名单里排除） | `undeclared_active_filter` | 坑一的另一半：模型顺手加 `active = true` |
| **R3** | 时间过滤所用的列必须等于该指标在语义层声明的 `date_field` | `date_field_mismatch` | 用 `create_date` 算赢率、用 `date_closed` 算线索数这类张冠李戴 |
| **R4** | 跨域查询只能通过语义层声明的连接键（当前只有 `sale_order.opportunity_id = crm_lead.id`）建立表间关系 | `undeclared_cross_domain_join` | 模型自由发明 join 路径导致的笛卡尔积与口径漂移 |

R1/R2 是同一个坑的两面，但必须是两条规则：R1 管"计划里说了没有"，R2 管"SQL 里做了什么"。M1 的契约校验正是用这种双向比对挡住"看起来正常但悄悄变了范围"的查询。

> **实现后的差异**：本表的错误码是设计时拟的名字。落地后 R1 是
> `missing_scope_declaration`、R2 是 `forbidden_scope_filter`，R3 并成"时间列按域给定"
> （不单独设码），R4 随 P4 一起做。判据从每个指标自己的 `scope` 推导而非按表硬判，
> 因此"不限 type"的转化率不会被 R1 误伤。实际形态见
> [域包与域路由 §5](24-domain-packs-and-routing.md)。

**预期会误伤。** [M1 §2.1](22-milestone-2026-09.md) 记录过 CTE 计算列、补充查询、点名客户三次合法误伤。CRM 上一定会再发生（最可能是 R3 遇到跨时间字段的对比类问题）。修法沿用同一条原则：**收紧判据，不放宽规则**。

### 2.5 语义层：从单文件到 DomainPack

现状（[semantic.py](../backend/app/bi/semantic.py)）：单个 `sales_semantics.json`，`retrieve()` 里用中文关键词硬编码选表，`SalesSemanticLayer.allowed_tables` 直接喂给守卫。加第二个域时这套东西有三个问题：一个扁平指标命名空间会撞词（"金额"、"客户"），一个守卫白名单意味着 CRM 问题能碰销售表，`retrieve()` 的关键词分支会线性膨胀。

目标形态：

```text
backend/app/bi/semantics/
├─ registry.py          # 域注册与域路由
├─ sales.json           # 现有内容原样搬迁，不改一个字
├─ crm.json             # 新增
└─ cross/crm_sales.json # 跨域包：只声明连接键与跨域指标
```

```python
@dataclass(frozen=True, slots=True)
class DomainPack:
    domain: str                  # "sales" | "crm" | "crm_sales"
    tables: dict[str, TableDef]
    metrics: dict[str, MetricDef]
    relations: list[str]
    examples: list[dict[str, str]]
    join_keys: list[JoinKey]     # 仅跨域包非空
    guard: ReadOnlySqlGuard      # 每个域一份独立实例
    prompt_id: str               # Langfuse Prompt Management 的域专属 Prompt
```

三条硬约束：

1. **每个域一份独立守卫实例。** 白名单是域包的属性，不是全局常量。CRM Analyst 结构上无法执行命中 `sale_order_line` 的 SQL——它的守卫里没有这张表。
2. **共享表（`res_partner` / `res_users` / `res_company` / `res_currency`）在每个域包里各自声明**，包括各自的字段白名单。允许重复声明，不做继承。理由：继承会让"CRM 到底能看客户的哪些字段"这个问题的答案分散在两个文件里，而这正是审计时最需要一眼看清的东西。重复的代价是一次性的，靠 §5 的一致性测试防漂移。
3. **跨域包不是两个域的并集**，而是一个只含连接键和跨域指标的第三个包，白名单是"两域白名单的交集 + 显式列出的连接列"。并集会把跨域路径变成一个万能后门。

### 2.6 指标 ID 命名：不迁移旧 ID

新指标统一 `crm_` 前缀；**现有 8 个销售指标 ID 保持不变**，不加 `sales_` 前缀。

代价是命名不对称（`sales_amount` vs `crm_won_revenue`）。收益是不动 76 题黄金集、`sales_result_references.jsonl` 结果签名、Langfuse 历史 Score 和 [18](18-native-wren-ab-benchmark.md) 的 A/B 基线。一次重命名会让所有历史基线不可横比——为了命名整齐丢掉可比性，不划算。把这个不对称写进 `odoo-crm-semantics` Skill，避免后来者"顺手统一一下"。

### 2.7 数据准备（P0，阻塞一切）

**必须先确认 CRM 是否已安装。** 本设计基于 `odoo-19.0+e.20250917/odoo/addons/crm` 的源码，但源码存在不等于模块已装、表已建。自检（只读账号）：

```sql
SELECT name, state FROM ir_module_module
 WHERE name IN ('crm','sales_team','sale_crm','utm') ORDER BY name;

SELECT table_name FROM information_schema.tables
 WHERE table_name LIKE 'crm%' ORDER BY table_name;

SELECT count(*) AS total,
       count(*) FILTER (WHERE type='lead')        AS leads,
       count(*) FILTER (WHERE type='opportunity') AS opps,
       count(*) FILTER (WHERE won_status='won')   AS won,
       count(*) FILTER (WHERE won_status='lost')  AS lost,
       min(create_date), max(create_date)
  FROM crm_lead;
```

`sale_crm` 未安装则 `sale_order.opportunity_id` 不存在，§2.1 的 `crm_won_order_amount` 和 R4 整条跨域路径都要推迟。

演示数据要求（扩展 [dev_demo_data](../../custom_addons/dev_demo_data/hooks.py)，它现在只播种 20 张销售单）：

| 要求 | 原因 |
| --- | --- |
| 赢 / 输 / 在途三态都有，且输单占比 ≥30% | 否则坑一的 bug 在演示数据上**看不出来**——这是本轮最重要的一条数据要求 |
| 跨越 ≥24 个月的 `create_date` 与 `date_closed` | 同比、成交周期、漏斗趋势全依赖它；与 M1 债务"数据只有 2025-10 起"是同一件事 |
| 部分商机 `opportunity_id` 关联到已有的 20 张演示单 | 跨域指标与 R4 需要真实连接数据 |
| 阶段、团队、输单原因、UTM 来源各有分布 | 维度下钻与漏斗图 |
| 少量脏数据：`expected_revenue = 0`、`partner_id` 为空、`date_closed` 为空的赢单 | M1 的经验是缺陷只在真实提问下现形；干净数据测不出空值处理 |

---

## 3. 多 Agent 编排

### 3.1 不做什么

先钉死边界，这一节比拓扑图重要。

| 不做 | 理由 |
| --- | --- |
| 不引入 CrewAI / AutoGen / 第二编排层 | [11 §6.3](11-roadmap.md) 的既有决策。LangGraph 的子图 + 条件边足以表达本设计的全部行为，加一层只会让 Checkpointer、Interrupt、Langfuse trace 三处各裂成两套 |
| 不做 Agent 之间的自由对话 | 自由对话的 token 消耗和失败模式都不可预算，且无法回归 |
| 不做动态生成 Agent | 域的数量是设计期已知的有限集合 |
| 不用模型做 Supervisor 路由（v1） | 沿用 [M1 §2.3](22-milestone-2026-09.md)：确定性优先。代价是脆，但可测、可回归、改一个词修一个 case |
| 不放宽只读 | 与 M1 一致，是设计而非限制 |
| 不让任何 Agent 直接产出最终答案 | 最终答案只有一个出口节点，否则"这句话是谁说的"无法追溯 |

### 3.2 拓扑

```text
                    ┌─ general_agent ───────────────┐
                    ├─ knowledge_agent (Wiki RAG) ──┤
 supervisor ────────┼─ semantic_agent (口径解释) ───┤
 (确定性路由        ├─ sales_analyst  [DomainPack:sales] ──┐
  + 预算控制)       ├─ crm_analyst    [DomainPack:crm]   ──┤ 可并行
                    └─ cross_domain_planner ──────────────┘
                              │  分解 → 并行分发 → 确定性合并
                              ▼
                       insight_agent (P5, 可选)
                              ▼
                    presenter (图表规划 + 答案合成 + 安全失败)
                              ▼
                       finalize_turn
```

`sales_analyst` 与 `crm_analyst` 是**同一份 Text2SQL 子图实现的两个实例**，差别只在注入的 DomainPack。这是整个多 Agent 设计的核心复用点，见 §3.3。

`general` / `knowledge` / `semantic` 三个分支是 M1 现有节点的原地封装，不改行为。

### 3.3 一份 Text2SQL 子图，多份域包

M1 的 Text2SQL 链路（`retrieve_*_context → generate_sql → compile → validate → analyze_error → repair → execute → profile → followups`）与"销售"这件事其实无关，它只依赖：语义上下文、守卫、Prompt、示例。把这四样抽成 DomainPack 注入，子图就是域无关的。

P2 的重构顺序（这是"改很多、行为不变"的关键）：

1. 抽出 `DomainPack`，用 `sales.json` 构造唯一一个包；
2. Text2SQL 节点全部改为从 `state["domain_pack"]` 取语义与守卫，删掉对 `SalesSemanticLayer` 的直接引用；
3. 把该子链路封成 LangGraph 子图，父图只留一条边指向它；
4. **跑 253 用例 + 76 题黄金集，要求零变化全绿。**

第 4 步不是形式。这一步如果出现任何一条 diff，说明抽象漏了状态，必须先修完再进 P3。

收益量化：之后新增采购或库存域 = 一个 JSON + 一份 Prompt + 一批黄金题，**零新增图代码**。这是"验证这套架构能不能复制"（[M1 §5 方向 B](22-milestone-2026-09.md)）的真正答案。

### 3.4 Supervisor 是确定性的

Supervisor 做两个决策，都不调用模型：

| 决策 | 方式 | 歧义处理 |
| --- | --- | --- |
| intent | 复用现有 `classify_intent`（`general/knowledge/source/semantic/data/hybrid`） | 现状不变 |
| domain | 新增确定性域分类：域包关键词 + 指标关键词命中打分 | **两域得分接近时不猜**，走跨域包；域完全无命中时进 Interrupt 问用户，不默认销售 |

新增的域判定让关键词路由承担了第二个决策，**脆性是叠乘而不是叠加**（intent 错 × domain 错）。缓解手段只有一个：黄金集必须覆盖 intent × domain 的组合，尤其是容易撞词的那些——"这个月客户金额多少"既像销售也像 CRM，必须有题覆盖。

"域完全无命中就问用户"这个选择，是宁可多问一句也不要默认到销售域然后给出一个答非所问但看起来正确的结果——和 M1 处理"点名客户"的做法同源。

### 3.5 预算是一等状态

多 Agent 最现实的失败模式不是答错，是**慢和贵到不能用**。M1 已实测一条取数 11.4s（11.1s 在两次模型调用）。所以预算进 `TurnState`，由 Supervisor 强制：

```python
@dataclass
class TurnBudget:
    max_model_calls: int      # 单域 2，跨域 4，归因 +2
    max_sql_executions: int   # 6（主 2 + 每域 followup 2×2）
    max_wall_clock_ms: int    # 单域 18_000（沿用现有 P50_LATENCY_BUDGET_MS），跨域 25_000
    spent: BudgetSpent
```

耗尽时的行为，沿用 M1 `safe_failure_answer` 的原则——**明确不宣称完整**：

> 已给出 CRM 侧的赢率，销售侧的落地金额因超出本轮预算未查询。

而不是悄悄少查一个域然后正常作答。这条要有专门的测试（§5）。

### 3.6 跨域：分解 → 并行 → 确定性合并

以"赢单商机最后落地了多少钱"为例：

1. `cross_domain_planner` 把问题分解为两个**域内** QueryPlan（CRM 侧赢单清单、销售侧关联订单金额），分解本身是模型调用，但输出是受约束的 Pydantic 协议（与 QueryPlan 同款设计）；
2. 两个 analyst **并行**执行，各走各的守卫；
3. 合并在 **Python 里确定性完成**，按声明的连接键对已经通过守卫的结果集做 join，不生成第二段 SQL、不再过模型。

合并放在 Python 而不是写一段跨域 SQL，有三个理由：跨域 SQL 需要一个并集白名单（§2.5 已否决）；并行取数的延迟收益会被单段 SQL 吃掉；两个结果集各自已经是"守卫通过、行数受限"的安全数据，在它们之上做 join 不引入新的数据访问风险。

代价：join 在应用层做，数据量受行数上限约束，不适合大结果集跨域分析。这是当前架构下的明确边界，不是待办。

### 3.7 状态与 Checkpointer

`AgentState` 现在已有约 40 个字段（[07 §3](07-langgraph-workflow.md)）。再乘以多个 Agent 会变成不可维护的上帝对象。拆分：

| 层 | 内容 |
| --- | --- |
| `TurnState`（父图） | question、intent、domain、budget、usage、trace、conversation、最终 answer / chart / warnings |
| `AnalystState`（子图，每实例一份） | 现有语义与 SQL 相关字段，加 `domain` 标签 |
| `MergeState`（跨域） | 各域结果引用、连接键、合并后结果 |

子图用显式的输入/输出 schema，只把需要的字段回写父图。**所有进入 Checkpointer 的值仍必须可序列化**（[07 §3.5](07-langgraph-workflow.md) 的既有约束）——并行子图会同时写父状态，reducer 必须是幂等且可交换的，否则 Interrupt 恢复后合并结果不确定。这是并行化引入的**新**风险点，必须有针对性测试。

### 3.8 可观测性：给执行链路加 agent 维度

现有 `_emit_stage` 把阶段记进 ContextVar 并随 `ChatResponse.trace_steps` 返回（[07 §4.2](07-langgraph-workflow.md)）。扩展两处：

1. stage 记录增加 `agent` 与 `domain` 字段，前端执行链路面板按 Agent 分组折叠；
2. Langfuse span 按 Agent 嵌套，`role_usage` 的归因增加 `agent` 维度（现有按 `generation_role` 分桶的机制直接复用，见 [20 P1-1](20-next-actions.md)）。

这顺带推进了 [M1 §5 方向 C](22-milestone-2026-09.md)：路由判定与守卫拒绝原因本来就该进 trace，多 Agent 让"哪个 Agent 在失败"成为必须回答的问题。**并行 span 的时序在 trace 上必须看得出是并发而非串行**，否则无法验证 §3.9 的延迟收益。

### 3.9 延迟与成本：判定多 Agent 是否值得的数字

| 场景 | M1 实测 | M2 门禁 | 说明 |
| --- | --- | --- | --- |
| 单域取数 p50 | 11.4s | **不退化，≤18s** | 沿用现有 `P50_LATENCY_BUDGET_MS`。域包让 Prompt 变小，理论上应该**更快** |
| 跨域取数 p50 | 不支持 | ≤25s | 若实测 ≥2× 单域，说明并行没生效，回退单图 |
| 跨域模型调用数 | — | ≤4 | 分解 1 + 两域 SQL 2 + 合成 1 |
| 单域模型调用数 | 1～2 | **不增加** | Supervisor 不调模型，所以单域路径的调用数必须与 M1 一致 |

最后一行是最关键的验收项：**它证明多 Agent 没有给简单问题增加成本**。如果单域路径的模型调用数上升了，说明 Supervisor 或域路由偷偷用了模型，违背 §3.4。

---

## 4. Waza 与项目级 Skills

设计时的现状（2026-09-15）：3 个 Skill、`mock` 执行器、Waza CLI 未安装、离线契约测试 `test_project_skills` 已绿。M2 要做的不是"装上 CLI"，是**让 Skill 覆盖新增的两个高危决策面，并把 Waza 接进门禁阶梯**。

> 这句"不是装上 CLI"后来被证明轻视了 CLI 的价值，见 §4.3 的实施后更新。
> 当前状态（5 个 Skill、CLI 已装并跑通）见 [17](17-project-skills-and-waza.md)。

### 4.1 新增两个 Skill

| Skill | 何时触发 | 固化什么 | 不负责 |
| --- | --- | --- | --- |
| `odoo-crm-semantics` | CRM 口径、指标、字段、守卫规则的开发或评审 | §2.2 五个坑、§2.4 四条守卫规则、§2.6 不迁移旧 ID 的决定 | 销售域口径、Odoo CRM 业务流程培训 |
| `odoo-multiagent-orchestration` | Supervisor、域路由、子图、预算、跨域合并的改动 | §3.1 不做什么、§3.4 确定性路由、§3.5 预算、§3.6 合并在 Python | 单域 Text2SQL 内部（归 `odoo-agent-development`） |

`odoo-crm-semantics` 是这两个里价值最高的。CRM 口径的错误**不报错、不触发守卫，只给出好看的错数字**（坑一），是典型的"必须写下来否则一定会重犯"。

同时更新现有两个：`odoo-agent-development` 增加指向本文与域包边界的链接；`odoo-readonly-testing` 把 CRM 黄金集与跨域回归纳入门禁清单。

**Token 预算是真约束**：`.waza.yaml` 给 `SKILL.md` 的上限是 3000、告警 1200。§2.1 的口径表和 §2.2 的五个坑**塞不进** SKILL.md。所以 Skill 里只写"判断规则 + 指向文档的链接"，明细留在文档里——这也正是现有三个 Skill 的写法（"Load the relevant contract"）。

### 4.2 从 trigger eval 到 behavior eval

现有 eval 只测触发边界（正例/负例路由到对的 Skill），`mock` 执行器给不出内容判断。M2 增加 behavior eval，断言的是"给了 Skill 之后模型的行为"：

| 断言 | 对应风险 |
| --- | --- |
| 写 `crm_lead` 查询时显式声明 `won_status` 范围 | 坑一 |
| 不自己拼 `probability = 100 AND is_won` | 坑二 |
| 不在 `crm_lead` 上引用 `currency_id` | 坑三 |
| 提到"线索转化率"时带上近似口径的说明 | 坑四 |
| 被要求"加个 Agent 框架"时引用 [11 §6.3](11-roadmap.md) 拒绝 | §3.1 |

**诚实的限制**：behavior eval 需要真实执行器，`mock` 判不了内容。所以它落在黄灯层（需授权 + 额度），trigger eval 继续做绿灯层。不要把 behavior eval 写完就宣称门禁变强了——没跑过的 eval 不是门禁。

### 4.3 门禁阶梯

| 灯 | 检查 | 依赖 |
| --- | --- | --- |
| 绿（离线、随时可跑） | `test_project_skills`（扩展至 5 个 Skill）+ §4.4 漂移测试 | 无 |
| 绿（离线，装了 CLI） | `waza check <skill>`、`waza run <eval>`（`mock`） | 需用户批准第三方二进制的版本与来源 |
| 黄（需授权） | behavior eval，真实执行器 + 真实模型 | 账号、网络、额度授权，结果写入被忽略的 `.waza-results/` |
| 红（必须用户做） | CRM 口径签字（§2.1 争议列）、是否接受 §2.2 坑四的近似口径 | — |

> **实施后更新（2026-09-17）**：设计时判断 Waza CLI 是**低优先**（引用了
> [20 §1](20-next-actions.md) 的 GitHub API 限流障碍）。**这个判断是错的。**
> 障碍有简单绕法（认证的 `gh`），而且装上后第一次运行就找出 3 个自研测试
> 按原理发现不了的问题——其中一个是这 5 份评测的路径基准全错、
> **从写下来那天起就没跑起来过**。
> 现在前两行绿灯都已实际跑通（5 份 Skill 全部 Medium-High、10 道触发用例全过），
> 结果与教训见 [17 §5](17-project-skills-and-waza.md)。
> 黄灯的 behavior eval 仍未运行。

### 4.4 Skill↔文档漂移测试

5 个 Skill × 25 篇文档，Skill 里的相对链接一定会烂。新增测试，断言：

1. 每个 `SKILL.md` 的相对文档链接指向存在的文件（现有测试已覆盖）；
2. **链接里的锚点在目标文档里确实存在**（新增）；
3. 每个 `SKILL.md` 的 token 数在 `.waza.yaml` 的预算内（新增）。

第 2 条是新的。本文大量引用 `[M1 §2.2]`、`[11 §6.3]` 这类节号，如果被引用的文档重排了节号，Skill 会把开发者指到错误的段落——比链接 404 更难发现。

---

## 5. 验收矩阵

按 [16](16-tdd-test-and-acceptance-matrix.md) 的红绿灯惯例，每阶段先写失败测试。

| 阶段 | 必须先红的测试 | 出口门禁 |
| --- | --- | --- |
| P0 | — | §2.7 三条自检查询有预期形态；输单占比 ≥30%；时间跨度 ≥24 个月 |
| P1 | — | §2.1 争议列全部有用户结论，写入 `24-crm-semantic-domain.md` |
| P2 | `test_domain_pack.py::test_sales_pack_matches_m1_semantics`（域包构造出的白名单、指标、示例与 M1 逐项相等） | **253 用例 + 76 题黄金集零变化全绿**；`node --check app.js` 通过 |
| P3 | `test_sql_guard_crm.py::test_rejects_crm_lead_query_without_won_status_scope`（R1）<br>`::test_rejects_undeclared_active_filter`（R2）<br>`::test_rejects_date_field_mismatch`（R3）<br>`test_semantic_domains.py::test_crm_guard_cannot_reach_sales_tables` | CRM 黄金集 ≥20 题全绿；四条规则各有红转绿；语义审计覆盖 CRM 表 |
| P4 | `test_supervisor.py::test_ambiguous_domain_interrupts_instead_of_defaulting_to_sales`<br>`test_supervisor.py::test_single_domain_model_call_count_matches_m1`<br>`test_cross_domain.py::test_analysts_run_concurrently`<br>`test_cross_domain.py::test_merge_uses_declared_join_key_only`（R4）<br>`test_budget.py::test_exhausted_budget_answers_partially_without_claiming_success`<br>`test_state.py::test_parallel_subgraph_reducers_are_order_independent` | 单域 p50 ≤18s 且模型调用数不增；跨域 p50 ≤25s；Langfuse trace 上并行 span 可见 |
| P5 | `test_insight.py::test_drilldown_respects_step_budget`<br>`::test_insight_failure_does_not_affect_main_answer` | 归因失败降级为 warning，不进修复循环（沿用补充查询的既有原则） |
| W | `test_project_skills.py::test_skill_anchors_resolve`<br>`::test_skill_token_budget` | 5 个 Skill 全部通过；§4.3 绿灯层全绿 |

---

## 6. 风险

| 风险 | 概率 | 缓解 | 残余 |
| --- | --- | --- | --- |
| 口径讨论比写代码慢，拖住 P3 | 高 | P1 独立成阶段，有明确签字项；期间可并行做 P2 重构 | 若口径迟迟不定，P2 完成后会空转 |
| P2 重构引入不可见回归 | 中 | "零变化全绿"门禁；纯重构不夹带功能 | 黄金集未覆盖的行为可能静默改变 |
| 域路由脆性叠乘（intent × domain） | 高 | 黄金集覆盖组合；歧义走 Interrupt 不猜 | 换个说法仍会走错，这是确定性路由的既知代价 |
| 新守卫规则误伤合法查询 | 高 | 预期发生，按"收紧判据不放宽规则"修 | 每次误伤都要一轮红绿，消耗时间 |
| 跨域并行没兑现延迟收益 | 中 | §3.9 的数字门禁；trace 上验证并发 | 若不达标需回退单图，P4 部分工作作废 |
| CRM 或 `sale_crm` 未安装 | 未知 | P0 第一件事就是自检 | 若 `sale_crm` 缺失，跨域与 R4 整块推迟 |
| 多 Agent 抬高单轮成本 | 中 | 预算上限 + 按 agent 成本归因 + 单域调用数不增门禁 | 跨域问题必然比单域贵，这是功能成本 |
| 单模型供应商仍是单点 | 高 | 本轮不解决 | M1 已实测发生过一次全站不可用；多 Agent 会放大影响面 |

---

## 7. 如果只做一件事

做 **P2 + P3**：DomainPack 重构 + CRM 域包，多 Agent 只做到 §3.4 的确定性域路由，把并行、跨域、归因全部推到下一轮。

理由：P2 是对"这套架构能不能复制"的真实检验，P3 是第一次复制。这两步做完，即便一行 Supervisor 代码都不写，你也已经拿到了扩域的边际成本这个关键数字。反过来，先做多 Agent 编排却只有一个半域，得到的只是一层没有信息量的转发。

Waza 的 `odoo-crm-semantics` 应当**在 P1 口径确认当天就写**——它的价值全在于把刚拍板的决定固化下来，晚写一周就会漏掉细节。

---

## 8. 待你确认

| # | 事项 | 不确认的后果 |
| --- | --- | --- |
| 1 | §2.1 争议列：赢率分母是否含 pending、线索数是否含归档、在途收入按哪个日期切分、赢单金额用商机预期还是落地订单 | P3 无法开始 |
| 2 | §2.2 坑四：接受线索转化率是近似口径（并在答案里说明），还是不提供这个指标 | 要么给出有偏差的数，要么少一个指标 |
| 3 | §2.6：接受 `sales_amount` / `crm_won_revenue` 的命名不对称 | 若要求统一，历史基线全部不可横比 |
| 4 | §7：是否接受把并行与跨域推到下一轮 | 影响 P4 排期 |
| 5 | CRM / `sale_crm` 当前安装状态（§2.7 自检） | 跨域能力是否在本轮范围内 |

---

## 9. 相关文档

- [M1 里程碑](22-milestone-2026-09.md) — 本设计的基线与三条站得住的决策
- [演进路线](11-roadmap.md) — §6.3 的"不引入第二编排层"是 §3.1 的来源
- [LangGraph 工作流](07-langgraph-workflow.md) — 被 §3.3 / §3.7 改动的现状
- [数据语义与安全](06-data-and-security.md) — 被 §2.4 扩展的守卫契约
- [Odoo 语义一致性审计](14-odoo-semantic-sync-and-audit.md) — 审计范围需随 §2.3 扩展
- [项目级 Skills 与 Waza](17-project-skills-and-waza.md) — 被 §4 扩展
- [TDD 测试与验收矩阵](16-tdd-test-and-acceptance-matrix.md) — §5 的门禁写法来源
- [下一步任务队列](20-next-actions.md) — 本设计通过后，阶段拆解进入该文档

## 10. 实施记录（2026-09-15）

### 10.1 已完成

| 阶段 | 结果 |
| --- | --- |
| **P0** | `crm` + `sale_crm` 已装；`dev_demo_data` 扩出 360 条线索/商机，67 张订单挂到赢单商机；数据形态逐条达标（§2.7） |
| **P2** | `DomainPack` / `DomainRegistry` 落地，Text2SQL 节点全部按域取语义与守卫；**M1 的 264 用例与 76 题黄金集零变化** |
| **P3** | CRM 域包（14 表 / 10 指标 / 15 关系 / 8 条域规则）、域路由、守卫规则 R1～R3 + 译名回退、CRM 黄金集 27 题、语义审计 9→17 张表 |
| **W** | 新增 `$odoo-crm-semantics` 与 `$odoo-multiagent-orchestration`；离线契约测试加章节漂移与 token 预算两项检查 |

门禁：后端 **319 用例全绿**，销售黄金集 **76/76**，CRM 黄金集 **27/27**，
`compileall` 与 `node --check` 通过。

### 10.2 未完成

**P4（Supervisor、并行分发、预算、跨域）与 P5（归因 Agent）没有开始。**
当前有的是"多域"，不是"多 Agent"：域包与确定性域路由已经就位，
但仍是单图串行执行，没有 Supervisor 节点、没有并行、没有轮次预算、
没有跨域查询。§3.9 那几个数字一个都还没实测。

跨域的数据前提已经备好（`sale_order.opportunity_id` 上有 67 条真实连接），
所以 P4 可以直接开工。

### 10.3 设计里估错的地方

| 设计怎么说 | 实际 |
| --- | --- |
| P2 是风险最高的一步 | **比预想小得多。** `ReadOnlySqlGuard` 的构造早就只依赖 `table_columns`/`company_id`/`max_rows`，`SemanticContextProvider` 也已经是 Protocol——守卫和语义层本来就是域无关的，重构面只有 14 处语义引用和 3 处守卫引用 |
| 守卫需要加 4 条新规则 | R1～R3 加了，R4（跨域连接键）随 P4 一起做。另外**多出两处设计没预见的改动**：`company_scoped_tables` 与 `metric_expression_fields`，见 §10.4 |
| 补 2024 年历史数据是 P0 必做项 | **已经做过了。** 实测销售数据是 2024-01～2026-09 连续 33 个月、1526 张确认单，M1 §4 记的"数据只有 2025-10 起"这条债已经还了 |
| 五个 Odoo 19 实测坑 | 全部证实。另外实测发现**第六个**：`crm_stage.is_won` 对非赢阶段是 NULL 而不是 false，写 `is_won = false` 会漏掉全部 NULL 行 |
| 域路由并列时该问用户（§3.4） | 暂缓。M1 的 76 题里大量问题不含任何域关键词，严格路由会把它们全部打断；当前一律回落默认域，"问用户"随 Supervisor 进 P4 |

### 10.4 实施中发现并修掉的四个真问题

这四个都不是"没做完"，是**做的过程中暴露出来的既有缺陷**。

1. **CRM 查询原本完全不受 `company_id` 约束。** 公司隔离那条规则写死了
   `{sale_order, sale_order_line}`，新域不在其中就不触发。单公司环境下看不出来，
   但这是隔离边界上的真实漏洞。现在它是域档案的一项。
2. **ORM 根本不允许写 `create_date`。** `odoo/orm/models.py` 在 create 和 write
   两处都把 `LOG_ACCESS_COLUMNS` 从 vals 里无条件剔掉（只有注册表加载期的 superuser
   例外），传了会被**静默丢弃**——第一次播种 360 条记录的 `create_date` 全落在当天，
   33 个月的时间序列一条都没有。只能走 SQL，且必须手动 `modified()` 触发
   `day_close` 等存储计算字段重算，否则平均成交周期整体是错的。
3. **`test_quality_api` 的环境隔离一直没生效。** `app/main.py` 有模块级
   `app = create_app()`，所以 `import app.main` 当场就把带着开发者本机口令的配置
   缓存进 lru_cache，晚于它的 `setUpModule` 再剥环境变量已经没用。它能过只是因为
   字母序在前的 `test_api` 顺带清了缓存——单独跑一直是红的，只是没人单独跑过。
   隔离器现在在 start/stop 两端都清缓存。
4. **意图词表是销售专用的。** `_DATA_WORDS` 里没有任何 CRM 词，"赢率是多少"
   会被判成 `general`、永远到不了取数。现在两个词表从各域的 `routing_keywords`
   与指标 `name` 自动追加（销售域原有的词一个不动）。

另有两处按 [M1 §2.1](22-milestone-2026-09.md)"收紧判据而不是放宽规则"修掉的误伤：
聚合 `FILTER (WHERE ...)` 里的列（转化率的计算式本身被判成未声明的过滤字段），
以及"输单原因分布"——CRM 有一个维度就叫**输单原因**，维度名里正好含着 hybrid
的线索词"原因"。两处的修法都是收紧，细节见
[域包与域路由 §4.4 / §5.2](24-domain-packs-and-routing.md)。

### 10.5 仍然开放的口径问题

§2.1 里"线索数与商机数是否含已归档记录"这一条**没有经过确认**，
当前按"含"实现（即不按 `active` 过滤）并在指标 `scope` 里写明。
选这个默认值的理由是：排除归档正是坑一本身，静默排除比多算更危险。
需要改成"不含"的话，改 `crm_semantics.json` 的 `scope` 即可，
但要同时补黄金题——两种口径在演示数据上的差值是可观的（线索 108 条里 8 条已归档，
商机 252 条里 98 条已输单）。
