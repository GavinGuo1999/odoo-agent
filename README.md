# Odoo Sales Agent

面向单公司、单用户、只读销售分析场景的 Odoo ChatBI 项目。

## 当前进度

- 静态 HTML 产品原型。
- FastAPI 后端骨架。
- DeepSeek 与硅基流动统一模型接口。
- Langfuse 全链路观测基础设施。
- Odoo 数据库尚未接入，当前接口不会查询或修改业务数据。

## 双击启动

直接双击项目根目录的 `start-odoo-agent.bat`。它会启动本机 FastAPI、等待服务就绪，然后自动打开网站。

应用地址：<http://127.0.0.1:8090/ui/index.html>

在“数据与模型”页面可以填写 DeepSeek、硅基流动与 Langfuse 配置。密钥只写入当前 Windows 用户环境变量，接口只返回“已配置/未配置”，不会把明文密钥发回网页。

## 开发方式启动后端

```powershell
Set-Location D:\odoo19e\odoo-agent
.\start-backend.ps1
```

启动后访问：

- API 文档：<http://127.0.0.1:8090/docs>
- 健康检查：<http://127.0.0.1:8090/api/health>

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

## 开发边界

- 第一阶段只读，不执行 INSERT、UPDATE、DELETE 或 DDL。
- `/api/chat` 当前仅验证模型调用，明确标记 `data_accessed=false`。
- 下一阶段接入 Odoo 只读账号、销售语义层和 SQL 校验器。
