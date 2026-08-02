# Odoo Sales Agent

面向单公司、单用户、只读销售分析场景的 Odoo ChatBI 项目。

## 当前进度

- 静态 HTML 产品原型。
- FastAPI 后端骨架。
- DeepSeek 与硅基流动统一模型接口。
- Langfuse 全链路观测基础设施。
- Odoo 数据库尚未接入，当前接口不会查询或修改业务数据。

## 启动后端

```powershell
Set-Location D:\odoo19e\odoo-agent
.\start-backend.ps1
```

启动后访问：

- API 文档：<http://127.0.0.1:8090/docs>
- 健康检查：<http://127.0.0.1:8090/api/health>

## 模型配置

模型密钥只从 Windows 环境变量读取，不写入项目文件。

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

