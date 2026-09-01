# Native / Wren 三轮真实 A/B 基准

> 执行日期：2026-09-01
>
> 数据库：`odoo19_dev` / `codex_readonly` / `transaction_read_only=on`
>
> 数据集：`odoo-agent/sales-golden-v1`，20 Case × 3 轮 × 2 语义层
>
> 模型：DeepSeek `deepseek-v4-pro`；SQL thinking=`disabled`，answer/general=`auto`

## 1. 评测合同

每个 Case 使用独立 session。16 个会访问数据且不应 Interrupt 的 Case 都有受 Guard 保护的参考 SQL；评测即时执行参考 SQL和候选 SQL，再比较：

- 列名与列顺序；
- 行数与行顺序（业务无序的 Case 显式关闭顺序敏感）；
- 数值绝对/相对容差；
- 日期与午夜时间戳等价；
- 当前月、上月、今年、去年和固定未来区间的真实边界。

报告只保存布尔检查、SHA-256 结果签名和不含值的差异路径，不保存业务结果行。运行产物在 `evals/reports/`，默认被 Git 忽略；本文件保存可审计汇总。

## 2. 首组基线与缺陷发现

| 语义层 | 总通过率 | 结构通过率 | 结果签名率 | p50 | p95 | Token | Cost USD | Repair |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 93.33% | 98.33% | 93.75% | 5.31s | 44.26s | 213,338 | 0.112451 | 8 |
| Wren | 61.67% | 61.67% | 54.17% | 6.92s | 17.08s | 238,533 | 0.116997 | 9 |

失败聚类暴露了两个确定性 Guard 缺陷：

1. Wren 在不同 CTE 内复用局部别名 `__source`，Guard 的全局 alias map 会互相覆盖，错误拒绝开放字段；
2. 正常 4 表 Wren SQL 固定产生 8 个包装子查询，高于原默认上限 6。

TDD 修复加入了按 `SELECT` scope 解析 CTE/物理表 lineage，并把默认子查询总数预算提高到 12；JOIN=6、CTE=6、嵌套深度=3 仍保持限制。真实 4 表 `dry-plan` 生成的 2,395 字符 SQL 已通过字段白名单、公司隔离、排序和 LIMIT 校验。

## 3. 修后同条件三轮 A/B

| 语义层 | 三轮 | 总通过率 | 结构通过率 | 结果签名率 | p50 | p95 | Token | Cost USD | Repair |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 19/20、19/20、18/20 | 93.33% | 98.33% | 93.75% | 5.32s | 30.54s | 208,915 | 0.110011 | 6 |
| Wren | 17/20、17/20、18/20 | 86.67% | 91.67% | 87.50% | 6.53s | 36.01s | 279,526 | 0.141307 | 11 |

Wren 修复前后：总通过率 `61.67% → 86.67%`，结果签名率 `54.17% → 87.50%`。修后 Wren 相对 Native：

- 总通过率低 6.66 个百分点；
- 结果签名率低 6.25 个百分点；
- p50 慢 1.21s，p95 慢 5.47s；
- 多 70,611 Token，多约 $0.031296；
- 多 5 次 Repair。

因此 Wren 已达到可继续实验的水平，但当前证据不支持把默认语义层从 Native 切为 Wren。

## 4. 剩余稳定失败

| Case | Native | Wren | 结论 |
| --- | --- | --- | --- |
| `comparison-invoice` | 3/3 失败 | 3/3 失败 | 查询返回销售量和开票量，但缺少问题要求的差额列；结果签名正确拦截 |
| `ranking-salespeople` | 3/3 通过 | 3/3 失败 | Wren 长上下文下模型未稳定给无显式 Top N 的排名设置 `row_limit`，两次 Repair 后停止 |
| `clarify-customer` | 1/3 失败 | 2/3 失败 | 模型偶发先产出无效计划，未稳定进入 Interrupt；需把明显指代歧义前移到确定性路由 |

以上不是数据库写入或公司隔离失败。任何业务数值仍需 UAT 与 Odoo 原生报表核对，自动参考 SQL 不能代替业务负责人签字。

## 5. 复现

```powershell
Set-Location D:\odoo19e\odoo-agent

& .\.venv\Scripts\python.exe .\evals\run_semantic_benchmark.py `
  --runs 3 `
  --providers native,wren `
  --model-provider deepseek `
  --output-dir .\evals\reports\native-wren-ab
```

当前默认 SiliconFlow 模型探针返回 HTTP 402，因此本次使用已经配置且可用的 DeepSeek 通道做请求级覆盖，没有修改持久化默认模型设置。
