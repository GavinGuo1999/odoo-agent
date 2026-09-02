# 评测文档入口

黄金问题集、静态/真实回归、报告、Langfuse Dataset 和发布门禁的正式说明见：

[评测与质量保障](../docs/09-evaluation-and-quality.md)

快速静态检查：

```powershell
Set-Location D:\odoo19e\odoo-agent
.\.venv\Scripts\python.exe .\evals\run_sales_eval.py
```

三轮 Native/Wren 真实 A/B（需要只读数据库和模型额度授权）：

```powershell
& .\.venv\Scripts\python.exe .\evals\run_semantic_benchmark.py `
  --runs 3 `
  --providers native,wren `
  --model-provider deepseek
```

只回归指定 Case：

```powershell
& .\.venv\Scripts\python.exe .\evals\run_semantic_benchmark.py `
  --runs 3 `
  --providers native,wren `
  --cases clarify-customer,comparison-invoice,ranking-salespeople `
  --model-provider deepseek
```

16 个可执行数据 Case 会同时运行参考 SQL，比较列、行、数值容差和时间边界。报告只保存结果签名与脱敏差异，不保存业务结果行。当前实测见 [Native / Wren 三轮真实 A/B 基准](../docs/18-native-wren-ab-benchmark.md)。
