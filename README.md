# Odoo Sales Agent

面向单用户、单公司、只读销售分析的本地 ChatBI Agent。用户用中文提问，系统通过 LangGraph、结构化 QueryPlan、SQLGlot Guard 和 Odoo PostgreSQL 生成真实结论、表格、ECharts 图表与可审计 SQL。

## 当前能力

- LiteLLM 统一接入 DeepSeek / 硅基流动，SQL、复杂解释、普通聊天按节点选择模型；
- Odoo PostgreSQL 实时只读查询和版本化销售语义层；
- Pydantic QueryPlan + Text2SQL + 最多两次安全修复；
- SQL AST 白名单、公司过滤、超时、LIMIT 和数据库只读事务；
- PostgreSQL Checkpointer、会话恢复和 LangGraph Interrupt；
- SSE 实时步骤，简单结果跳过第二次 LLM；
- ECharts 图表、固定销售看板和 CSV 导出；
- Langfuse Trace、Session、Token、Cost、Score 和 Dataset；
- 20 条首批黄金问题、静态/真实自动回归。

## 快速启动

```powershell
# 启动本机 Odoo 与 PostgreSQL
Set-Location D:\odoo19e
.\start-odoo19-dev.ps1

# 启动 Agent；也可直接双击 start-odoo-agent.bat
Set-Location D:\odoo19e\odoo-agent
.\start-backend.ps1 -NoReload
```

- 应用：<http://127.0.0.1:8090/ui/index.html>
- API 文档：<http://127.0.0.1:8090/docs>
- 健康检查：<http://127.0.0.1:8090/api/health>

首次配置状态库：

```powershell
.\configure-agent-state.ps1
```

模型、Langfuse 和 Odoo 数据源建议在“数据与模型”页面配置。密钥只保存到当前 Windows 用户环境变量，不写入仓库。

## 正式文档

完整文档统一维护在 [docs/README.md](docs/README.md)：

- [产品与范围](docs/01-product-overview.md)
- [系统架构](docs/02-system-architecture.md)
- [安装与配置](docs/03-installation-and-configuration.md)
- [用户使用手册](docs/04-user-guide.md)
- [API 参考](docs/05-api-reference.md)
- [数据语义与安全](docs/06-data-and-security.md)
- [LangGraph 工作流](docs/07-langgraph-workflow.md)
- [Langfuse 可观测性](docs/08-langfuse-observability.md)
- [评测与质量](docs/09-evaluation-and-quality.md)
- [开发与运维](docs/10-development-and-operations.md)
- [演进路线](docs/11-roadmap.md)

## 验证

```powershell
Set-Location D:\odoo19e\odoo-agent\backend
& 'D:\odoo19e\odoo-agent\.venv\Scripts\python.exe' -m unittest discover -s tests -v

Set-Location D:\odoo19e\odoo-agent
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py
node --check .\app.js
```

## 安全边界

- 当前无登录和权限系统，只监听本机，不应直接暴露公网；
- 第一阶段只允许 SELECT，不执行 Odoo 写操作；
- 状态数据库必须与 Odoo 业务数据库分离；
- 真实 API Key、数据库密码、Token、日志和导出不得提交 Git。
