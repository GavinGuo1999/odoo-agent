# LangGraph Agent 工作流

> 适用应用版本：0.2.0
>
> LangGraph：1.2.10
> Checkpointer：langgraph-checkpoint-postgres 3.1.1

## 1. 设计目标

SalesAgent 使用 LangGraph 作为唯一编排层，目标是：

- 普通聊天、指标解释、Wiki 知识、源码知识、数据分析和混合分析自动路由；
- Text2SQL 的每个高风险步骤可观察、可校验、可重试；
- 模型输出先进入 Pydantic 协议，再进入 SQL AST Guard；
- 关键歧义可 Interrupt，用户补充后从 Checkpoint 恢复；
- SQL 错误先分类，再决定修复、澄清或安全终止；
- 数据结果先生成确定性画像，再由受约束的 Chart Planner 规划展示；
- 单行 KPI 等简单结果跳过回答和图表模型；
- 每个步骤通过 SSE 和 Langfuse 暴露；
- 所有业务查询保持只读。

## 2. Graph 节点

| 节点 | 类型 | 是否调用模型 | 作用 |
| --- | --- | --- | --- |
| `classify_intent` | 确定性 | 否 | 判断 general/knowledge/source/semantic/data/hybrid |
| `answer_general` | Generation | 是，general | 普通聊天 |
| `retrieve_wiki_context` | Retriever | 否 | 只读检索 learn_odoo 已审核笔记 |
| `answer_knowledge` | Generation | 是，answer | 根据 Wiki 回答并附来源 |
| `explain_metric` | 确定性 | 否 | 从语义层解释指标 |
| `detect_data_ambiguity` | 确定性 | 否 | 在数据库检查和模型调用前识别无上下文的客户指代 |
| `retrieve_sales_context` | Retriever + Tool | 否 | 检查数据库、发现字段、检索指标/表/示例 |
| `generate_sales_sql` | Generation | 是，sql | 一次返回 QueryPlan + SQL |
| `clarify_query_plan` | Interrupt | 否 | 暂停并请求关键条件 |
| `compile_semantic_sql` | Tool | 否 | 使用 Wren MDL dry-plan 或准备原生 SQL |
| `validate_sales_sql` | Tool | 否 | SQLGlot 安全校验和规范化 |
| `analyze_sql_error` | Analyzer | 否 | 把失败归类为可修复、需澄清或必须终止 |
| `clarify_sql_error` | Interrupt | 否 | 错误源于业务歧义时请求用户补充 |
| `repair_sales_sql` | Generation | 是，sql | 根据错误修复 QueryPlan + SQL |
| `execute_sales_sql` | Tool | 否 | 在只读事务执行 SQL |
| `profile_sales_result` | Analyzer | 否 | 生成字段类型、基数和数值范围画像 |
| `run_followup_queries` | 确定性 | 否 | 执行模型声明的补充查询（最多 2 条），走同一套 SQL 守卫；失败降级为警告，不影响主答案 |
| `plan_chart` | Generation/确定性回退 | 视结果而定，answer | 生成并校验 ChartPlan，不生成 ECharts 代码 |
| `format_simple_answer` | 确定性 | 否 | KPI、排名、趋势、空结果快速回答 |
| `synthesize_sales_answer` | Generation | 是，answer | 复杂结果解释 |
| `safe_failure_answer` | 确定性 | 否 | 失败时明确不宣称查询成功 |
| `finalize_turn` | 确定性 | 否 | 完成会话历史和最终状态 |

## 3. AgentState

Graph 状态按功能可分为以下组。

### 3.1 输入与会话

| 字段 | 说明 |
| --- | --- |
| `question` | 当前实际处理问题；恢复后会附加用户补充 |
| `display_question` | 原始展示问题 |
| `clarification_answer` | Interrupt 后的补充答案 |
| `history` | 本轮推理使用的历史，最多保留最近部分 |
| `conversation` | 持久化的用户/助手对话 |

### 3.2 路由和回答

| 字段 | 说明 |
| --- | --- |
| `intent` | `general/knowledge/source/semantic/data/hybrid` |
| `answer` | 最终文本 |
| `answer_mode` | `llm/deterministic/knowledge/semantic/failure` |
| `provider`, `model` | 最后一次模型执行信息 |
| `model_roles` | 实际执行过的角色及 provider/model |

### 3.3 语义与 SQL

| 字段 | 说明 |
| --- | --- |
| `semantic_context` | 本轮检索到的开放表、指标、关系、示例 |
| `knowledge_context` | Wiki 检索后供知识回答或混合解释使用的受限上下文 |
| `knowledge_citations` | 可序列化的 Wiki 引用列表 |
| `knowledge_index_fingerprint` | 本轮使用的 Wiki 索引版本指纹 |
| `metric_ids` | 识别到且在白名单内的指标 |
| `query_plan` | Pydantic QueryPlan 的 JSON 可序列化形式 |
| `sql` | 模型生成 SQL |
| `safe_sql` | Guard 通过并规范化后的 SQL |
| `sql_errors` | 结构、Guard 或执行错误 |
| `sql_error_stage` | planning/semantic_compilation/validation/execution |
| `sql_error_analysis` | Pydantic SqlErrorAnalysis 的路由结论 |
| `sql_fingerprints` | 各次 SQL + QueryPlan 契约的短指纹，用于检测真正重复的修复循环 |
| `tables` | SQL 使用的开放物理表 |
| `repair_count` | 已执行修复次数，最大 2 |

### 3.4 数据与展示

| 字段 | 说明 |
| --- | --- |
| `database_ready` | Odoo 只读健康检查是否通过 |
| `currency` | 当前公司币种 |
| `columns`, `rows` | 受限查询结果 |
| `query_ms` | 数据库执行耗时 |
| `truncated` | 是否被行数上限截断 |
| `data_accessed` | 是否成功执行真实 SQL |
| `data_profile` | 字段类型、非空数、唯一值数和数值范围的确定性画像 |
| `chart` | Pydantic ChartPlan；仅含字段引用和展示参数，不含可执行代码 |
| `warnings` | 补零、截断等提示 |
| `filled_time_buckets` | 自动补齐的时间桶数量 |

### 3.5 Usage

| 字段 | 说明 |
| --- | --- |
| `input_tokens` | 本轮所有 Generation 输入 Token 累计 |
| `output_tokens` | 输出 Token 累计 |
| `total_tokens` | 总 Token 累计 |
| `estimated_cost_usd` | 按各节点供应商单价累计的估算美元成本 |

所有进入 Checkpointer 的值必须保持可序列化。数据库 Decimal、日期、UUID 等在客户端层先转换为 JSON 兼容值。

## 4. 意图路由

入口节点不调用模型，使用问题文本和历史执行快速分类：

```text
general  -> answer_general
knowledge/source -> retrieve_wiki_context -> answer_knowledge
semantic -> explain_metric
data     -> detect_data_ambiguity -> retrieve_sales_context
hybrid   -> retrieve_wiki_context -> detect_data_ambiguity -> retrieve_sales_context -> Text2SQL -> synthesis
```

`detect_data_ambiguity` 命中无历史上下文的“那个客户/该客户”时，直接构造最小 QueryPlan 并进入 `clarify_query_plan -> interrupt()`；此时不检查数据库，也不调用 SQL 模型。恢复后把补充条件写入问题，再继续语义检索和 Text2SQL。这样普通问答、知识问答和明确指标解释不承担 Text2SQL 的延迟和成本。混合问题先获取知识证据，但知识正文不会进入 SQL Prompt，只在查询完成后的解释节点使用。分类错误会直接影响后续路径，因此黄金集应覆盖六类问题。

## 4.1 补充查询（2026-09-15 起）

主查询之外，模型可以在 `followups` 里声明最多 **2 条**补充查询，用途是给"为什么"类
问题提供下钻证据——主查询给出各月销售额，补充查询给出跌得最狠那几个月的客户构成，
否则模型只能凭空归因。

三条硬规则：

1. **同一套守卫。** 每条补充查询带自己的 QueryPlan，走同一个 `ReadOnlySqlGuard`。
   唯一放宽的是**展示层**契约（`select_columns` 对齐、ORDER BY 对齐、Top-N 声明、
   产品名 COALESCE）——那些规则保证的是"给用户看的表格不被悄悄截断或串列"，
   而补充查询从不展示。表/字段白名单、`company_id`、禁写、`SELECT *`、行数上限、
   以及"WHERE 里不得出现计划未声明的字段"，一条都不跳过。
2. **失败不影响主答案。** 主查询已经成功了，补充数据只是证据；出错记一条 warning
   继续走，绝不把整轮拖进修复循环。
3. **不参与画图。** 图表只反映主查询，否则用户看不出图上画的是哪份数据。

契约校验里的 Top-N 判据看的是补充查询**自己的 `purpose`**，不是主问题：主问题问
"下降最大的三个月"，而补充查询要的是那几个月的*完整*客户构成，拿主问题去判会
要求它声明 `row_limit`，直接把它挡在门外。

## 4.2 执行链路记录

每个节点的 `_emit_stage` 除了推送 SSE 进度，还会记进一个 ContextVar
（不是 AgentState——`_emit_stage` 分散在二十多个节点里，让每个节点都往返回值里
追加一条既啰嗦又容易漏）。这份链路随 `ChatResponse.trace_steps` 返回，
并写进消息产物，所以翻回历史也看得到。

实测一条典型的取数问题：11 步 11.4 秒，其中 `semantic-compile` 6.2 秒、
`deterministic-answer` 4.9 秒，其余 9 步全在毫秒级。

## 5. QueryPlan 协议

模型必须返回：

```json
{
  "plan": {
    "query_type": "kpi",
    "metric_ids": ["sales_amount"],
    "dimensions": [],
    "filters": [],
    "time_range": {
      "label": "current_month",
      "start": null,
      "end": null,
      "grain": "none"
    },
    "result_shape": "scalar",
    "select_columns": ["sales_amount"],
    "sort": [],
    "row_limit": null,
    "assumptions": [],
    "ambiguities": [],
    "requires_clarification": false,
    "clarification_question": null
  },
  "sql": "SELECT ..."
}
```

### 5.1 QueryPlan 字段

| 字段 | 约束 |
| --- | --- |
| `query_type` | `kpi/trend/ranking/detail/comparison` |
| `metric_ids` | 最多 12 个；解析后过滤为本轮召回的开放指标 |
| `dimensions` | 最多 12 个；常用维度归一为 `month/year/customer/product/salesperson` 等稳定 ID |
| `filters` | 最多 20 个 QueryFilter |
| `time_range` | label、start、end、grain；end 不能早于 start |
| `assumptions` | 最多 10 条 |
| `ambiguities` | 最多 10 条 |
| `requires_clarification` | 是否必须暂停 |
| `clarification_question` | 需要澄清时必须非空 |

QueryFilter：

```json
{"field":"customer","operator":"contains","value":"CODEX"}
```

允许 operator：`eq`、`neq`、`gt`、`gte`、`lt`、`lte`、`in`、`not_in`、`contains`。

### 5.2 严格解析

- `extra=forbid`，未知字段直接失败；
- SQL 最大 20000 字符；
- JSON 提取后调用 Pydantic `model_validate`；
- 时间范围执行模型级校验；
- `time_range.start/end` 只保留 ISO 日期；模型写入动态 SQL 日期表达式时清为 `null`，实际过滤仍由 SQL、filters 和 Guard 校验；
- 根据用户问题把高置信的排行、趋势、比较类型和常用维度展示名归一为稳定协议；
- 无历史上下文的“那个客户/该客户”等指代在 SQL 模型前强制进入 Clarification，不允许退化为全体客户聚合；
- 明确“前 N/Top N”时 `row_limit` 和 SQL `LIMIT` 必须一致；仅问“各项排名”而未给 N 时允许 `row_limit=null`，Guard 自动追加全局 500 行安全上限；
- “销售数量与已开票数量的差额”必须同时输出 `sales_quantity`、`invoiced_quantity` 和 `uninvoiced_quantity`，差额公式为 `SUM(product_uom_qty - qty_invoiced)`；
- 需澄清但没有问题文本时判为无效；
- 结构失败不会直接执行任何 SQL。

## 6. 语义检索

`retrieve_sales_context` 根据关键词选择最小表集合和指标集合：

- 基础销售：`sale_order`、`res_company`、`res_currency`；
- 产品/数量/交付/开票：增加订单明细、产品和单位；
- 客户：增加 `res_partner`；
- 销售员：增加 `res_users` 和 `res_partner`。

同时从数据库 `information_schema` 发现这些白名单字段是否真实存在，并把类型加入 Prompt。模型不会看到完整数据库 Schema。

## 7. SQL 校验和修复

### 7.1 首次校验

模型 SQL 进入 `ReadOnlySqlGuard`。Guard 返回：

```text
safe
normalized sql
errors
tables
```

### 7.2 修复策略

任何失败都先进入 `analyze_sql_error`。当前分类包括：

- QueryPlan/语义编译、语法、结果契约、运行时未知字段/表和类型错误：可以修复；
- 业务歧义：进入 `clarify_sql_error` 并等待用户；
- 写操作、越权表/字段/函数、权限、连接和超时：不做盲目修复；
- SQL 与 QueryPlan 契约同时重复：判定修复循环并终止；SQL 不变但契约已修正时允许重新校验。

只有 `repairable=true` 且修复次数不足时才进入 `repair_sales_sql`：

- QueryPlan/JSON 解析失败导致没有可用 SQL；
- SQL AST Guard 不通过；
- 数据库执行失败且修复次数不足。

修复 Prompt 包含：

- 原问题；
- 语义上下文；
- 上一版 QueryPlan；
- 上一版 SQL；
- 确定性错误列表。
- 结构化 SqlErrorAnalysis 和修复提示。

修复结果仍必须返回完整 QueryPlan + SQL，并重新经过 Wren 编译和同一 Guard。最多两次；如果 SQL + QueryPlan 的组合指纹已经出现过，会立即停止循环。安全违规不能通过增加重试绕过。

## 8. 数据库执行

执行前提：

```text
safe_sql 非空
sql_errors 为空
数据库健康检查为只读
```

执行后写入：列、行、耗时、截断、`data_accessed=true`。如果时间序列为当前年度月度数据，系统可补齐截至当前月的缺失月份并以 0 展示，同时增加 warning。

成功结果随后进入 `profile_sales_result`。画像不调用模型，只计算字段类型、非空数、唯一值数以及数值最小/最大值；它同时提供给 Chart Planner 和复杂回答节点。

### 8.1 Chart Planner

多行且具有可绘制字段的结果调用 `plan-sales-chart`。模型只返回 JSON，随后必须通过 `ChartPlan` 和实际结果字段双重校验：

- 类型只允许 none/table/kpi/line/bar/pie/scatter；
- X、series、排序字段必须真实存在；
- series 必须是数值字段，line 必须使用时间轴，scatter 两轴必须为数值；
- pie 超过 12 类必须 TopN 或改用其他展示；
- series 最多 4 个，TopN 最大 50；
- 禁止返回 ECharts option、JavaScript formatter 或任何可执行代码。

模型不可用、JSON 无效或字段不合法时，Graph 使用确定性规则回退，数据回答仍继续。浏览器只把 ChartPlan 编译成项目内置的 ECharts option，并在展示层执行排序和 TopN。

## 9. 确定性快速路径

### 9.1 判定条件

只有满足以下基础条件才考虑快速回答：

- QueryPlan 有效；
- 结果未截断；
- 结果不超过 24 行。

支持：

| Query type | 条件 |
| --- | --- |
| 空结果 | 任意 QueryPlan，直接输出无数据说明 |
| KPI | 单行、1～4 个数值列 |
| ranking | 单数值指标、至少一个维度 |
| trend | 单数值指标、至少一个维度 |

`detail`、`comparison`、多指标复杂结果继续调用 answer 模型。

### 9.2 设计意义

- 数字不经过第二次模型改写；
- 减少输出 Token；
- 降低延迟；
- 对单行 KPI，Langfuse `model_roles` 可以验证只有 `sql` 角色执行；多行可视化结果会增加 `chart-planner` 角色。

## 10. Model Routing

```text
sql     -> generate-sales-sql / repair-sales-sql
answer  -> plan-sales-chart / explain-sales-result
general -> answer-general-question
```

每次模型调用都把结果合并到 Graph State：

- provider / model；
- 角色映射；
- 输入、输出和总 Token；
- 估算输入、输出和总 Cost。

底层调用已统一经过 LiteLLM Python SDK；应用层仍保存 `sql`、`answer`、`general` 三类业务角色和供应商配置。当前没有启用 LiteLLM Router 自动 fallback，模型不可用时仍返回受控错误。等黄金集能够验证 fallback 不改变口径后，再配置重试、fallback 或负载均衡。

## 11. Checkpointer

### 11.1 配置

Graph 编译时注入一个进程级 Checkpointer：

```text
已配置且可连接 -> AsyncPostgresSaver
否则           -> InMemorySaver
```

`thread_id`：

```text
odoo-sales-agent-v1:{session_id}
```

前缀是 Graph 状态协议版本的一部分。如果未来进行不兼容 State 变更，应提升前缀，避免旧 Checkpoint 反序列化到新协议。

### 11.2 Windows event loop

psycopg 异步连接在 Windows 使用 SelectorEventLoop。启动脚本向 Uvicorn 指定 `app.windows_loop:selector_loop_factory`。

### 11.3 降级

状态库连接或 `.setup()` 失败时：

- 记录 `error_type`；
- 使用内存 Checkpointer；
- Web 和聊天继续工作；
- 设置 API 返回 `active_mode=memory`；
- 后端重启后不能保证恢复。

## 12. Interrupt / Resume

### 12.1 触发

当 QueryPlan：

```text
requires_clarification = true
clarification_question 非空
```

Graph 在 `clarify_query_plan` 调用 `interrupt(payload)`：

```json
{
  "type": "clarification",
  "question": "你希望按未税订单金额还是含税订单金额统计？",
  "ambiguities": ["销售额口径不明确"]
}
```

### 12.2 恢复

API 使用：

```python
Command(resume=answer)
```

恢复后节点从 `interrupt()` 返回：

1. 把补充答案附加到原问题；
2. 清除 QueryPlan 的 clarification 标志；
3. 清空旧 SQL 和错误；
4. 重新检索语义并生成 SQL。

Interrupt 所在节点恢复时可能重新执行，所以中断前不得产生非幂等写操作。当前工作流只读，重复数据库查询没有业务副作用。

## 13. SSE 与 Graph Stream

Graph 使用：

```text
stream_mode = ["custom", "values"]
version = "v2"
```

节点通过 Stream Writer 发出：

```json
{"type":"progress","stage":"sql-validation","label":"正在执行只读 SQL 安全校验"}
```

API 只把 custom progress 和最终 AgentOutcome 发送前端；`values` 用于 Graph 内部完成状态，不直接把整个 State 逐步暴露浏览器。

## 14. Trace 映射

| Graph 操作 | Langfuse Observation |
| --- | --- |
| 整个 Graph | `route-and-answer-odoo-question` Agent |
| SQL 生成 | `generate-sales-sql` Generation |
| SQL 修复 | `repair-sales-sql` Generation |
| SQL 错误分类 | `analyze-sql-error` Tool |
| 图表规划 | `plan-sales-chart` Generation |
| 结果解释 | `explain-sales-result` Generation |
| 普通聊天 | `answer-general-question` Generation |
| 语义检索 | `retrieve-sales-semantic-context` Retriever |
| 数据库检查 | `check-odoo-readonly-database` Tool |
| SQL 校验 | `validate-readonly-sales-sql` Tool |
| SQL 执行 | `execute-readonly-sales-sql` Tool |

稳定名称应被当作观测 API；修改名称会影响 Saved View、Dashboard 和 Evaluator。

## 15. 扩展节点的规则

新增节点时必须：

1. 定义确定的输入/输出 State 字段；
2. 决定是否需要 Checkpoint；
3. 为用户可感知的长步骤发 SSE stage；
4. 使用最具体的 Langfuse Observation 类型；
5. 模型节点必须记录 role、usage、cost；
6. 外部写操作必须考虑 Interrupt 后重放和幂等；
7. 增加单元测试、黄金案例和失败路径；
8. 不得绕过 SQL Guard 或数据库只读连接。

## 16. 相关文档

- [系统架构](02-system-architecture.md)
- [API 参考](05-api-reference.md)
- [数据语义与安全](06-data-and-security.md)
- [Langfuse 可观测性](08-langfuse-observability.md)
