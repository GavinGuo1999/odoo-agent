# API 参考

> API 版本：随应用 0.2.0
>
> Base URL：`http://127.0.0.1:8090/api`
> OpenAPI：`http://127.0.0.1:8090/docs`

## 1. 通用约定

- 编码：UTF-8；
- REST 请求/响应：`application/json`；
- 流式聊天：`text/event-stream`；
- 日期：ISO 8601；
- 金额：JSON number，币种通过 `currency` 或销售看板 `currency` 返回；
- **认证**：配置 `AGENT_UI_PASSWORD_HASH` 后，除 `/auth/*` 外**所有**接口都需要
  `Authorization: Bearer <token>`，否则返回 401。留空则不启用（本机开发默认如此）。
  它是单口令演示门禁，不是多用户权限系统；
- 密钥和数据库密码永远不会通过 GET 设置接口回显。

## 2. 接口总览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 应用、模型、Langfuse 和 Odoo 健康状态 |
| GET | `/database/status` | Odoo 只读连接详情 |
| GET | `/database/schema` | 实际开放表字段 |
| GET | `/sales/dashboard` | 固定销售看板数据 |
| GET | `/sales/metrics` | 版本化指标定义 |
| POST | `/chat` | 非流式问答 |
| POST | `/chat/stream` | SSE 流式步骤和最终结果 |
| POST | `/chat/resume` | 非流式恢复 Interrupt |
| POST | `/chat/resume/stream` | SSE 恢复 Interrupt |
| GET | `/auth/session` | 探测是否需要登录、当前 token 是否有效（公开） |
| POST | `/auth/login` | 用口令换取 token（公开） |
| POST | `/auth/logout` | 作废当前 token |
| GET | `/chat/sessions/{session_id}` | 恢复会话、待处理 Interrupt 和每条消息的渲染产物 |
| GET | `/chat/conversations` | 按最近活动时间列出会话 |
| DELETE | `/chat/conversations/{session_id}` | 删除会话目录和 Checkpoint |
| POST | `/chat/conversations/{session_id}/detach` | 标记页面刷新导致的流断开 |
| POST | `/chat/feedback` | 写入 Langfuse 点赞/点踩 |
| GET | `/settings` | 安全读取设置状态 |
| PUT | `/settings` | 保存设置到 Windows 用户环境 |
| GET | `/settings/models/{provider}` | 读取供应商模型目录 |
| GET | `/semantic-audit/latest` | 读取最近一次 Odoo 语义一致性审计 |
| POST | `/semantic-audit` | 运行一次只读语义一致性审计 |
| GET | `/wiki/status` | 读取或自动更新 Wiki 索引状态 |
| GET | `/wiki/search` | 检索已审核 Wiki 笔记 |
| POST | `/wiki/reindex` | 强制重建 BI 项目侧 Wiki 索引 |

## 3. 系统接口

### 3.1 GET `/health`

返回应用基本状态、两个模型供应商配置状态和 Odoo 健康摘要。

```powershell
Invoke-RestMethod 'http://127.0.0.1:8090/api/health'
```

主要字段：

| 字段 | 说明 |
| --- | --- |
| `status` | FastAPI 可响应时为 `ok` |
| `version` | 应用版本 |
| `ready_for_model_calls` | 默认供应商是否具备 Key、URL 和 Model |
| `selected_provider` | 当前默认供应商 |
| `services.langfuse.configured` | Langfuse 是否启用且 Key 完整 |
| `services.providers.*.configured` | 供应商是否配置 |
| `services.odoo_database.status` | `connected` / `not-connected` |
| `services.odoo_database.read_only` | 当前事务是否只读 |

`status=ok` 只表示 Web 服务可用，不保证模型和数据库都可用；调用方应继续检查 `services`。

## 3.2 认证接口

只有这三个是公开的；其余全部需要 token。

### GET `/auth/session`

前端启动时先问这里，决定要不要弹登录框。

- 未配置口令：`200 {"required": false, "authenticated": true}`
- 配置了且 token 有效：`200 {"required": true, "authenticated": true}`
- 配置了但没带/带错 token：`401`

### POST `/auth/login`

```json
{"password": "..."}
```

成功返回 `{"required": true, "token": "...", "expires_in": 43200}`。

口令错误返回 `401`，**不区分"口令错"和"未配置口令"**——避免把服务端状态透露出去。
同一来源连错 5 次锁 5 分钟，锁定期间返回 `429` 并带剩余秒数。

### POST `/auth/logout`

作废当前 token。总是返回 `{"ok": true}`。

## 4. 数据库接口

### 4.1 GET `/database/status`

响应示例：

```json
{
  "connected": true,
  "read_only": true,
  "user": "codex_readonly",
  "database": "odoo19_dev",
  "company_id": 1,
  "company_name": "My Company",
  "currency": "USD",
  "order_count": 23,
  "response_ms": 180.5,
  "error_type": null
}
```

数据库失败时接口仍返回结构化健康结果，`connected=false`，并在 `error_type` 中提供错误类型而非密码或完整连接串。

### 4.2 GET `/database/schema`

返回语义层版本和数据库中实际存在的开放字段。

```json
{
  "version": "2026-08-03.v1",
  "tables": [
    {
      "name": "sale_order",
      "description": "销售订单主表；一行代表一张报价单或销售订单。",
      "columns": [
        {"name": "id", "type": "integer"},
        {"name": "amount_untaxed", "type": "numeric"}
      ]
    }
  ]
}
```

接口只检查白名单字段，不返回完整 Odoo Schema。

## 5. 销售看板接口

### 5.1 GET `/sales/dashboard`

查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `period` | `week/month/quarter` | `month` | KPI 当前周期 |
| `salesperson_id` | positive integer | 无 | 可选销售员过滤 |

示例：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8090/api/sales/dashboard?period=month'
```

响应包含：

- `generated_at`、公司、币种和日期范围；
- 销售额、订单数、平均订单额、活跃客户及对比期；
- 趋势和月度趋势；
- 订单状态；
- 客户和产品排名；
- 待处理关注项；
- 销售员列表和最近订单。

该接口使用固定 SQL，不调用 LLM。

### 5.2 GET `/sales/metrics`

返回语义层版本和全部销售指标：

```json
{
  "version": "2026-08-03.v1",
  "metrics": [
    {
      "id": "sales_amount",
      "name": "销售额",
      "description": "已确认销售订单的未税金额。",
      "expression": "SUM(sale_order.amount_untaxed)",
      "states": ["sale", "done"],
      "date_field": "sale_order.date_order"
    }
  ]
}
```

## 6. 聊天请求

### 6.1 ChatRequest

```json
{
  "question": "本月销售额是多少？",
  "session_id": "browser-session-id",
  "provider": null,
  "history": []
}
```

| 字段 | 约束 | 说明 |
| --- | --- | --- |
| `question` | 1～4000 字符 | 用户问题 |
| `session_id` | 可选，1～200 字符 | 为空时后端生成；流式前端通常主动生成 |
| `provider` | `deepseek/siliconflow/null` | 可选整轮供应商覆盖；设置后该轮三个角色使用同一供应商 |
| `history` | 最多 20 条 | `role=user/assistant`，每条 1～4000 字符 |

若 `history` 为空且状态库存在同一 Session，Agent 从 Checkpointer 读取持久历史。

### 6.2 POST `/chat`

非流式返回完整 `ChatResponse`。

```powershell
$body = @{
    question = '本月销售额是多少？'
    session_id = 'api-demo-session'
    history = @()
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Uri 'http://127.0.0.1:8090/api/chat' `
    -Method Post `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

### 6.3 POST `/chat/stream`

请求体与 `/chat` 相同，响应为 SSE。

```powershell
$body = @{
    question = '销售额最高的十个客户是谁？'
    session_id = 'api-stream-demo'
    history = @()
} | ConvertTo-Json -Depth 5

Invoke-WebRequest `
    -Uri 'http://127.0.0.1:8090/api/chat/stream' `
    -Method Post `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

响应头：

```text
Content-Type: text/event-stream
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
```

## 7. SSE 事件协议

### 7.1 `progress`

```text
event: progress
data: {"type":"progress","stage":"sql-generation","label":"正在生成查询计划和 SQL"}
```

稳定的 `stage` 值：

| stage | 含义 |
| --- | --- |
| `classify` | 判断问题类型 |
| `general` | 普通回答 |
| `semantic` | 指标解释 |
| `knowledge-retrieval` | 检索已审核 Odoo Wiki |
| `knowledge-answer` | 根据 Wiki 组织带引用回答 |
| `database-check` | Odoo 只读连接检查 |
| `semantic-retrieval` | 销售语义和 Schema 检索 |
| `sql-generation` | QueryPlan + SQL 生成 |
| `clarification` | 即将或正在等待用户澄清 |
| `sql-validation` | SQL 安全校验 |
| `sql-repair` | SQL 修复 |
| `sql-execution` | Odoo 查询 |
| `deterministic-answer` | 确定性结果摘要 |
| `answer-synthesis` | 复杂结果模型解释 |
| `safe-failure` | 安全失败处理 |
| `complete` | Graph 完成 |

新增 stage 应被前端按未知阶段兼容展示，不能导致解析失败。

### 7.2 `result`

```text
event: result
data: { ...完整 ChatResponse... }
```

### 7.3 `error`

```text
event: error
data: {"detail":"Agent request failed: ErrorType"}
```

SSE HTTP 连接建立成功后，Graph 错误通过事件返回，调用方不能只看 HTTP 状态码判断最终成功。

## 8. ChatResponse

主要字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `answer` | string | 最终回答或澄清问题 |
| `session_id` | string | 会话 ID |
| `provider`, `model` | string | 最后执行模型；具体角色见 `model_roles` |
| `usage` | object | 输入、输出、总 Token 和估算 USD Cost |
| `trace_id`, `trace_url` | nullable string | Langfuse 追踪信息 |
| `status` | `completed/interrupted` | 是否等待用户恢复 |
| `interrupt` | nullable object | 澄清问题和歧义列表 |
| `data_accessed` | boolean | 是否真实执行 Odoo SQL |
| `phase` | `general-chat/knowledge-base/semantic-layer/text2sql` | 处理阶段 |
| `intent` | `general/knowledge/source/semantic/data/hybrid` | 问题类型 |
| `sql` | nullable string | 通过 Guard 的最终 SQL |
| `columns`, `rows` | array | 查询结果 |
| `chart` | nullable object | 白名单图表协议 |
| `metrics` | array | 识别到的语义指标 ID |
| `query_ms` | nullable number | 数据库执行耗时 |
| `truncated` | boolean | 是否因行数上限截断 |
| `warnings` | array | 补零、截断等提醒 |
| `query_plan` | nullable object | 结构化查询计划 |
| `answer_mode` | string | `llm/deterministic/knowledge/semantic/failure` |
| `model_roles` | object | 每个实际执行角色的 provider/model |
| `citations` | array | Wiki 标题、章节、摘要、路径和 Obsidian URI |

QueryPlan：

```json
{
  "query_type": "trend",
  "metric_ids": ["sales_amount"],
  "dimensions": ["month"],
  "filters": [],
  "time_range": {
    "label": "current_year",
    "start": null,
    "end": null,
    "grain": "month"
  },
  "assumptions": [],
  "ambiguities": [],
  "requires_clarification": false,
  "clarification_question": null
}
```

## 9. Interrupt 恢复

### 9.1 ChatResumeRequest

```json
{
  "session_id": "api-stream-demo",
  "answer": "按销售订单未税金额，并且只看今年。",
  "provider": null
}
```

### 9.2 POST `/chat/resume`

非流式恢复，返回 `ChatResponse`。

### 9.3 POST `/chat/resume/stream`

流式恢复，事件协议与 `/chat/stream` 相同。必须使用触发 Interrupt 的原 `session_id`。对不存在或没有待恢复 Interrupt 的 Session 调用，Graph 将返回受控错误事件。

## 10. 会话接口

### GET `/chat/sessions/{session_id}`

```json
{
  "session_id": "api-stream-demo",
  "history": [
    {"role": "user", "content": "本月销售额是多少？", "artifact": null},
    {
      "role": "assistant",
      "content": "查询结果：销售额为 ...",
      "artifact": {
        "provider": "deepseek", "model": "deepseek-v4-pro",
        "usage": {"total_tokens": 5304, "estimated_cost_usd": 0.0039},
        "trace_id": "7b0253b...", "trace_url": "https://cloud.langfuse.com/...",
        "trace_steps": [{"stage": "classify", "label": "正在判断问题类型", "at_ms": 0.0}],
        "repair_count": 0,
        "sql": "SELECT ...", "columns": ["month", "sales_amount"],
        "rows": [{"month": "2026-01-01", "sales_amount": 450315.75}],
        "row_count": 9, "chart": {"type": "line"},
        "column_labels": {"month": "月份"}, "citations": []
      }
    }
  ],
  "pending_interrupt": null,
  "persistence_mode": "postgres",
  "run_status": "completed"
}
```

`artifact` 是这条消息重绘所需的一切：图表规格、明细行、SQL、Wiki 引用，以及
出处信息（哪个模型、花了多少、Langfuse 在哪、走过哪些节点）。用户消息恒为 `null`。

字段名刻意与 `ChatResponse` 保持一致，前端可以用同一段渲染代码处理"刚回答的"
和"从历史恢复的"两种情况。

产物存在独立表 `odoo_agent_message_artifacts`（按 `session_id` + 消息下标），
不在 Checkpoint 里；单轮明细行上限 `500`，超出时 `rows_trimmed=true` 而
`row_count` 仍是真实行数。

`trace_steps` 里的 `at_ms` 是**距本轮开始**的累计毫秒；某一步自己花的时间要用
相邻两项相减。

`run_status` 可能为 `new/running/completed/interrupted/failed/cancelled`。生成开始前，用户问题会以 `pending_question` 形式写入独立状态库，并在该接口的 `history` 中恢复。

`persistence_mode=memory` 表示后端重启后无法保证恢复。

### GET `/chat/conversations`

返回会话标题、创建时间、最近活动时间和当前持久化模式。标题来自第一条用户问题，不额外调用模型。

### DELETE `/chat/conversations/{session_id}`

同时删除会话目录记录和该 Session 的 LangGraph Checkpoint。

### POST `/chat/conversations/{session_id}/detach`

网页在生成过程中刷新或关闭时使用。接口只把仍处于 `running` 的记录标记为 `cancelled` 并保留问题；如果后台任务随后正常完成，状态仍可更新为 `completed`。

## 11. 用户反馈接口

### POST `/chat/feedback`

```json
{
  "trace_id": "0123456789abcdef0123456789abcdef",
  "positive": true,
  "comment": "数字和口径正确"
}
```

约束：

- `trace_id` 必须为 32 位十六进制；
- `comment` 最长 500 字符；
- Langfuse 未配置时返回 503；
- 后端写入 BOOLEAN `user-thumbs` Score。

成功响应：

```json
{"recorded": true, "score_name": "user-thumbs"}
```

## 12. 设置接口

### 12.1 GET `/settings`

返回：

- 默认供应商；
- 两个供应商的 configured、URL、model、单价和币种；
- 三个节点角色路由；
- Langfuse configured、URL 和 enabled；
- Odoo 非敏感配置及密码是否已配置；
- 状态库非敏感配置、`active_mode` 和 `error_type`；
- `persistence=windows-user-environment`。

不会返回 API Key 或密码。

### 12.2 PUT `/settings`

该接口要求提交完整配置。Secret 字段传 `null` 表示保留现有值。

```json
{
  "selected_provider": "siliconflow",
  "deepseek": {
    "api_key": null,
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-pro",
    "input_price_per_million": 0.435,
    "output_price_per_million": 0.87
  },
  "siliconflow": {
    "api_key": null,
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "deepseek-ai/DeepSeek-V4-Pro",
    "input_price_per_million": 4,
    "output_price_per_million": 12
  },
  "routing": {
    "sql": {"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V4-Pro"},
    "answer": {"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V4-Pro"},
    "general": {"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V4-Pro"}
  },
  "langfuse": {
    "public_key": null,
    "secret_key": null,
    "base_url": "https://cloud.langfuse.com",
    "enabled": true
  },
  "database": {
    "host": "127.0.0.1",
    "port": 55432,
    "database": "odoo19_dev",
    "user": "codex_readonly",
    "password": null,
    "company_id": 1,
    "statement_timeout_ms": 15000,
    "max_rows": 500
  },
  "state_database": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 55432,
    "database": "odoo_agent_state",
    "user": "odoo_agent_state",
    "password": null
  }
}
```

`restart_required=true` 表示状态库或已初始化的 Langfuse 连接发生变化，应重启后端。

### 12.3 GET `/settings/models/{provider}`

`provider` 只允许 `deepseek` 或 `siliconflow`。后端使用已保存 Key 请求供应商模型目录，最多返回 500 个唯一 Model ID。Key 不会返回客户端。

## 13. 语义审计接口

### 13.1 GET `/semantic-audit/latest`

返回最近一次 PostgreSQL、Odoo ORM、本地源码和正式 Wren MDL 的一致性审计摘要。首次审计前返回 `404`。

### 13.2 POST `/semantic-audit`

运行只读审计并返回摘要。响应包含 `status`、错误/警告/提示数量、扫描统计、报告路径、快照路径、隔离 Wren 草稿路径和差异明细。正式 MDL 不会被修改；已有审计运行时返回 `409`，数据库或源码不可用时返回 `503`。

完整工作流见 [Odoo 语义同步与一致性审计](14-odoo-semantic-sync-and-audit.md)。

## 14. HTTP 状态码

| 状态码 | 场景 |
| --- | --- |
| 200 | 正常成功；SSE 需继续检查事件 |
| 204 | `/favicon.ico` |
| 404 | 不支持供应商、非白名单 UI 文件等 |
| 422 | Pydantic 校验失败、Langfuse Key 不成对、状态库与 Odoo 同库 |
| 502 | 模型调用、模型目录或 Langfuse Feedback 失败 |
| 503 | 所需模型 Key 未配置、Langfuse 未配置，或语义审计的数据源/源码不可用 |
| 500 | Windows 用户环境保存失败 |

## 15. 兼容性规则

- 调用方必须忽略未知响应字段，以便后续扩展；
- SSE 客户端必须兼容未知 `stage`；
- `trace_id`、`session_id` 应按不透明字符串处理；
- SQL 和 QueryPlan 是可观测输出，不应作为写操作输入；
- API 当前未做版本前缀，破坏性变更前应先引入 `/api/v1`。

## 16. 相关文档

- [系统架构](02-system-architecture.md)
- [用户使用手册](04-user-guide.md)
- [LangGraph Agent 工作流](07-langgraph-workflow.md)
