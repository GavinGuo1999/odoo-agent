# Odoo Sales Agent

面向单公司、单用户、只读销售分析场景的 Odoo ChatBI 项目。

## 当前进度

- FastAPI 托管的网站界面与真实模型聊天。
- DeepSeek 与硅基流动统一模型接口，可在页面切换供应商和模型。
- Odoo PostgreSQL 实时只读连接、连接测试和开放 Schema 发现。
- 销售语义层：销售额、订单数、平均订单额、销量、交付和开票数量。
- LangGraph 自动路由普通问答、指标解释和 Text2SQL，错误 SQL 最多修复两次。
- SQLGlot 单语句/表/字段/函数/公司过滤校验，数据库账号再次强制只读。
- 真实查询结果支持结论、表格、本地 ECharts 图表、SQL 展开和查询耗时。
- 工作台与销售看板直接读取 Odoo 实时汇总，支持周/月/季度、销售员筛选、订单待办和真实 CSV 导出。
- Langfuse 记录 Agent、语义检索、模型、SQL 校验和只读执行全链路。

## 双击启动

直接双击项目根目录的 `start-odoo-agent.bat`。它会启动本机 FastAPI、等待服务就绪，然后自动打开网站。

应用地址：<http://127.0.0.1:8090/ui/index.html>

在“数据与模型”页面可以填写 DeepSeek、硅基流动、Langfuse 和 Odoo 只读数据库配置。密钥与数据库密码只写入当前 Windows 用户环境变量，接口只返回配置状态，不会把明文发回网页。

## 开发方式启动后端

```powershell
Set-Location D:\odoo19e\odoo-agent
.\start-backend.ps1
```

启动后访问：

- API 文档：<http://127.0.0.1:8090/docs>
- 健康检查：<http://127.0.0.1:8090/api/health>
- 数据库状态：<http://127.0.0.1:8090/api/database/status>
- 开放 Schema：<http://127.0.0.1:8090/api/database/schema>
- 销售看板数据：<http://127.0.0.1:8090/api/sales/dashboard?period=month>
- 销售指标定义：<http://127.0.0.1:8090/api/sales/metrics>

## 模型配置

推荐直接在网站的“数据与模型”页面填写。模型密钥只从 Windows 用户环境变量读取，不写入项目文件。

命令行配置脚本保留作为备用方式，它会使用隐藏输入并自动发送一条短请求验证模型和 Langfuse：

```powershell
.\configure-model.ps1 -Provider deepseek
```

切换到硅基流动：

```powershell
.\configure-model.ps1 -Provider siliconflow
```

```text
LLM_PROVIDER=deepseek 或 siliconflow
DEEPSEEK_API_KEY
DEEPSEEK_MODEL=deepseek-v4-pro
SILICONFLOW_API_KEY
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V3.1-Terminus
```

## Odoo 数据库配置

推荐直接在“数据与模型 → Odoo 数据源”填写并点击“保存并测试数据库连接”。本机默认值为：

```text
地址：127.0.0.1:55432
数据库：odoo19_dev
用户：codex_readonly
公司 ID：1
最大返回：500 行
超时：15 秒
```

应用只接受只读账号。销售查询必须包含当前公司过滤，不会混入其他测试公司的数据。

## 验证

```powershell
Set-Location D:\odoo19e\odoo-agent\backend
& 'D:\odoo19e\odoo-agent\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

## 开发边界

- 第一阶段只读，不执行 INSERT、UPDATE、DELETE 或 DDL。
- `/api/chat` 支持普通问答、指标口径和多轮数据问题。
- 数据库未连接、SQL 未通过安全校验或执行失败时，返回 `data_accessed=false`，不生成虚假业务数字。
- 当前只开放销售相关的 9 张表和 71 个白名单字段，不提供通用全库查询。
