# 开发与运维

> 适用应用版本：0.2.0
>
> 平台：Windows / PowerShell
> 仓库：`GavinGuo1999/odoo-agent`

## 1. 项目结构

```text
odoo-agent/
├─ backend/
│  ├─ app/
│  │  ├─ api/                 # FastAPI 路由
│  │  ├─ bi/                  # Agent、语义层、Prompt、图表、时间序列
│  │  ├─ database/            # Odoo Client 与 SQL Guard
│  │  ├─ llm/                 # 模型网关
│  │  ├─ observability/       # Langfuse 适配与脱敏
│  │  ├─ schemas/             # Pydantic API/内部协议
│  │  ├─ services/            # 看板和 Windows 环境服务
│  │  ├─ state/               # Checkpointer 生命周期
│  │  ├─ config.py
│  │  ├─ main.py
│  │  └─ windows_loop.py
│  ├─ tests/
│  └─ requirements.txt
├─ docs/                      # 正式文档
├─ evals/
│  ├─ datasets/
│  │  └─ sales_golden.jsonl
│  └─ run_sales_eval.py
├─ app.js                     # 静态前端逻辑
├─ echarts.min.js             # 本地图表运行时
├─ index.html
├─ chat.html
├─ dashboard.html
├─ settings.html
├─ styles.css
├─ start-odoo-agent.bat
├─ start-backend.ps1
├─ stop-odoo-agent.bat
├─ stop-backend.ps1
└─ configure-*.ps1
```

## 2. 技术栈和版本

| 层 | 技术 |
| --- | --- |
| API | FastAPI 0.141.1 |
| Schema/Config | Pydantic Settings 2.14.2 |
| Agent | LangGraph 1.2.10 |
| Checkpoint | langgraph-checkpoint-postgres 3.1.1 |
| LLM | OpenAI Python SDK 2.52.0，OpenAI 兼容接口 |
| Observability | Langfuse 4.14.3 |
| SQL | SQLGlot 30.14.0 |
| PostgreSQL | psycopg 3.3.4 |
| Server | Uvicorn 0.41.0 |
| Frontend | 原生 HTML/CSS/JavaScript + ECharts |

依赖版本固定，升级时必须跑全量测试和真实 Trace 自检。

## 3. 开发启动

### 3.1 启动依赖

```powershell
Set-Location D:\odoo19e
.\start-odoo19-dev.ps1
```

验证 Odoo：

```powershell
Invoke-WebRequest 'http://127.0.0.1:8019/web/login' -UseBasicParsing
```

### 3.2 启动后端

```powershell
Set-Location D:\odoo19e\odoo-agent
.\start-backend.ps1
```

Uvicorn 默认 reload，修改 Python 文件后自动重启。涉及状态库连接或 Langfuse 进程级客户端时，建议完整停止后重新启动。

### 3.3 访问入口

```text
UI       http://127.0.0.1:8090/ui/index.html
OpenAPI  http://127.0.0.1:8090/docs
Health   http://127.0.0.1:8090/api/health
```

## 4. 测试

### 4.1 全量后端测试

```powershell
Set-Location D:\odoo19e\odoo-agent\backend
& 'D:\odoo19e\odoo-agent\.venv\Scripts\python.exe' `
  -m unittest discover -s tests -v
```

### 4.2 Python 编译

```powershell
Set-Location D:\odoo19e\odoo-agent
& '.\.venv\Scripts\python.exe' -m compileall -q .\backend\app .\evals
```

### 4.3 前端语法

```powershell
node --check .\app.js
```

### 4.4 黄金集

```powershell
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py
```

真实回归：

```powershell
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py `
  --live `
  --output .\evals\reports\latest.json
```

### 4.5 Git 检查

```powershell
git diff --check
git status --short
```

提交前检查 Secret 时只输出疑似文件名，不要把匹配行回显到共享日志。

## 5. 开发约定

### 5.1 配置

- Secret 只能来自系统环境或设置 API；
- 不引入 `.env` 作为正式配置源；
- 新增可保存变量必须加入 Windows 环境白名单；
- GET 设置接口不得返回明文 Secret；
- 状态库和 Odoo 同库必须继续被拒绝。

### 5.2 Agent

- LangGraph 是唯一编排层；
- 内部结构化模型输出必须使用 Pydantic；
- 模型节点必须经过统一 LLM Gateway；
- 数据 SQL 必须经过 ReadOnlySqlGuard；
- 不在节点内直接绕过数据库 Client；
- 新增长步骤必须发 SSE progress；
- 新模型调用必须记录 Generation、role、usage 和 cost；
- Interrupt 前不得执行非幂等写操作。

### 5.3 前端

- 不把 Secret 放进 JavaScript；
- ECharts 配置由受信 ChartSpec 转换，不执行模型生成脚本；
- SSE 必须兼容未知 stage；
- 最终结果以 `result` 事件为准；
- 断线或 error 后必须恢复输入控件；
- 修改静态资源后提升 HTML 中查询版本，避免浏览器旧缓存。

### 5.4 数据

- 新表/字段采用最小白名单；
- 指标变更提升语义版本；
- 公司过滤和默认状态不可由 Prompt 单独保证；
- 业务数字只能来自查询结果；
- 空结果不得替换为演示数据。

## 6. 常见改动流程

### 6.1 新增指标

1. 更新 `sales_semantics.json`；
2. 提升语义版本；
3. 增加关键词、表达式和状态；
4. 如需新字段，扩展表字段白名单；
5. 更新展示标签和格式；
6. 增加 SQL Guard/语义/Agent 测试；
7. 增加黄金案例；
8. 跑静态与真实回归；
9. 更新数据文档。

### 6.2 新增模型供应商

1. 在 `ProviderName` 扩展枚举；
2. 添加 Settings 和 ProviderConfig；
3. 更新设置 Schema、路由和 Windows 白名单；
4. 更新设置页面；
5. 确认 OpenAI 兼容差异、JSON Mode、usage 字段和模型目录；
6. 配置价格和币种换算；
7. 增加网关/API 测试；
8. 使用黄金集对比；
9. 供应商达到三个以上时重新评估 LiteLLM。

### 6.3 新增 Graph 节点

参照 [LangGraph Agent 工作流](07-langgraph-workflow.md) 的扩展规则，同时更新：

- Graph 图；
- AgentState；
- SSE stage；
- Langfuse Observation；
- 测试；
- API/文档。

## 7. 服务停止

```powershell
Set-Location D:\odoo19e\odoo-agent
.\stop-backend.ps1

Set-Location D:\odoo19e
.\stop-odoo19-dev.ps1
```

`stop-backend.ps1` 会核对 Python 命令行、app-dir、port 和项目启动脚本，只停止本项目进程。如果端口由其他程序占用，脚本退出且不执行停止。

## 8. 运行状态检查

### 8.1 Web/API

```powershell
Invoke-RestMethod 'http://127.0.0.1:8090/api/health'
Invoke-RestMethod 'http://127.0.0.1:8090/api/settings'
Invoke-RestMethod 'http://127.0.0.1:8090/api/database/status'
```

### 8.2 关键状态

| 项目 | 正常值 |
| --- | --- |
| FastAPI | health `status=ok` |
| Odoo DB | `connected=true`, `read_only=true` |
| 模型 | 所有路由角色对应 provider 已配置 |
| State | `active_mode=postgres` |
| Langfuse | `configured=true`, `enabled=true` |

## 9. 故障排查

### 9.1 端口 8090 冲突

```powershell
Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8090 -State Listen
```

先使用项目停止脚本；不要用“停止所有 python.exe”的方式处理。

### 9.2 PostgreSQL Checkpointer 失败

- 确认 PostgreSQL 服务运行；
- 确认状态库密码是当前脚本生成值；
- 确认数据库 owner/权限；
- 确认 Windows Selector loop 参数存在；
- 查看 `/api/settings` 的 `error_type`；
- 确认不是 Odoo 业务数据库。

### 9.3 数据问题返回 failure

依次检查：

1. `/api/database/status`；
2. Langfuse Tool `check-odoo-readonly-database`；
3. QueryPlan；
4. `validate-readonly-sales-sql` errors；
5. repair 次数；
6. `execute-readonly-sales-sql` error type；
7. timeout、行数和 Odoo 表结构。

### 9.4 Trace 未出现

- 设置页确认 Langfuse 已配置并启用；
- 修改 Key/Base URL 后重启；
- 运行 `backend/scripts/verify_langfuse.py`；
- 等待 SDK flush；
- 检查 Base URL 区域；
- 业务回答不应因 Trace 失败而中断。

### 9.5 Cost 异常

- 查看 Generation usage；
- 核对模型路由；
- 核对供应商当前单价和币种；
- 核对 CNY/USD 汇率；
- 以供应商账单为最终依据。

## 10. 日志策略

当前开发模式主要使用 Uvicorn 控制台日志。日志要求：

- 不打印 API Key、数据库密码、Cookie、Token 或连接串；
- 模型/数据库错误只记录类型和受控状态；
- 详细推理、Prompt、SQL 和结果优先在已脱敏 Langfuse Trace 中查看；
- 本机日志和导出同样视为敏感数据；
- 引入文件日志前先定义轮转、保留期和脱敏规则。

## 11. 发布流程

### 11.1 发布前

- [ ] 明确变更范围和风险；
- [ ] 更新应用版本（需要时）；
- [ ] 更新正式文档；
- [ ] 全量单元测试；
- [ ] 静态黄金集 100%；
- [ ] 受影响真实案例通过；
- [ ] 前端语法和本地浏览器检查；
- [ ] `git diff --check`；
- [ ] Secret 扫描；
- [ ] 真实 Trace 自检；
- [ ] Odoo 只读状态确认。

### 11.2 提交

```powershell
git status --short
git add README.md docs
git commit -m "feat: ..."
git push origin main
```

当前项目为个人开发仓库；如果后续多人协作，应改为分支 + Pull Request + 必须检查。

### 11.3 发布后

- 启动 Odoo 和 Agent；
- 检查工作台、聊天、看板和设置页；
- 执行普通、KPI、趋势、歧义四类冒烟；
- 回查 Langfuse Trace、Cost、Score；
- 重启 Agent 验证 Session 恢复；
- 记录已知问题和回滚点。

## 12. 备份和恢复

当前主要持久数据：

- Git 仓库：代码、语义层、黄金集、文档；
- Windows 用户环境：Secret 和连接配置；
- `odoo_agent_state`：会话和 Checkpoint；
- Langfuse Cloud：Trace、Score、Dataset。

当前未提供自动备份脚本。开发测试阶段至少应确保：

- Git 已推送远程；
- 能重新创建状态数据库；
- Secret 可在供应商控制台轮换；
- 黄金集本地真源已提交；
- 不依赖某条 Langfuse Trace 作为唯一业务记录。

## 13. 相关文档

- [安装与配置](03-installation-and-configuration.md)
- [评测与质量保障](09-evaluation-and-quality.md)
- [演进路线](11-roadmap.md)
