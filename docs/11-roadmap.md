# 演进路线

> 基线版本：0.2.0
>
> 日期：2026-08-06
> 当前边界：单用户、单公司、销售分析、只读、本地部署

## 1. 当前完成情况

| 工作包 | 状态 | 说明 |
| --- | --- | --- |
| FastAPI + 网站 | 已完成 | 静态 UI 由 FastAPI 托管 |
| Odoo 实时只读 | 已完成 | PostgreSQL 强制只读、超时、行数限制 |
| 销售语义层 | 已完成首版 | 9 表、71 字段、7 指标、3 个验证示例 |
| Odoo 语义一致性审计 | 已完成首版 | PostgreSQL、ORM、源码、正式 MDL 四源对照；版本化报告与隔离草稿 |
| Text2SQL | 已完成首版 | QueryPlan、SQL 生成、Guard、最多两次修复 |
| ECharts | 已完成 | KPI、折线、柱状、饼图白名单 |
| 节点模型路由 | 已完成 | SQL、回答、普通聊天独立配置 |
| LiteLLM | 已完成 SDK 首版 | 统一 DeepSeek 与硅基流动调用；尚未启用 Proxy/Router fallback |
| Langfuse | 已完成首版 | Trace、Session、Token、Cost、Score、Dataset |
| 黄金问题集 | 已完成首版 | 销售 20 条，静态和真实运行器；另有 Wiki 知识黄金集 20 条 |
| Wiki 检索 | 已完成 lexical 与 hybrid | 2026-09-06 实测 hybrid recall 0.95、lexical 0.80，无 fallback |
| Prompt Management | 已完成 | production label、缓存、超时、代码 fallback、版本回链 Generation |
| Langfuse Dataset Experiment | 代码已完成，未执行 | 三个确定性 evaluator 与 pass-rate 聚合已实现 |
| 持久会话 | 已完成 | PostgreSQL Checkpointer、内存降级 |
| Interrupt | 已完成首版 | QueryPlan 歧义暂停/恢复 |
| SSE | 已完成阶段流 | 尚未流式输出模型 Token |
| 快速回答 | 已完成 | KPI、排名、趋势、空结果跳过第二次 LLM |
| 用户与权限 | 未开始 | 当前明确不在范围 |
| Odoo 写操作 | 未开始 | 当前明确禁止 |

## 2. 当前主要瓶颈

### 2.1 SQL Generation 延迟

真实链路中数据库执行通常远快于模型生成。强模型可能输出较多 Token，使简单 KPI 即使跳过第二次模型仍然较慢。

优先措施：

1. 缩短 SQL Prompt 和语义上下文；
2. 对 QueryPlan/SQL 设置更明确的简洁输出约束；
3. 比较更快的 SQL 模型；
4. 用黄金集确认速度优化没有损失正确率；
5. 记录 SQL Generation p50/p95 和 Cost。

### 2.2 评测仍以结构为主

真实回归已检查 intent、query type、metrics、dimensions、data access 和只读 SQL，但尚未自动比较：

- 参考结果签名；
- 数值误差；
- 时间边界；
- SQL 语义等价；
- 回答数字忠实度。

### 2.3 状态管理后续治理

当前已完成会话列表、标题、新建、切换、删除、刷新恢复和 Interrupt 恢复。后续仍需：

- 归档；
- 保留期；
- Checkpoint 迁移工具。

### 2.4 观测治理（2026-09-06 更新）

已完成：

- Langfuse environment 显式映射（`configure_langfuse_environment`）；
- Prompt 已迁移到 Prompt Management，并回链到 Generation；
- 点踩原因分类（`user-feedback-reason`，CATEGORICAL）；
- Dataset 与 Experiment 代码，含 `sql-safe`、`metric-correct`、`answer-grounded` 三个确定性 evaluator。

仍需补齐：

- Experiment 从未实际执行；
- 基准的 p50/p95、Token 与 Cost 仍只落本地 JSON，Langfuse 内无版本间对比视图；
- 延迟与成本没有会失败的门禁阈值；
- 尚无 LLM Judge（BI 侧）；
- 生产部署中 `LANGFUSE_ENABLED` 默认为 `false`。

## 3. 近期路线：P0

### P0.1 降低 SQL 模型延迟

交付：

- SQL Prompt Token 预算；
- QueryPlan 简洁输出说明；
- 节点级 max output token；
- 至少两种 SQL 模型对比报告；
- Langfuse p50/p95 和 Cost 基线。

验收：

- 简单 KPI 仍只调用一个 Generation；
- 黄金集不下降；
- SQL Generation p50 明显下降；
- 无新增 SQL 安全失败。

### P0.2 强化真实回归

交付：

- 参考查询或结果签名；
- 数值容差；
- 每个 Case 的 latency/token/cost；
- 基线报告版本化；
- 失败差异摘要。

### P0.3 SQL 复杂度保护

在现有 SQLGlot Guard 增加：

- 最大 JOIN 数；
- 最大 CTE / 子查询数；
- 笛卡尔积检查；
- 明细查询时间范围要求；
- 可选 `EXPLAIN (FORMAT JSON)` 计划成本门槛。

高成本查询可触发 Interrupt，而不是直接执行。

### P0.4 Langfuse 环境和 Prompt 版本

> 状态（2026-09-06）：环境映射与 Prompt 版本回链已完成；仅剩“发布前 Dataset Run 对比 Prompt 版本”未执行。

- 显式映射 development/test/production；
- SQL、repair、answer Prompt 建立稳定名称；
- Trace 关联 Prompt 版本；
- 发布前 Dataset Run 对比 Prompt 版本。

## 4. 中期路线：P1

### P1.1 会话管理（核心功能已完成）

已实现：

```text
GET    /api/chat/conversations
GET    /api/chat/sessions/{session_id}
DELETE /api/chat/conversations/{session_id}
POST   /api/chat/conversations/{session_id}/detach
```

前端已具备列表、标题、新建、切换、删除确认、生成中后台切换和刷新恢复。归档与保留期仍未实现。

### P1.2 用户反馈原因

> 状态（2026-09-06）：已完成。后端记录 `user-thumbs`（BOOLEAN）与 `user-feedback-reason`（CATEGORICAL），前端已提供原因下拉。

点踩后增加可选原因：

- 数字不对；
- 指标口径不对；
- SQL 不对；
- 没有回答；
- 图表不合适；
- 太慢；
- 其他。

每个信号使用稳定 Score/metadata，避免把全部问题压成一个综合分数。

### P1.3 自动 Experiment 和 CI

> 状态（2026-09-06）：Experiment 与 evaluator 代码已实现但从未运行；CI 与阈值门禁未开始。

- Langfuse Dataset Experiment；
- 静态集在每次提交运行；
- 真实集在手动发布候选运行；
- 准确率、延迟和 Cost 阈值；
- 自动产出对比报告；
- 失败阻止发布。

### P1.4 Token Streaming

当前 SSE 只流步骤。第二阶段可流普通回答和复杂解释 Token，但需同时解决：

- OpenAI SDK stream；
- 前端增量 Markdown；
- Generation usage/cost 收尾；
- 中断和断线恢复；
- Trace 输出一致性。

SQL JSON 生成不建议向最终用户流式展示。

### P1.5 语义层扩展

在销售域稳定后，按独立主题逐步加入：

- 发票金额；
- 收款；
- 库存和实际出库；
- 毛利；
- 销售团队和目标。

每个主题都需要自己的指标、表字段、安全测试和黄金问题，不能一次性开放全库。

### P1.6 语义检索与实体匹配

在现有结构审计基础上逐步加入：

- 已验证 SQL、业务术语和字段帮助的混合检索；
- 客户、产品、销售员名称与数据库 ID 的实体值检索；
- 审计差异与黄金问题发布门禁；
- 关键词召回不足且语料达到规模后，再引入 pgvector。

这仍保持销售白名单和 SQLGlot Guard，不做完整源码的无边界向量检索。

## 5. 长期路线：P2

只有需求真正出现时考虑：

| 能力 | 触发条件 |
| --- | --- |
| 用户登录/RBAC | 从单用户本机转为多人共享 |
| 行级/公司权限 | 多公司或销售员只能看自己的数据 |
| LiteLLM Proxy/Router | 三个以上供应商、多个应用共享、集中预算/限流或需要自动 fallback |
| dbt Core | 重复复杂 JOIN、多消费者共享指标、需要数据测试 |
| pgvector | 50 条以上已验证 SQL 示例，关键词检索明显不足 |
| Ragas | 真正接入文档 RAG 和向量 Context |
| DeepEval / LLM Judge | 确定性回归成熟后补充语义质量 |
| Redis | 并发、成本或延迟成为真实瓶颈且缓存键可安全版本化 |
| Temporal | 出现跨小时审批、同步、日报或通知工作流 |
| MCP | 需要让 ChatGPT、Claude、IDE 调用业务工具 |
| Odoo 写回 | 完成认证、审批、审计、幂等和回滚设计之后 |

## 6. 暂不引入

### 6.1 Metabase

当前直接使用 ECharts，原因：

- 结果与聊天消息同一交互；
- 图表配置由后端白名单控制；
- 无需额外服务和嵌入认证；
- 当前数据量和用户数不需要独立 BI 平台。

### 6.2 Instructor

当前仅一个核心结构化 LLM 协议，Pydantic + JSON Mode + 修复已足够。出现三个以上结构化节点或首轮合法率低于 98% 时再评估。

### 6.3 多 Agent 框架

不同时引入 CrewAI、AutoGen 等第二编排层。现有 LangGraph 节点足以表达路由、工具、恢复和 Interrupt。

## 7. 目标指标

| 维度 | 目标 |
| --- | --- |
| SQL 安全测试 | 100% |
| 静态黄金集 | 100% |
| QueryPlan 首次合法率 | ≥98% |
| 指标/维度/时间正确率 | ≥90%，逐步提升 |
| 简单问题模型调用 | 1 次 |
| DB 执行 p95 | 保持远低于模型耗时 |
| 简单数据问题 p50 | 先降到 18 秒内，再继续优化 |
| Generation Cost 覆盖 | 100% 有 usage 和 cost |
| 会话恢复 | 刷新和重启均可恢复 |
| 用户反馈 | Trace 正确关联，点踩可分类复盘 |

## 8. 规划原则

1. 先用黄金集量化问题，再引入新组件；
2. 每次只改变一层，保持可归因；
3. 数据真实性和 SQL 安全优先于回答文风；
4. Odoo 只读边界不因功能扩展而弱化；
5. 新依赖必须带来当前可验证收益；
6. 规划项必须写触发条件和验收标准；
7. 不把低并发单用户问题提前设计成分布式平台。

## 9. 相关文档

- [产品与范围](01-product-overview.md)
- [系统架构](02-system-architecture.md)
- [评测与质量保障](09-evaluation-and-quality.md)
