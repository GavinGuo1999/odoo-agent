# 安装与配置

> 适用应用版本：0.2.0
>
> 适用平台：Windows / PowerShell
> 默认项目目录：`D:\odoo19e\odoo-agent`

## 1. 前置条件

| 依赖 | 要求 | 当前本机默认 |
| --- | --- | --- |
| Windows | Windows 10/11 或 Windows Server | PowerShell 5.1 可用 |
| Python | 3.12，独立虚拟环境 | `D:\odoo19e\odoo-agent\.venv` |
| PostgreSQL | 能访问 Odoo 数据库；持久状态需要创建独立数据库 | `127.0.0.1:55432` |
| Odoo | Odoo 19 本地环境 | `http://127.0.0.1:8019` |
| 模型 API | DeepSeek 或硅基流动至少一个有效 Key | 页面配置 |
| Langfuse | 可选但强烈推荐 | EU Cloud：`https://cloud.langfuse.com` |
| Node.js | 仅开发时检查 `app.js` 语法 | 非运行时依赖 |

生产依赖固定在 `backend/requirements.txt`。前端不需要 npm 构建，ECharts 作为本地静态文件提供。

## 2. 目录确认

```powershell
Set-Location D:\odoo19e\odoo-agent
Get-ChildItem
```

应至少看到：

```text
backend/
docs/
evals/
.venv/
start-odoo-agent.bat
start-backend.ps1
stop-odoo-agent.bat
configure-agent-state.ps1
configure-langfuse.ps1
```

项目虚拟环境与 Odoo 的 `D:\odoo19e\.venv_dev` 相互独立，不要混用。

## 3. Python 环境

如果 `.venv` 已存在，通常不需要重新创建：

```powershell
Set-Location D:\odoo19e\odoo-agent
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install -r .\backend\requirements.txt
```

如果执行策略阻止激活，可直接使用完整 Python 路径：

```powershell
& 'D:\odoo19e\odoo-agent\.venv\Scripts\python.exe' --version
```

## 4. 推荐配置方式：网站设置页

先启动 Agent，再打开：

<http://127.0.0.1:8090/ui/settings.html>

设置页分为五部分。

### 4.1 模型服务

分别配置 DeepSeek 和硅基流动：

- Base URL；
- 默认 Model ID；
- API Key；
- 输入价格 / 百万 Token；
- 输出价格 / 百万 Token。

API Key 留空表示保留后端已有值。设置接口永远不会把已保存密钥明文返回页面。

价格仅用于 Langfuse Cost 估算：

- DeepSeek 单价按 USD 填写；
- 硅基流动单价按 CNY 填写；
- CNY 使用 `CNY_PER_USD` 换算为 USD；
- 估算 Cost 不等同于供应商账单。

### 4.2 节点级模型路由

| 角色 | 用途 | 建议 |
| --- | --- | --- |
| SQL 生成与修复 | QueryPlan、SQL、修复 | 使用 Text2SQL 稳定性最好的模型 |
| 复杂结果解释 | 多指标、比较、洞察 | 可使用更快或更便宜的模型 |
| 普通聊天 | 日期、模型、非数据问答 | 优先低延迟模型 |

单 KPI、简单排名、简单趋势和空结果不会调用复杂结果解释模型。

### 4.3 Langfuse

配置区域、Base URL、Public Key、Secret Key 和启用开关。Public/Secret Key 必须同时填写；EU Cloud 使用：

```text
https://cloud.langfuse.com
```

### 4.4 Odoo 数据源

本机默认配置：

| 字段 | 默认值 |
| --- | --- |
| 地址 | `127.0.0.1` |
| 端口 | `55432` |
| 数据库 | `odoo19_dev` |
| 用户 | `codex_readonly` |
| 公司 ID | `1` |
| 查询超时 | `15000 ms` |
| 最大行数 | `500` |

点击“保存并测试数据库连接”后，页面应显示：

- 只读已连接；
- 公司名称；
- 币种；
- 订单数量；
- 最近响应耗时。

连接即使使用权限较高的账号，客户端仍会设置 `default_transaction_read_only=on`。但正式使用仍必须提供数据库层只读账号。

### 4.5 Agent 状态数据库

状态库用于 Checkpoint、会话恢复和 Interrupt。必须与 Odoo 业务数据库分离。

推荐值：

| 字段 | 推荐值 |
| --- | --- |
| 地址 | `127.0.0.1` |
| 端口 | `55432` |
| 数据库 | `odoo_agent_state` |
| 用户 | `odoo_agent_state` |

修改状态库连接后需要重启 Agent，使新的 `AsyncPostgresSaver` 在应用生命周期开始时初始化。

## 5. 命令行配置脚本

### 5.1 创建独立状态库

先启动本地 PostgreSQL，然后执行：

```powershell
Set-Location D:\odoo19e\odoo-agent
.\configure-agent-state.ps1
```

脚本会：

1. 校验数据库名和用户名；
2. 创建或收紧 `odoo_agent_state` 角色；
3. 创建独立 `odoo_agent_state` 数据库；
4. 随机生成密码；
5. 仅写入当前 Windows 用户环境变量；
6. 不打印密码、不写 `.env`、不写 Git。

可覆盖默认 PostgreSQL 参数：

```powershell
.\configure-agent-state.ps1 `
  -PostgresBin 'C:\Program Files\PostgreSQL\18\bin' `
  -HostName '127.0.0.1' `
  -Port 55432 `
  -PostgresAdminUser 'odoo19' `
  -Database 'odoo_agent_state' `
  -User 'odoo_agent_state'
```

### 5.2 配置 Langfuse

```powershell
.\configure-langfuse.ps1 -Region eu
```

脚本使用隐藏输入保存 Key，并发送验证 Trace。其他区域可使用 `-Region us` 或按脚本参数指定 Base URL。

### 5.3 配置模型

```powershell
.\configure-model.ps1 -Provider deepseek
```

或：

```powershell
.\configure-model.ps1 -Provider siliconflow
```

该脚本适合首次配置和连接验证；节点级路由建议在网站设置页完成。

## 6. 环境变量参考

配置保存在当前 Windows 用户环境中，不使用项目 `.env`。

### 6.1 应用与模型

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ODOO_AGENT_APP_NAME` | `Odoo Sales Agent` | 应用名 |
| `ODOO_AGENT_ENVIRONMENT` | `development` | `development/test/production` |
| `LLM_PROVIDER` | `deepseek` | 默认供应商 |
| `LLM_TIMEOUT_SECONDS` | `90` | 模型请求超时，1～300 秒 |
| `CNY_PER_USD` | `7.2` | CNY Cost 换算汇率 |

### 6.2 DeepSeek

| 变量 | 默认值 / 格式 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 无；Secret |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-v4-pro` |
| `DEEPSEEK_INPUT_PRICE_PER_MILLION` | `0.435` USD |
| `DEEPSEEK_OUTPUT_PRICE_PER_MILLION` | `0.87` USD |

### 6.3 硅基流动

| 变量 | 默认值 / 格式 |
| --- | --- |
| `SILICONFLOW_API_KEY` | 无；Secret |
| `SILICONFLOW_BASE_URL` | `https://api.siliconflow.cn/v1` |
| `SILICONFLOW_MODEL` | `deepseek-ai/DeepSeek-V3.1-Terminus` |
| `SILICONFLOW_INPUT_PRICE_PER_MILLION` | `4` CNY |
| `SILICONFLOW_OUTPUT_PRICE_PER_MILLION` | `12` CNY |

### 6.4 节点路由

| 变量 | 说明 |
| --- | --- |
| `SQL_LLM_PROVIDER` | `deepseek` 或 `siliconflow` |
| `SQL_LLM_MODEL` | SQL 生成和修复 Model ID |
| `ANSWER_LLM_PROVIDER` | 复杂结果解释供应商 |
| `ANSWER_LLM_MODEL` | 复杂结果解释 Model ID |
| `GENERAL_LLM_PROVIDER` | 普通聊天供应商 |
| `GENERAL_LLM_MODEL` | 普通聊天 Model ID |

未设置某角色时，该角色继承 `LLM_PROVIDER` 及对应供应商默认模型。

### 6.5 Odoo 数据库

| 变量 | 默认值 |
| --- | --- |
| `ODOO_DB_HOST` | `127.0.0.1` |
| `ODOO_DB_PORT` | `55432` |
| `ODOO_DB_NAME` | `odoo19_dev` |
| `ODOO_DB_USER` | `codex_readonly` |
| `ODOO_DB_PASSWORD` | 无；Secret |
| `ODOO_COMPANY_ID` | `1` |
| `ODOO_STATEMENT_TIMEOUT_MS` | `15000` |
| `ODOO_MAX_ROWS` | `500`，允许范围 1～5000 |

### 6.6 状态数据库

| 变量 | 默认值 |
| --- | --- |
| `AGENT_STATE_ENABLED` | `false` |
| `AGENT_STATE_DB_HOST` | `127.0.0.1` |
| `AGENT_STATE_DB_PORT` | `55432` |
| `AGENT_STATE_DB_NAME` | `odoo_agent_state` |
| `AGENT_STATE_DB_USER` | `odoo_agent_state` |
| `AGENT_STATE_DB_PASSWORD` | 无；Secret |

### 6.7 Langfuse

| 变量 | 说明 |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY` | Public Key；Secret-like，不写 Git |
| `LANGFUSE_SECRET_KEY` | Secret Key |
| `LANGFUSE_BASE_URL` | EU 默认 `https://cloud.langfuse.com` |
| `LANGFUSE_ENABLED` | 是否启用项目侧 Trace |
| `LANGFUSE_TRACING_ENABLED` | Langfuse SDK Trace 开关 |

## 7. 启动与停止

### 7.1 一键启动

1. 运行 `D:\odoo19e\start-odoo19-dev.ps1`；
2. 双击 `D:\odoo19e\odoo-agent\start-odoo-agent.bat`；
3. 等待浏览器自动打开。

BAT 会检测 8090 端口，避免重复启动同一后端。

### 7.2 开发启动

```powershell
Set-Location D:\odoo19e\odoo-agent
.\start-backend.ps1
```

默认开启 reload。稳定验收可使用：

```powershell
.\start-backend.ps1 -NoReload
```

Windows 使用项目自带 Selector event loop，以兼容 psycopg 异步状态连接。

### 7.3 停止

```powershell
Set-Location D:\odoo19e\odoo-agent
.\stop-backend.ps1
```

或双击 `stop-odoo-agent.bat`。脚本只停止命令行匹配本项目和目标端口的进程；如果 8090 被其他程序占用，不会误杀。

停止 Odoo 和 PostgreSQL：

```powershell
Set-Location D:\odoo19e
.\stop-odoo19-dev.ps1
```

## 8. 首次验证

依次访问：

- 应用：<http://127.0.0.1:8090/ui/index.html>
- 健康检查：<http://127.0.0.1:8090/api/health>
- 设置状态：<http://127.0.0.1:8090/api/settings>
- 数据库状态：<http://127.0.0.1:8090/api/database/status>
- API 文档：<http://127.0.0.1:8090/docs>

预期：

- `status=ok`；
- Odoo `status=connected`、`read_only=true`；
- 设置页状态库 `active_mode=postgres`；
- Langfuse `configured=true`；
- 页面不显示或回传任何密钥明文。

## 9. 常见配置问题

### 9.1 8090 地址已被占用

先双击 `stop-odoo-agent.bat`。若提示端口由其他程序占用，按提示确认该程序，不要盲目终止所有 Python 进程。

### 9.2 设置已保存但状态库仍是 memory

- 确认 PostgreSQL 已启动；
- 确认状态数据库不是 `odoo19_dev`；
- 确认状态账号有该独立数据库写权限；
- 保存状态连接后重启 Agent；
- 查看设置 API 的 `state_database.error_type`。

### 9.3 Odoo 连接成功但 read_only=false

停止使用该配置。检查数据库账号和连接参数；应用健康检查必须确认当前事务只读后才将连接视为成功。

### 9.4 模型列表读取失败

- 先保存供应商 API Key；
- 检查 Base URL 是否为 OpenAI 兼容地址；
- 检查网络、Key 权限和供应商状态；
- 手工填写正确 Model ID 仍可保存。

### 9.5 Langfuse 保存后要求重启

修改已初始化客户端的 Key 或 Base URL 时需要重启，使进程级 Langfuse Client 使用新项目。仅启停 Trace 或调整普通模型通常无需重启。

## 10. 安全提醒

- 不要创建 `.env` 保存真实密钥；`.env*` 已被 Git 忽略，但正式约定仍是 Windows 用户环境。
- 不要把配置 API 的完整请求、环境变量列表或浏览器开发工具内容贴到公开位置。
- 不要将应用监听地址改为 `0.0.0.0` 后直接暴露公网；当前没有登录和权限系统。
- Odoo 分析必须使用只读账号。
- Agent 状态库账号必须与 Odoo 只读账号分开。

## 11. 相关文档

- [用户使用手册](04-user-guide.md)
- [数据语义与安全](06-data-and-security.md)
- [开发与运维](10-development-and-operations.md)
