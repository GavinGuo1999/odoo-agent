# Odoo Agent 的 Langfuse 使用指南

这份指南针对当前项目，不包含任何 API Key 或数据库密码。

## 1. 日常最快用法

1. 在聊天页问一个问题。
2. 回答底部点击“打开 Langfuse Trace ↗”。
3. 先看 Trace 顶部的输入、最终回答、总耗时和 Token。
4. 再展开耗时最长或报错的节点，不需要逐个节点都看。

当前项目遵循“一轮用户问答一个 Trace”。同一个聊天窗口内的多轮问答使用同一个 Session ID，因此既能逐轮排错，也能在 Sessions 中查看整段对话。

## 2. 当前 Trace 结构

```text
Trace: odoo-chat-turn
└─ Agent: answer-user-question
   └─ Agent: route-and-answer-odoo-question
      ├─ Tool: check-odoo-readonly-database
      ├─ Retriever: retrieve-sales-semantic-context
      ├─ Generation: generate-sales-sql
      ├─ Tool: validate-readonly-sales-sql
      ├─ Tool: execute-readonly-sales-sql
      └─ Generation: explain-sales-result（仅复杂结果）
```

普通聊天通常只有回答模型节点；销售数据问题才会出现数据库检查、语义检索、SQL 生成、安全校验和只读执行。单 KPI、简单排名/趋势和空结果显示 `deterministic-answer`，不会再产生第二个 Generation。

## 3. 看一个 Trace 时重点检查什么

### 回答不对

- 看根节点 `answer-user-question`：输入问题和最终回答是否一致。
- 看 `retrieve-sales-semantic-context`：是否召回了正确指标和表。
- 看 `generate-sales-sql`：模型生成的 SQL 是否符合业务口径。
- 看 `validate-readonly-sales-sql`：SQL 是否通过只读、表字段白名单和公司过滤。
- 看 `execute-readonly-sales-sql`：原始行数、展示行数、耗时、是否截断。
- 看 `explain-sales-result`：模型是否忠实总结了查询结果。

### 回答太慢

- 先比较两个 Generation 的耗时：`generate-sales-sql` 和 `explain-sales-result`。
- 数据库节点通常应明显更快；若数据库节点变慢，再检查 SQL、返回行数和数据库状态。
- Token 很高时，重点检查语义上下文、历史消息和结果行是否过多。

### SQL 被修复或失败

- 过滤或展开 `repair-sales-sql`。
- 查看前一条 SQL 的校验错误，再看修复后的 SQL。
- 如果同类问题经常触发修复，应优先补语义层示例或收紧生成提示词，而不是只增加重试次数。

## 4. 推荐的筛选方式

- Trace name：`odoo-chat-turn`
- Environment：本地开发通常是 `development`
- Tags：`odoo-agent`、`chatbi`、`sales`
- Session ID：查看同一聊天窗口的上下文
- Model：对比 DeepSeek 与硅基流动上的不同模型
- Status/Level：优先找 Error

稳定名称很重要。不要把用户名、日期或问题正文拼进 Trace/Observation 名称；这些内容应放在 input、metadata、tags 或 session 中。

## 5. Token 和 Cost 怎么看

当前项目不依赖 Langfuse 是否内置了硅基流动模型价格。模型网关会把供应商返回的 Token usage 和应用设置页中的百万 Token 单价，显式写入每个 Generation 的 `usage_details` 与 `cost_details`。因此新 Trace 的 Generation 和 Trace 汇总都应显示非零 Cost。

使用方法：

1. 打开网站“数据与模型”。
2. 在供应商卡片填写最新的“输入/输出价格（每百万 Token）”。
3. 保存后发起一条新问题。
4. 在回答底部先看本轮估算美元成本，再打开 Trace 查看每个 Generation 的拆分。

硅基流动的页面单价按人民币填写，应用使用内部估算汇率换算成美元后写入 Langfuse；DeepSeek 页面单价按美元填写。供应商价格会变化，模型切换后请同步更新。

Langfuse 成本按美元记录。硅基流动报价为人民币，项目使用内部汇率 `R = 人民币/美元` 换算单 Token 美元价格：

```text
input_price_usd_per_token  = 4 ÷ R ÷ 1,000,000
output_price_usd_per_token = 12 ÷ R ÷ 1,000,000
cached_input_usd_per_token = 0.40 ÷ R ÷ 1,000,000
```

这些是应用侧估算成本，不是账单。核对费用时仍以供应商控制台为准。若以后需要多个应用共享价格，再把模型价格统一迁移到 Langfuse Model Definition 或集中模型网关。

## 6. Session 怎么用

- 一个浏览器聊天窗口就是一个 Session。
- 点击“新会话”后应生成新的 Session ID。
- 在 Langfuse 的 Sessions 页面可以查看整段对话、每轮耗时、Token 和错误。
- 调试追问理解问题时，应按 Session 看，而不是只看单个 Trace。

例如用户先问“今年销售趋势”，再问“那上个月呢”，第二问是否正确依赖前一轮上下文，最适合按 Session 检查。

## 7. 点赞/点踩、Score 与黄金 Dataset

每条带 Trace 的回答下方都有 👍/👎：

- 浏览器只把 Trace ID、布尔值和可选备注发给本机后端；
- 后端使用 Secret Key 写入 Langfuse，密钥不会进入浏览器；
- Score 固定名为 `user-thumbs`，数据类型为 BOOLEAN，👍=1、👎=0；
- 在 Langfuse Traces 里按 `user-thumbs = 0` 建 Saved View，可以优先复盘差评。

本地黄金集位于 `evals/datasets/sales_golden.jsonl`，是 Git 中的真源。首批 20 条覆盖：

- 单指标：本月销售额、订单数、平均订单额
- 排名：客户、产品、销售员 Top N
- 趋势：今年每月、最近 12 个月、同比、环比
- 追问：那上个月呢、换成含税金额、只看某客户
- 空数据、歧义问题和越权 SQL 尝试

快速静态回归不会调用模型或数据库：

```powershell
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py
```

真实回归会调用模型和 Odoo，可能产生费用：

```powershell
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py --live --output .\evals\reports\latest.json
```

同步 Langfuse Dataset：

```powershell
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py --sync-langfuse
```

Dataset 名称固定为 `odoo-agent/sales-golden-v1`。后续可为每条 Trace 或 Dataset Run 增加这些分数：

- `sql_safe`：是否通过只读安全校验，0/1
- `sql_executes`：SQL 是否可执行，0/1
- `metric_correct`：指标口径是否正确，0/1
- `answer_grounded`：回答是否只使用查询结果，0～1
- `chart_suitable`：图表类型是否合适，0～1
- `user_feedback`：人工好评/差评，0/1

Ragas 更适合评估 RAG 的检索与忠实度；Text2SQL 还应保留确定性的 SQL 安全、执行和结果对照评分。不要只看一个综合分数。

## 8. Prompt Management 什么时候接

当前阶段提示词在代码中更容易调试。等 SQL 生成和结果解释提示词趋于稳定后，再迁移到 Langfuse Prompt Management：

- SQL 生成和结果解释使用两个独立 Prompt。
- 使用稳定名称与版本标签，例如 `production`、`staging`。
- 发布新版本前先跑固定 Dataset，比较正确率、延迟、Token 和成本。
- Trace 中记录具体 Prompt 版本，出现回归时可以定位。

## 9. 常见问题

### 页面显示“Trace 已记录”，Langfuse 里暂时没有

- 等几秒后刷新；SDK 会批量发送数据。
- 确认运行应用的进程是在配置环境变量之后启动的。
- 到“数据与模型”页确认 Langfuse 显示已配置。
- 运行项目自带的 Langfuse 验证脚本检查认证和上传。

### Trace 有 Token 但 Cost 为 0

先确认这是本次更新后的新 Trace；历史 Trace 不会自动补写应用侧成本。然后检查设置页对应供应商的输入/输出单价是否大于 0，并展开 Generation 查看 `cost_details`。若只改了 Windows 环境变量，请重启后端。

### Time to First Token 为空

当前应用使用非流式回答，因此没有首 Token 时间是正常的。未来改成流式输出后，再记录首 Token 时间更有意义。

## 10. 官方参考

- Langfuse Observability 最佳实践：https://langfuse.com/docs/observability/best-practices
- Trace URL：https://langfuse.com/docs/observability/features/url
- Token 与成本：https://langfuse.com/docs/observability/features/token-and-cost-tracking
- Sessions：https://langfuse.com/docs/observability/features/sessions
- Environments：https://langfuse.com/docs/observability/features/environments
- 硅基流动模型与价格：https://www.siliconflow.cn/models
