# TDD 测试与验收红绿灯

> 基线日期：2026-08-28
>
> 最近增量验证：2026-09-03
>
> 适用应用版本：0.2.0
>
> 适用范围：`odoo-agent`、`custom_addons/custom_ai_siliconflow`、`text2sql-benchmark-lab` 和 `learn_odoo`
>
> 目标：明确测试先行方式、自动化边界、用户验收责任和发布门禁

## 1. 两套状态不要混淆

开发过程采用标准 TDD 循环：

1. **Red**：先写能稳定复现需求或缺陷的失败测试；
2. **Green**：用最小实现让测试通过；
3. **Refactor**：在测试保护下整理实现，不改变行为；
4. **Regression**：运行受影响范围和发布门禁，防止旧能力退化。

交付过程使用下面的红绿灯。这里的红灯不一定表示代码失败，也可能表示必须由用户完成人工或业务验收。

| 灯色 | 含义 | 默认执行者 | 转绿条件 |
| --- | --- | --- | --- |
| 🟢 绿灯 | Codex 可独立、可重复、无账号风险地自动验证，且当前基线已通过 | Codex | 自动检查退出码为 0，结果满足明确断言 |
| 🟡 黄灯 | Codex 能执行，但需要用户授权、运行中的本地服务、测试数据库、模型额度或测试账号 | Codex + 用户提供前置条件 | 前置条件满足后由 Codex 执行，并保存可审计证据 |
| 🔴 红灯 | 必须由用户判断业务正确性、视觉体验、权限边界、费用接受度或高风险写操作 | 用户 | 用户明确签字确认，记录日期、环境和证据 |

任何发布候选都不得存在“自动测试失败”的红灯。人工红灯可以暂缓，但必须明确标成未验收，不能写成已完成。

## 2. 当前基线

| 仓库 | 基线提交 | 工作树 | 当前证据 |
| --- | --- | --- | --- |
| `odoo-agent` | `313bdd4` 后的本轮待提交变更 | 产品名称中英回退 Guard 与当前代码全量 A/B | 后端 115/115；前端语法通过；静态黄金集 20/20；Native 全量 60/60，Wren 59/60 且唯一失败为上游 504，已执行结果签名均为 100% |
| `custom_addons` | `7f8dda1` | 提交后干净 | SiliconFlow 模块 11 个 Python、2 个 XML 文件静态解析通过 |
| `text2sql-benchmark-lab` | `3d7f01c` | 干净 | 7 项基准核心测试通过 |
| `learn_odoo` | `a014c51` | 提交后干净 | Markdown 严格 UTF-8、Obsidian JSON 和 Canvas JSON 解析通过 |

> 2026-09-06 提醒：`313bdd4` 之后仍有一批未提交改动（Wiki 向量检索、RAGAS 评测、Prompt Management、Langfuse Dataset Experiment、settings 前端），因此上表的 115/115 基线**不代表当前工作树**。提交前需重跑全量门禁。未关闭差异见 [当前状态与未关闭差异](19-current-status-and-open-gaps.md)。

本轮已在 `odoo19_dev`、`codex_readonly`、DeepSeek 真实模型和 Langfuse development 环境完成三轮 Native/Wren A/B。Odoo 模块安装、一次性测试库 TransactionCase 和用户业务验收仍未执行。

## 3. Codex 可独立完成的绿灯测试

这些检查不需要真实 API Key、个人账号或业务数据库写入。修改相关代码后，Codex 应主动运行，不需要再次询问。

| ID | 范围 | 检查 | 当前 | 通过标准 |
| --- | --- | --- | --- | --- |
| AUTO-01 | Agent 后端 | `unittest` 全量测试 | 🟢 115/115 | 全部通过，无测试进程残留 |
| AUTO-02 | Agent 前端 | `node --check app.js` | 🟢 | 退出码为 0 |
| AUTO-03 | 静态黄金集 | 20 条意图路由静态评测 | 🟢 20/20 | 通过率 100% |
| AUTO-04 | SQL 安全 | 写操作、未知表/字段、公司过滤、输出契约、LIMIT、JOIN/CTE/子查询、笛卡尔积、明细时间边界 | 🟢 | 所有安全断言通过；正常 4 表 Wren SQL 不回归 |
| AUTO-05 | Agent 工作流 | 路由、Interrupt/Resume、SSE、会话、快速回答 | 🟢 | 对应单元/API 测试全部通过 |
| AUTO-06 | 语义能力 | Wren provider、语义审计、Wiki 检索与引用 | 🟢 | 对应测试全部通过 |
| AUTO-07 | 观测适配 | Langfuse 缺少凭据时降级、脱敏、反馈 Score | 🟢 | Mock/本地测试全部通过且无密钥输出 |
| AUTO-08 | 基准实验室 | SQL inspection 与结果语义比较 | 🟢 7/7 | 全部通过 |
| AUTO-09 | Odoo 自定义模块静态检查 | Python AST、XML、Manifest、敏感信息扫描 | 🟢 | 可解析、无真实密钥、无明文凭据 |
| AUTO-10 | 知识库 | UTF-8、Obsidian JSON、Canvas JSON | 🟢 | 所有文件可解析 |
| AUTO-11 | Git 交付 | `git diff --check`、状态和提交内容复核 | 🟢 | 无空白错误、无意外文件、无凭据 |
| AUTO-12 | SQL Error Analyzer | 分类、危险 SQL 拒绝、可修复执行错误、重复指纹终止 | 🟢 | 危险项不修复；可修复项最多两次；无循环执行 |
| AUTO-13 | Chart Planner | DataProfile、Pydantic ChartPlan、字段/类型白名单和规则回退 | 🟢 | 非法字段被拒绝；模型失败不影响数据回答 |
| AUTO-14 | 项目级 Skills/Waza 资产 | 3 个 Skill、UI 元数据、文档链接、Mock 默认值、正负触发样例与官方 Schema | 🟢 3 Skills / 6 Tasks | Skill Creator 3/3；Waza Schema 1 配置、3 Eval、6 Task 全部通过 |

标准命令：

```powershell
# Agent 后端
Set-Location D:\odoo19e\odoo-agent\backend
& 'D:\odoo19e\odoo-agent\.venv\Scripts\python.exe' -m unittest discover -s tests -v

# Agent 前端与静态黄金集
Set-Location D:\odoo19e\odoo-agent
node --check .\app.js
& '.\.venv\Scripts\python.exe' '.\evals\run_sales_eval.py'
& '.\.venv\Scripts\python.exe' -m unittest backend.tests.test_project_skills -v

# Text2SQL 实验室
Set-Location D:\odoo19e\text2sql-benchmark-lab
& '.\.venv-benchmark\Scripts\python.exe' -m unittest discover -s tests -v
```

静态黄金集目前主要检查意图分类，不能替代真实 SQL、数值和时间边界评测。

## 4. Codex 可执行、但需要用户授权的黄灯测试

| ID | 测试 | 为什么需要授权或前置条件 | Codex 负责 | 用户负责 | 当前 |
| --- | --- | --- | --- | --- | --- |
| COLLAB-01 | 启动 Odoo/PostgreSQL 并检查 `/web/login` | 现有启动脚本会删除并重建受保护日志 | 启停、端口、HTTP、日志摘要 | 明确授权脚本修改日志 | 🟢 HTTP 200，已执行 |
| COLLAB-02 | 启动 Agent 并检查 `/api/health`、`/docs`、首页 | 依赖本地 Odoo、状态库和环境配置 | 启停、API smoke、进程清理 | 允许使用当前本地配置 | 🟢 只读 DB/状态库/UI 已执行 |
| COLLAB-03 | 真实 20 条销售黄金集 | 会访问只读业务库并调用付费模型 | 执行、比较、生成失败差异 | 确认可使用模型额度和本地只读数据 | 🟢 DeepSeek 20/20 |
| COLLAB-04 | 最新三轮 Text2SQL 基准 | 会消耗模型额度 | 运行、统计结果签名、p50/p95、Token、Cost、Repair | 授权模型调用和只读数据库访问 | 🟢 2026-09-03 当前代码：Native 60/60；Wren 59/60，唯一失败为上游 504，结果签名 47/47 |
| COLLAB-05 | Langfuse 实际 Trace | 会连接已配置的外部 Langfuse 项目 | 生成测试 Trace并核对字段、脱敏和 Cost | 授权使用该项目连接 | 🟢 已用于首错定位和真实 Trace 复核 |
| COLLAB-06 | PostgreSQL Checkpointer 重启恢复 | 需要独立状态数据库和服务重启 | 创建测试会话、重启、验证恢复 | 确认可使用测试状态库 | 🟡 未执行 |
| COLLAB-07 | SiliconFlow 模块安装/升级 | 安装或升级会写数据库元数据 | 在一次性测试库安装、记录日志和模块状态 | 授权创建/使用一次性测试库 | 🟡 未执行 |
| COLLAB-08 | SiliconFlow 9 个 Odoo TransactionCase | 需要 Odoo registry 和测试数据库 | 运行测试标签、收集结果、清理测试进程 | 提供或授权创建测试库 | 🟡 已写未运行 |
| COLLAB-09 | SiliconFlow 真实 API smoke | 使用用户配置的 API Key并产生费用 | 调用 chat、tool、structured output、embedding | 在本机设置密钥并授权额度；不把密钥发到聊天或仓库 | 🟡 Chat 返回 HTTP 402，需补充额度；适配代码已测 |
| COLLAB-10 | Localhost 浏览器自动化 | 需要服务运行，部分页面需要 Odoo 测试登录 | 自动点击、截图、检查控制台与网络错误 | 提供专用测试账号或完成登录 | 🟢 工作台/助手/历史恢复，控制台 0 error/warn |
| COLLAB-11 | 真实 Chart Planner 模型调用 | 会使用 answer 模型额度并把脱敏后的字段画像发给供应商 | 固定样例运行、记录 Schema 通过率、延迟、Token、Cost | 授权当前模型额度和外部数据处理边界 | 🟢 KPI/line/bar/none 均有真实样例 |

黄灯执行规则：

- 默认只访问 `127.0.0.1` 和明确授权的模型/Langfuse服务；
- 数据库分析使用只读账号；
- Odoo 模块安装和 TransactionCase 优先使用一次性测试库，不对 `odoo19_dev` 做破坏性操作；
- 密钥只保存在当前 Windows 用户环境变量或 Odoo 配置参数中，不打印、不复制到文档、不提交；
- 测试结束后关闭由测试启动的 Odoo、Agent 和临时进程；
- 任何失败都保留测试名、错误类型和脱敏摘要，不保存敏感请求正文。

### 4.1 本轮真实评测与 TDD 证据

环境：`odoo19_dev`、`codex_readonly`、公司 `My Company`、币种 `USD`、语义 provider `native`、DeepSeek `deepseek-v4-pro` 请求级覆盖、PostgreSQL Checkpointer、Langfuse development Trace。没有修改默认供应商设置，没有执行写 SQL。

| 阶段 | 结果 | 真实发现 | Red → Green |
| --- | --- | --- | --- |
| 首轮 | 🔴 3/20（15%） | 模型 SQL 接近正确，但 QueryPlan 不接受 `lt/gt`；动态日期元数据验证失败；Repair 未重述完整契约 | 先补失败测试，再加入范围操作符、动态日期元数据归一化、字段级脱敏错误和完整 Repair Schema |
| 第二轮 | 🟡 14/20（70%） | 时间范围被误判为未授权；客户名称字段存在 `partner_id/partner_name/name` 漂移；维度和平均订单额 ID 不统一 | 先复现 Guard/别名/指标测试，再对齐 `date_order` 授权、客户名称字段、稳定维度 ID 和 `average_order_value` |
| 最终 | 🟢 20/20（100%） | 全部结构断言、只读 SQL、数据访问、空结果、提示注入和 Interrupt 均通过 | 前 12 条通过后测试宿主退出；重启本地服务并从第 13 条断点续跑 8/8，合计逐 Case 20/20 |

最终自动证据：

- 后端 `unittest`：115/115；
- 静态黄金集：20/20；
- 前端 `node --check app.js`：通过；
- 真实黄金集：20/20；
- Human-in-the-loop：首次请求 `interrupted`、`data_accessed=false`、无 SQL；补充客户后从同一 session 恢复，执行只读 SQL 并返回 KPI；
- Chart Planner：真实返回 KPI、line、bar 和空结果 none，配置经 Pydantic 和结果字段白名单验证；
- SQL 安全：提示注入用例只执行 SELECT/WITH；未知/未授权字段未绕过 Guard；
- 日期授权收紧后的当前代码实测：`上个月`、`2099 年`、本月基线及同会话“改成上个月”共 4/4，通过只读 SQL 返回预期数据或零值；
- 浏览器：工作台和智能助手可加载、数据库显示只读、历史会话可恢复，控制台 0 error/warn；
- Langfuse：用首个失败 Observation 定位问题，未在文档或日志中保存 API Key。

当前 16 个可执行且不应 Interrupt 的数据 Case 已全部建立参考 SQL 结果签名，检查列、行、数值容差和时间边界；另外 4 个普通问答、语义解释或澄清 Case 继续验证结构行为。参考 SQL 是自动回归基线，仍不能代替 UAT-01 的业务口径签字。

### 4.2 结果签名、复杂度 Guard 与三轮 A/B（2026-09-01）

- Red：结果列/数值不一致未失败；JOIN/CTE/子查询/笛卡尔积和无时间明细未被拒绝；Wren 同名 CTE/重复 `__source` 产生错误 lineage；
- Green：SHA-256 结果签名、绝对/相对数值容差、午夜日期等价、16 条参考 SQL、复杂度预算和按 scope 的 Wren lineage 测试全部通过；
- 真实参考 SQL：16/16 通过 `codex_readonly` 执行；
- 修后 Native：三轮 19/20、19/20、18/20，总通过率 93.33%，结果签名率 93.75%；
- 修后 Wren：三轮 17/20、17/20、18/20，总通过率 86.67%，结果签名率 87.50%；
- Wren 修复前后总通过率：61.67% → 86.67%；
- 当时仍失败：发票差额列、Wren 无显式 Top N 的销售员排名、客户指代澄清稳定性；这些问题已在 4.3 的 2026-09-02 增量中关闭；
- 默认 SiliconFlow 探针仍返回 HTTP 402；本轮只在评测进程覆盖为已配置 DeepSeek，没有修改持久设置；
- 一次首组基准中 Langfuse 批量上报出现旁路超时，本地报告未丢失，修后基准未再出现。

完整数据见 [Native / Wren 三轮真实 A/B 基准](18-native-wren-ab-benchmark.md)。

### 4.3 前三项 TDD 闭环（2026-09-02）

| 主题 | Red | Green | 针对性真实三轮 |
| --- | --- | --- | --- |
| 确定性客户澄清 | SQL 模型返回坏 QueryPlan 时不能稳定 Interrupt | 新增 `detect_data_ambiguity`，模型和数据库之前直接 Interrupt；同 session 补充后恢复 | Native 3/3；Wren 3/3 |
| 未开票差额 | 只返回销售量/开票量或只返回差额都会被结果签名拒绝 | 新增 `uninvoiced_quantity`，并强制 `product + sales_quantity + invoiced_quantity + uninvoiced_quantity` 输出合同 | Native 3/3；Wren 3/3 |
| 无显式 Top N 排名 | Wren 为完整销售员排名反复补 `row_limit` 失败 | 显式 Top N 才要求 LIMIT；完整排名由 Guard 加 500 行上限，ChartPlan 展示前 10 | Native 3/3；Wren 3/3 |

第一次针对性运行双方均为 6/9，结果签名指出发票差额列仍不完整；没有修改参考结果，而是新增确定性 QueryPlan 合同后重跑。修后 Native/Wren 都是总通过率、结构通过率和结果签名率 100%。后端全量 114/114、静态黄金集 20/20、Wren 9 模型构建和 `node --check app.js` 均通过。该结果是三个受影响 Case 的窄回归；4.4 记录了后续当前代码全量三轮。

### 4.4 产品名称回退合同与当前代码全量三轮（2026-09-03）

| 阶段 | 结果 | 结论 |
| --- | --- | --- |
| 首次全量复验 | Native 59/60；Wren 60/60 | Native 的 `comparison-invoice` 结构通过但结果签名失败，证明真实数据回归仍能发现 Prompt/结构断言之外的问题 |
| Trace 根因 | 产品名只取 `zh_CN`，未回退 `en_US` | 中文名为空的产品被合并；参考 SQL 与既有业务规则正确，不放宽结果签名 |
| Red → Green | 新增 Guard 失败测试并强制 `COALESCE(zh_CN, en_US)` 顺序 | 后端全量升至 115/115；针对该 Case 的 Native/Wren 各三轮均 3/3 |
| 最终全量复验 | Native 60/60；Wren 59/60 | Wren 唯一失败为模型调用 HTTP 504、未生成 SQL；Native 48/48、Wren 47/47 已执行结果签名均为 100% |

最终全量还通过静态黄金集 20/20、Wren 9 模型构建和 `node --check app.js`。Wren 相比 Native 的 p50 增加 5.39s、Token 增加 71,656、估算费用增加 $0.032997、Repair 增加 4 次；p95 降低 0.64s。外部 504 作为可用性失败原样保留，没有用重跑替换。完整数据见 [Native / Wren 三轮真实 A/B 基准](18-native-wren-ab-benchmark.md)。

## 5. 必须由用户完成的红灯验收

这些项目需要业务判断或高风险授权。Codex 可以准备步骤、生成对照数据和记录模板，但不能代替用户签字。

| ID | 用户验收项 | 建议做法 | 转绿证据 | 当前 |
| --- | --- | --- | --- | --- |
| UAT-01 | 销售额、订单数、销量等口径是否符合公司规则 | 在 Odoo 原生列表/Pivot 与 Agent 同条件对照至少 10 题 | 用户确认的对照表、日期、公司和筛选条件 | 🔴 待验收 |
| UAT-02 | 中文答案、图表和空结果是否易懂 | 检查 KPI、趋势、排名、比较、空结果和澄清问题 | 用户签字或问题清单 | 🔴 待验收 |
| UAT-03 | 会话切换、刷新恢复和长时间等待体验 | 连续创建多个会话，生成中切换并刷新页面 | 用户确认，无丢失或误归属消息 | 🔴 待验收 |
| UAT-04 | SiliconFlow 模型质量和费用可接受 | 用实际候选模型运行固定问题，查看账单和输出 | 用户确认模型 ID、质量、延迟和预算 | 🔴 待验收 |
| UAT-05 | Odoo AI 工具调用符合业务边界 | 在一次性测试库验证允许/禁止的工具 | 用户确认工具白名单和失败提示 | 🔴 待验收 |
| UAT-06 | Documents/Knowledge source 权限隔离 | 使用管理员、普通用户、项目协作者测试同一来源 | 三类账号的可见性矩阵和用户确认 | 🔴 待验收 |
| UAT-07 | 任何 Odoo 写动作 | 仅在一次性测试库验证创建活动、标签或草稿建议 | 用户逐项授权并确认审计、幂等和回滚 | 🔴 未授权 |
| UAT-08 | 是否进入多人/公网场景 | 评审登录、RBAC、多公司、行级权限和部署方案 | 用户确认范围；未确认前保持本机单用户 | 🔴 范围未确认 |
| UAT-09 | Chart Planner 业务适配 | 对 KPI、趋势、排名、占比、散点、类别过多和纯明细各检查至少 2 题 | 用户确认图表类型、标题、排序、TopN 和 table 回退符合阅读习惯 | 🔴 待验收 |

用户验收记录至少包含：

- 测试 ID；
- 日期和执行人；
- 数据库或环境名称；
- 使用的模型 ID；
- 预期与实际结果；
- 截图、Trace URL 或脱敏报告位置；
- 结论：通过、阻塞或拒绝；
- 需要修复时对应的 Issue/任务。

## 6. 下一批应先写的失败测试

以下项目应先进入 TDD 的 Red 阶段，再实现功能。

| 优先级 | 测试主题 | 首个失败测试 | 完成标准 | 当前灯色 |
| --- | --- | --- | --- | --- |
| P0 | 真实结果签名 | 同一 Case 的行列、数值、时间边界不一致时必须失败 | 支持参考 SQL/结果签名和数值容差 | 🟢 16/16 参考 SQL |
| P0 | SQL JOIN 上限 | 超过允许 JOIN 数的查询被 Guard 拒绝 | Guard、错误分类和单测齐全 | 🟢 默认 6 |
| P0 | CTE/子查询复杂度 | 超过阈值或嵌套过深时被拒绝 | 可配置阈值，正常 Wren SQL 不回归 | 🟢 CTE 6/子查询 12/深度 3 |
| P0 | 笛卡尔积 | 无连接条件的 JOIN/CROSS JOIN 被拒绝 | 安全错误可进入一次修复或 Interrupt | 🟢 已拦截并分类 |
| P0 | 明细时间范围 | 明细查询无时间范围时不直接执行 | 返回澄清或高成本确认 | 🟢 缺范围 Interrupt；单边界拒绝 |
| P0 | SiliconFlow Odoo 测试 | 在一次性测试库运行现有 9 项测试 | 9/9 通过且测试库可回收 | 🟡 等授权 |
| P0 | 当前代码真实基准 | 旧基线不能代表最新代码 | 三轮新报告可复现并提交 | 🟢 2026-09-03 当前代码 Native/Wren 全量三轮已完成；唯一外部 504 已单独分类 |
| P1 | 延迟预算 | 简单 KPI 超过目标 p50 时评测失败 | p50 ≤18 秒，准确率不下降 | 🟡 待建 |
| P1 | Langfuse 环境 | Trace 缺少 development/test/production 时失败 | 环境、Prompt 版本、成本完整 | 🟡 待建 |
| P1 | 扩展黄金集 | 退款、税、空值、多币种等缺少覆盖 | 至少 60 题，含有区分度测试数据 | 🔴 需业务确认口径 |
| P1 | 会话生命周期 | 归档、保留期和迁移行为缺少测试 | API、状态库和 UI 契约完成 | 🔴 需产品规则 |

## 7. 每类改动的最小测试门禁

| 改动类型 | 合并前必须运行 |
| --- | --- |
| Python 后端 | 受影响测试 + 当前 115 项后端全量测试 |
| SQL Guard / QueryPlan / Prompt | 后端全量 + 静态黄金集；获授权时加真实黄金集 |
| 前端 JavaScript/HTML/CSS | `node --check` + 相关 API 测试；获授权时加 localhost UI smoke |
| Wren MDL/语义层 | provider/审计测试 + Wren compile/dry-plan；获授权时加真实基准 |
| Wiki 检索/知识库 | Wiki 单测 + UTF-8/元数据/引用检查 |
| Odoo 自定义模块 | Python/XML 静态检查 + 一次性测试库 TransactionCase |
| 模型或 Prompt 变更 | 静态黄金集 + 真实黄金集 + latency/token/cost 对比 |
| 文档 | 链接、命令、版本和安全示例检查；不得出现密钥 |

## 8. 发布判定

发布候选只有在以下条件全部满足时才能标记为“可发布”：

- 所有范围内自动化测试为 🟢；
- 没有未解释的测试跳过、进程残留或敏感信息；
- 所有黄灯都有执行结果，或明确写入本次发布的已知限制；
- 本次范围涉及的用户验收已由用户从 🔴 改为 🟢；
- 真实评测没有 SQL 安全、公司隔离或数字忠实度失败；
- 性能、Token 和 Cost 相对基线没有不可解释的退化；
- Git 工作区干净，提交信息能对应测试证据；
- 启动过的本地服务已按约定关闭。

## 9. 本轮结论

当前自动化层是 🟢：Agent 115/115、前端语法、静态黄金集 20/20、结果签名、SQL 复杂度 Guard、基准汇总、模块静态解析、知识库格式和项目级 Skill/Waza 资产检查均通过。

当前运行层的数据与 SQL 正确性是 🟢：Odoo/Agent、只读数据库、DeepSeek、Langfuse、参考 SQL、当前代码 Native/Wren 全量三轮、Chart Planner、Interrupt/Resume 和 localhost UI smoke 已执行；所有已执行候选结果签名均通过。模型服务可用性是 🟡：Wren 三轮中的一次请求收到上游 HTTP 504；此外 SiliconFlow HTTP 402、服务重启后的 pending interrupt 恢复、SiliconFlow 模块安装及 Odoo TransactionCase 仍未关闭。

当前业务验收层是 🔴：销售数值口径、图表阅读体验、模型费用、权限隔离和任何写动作仍需用户确认。代码测试通过不代表这些业务判断已由用户签字。
