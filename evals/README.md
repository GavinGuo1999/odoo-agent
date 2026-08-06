# 销售黄金问题集与自动回归

`datasets/sales_golden.jsonl` 首批包含 20 个案例，覆盖普通问答、指标口径、KPI、趋势、排名、比较、追问、空结果、关键歧义和 Prompt Injection。

快速静态检查不会调用模型或数据库：

```powershell
Set-Location D:\odoo19e\odoo-agent
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py
```

真实回归会调用当前配置的节点模型与 Odoo 只读数据库，可能产生 API 费用：

```powershell
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py --live --output .\evals\reports\latest.json
```

把问题及期望结构同步到 Langfuse Dataset（不上传查询结果和 Odoo 明细）：

```powershell
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py --sync-langfuse
```

Langfuse Dataset 名称为 `odoo-agent/sales-golden-v1`。本地 JSONL 是 Git 中的基准真源；Langfuse 用于实验运行比较和人工检查。
