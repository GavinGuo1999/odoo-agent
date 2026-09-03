# Native / Wren 三轮真实 A/B 基准

> 首次执行：2026-09-01；当前代码全量复验：2026-09-03
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

## 5. 2026-09-02 三项针对性闭环

对上表三个 Case 先写失败测试，再完成以下修改：

1. `clarify-customer`：增加模型前的确定性 `detect_data_ambiguity` 节点；命中时直接 Interrupt，不访问数据库、不消耗模型 Token，恢复后继续原图；
2. `comparison-invoice`：增加稳定指标 `uninvoiced_quantity = SUM(product_uom_qty - qty_invoiced)`，并用 QueryPlan 合同强制同时返回销售数量、已开票数量和差额三列；
3. `ranking-salespeople`：只有用户明确要求前 N/Top N 时才强制 `row_limit=LIMIT`；未指定 N 的完整排名允许 `row_limit=null`，由 Guard 追加 500 行安全上限，ChartPlan 只展示前 10。

第一次针对性三轮中，Native/Wren 都是 6/9；唯一失败是模型只输出差额列。结果签名没有放宽，而是增加 `QueryPlanInvoiceDifferenceColumnsMissing` 合同并进入 Repair。修后同条件结果：

| 语义层 | 三轮 | 总通过率 | 结构通过率 | 结果签名率 | p50 | p95 | Token | Cost USD | Repair |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 3/3、3/3、3/3 | 100% | 100% | 100% | 23.31s | 60.94s | 51,986 | 0.028854 | 3 |
| Wren | 3/3、3/3、3/3 | 100% | 100% | 100% | 21.02s | 39.39s | 43,400 | 0.023253 | 0 |

报告目录：`evals/reports/20260902-first-three-targeted-fixed`。这是三个受影响 Case 的针对性证据；下一节记录随后完成的 20 Case 全量三轮复验。

## 6. 2026-09-03 当前代码全量闭环

前三项修复后的第一组全量复验中，Native 为 59/60，Wren 为 60/60。Native 唯一失败是 `comparison-invoice` 的真实结果签名不一致。Langfuse Trace 显示 SQL 只使用 `product_template.name->>'zh_CN'`，没有按既有语义规则回退 `en_US`；当中文名为空时，PostgreSQL 会把空值产品聚为一组。SQL 结构和 QueryPlan 当时都能通过，因此这是 Guard 合同缺口，不是参考结果问题。

本轮按 TDD 修复：

1. Red：新增产品维度 SQL 只取 `zh_CN` 时必须失败的 Guard 测试；
2. Green：当 QueryPlan 包含 `product` 维度时，最终 `product` 投影必须使用 `COALESCE(zh_CN, en_US)` 且顺序正确；
3. 针对性真实回归：`comparison-invoice` 的 Native/Wren 各三轮均为 3/3，结构和结果签名均为 100%；
4. 全量复验：同一模型、数据库、Case 和参数重新执行 20 Case × 3 轮 × 2 provider。

最终原始结果如下：

| 语义层 | 三轮 | 总通过率 | 结构通过率 | 结果签名率 | p50 | p95 | Token | Cost USD | Repair |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 20/20、20/20、20/20 | 100% | 100% | 100%（48/48） | 6.27s | 45.22s | 203,370 | 0.107130 | 4 |
| Wren | 20/20、19/20、20/20 | 98.33% | 98.33% | 100%（47/47） | 11.66s | 44.58s | 275,026 | 0.140127 | 8 |

Wren 第 2 轮的 `ranking-products` 在模型调用阶段收到上游 HTTP 504，`agent_run=false`，没有 SQL 可做结构或结果比较。报告保留这次真实可用性失败，没有用重跑替换原始数据。其余实际执行的 Wren SQL 没有结构不一致或结果签名失败。

以原始端到端口径比较，Wren 相对 Native：

- 总通过率低 1.67 个百分点，差异来自一次模型服务 504；
- 已执行结果的签名率相同，均为 100%；
- p50 慢 5.39s，p95 快 0.64s；
- 多 71,656 Token，多约 $0.032997；
- 多 4 次 Repair。

当前证据说明 Wren 已达到可用实验状态，但没有显示出成功查询准确率优势，同时中位延迟、Token、费用和 Repair 更高。因此继续保持 `native` 为默认、`wren` 为实验选项；是否切换默认还需扩展到更有区分度的业务 Case，并完成用户业务 UAT。

本地报告目录：`evals/reports/20260903-native-wren-ab-final`。报告默认被 Git 忽略，不保存业务结果行。

## 7. 复现

```powershell
Set-Location D:\odoo19e\odoo-agent

& .\.venv\Scripts\python.exe .\evals\run_semantic_benchmark.py `
  --runs 3 `
  --providers native,wren `
  --model-provider deepseek `
  --output-dir .\evals\reports\native-wren-ab
```

当前默认 SiliconFlow 模型探针返回 HTTP 402，因此本次使用已经配置且可用的 DeepSeek 通道做请求级覆盖，没有修改持久化默认模型设置。

针对三个 Case：

```powershell
& .\.venv\Scripts\python.exe .\evals\run_semantic_benchmark.py `
  --runs 3 `
  --providers native,wren `
  --cases clarify-customer,comparison-invoice,ranking-salespeople `
  --model-provider deepseek `
  --output-dir .\evals\reports\first-three-targeted
```
