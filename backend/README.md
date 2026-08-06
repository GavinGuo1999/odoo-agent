# Odoo Agent 后端环境

项目自己的 Python 虚拟环境位于：

`D:\odoo19e\odoo-agent\.venv`

它和 Odoo 使用的 `D:\odoo19e\.venv_dev` 完全分开。

## 激活环境

```powershell
Set-Location D:\odoo19e\odoo-agent
.\.venv\Scripts\Activate.ps1
```

## 配置 Langfuse

不要把密钥写入 `.env` 或任何项目文件。先在 Langfuse 中撤销已经公开过的密钥，创建新密钥，然后运行：

```powershell
Set-Location D:\odoo19e\odoo-agent
.\configure-langfuse.ps1 -Region eu
```

脚本会隐藏输入、把配置保存到当前 Windows 用户环境变量，并立即发送一条验证 trace。成功后会直接输出 Langfuse 地址，不需要手工激活 venv。

如果只想重新运行验证：

```powershell
.\.venv\Scripts\python.exe .\backend\scripts\verify_langfuse.py
```

成功后会输出一条测试 trace 的 Langfuse 地址。

## 后端接入约定

- 每次用户提问创建一个 `answer-user-question` 根 trace，并在内部记录 LangGraph Agent。
- 同一聊天的多轮提问使用相同 `session_id`。
- 普通回答、Text-to-SQL、SQL 修复和答案生成使用 `generation`。
- Odoo 元数据、指标口径和示例检索使用 `retriever`。
- 只读 SQL 执行使用 `tool`。
- 调用结束时使用 `update_observation(...)` 更新 `output`；模型调用同时记录实际 token usage。
- 每个 Generation 显式记录 `usage_details` 和按页面配置单价估算的 `cost_details`。
- 密钥、密码、Token、Cookie 等内容会在发送前被替换为 `[REDACTED]`。

## 独立状态数据库

首次配置：

```powershell
Set-Location D:\odoo19e\odoo-agent
.\configure-agent-state.ps1
```

它创建 `odoo_agent_state` 数据库和独立读写账号，并配置 `AsyncPostgresSaver`。应用启动时自动初始化 Checkpointer 表；连接失败时降级为进程内存模式，不会改用 Odoo 业务库。Windows 上 Uvicorn 使用项目提供的 Selector event loop，以兼容 psycopg 异步连接。

## 对话协议

- `POST /api/chat/stream`：SSE 返回 `progress` 与最终 `result` 事件。
- `POST /api/chat/resume/stream`：使用原 `session_id` 和澄清答案恢复 Interrupt。
- `GET /api/chat/sessions/{session_id}`：读取已保存历史、待处理 Interrupt 和持久化模式。
- `POST /api/chat/feedback`：由后端把 👍/👎 写成 Langfuse BOOLEAN `user-thumbs` Score。

SQL 模型一次返回严格的 Pydantic `QueryPlan + sql`。简单 KPI、Top N、趋势和空结果使用确定性回答；复杂结果才调用回答模型。三个模型角色通过 `SQL_LLM_*`、`ANSWER_LLM_*`、`GENERAL_LLM_*` 配置，也可以直接在设置页修改。
