---
name: odoo-crm-semantics
description: Implement or review odoo-agent CRM semantic-layer, metric-definition, and CRM SQL guard work. Use for crm.lead, stage, team, lost-reason, win-rate, pipeline, and conversion-rate changes; do not use for sales-order analytics or Odoo CRM end-user training.
---

# Odoo Agent CRM 口径

CRM 口径的错误**不报错、不触发守卫，只给出好看的错数字**。最典型的一条：
顺手写 `active = true` 会把全部输单抹掉，赢率变成 100%。

## 先读契约

- [CRM 设计与五个实测坑](../../../docs/23-m2-crm-multiagent-waza-design.md) — §2.1 口径表、§2.2 坑、§2.4 守卫规则、§2.6 命名决定
- [域包与域路由](../../../docs/24-domain-packs-and-routing.md) — §5 守卫域档案
- [数据语义与安全](../../../docs/06-data-and-security.md) — 守卫契约与敏感字段

明细在文档里，本文只放判据。

## 七条判据

1. **赢/输/在途一律用 `won_status`**（存储列，`won`/`lost`/`pending`）。
   不要自己拼 `probability = 100 AND stage_id.is_won`——那是第二处口径定义，
   Odoo 改了判定这边不会跟着变。
2. **永远不要按 `active` 过滤 `crm_lead`。** 输单记录是 `active = false`
   （`crm_lead.action_set_lost`）。守卫 R2 会拒，声明进 `plan.filters` 也拒。
3. **`crm_lead` 没有 `currency_id` 列。** `expected_revenue` 已是公司币种；
   币种符号走 `res_company.currency_id → res_currency`。
4. **时间字段按指标声明取用**：线索/商机数看 `create_date`，赢单/输单看
   `date_closed`，在途看 `date_deadline`。混用不报错。
5. **`crm_stage.is_won` 对非赢阶段是 NULL 不是 false。** 用 `is_won IS NOT TRUE`。
6. **译名列必须回退**：`crm_stage`/`crm_lost_reason`/`crm_team`/`crm_tag` 的 `name`
   是 JSONB，要 `COALESCE(x.name->>'zh_CN', x.name->>'en_US')`。
   `utm_*` 的 `name` 是普通文本，不要套。
7. **线索转化率是近似值，必须把 `caveat` 答出来。** Odoo 里线索转商机是同一条记录
   改 `type`、不留副本。删掉那句等于把近似值伪装成精确值。

## 已拍板，不要重开

- **赢率 = won / (won + lost)**，分母不含 `pending`。
- **线索数与商机数含已归档/已输单记录。**
- **销售域 8 个指标 ID 一个都不改名**；新增 CRM 指标一律 `crm_` 前缀。
  命名不对称是刻意接受的：改名会让 76 题黄金集、结果签名与 Langfuse 基线
  全部不可横比。

## 改动流程

口径变更**先改文档 §2.1，再改 JSON**。守卫判据写进 `sql_profile` 而非 Python 分支；
`scope_required_metrics` 与 `metric_expression_fields` 从指标自己的
`scope`/`expression` 推导。先加失败测试（`test_crm_semantics.py` 管口径，
`test_sql_guard_crm.py` 管守卫）。新增字段前对真库核存在性——库是唯一事实来源。
跑后端全量 + 两个黄金集，销售侧必须零变化。

守卫会误伤合法查询。修法是**收紧判据，不是放宽规则**，并在黄金集留一题钉住。

## USE FOR / DO NOT USE FOR

**USE FOR:**

- "加一个 CRM 指标" / add a CRM metric such as win rate or pipeline revenue
- "赢率 / 输单 / 线索转化率算得对不对" / verify win rate, lost deals, or lead conversion
- "改 crm_lead 的开放字段" / change the crm_lead field whitelist
- "CRM 的守卫规则拒了合法查询" / a CRM guard rule rejected a valid query
- "漏斗 / 阶段 / 团队 / 输单原因维度" / stage, team, or lost-reason dimensions

**DO NOT USE FOR:**

- 销售订单分析（归 `$odoo-agent-development`）
- 域路由与跨域编排（归 `$odoo-multiagent-orchestration`）
- Odoo CRM 的最终用户操作培训
- 未经用户确认就改动已拍板的口径
