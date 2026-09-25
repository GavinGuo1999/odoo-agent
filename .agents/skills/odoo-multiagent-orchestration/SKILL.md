---
name: odoo-multiagent-orchestration
description: Implement or review how odoo-agent routes a question between business domains and merges results across them. Use when work touches domain packs, a supervisor node that routes between the sales and crm domains, per-domain SQL guards, deterministic domain routing, turn budgets that cap model calls, or cross-domain result merging and joins. Do not use for single-domain Text2SQL internals such as QueryPlan repair or chart planning.
---

# Odoo Agent 多 Agent 编排

多 Agent 的正当理由只有两条：**并行降延迟**、**每域一份独立守卫白名单**。
不兑现这两条的复杂度应当被拒绝。

## 先读契约

- [M2 设计](../../../docs/23-m2-crm-multiagent-waza-design.md) — §3，尤其 §3.1"不做什么"
- [域包与域路由](../../../docs/24-domain-packs-and-routing.md) — 已实现部分
- [LangGraph 工作流](../../../docs/07-langgraph-workflow.md) — 节点、状态、Interrupt
- [演进路线](../../../docs/11-roadmap.md) — §6.3 "不引入第二编排层"的决定来源

## 硬约束

1. **LangGraph 是唯一编排层。** 不引入 CrewAI、AutoGen 或任何第二编排框架。
   被要求"加个 Agent 框架"时引用 roadmap §6.3 拒绝：加一层会让 Checkpointer、
   Interrupt、Langfuse trace 三处各裂成两套。
2. **每域一份独立 `ReadOnlySqlGuard`。** 白名单是域包属性，不是全局常量。
   不要把多域表合成一个白名单——那是万能后门。跨域是**第三个包**
   （白名单 = 两域交集 + 显式声明的连接列），不是并集。
3. **域路由和意图路由都不调模型。** 确定性判定可测、可回归、改一个词修一个 case。
   脆性用黄金集覆盖组合来缓解，不是改用模型。
4. **判据写在 `*_semantics.json` 里。** 加一个域应当是"加一个 JSON + 一批黄金题"，
   不动任何节点或边。
5. **零命中和并列回落默认域。** M1 的 76 题里大量问题不含域关键词。
   改成"问用户"前必须先让黄金集证明不打断既有行为。
6. **跨域合并在 Python 里做，不生成跨域 SQL。**
7. **预算是一等状态**（模型调用数、SQL 次数、墙钟），由 Supervisor 强制。
   耗尽时必须说出"哪个域没查"，绝不悄悄少查一个域然后正常作答。
8. **最终答案只有一个出口节点**，否则"这句话是谁说的"无法追溯。

不许做：Agent 间自由对话、动态生成 Agent、让模型决定跑哪个 Agent、放宽只读。

## 必须守住的数字

**单域路径的模型调用数不得增加**——它证明多 Agent 没给简单问题加成本；涨了说明
Supervisor 或域路由偷偷用了模型。单域 p50 ≤ 18s，跨域 ≤ 25s；跨域若 ≥ 2× 单域，
说明并行没生效，应当回退而不是继续加功能。

## 状态、并发与可观测性

子图用显式输入/输出 schema，只回写父图需要的字段。**并行子图同时写父状态，
reducer 必须幂等且可交换**，否则 Interrupt 恢复后合并结果不确定——这是并行化
引入的新风险，必须有针对性测试。进 Checkpointer 的值仍必须可序列化。

阶段记录带 `agent` 与 `domain`，Langfuse span 按 Agent 嵌套，成本归因加 `agent`
维度。**并行 span 必须看得出是并发而非串行**，否则无法验证延迟收益。

## USE FOR / DO NOT USE FOR

**USE FOR:**

- "在 sales 和 crm 域之间路由问题" / route a question between the sales and crm domains
- "加一个 supervisor 节点" / add a supervisor node
- "跨域合并结果" / merge results across domains
- "给一轮加预算上限" / cap model calls with a turn budget
- "新增一个业务域（采购 / 库存 / 制造）" / register a new domain pack

**DO NOT USE FOR:**

- 单域内部的 QueryPlan、SQL 修复、图表规划（归 `$odoo-agent-development`）
- CRM 指标口径本身（归 `$odoo-crm-semantics`）
- 引入 CrewAI / AutoGen 等第二编排层——见上文硬约束 1
