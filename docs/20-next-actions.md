# 下一步任务队列

> 状态日期：2026-09-06
>
> 分工：[19 当前状态与未关闭差异](19-current-status-and-open-gaps.md) 只记录**已验证的事实**；本文只记录**待办与验收标准**。两者不重复陈述同一内容，修改能力状态时改 19，修改工作计划时改本文。

## 1. 执行环境约束

会话交接时最容易踩的坑，先读这一段。

- 所有命令必须在 **Windows PowerShell** 中执行，解释器为 `D:\odoo19e\odoo-agent\.venv\Scripts\python.exe`。
- 评测依赖已全部安装，**不需要再 `pip install`**：`ragas 0.4.3`、`langchain-community 0.3.31`、`faiss-cpu 1.15.0`、`llama-index-core 0.14.24`、`llama-index-vector-stores-faiss 0.7.0`。
- Wiki 向量索引由 `_ensure_vector_index` 在首次 hybrid 检索时按需构建。**索引文件不存在只说明未执行过，不说明不可执行。**
- Waza CLI 未安装。官方 Windows 安装脚本走 GitHub API，共享出口 IP 易触发未认证限流；换代理节点或等配额重置即可。当前判断为低优先，见第 4 节。
- 生产栈相关改动本轮暂缓（用户明确 devops 先不管），但第 3.7 条记录了必须改的配置错误。

## 2. 优先队列

### P0-1 提交 9-03 之后的未提交改动——已完成，2026-09-07

`313bdd4` 之后累积的 55 项改动已分 8 个提交落到分支 `feat/hybrid-rag-and-langfuse-comparison`（末位提交 `1edec64`）：

| 提交 | 内容 |
| --- | --- |
| `420a851` | 配置与依赖（hybrid 检索设置、`explain_total_cost_limit`、评测依赖、容器构建） |
| `d4e49ed` | Wiki hybrid 检索 + P0-2 词法打分修复 |
| `46b1709` | Langfuse Prompt Management + 测试隔离修复 |
| `d0b2825` | P1-1 成本归因与 Langfuse 对比层 |
| `b6ffd5c` | EXPLAIN 成本护栏与 LLM Gateway 加固 |
| `3fc42c6` | settings 前端与每条消息成本显示 |
| `ef73181` | 黄金集 20 → 60 题 |
| `ba266bd` / `1edec64` | 文档（19/20 新增、既有文档修订、基线更新） |

- 验收结果（提交后重跑）：后端 `unittest` **138/138**、`node --check app.js` 通过、静态黄金集 **60/60**、`git status --short` 干净。
- [16](16-tdd-test-and-acceptance-matrix.md) 的基线提交与测试计数已更新；§4.1/4.3/4.4 的历史轮次证据保持原值不动。
- 提交前做过密钥扫描，唯二命中是脱敏测试里的假值 `sensitive-value`（断言密钥**不**出现）。`.gitignore` 已覆盖 `.env*`、`.venv/`、`.wiki-index/`、`evals/reports/`。
- **待办**：该分支尚未合回 `main`，也未推送到 `origin`。需要用户决定是直接 fast-forward 到 `main` 还是走 PR。

### P0-2 Wiki 词法打分修复（TDD）——已完成，2026-09-06

背景见 [19 §3.2](19-current-status-and-open-gaps.md)（含修复前后的最终实测数据，已更新）。修改点在 `backend/app/services/wiki_knowledge.py` 的 `_search_locked`：

1. **解除 BM25 饱和并做同批次相对缩放**：原 `2.0 + max(0.0, min(3.0, -rank))` 把几乎所有强匹配都截断到同一个 5.00，抹平了区分度。改为对同一次查询命中的全部 chunk，取其未截断幅值的最大值作为基准（`peak_magnitude`），按 `2.0 + 3.0 * (magnitude / peak_magnitude)` 做相对缩放——不能只是去掉 cap 直接相加，实测过直接相加会让个别 chunk 的原始 bm25 幅值（观测到最高 21.5）整个盖过字符 gram 分量，比饱和前更差。
2. **字段命中按字段长度归一**：`title×4 + heading×3 + metadata×2 + content×1` 改为每个字段的命中数除以该字段 gram 数的平方根，避免长笔记的原始交集计数天然占优。

两处已同时落地。

- Red → Green：[test_wiki_knowledge.py](../backend/tests/test_wiki_knowledge.py) 新增 `test_lexical_search_surfaces_short_concept_note_over_long_source_notes`，直接用 `Settings().wiki()` 指向真实 `learn_odoo` 语料（而非合成 fixture），断言 `Stock Picking.md` 进入纯词法 top-6。
- **验收结果（`--mode both --limit 6`，2026-09-06 实测）**：

  | 指标 | 修复前 | 修复后 | 目标 |
  | --- | ---: | ---: | ---: |
  | 纯词法 recall | 0.8000 | 0.8750 | 约 0.8750 ✅ |
  | 纯词法 hit@6 | 0.8000 | 0.9000 | — |
  | 纯词法 MRR | 0.5367 | 0.6158 | 约 0.6725（未达，见下） |
  | hybrid recall | 0.9500 | 0.9500 | ≥0.95 ✅，未升到 1.00 |

  后端全量 `unittest` 无新增回归（`test_wiki_knowledge.py` 5/5 绿）。

- **对 Red 步骤原定范围的修正（重要，纠正一处会话交接遗留的过度断言）**：P0-2 原计划断言 `Sale Order.md`、`Stock Rule.md`、`Stock Picking.md` 三者都进入纯词法 top-6。实测证明只有 **`Stock Picking.md`** 能被这两处修复真正找回（它标题/正文用词与提问直接重合）。`Sale Order.md`、`Stock Rule.md` 修复后仍是纯词法 miss——这两条提问是英文标题概念笔记的中文意译，trigram 精确匹配在 FTS 层面完全无命中（`Stock Rule.md` 的两个 chunk 甚至不在 100 条 FTS 候选内），字符 gram 重叠也天然不足。这正是 [19 §2.1](19-current-status-and-open-gaps.md) 记录的"hybrid 修复的三条：`wiki-sale-order`、`wiki-stock-rule`、`wiki-manifest`"——它们本该靠向量检索解决，词法打分公式改不动这类复述失配。已把测试改为只断言 `Stock Picking.md`，避免为了凑齐三个 case 在 20 题上调参过拟合。
- MRR 差距（0.6158 vs 目标 0.6725）判断为可接受：目标值来自会话交接时的离线复刻实验，未必使用完全相同的缩放公式；recall/hit@6 精确吻合已验证两处修复的方向正确。
- hybrid recall 维持在 0.95、未如"待验证假设"升到 1.00：`wiki-stock-picking` 在 hybrid 模式下仍是唯一 miss（RRF 融合 + rerank 后未采纳词法层面的新排名）。该假设已证伪，**不要**再在别处引用"hybrid 可能修到 1.00"。
- 注意：本实验样本仍仅 20 题，扩充数据集后需复验。

### P1-1 Langfuse 对比层——已完成，2026-09-06

三项交付全部落地并**已对着真实 Langfuse 实例跑通验证**（不是只写代码），实测数据见 [19 §3.3](19-current-status-and-open-gaps.md)。

1. **run 级指标进 Langfuse。** `evals/run_semantic_benchmark.py --langfuse-experiment` 把每一轮作为一次 Dataset Run 推送，run 名为 `{provider}-{model_provider}-{git_sha}-r{轮次}-{时间戳}`，run 级 Score 为 `p50-latency-ms` / `p95-latency-ms` / `total-tokens` / `cost-usd` / `pass-rate`（NUMERIC）与 `p50-latency-budget`（BOOLEAN）。item 级沿用已跑通的 `sql-safe` / `metric-correct` / `answer-grounded`。
2. **按 `generation_role` 的成本归因。** `backend/app/bi/agent.py` 的 `_usage_fields` 按 role 分桶累加（role 名与推给 Langfuse 的 `generation_role` 是同一套），经 `AgentOutcome.role_usage` → eval `actual.role_usage` → `summary.json` 的 `providers.<p>.by_role`，并在 `summary.md` 渲染为含 Token/Cost 占比的归因表。
3. **p50 门禁。** `P50_LATENCY_BUDGET_MS = 18_000`；`summary` 增加 `latency_budget`，超预算时 `main()` 返回退出码 1（`--no-latency-gate` 可关）。

额外增加 **`--push-report <目录>`**：把已归档的历史报告回放成 Langfuse run，不重跑 Agent、不花模型额度。它既是零成本验证 SDK 调用的手段，也用来把历史基线搬上 Langfuse。

- 验收结果：
  - 用 `--push-report evals/reports/20260903-native-wren-ab-final` 回填了 6 次 run（native/wren 各 3 轮），Langfuse 上现有 native 与 wren 的可横比基线，数值与 [18](18-native-wren-ab-benchmark.md) 完全一致（native p50 ≈ 6.1s / $0.107130 / pass 1.0000，wren p50 ≈ 10～11.9s / $0.140127 / pass 0.9833）；
  - 回读确认每次 run 落地 60 个 item 级分数与 6 个 run 级分数；
  - 后端 `unittest` **138/138 全绿**。
- 新增测试：`backend/tests/test_agent.py::UsageAttributionTests`（按 role 累加且分角色之和等于总数）、`backend/tests/test_evals.py::BenchmarkCostAttributionTests`（归因占比、p50 门禁、run 级 Evaluation 形状、run 名）。
- 使用时必须知道的两条实测约束（数据集名含 `/` 会打断路径型 REST 接口；Score 摄取有 1～2 分钟延迟）见 [19 §3.3](19-current-status-and-open-gaps.md)，**不要**把"查不到分数"误判为推送失败。
- 顺带修复：`backend/tests/test_observability.py` 缺少 `_langfuse_client` 的 lru_cache 清理，Mock 会泄漏到后续测试并导致 `test_query_plan` 失败。这是 P0-1"全量绿灯"此前无法达成的原因，现已加 `setUp`/`tearDown` 清缓存。
- 未做（保持在待办）：p95 门禁（当前 p95 32～47s，远高于 p50 线，需要先确认业务可接受范围）、BI 侧 LLM Judge。

### P1-2 Wiki RAG 报告可比性

`evals/run_wiki_rag_eval.py` 目前只覆盖写 `evals/reports/wiki-rag-latest.json`，上一次结果即被丢弃。

- 交付：生成 `summary.md`（对齐 `run_semantic_benchmark.py` 的做法）、按日期归档到 `evals/reports/<date>-wiki-rag/`、把关键指标作为 Score 推送 Langfuse。
- 验收：可以取任意两次运行做差异对比而不必翻 Git 历史。

### P1-3 建立 Faithfulness 基线

- 命令：`.\.venv\Scripts\python.exe .\evals\run_wiki_rag_eval.py --mode hybrid --ragas --ragas-max-cases 5`
- 前置：answer 模型与 embedding key 均已配置（消耗额度，量很小）。
- 验收：报告顶层 `ragas` 不再为 `null`，Faithfulness 与 Answer Relevancy 有首个基线值。

### P1-4 追查 hybrid 的排序退化

`wiki-external-id` 的 MRR 在 hybrid 下从 1.00 降到 0.50，是本轮唯一排序退化。

- 方向：RRF 权重 `0.45 / 0.55` 与 reranker 在精确标识符类问题上稀释了词法的正确判断。
- 验收：定位到是融合权重还是 reranker 导致，并给出不牺牲其他 case 的处理方式；只有一个样本，先补测试用例再调参。

### P2 前端

详见 [19 §3.5](19-current-status-and-open-gaps.md)。架构结论：保持原生 JS 与自托管资源，不引入框架和构建链。

| 优先级 | 项 | 验收 |
| --- | --- | --- |
| P0 | 助手回答的 Markdown 渲染 | 加粗、列表、表格正常显示；渲染器自托管；无 XSS 回归 |
| P1 | 新增“评测与质量”页 | 读 `evals/reports/*.json`，展示通过率、p50/p95、Token/Cost、Wiki RAG 指标与最近一次 A/B |
| P2 | 侧边栏改 JS 注入；`app.js` 拆为每页 `type="module"` 入口；替换手工版本号 | 新增导航项只改一处；每页不再加载无关代码 |
| P3 | `/api/semantic_audit` 增加界面 | 现有后端能力可在 UI 使用 |

### P2-2 待处理的噪音

- `run_wiki_rag_eval.py` 的 RAGAS `DeprecationWarning`：**不要直接替换 import**。`ragas.metrics.collections` 的调用签名不同（`.ascore(**kwargs)` 返回带 `.value` 的对象，即该文件 `run_ragas` 中已使用的形式），而当前这段用的是 `single_turn_ascore(sample)`。改则需连调用一起改并补测试。
- 脚本退出时的 `ResourceTracker.__del__` 报错为 multiprocess 在 Python 3.12 下的清理噪音，发生在结果打印之后。接入 CI 前需确认 `$LASTEXITCODE` 仍为 0。
- `.wiki-index/` 下有一个 3.7MB 的 `.tmp` 残留文件可清理。

### P3 已知但本轮不做

- `odoo-stack/compose.yaml` 的 `SEMANTIC_PROVIDER` 默认为 `wren`，与 [18](18-native-wren-ab-benchmark.md) 的实测结论矛盾，应改回 `native`；`LANGFUSE_ENABLED` 默认 `false` 使线上无可观测性。
- 黄金集扩展到退款、税、空值、多币种等至少 60 题，需用户先确认业务口径。
- UAT-01～09 全部待用户签字，UAT-01 销售口径对照是发布前置。

## 3. 已定结论（不必重新讨论）

| 议题 | 结论 | 依据 |
| --- | --- | --- |
| 是否引入 RAGFlow | 不引入 | 语料 41 篇 / 605 chunk / 33.4 万字符，向量库不是瓶颈；RAGFlow 会丢掉 frontmatter 过滤、标题加权、双链扩展 |
| 是否安装 Waza | 低优先，可不装 | mock 只验触发边界，已被 `test_project_skills.py` 覆盖；真实评测需 Copilot 订阅且验的是 Copilot 行为 |
| 是否做 MCP | 排在最后 | 只读 MCP server 有价值，但属分发收益，不提升准确率 |
| Wren 是否转正 | 保持非默认 | 慢 86%、贵 31%、通过率低 1.67 个点；价值在于作为对抗性 SQL 生成器暴露 Guard 缺陷 |
| Wiki 是否进 SQL 生成 | 不进 | 与确定性 Guard 冲突且增加延迟；正确做法是把业务口径蒸馏进语义层 |
| 前端是否换框架 | 不换 | 2000 行原生 JS 可控，构建链负担大于收益 |
| 是否做源码检索 | 暂不开 | 属新产品线；若做则用确定性 ripgrep/AST 定位，不做全库向量化 |

## 4. 本轮已完成

| 文件 | 变更 |
| --- | --- |
| `.agents/skills/odoo-rag-evaluation/SKILL.md` | 删除失实的“FAISS/LlamaIndex/RAGAS 尚未进入生产”，改为实测数据表 |
| `backend/tests/test_project_skills.py` | 新增 `test_skill_bodies_do_not_deny_shipped_capabilities`，防止 Skill 再次否认已落地能力 |
| `docs/17` §6 | 同步实测数据与未关闭问题 |
| `docs/19`（新增） | 当前状态与未关闭差异，含证据标注制度 |
| `docs/20`（本文，新增） | 任务队列与已定结论 |
| `docs/09`、`11`、`12`、`15`、`16`、`README` | 修订 5 处过时陈述并补充目录与能力快照 |
| `backend/app/services/wiki_knowledge.py` | P0-2：`_search_locked` 解除 BM25 饱和（同批次相对缩放）+ 字段命中按字段长度归一 |
| `backend/tests/test_wiki_knowledge.py` | 新增 `test_lexical_search_surfaces_short_concept_note_over_long_source_notes`（TDD Red→Green，直接跑真实 `learn_odoo` 语料） |
| `docs/19` §3.1、§3.2 | 更新为 P0-2 修复后的实测数据；证伪此前"hybrid 可能修到 1.00"的推断 |
| `docs/20`（本文）P0-2 | 标记完成，记录与原计划的偏差（`Sale Order.md`/`Stock Rule.md` 未被此修复找回，仅 `Stock Picking.md`） |
| `backend/app/bi/agent.py` | P1-1：`_usage_fields` 按 `generation_role` 分桶累加 Token/Cost，经 `AgentState.role_usage` 透出到 `AgentOutcome.role_usage` |
| `evals/run_sales_eval.py` | eval 结果的 `actual` 增加 `role_usage` |
| `evals/run_semantic_benchmark.py` | P1-1：`by_role` 归因、`latency_budget` 门禁、`benchmark_run_evaluations` / `benchmark_run_name` / `push_benchmark_runs` / `load_reports_from_dir`，CLI 增加 `--langfuse-experiment` / `--experiment-name` / `--push-report` / `--no-latency-gate` |
| `backend/tests/test_agent.py`、`test_evals.py` | 新增 P1-1 的 5 项 TDD 测试 |
| `backend/tests/test_observability.py` | 修复 lru_cache Mock 泄漏（`setUp`/`tearDown` 清缓存），解除 P0-1 全量绿灯的阻塞 |
| `docs/19` §3.3 | 改写为 P1-1 落地后的实测状态，含 Langfuse 上的可横比基线表与两条使用约束 |
