# 下一步任务队列

> 状态日期：2026-09-07
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

### P1-2 Wiki RAG 报告可比性——已完成，2026-09-07

- `archive_report()` 每次运行归档到 `evals/reports/<时间戳>-wiki-rag/`，内含 `report.json` 与 `summary.md`；`wiki-rag-latest.json` 仍然刷新，作为“最近一次”的指针。
- `render_markdown()` 输出两种模式的指标表、hybrid−lexical 的差值，以及 RAGAS 一节；RAGAS 没跑过时明确写“未运行”，**不写 0**。
- `--push-langfuse` 把检索指标与 RAGAS 指标作为 NUMERIC Score 挂到一条 `wiki-rag-eval-<run_id>` trace 上。
- 验收：任意两次运行可直接 diff 两个归档目录，不必翻 Git 历史。新增 `backend/tests/test_wiki_rag_eval.py::WikiRagReportTests` 5 项测试覆盖渲染、归档不覆盖、Score 载荷。

### P1-3 建立 Faithfulness 基线——已完成，2026-09-07

基线数据见 [19 §2.3](19-current-status-and-open-gaps.md)：Faithfulness 0.9075、Answer Relevancy 0.8376、Context Precision 0.8820、Context Recall 1.0000，四项全部评分成功。

跑通过程暴露并修复了三个问题，都不是"配置一下就行"：

1. **判官超时**：默认 90s 不够，NLI 判定比一次普通问答慢得多。新增 `--ragas-timeout`（现 300s）。
2. **`IncompleteOutputException`**：RAGAS 的 `InstructorModelArgs.max_tokens` 默认 1024，而判官是带 thinking 的推理模型，思考 token 先吃光预算导致结构化输出被截断，`faithfulness` 每个 case 都失败。新增 `--ragas-max-tokens`（现 8192）。
3. **`push_wiki_scores` 用错 API**：`start_as_current_span` 在 4.x 客户端上不存在（应为 `start_as_current_observation`），`span.update_trace` 也不存在（应为 `span.update` + `span.score_trace`）。已修并回读确认 8 个 `wiki-*` Score 全部落地。

**这条基线的两个已知局限**（引用时必须一并说明）：样本只有 5 个 case；判官与被评作答用的是同一个模型，自评偏高的风险未消除。换判官模型与扩样本后需复验。

复现命令（`--ragas-timeout 300` 与 `--ragas-max-tokens 8192` 已是代码默认值，此处显式写出只为说明这两个值是必要的，不是可有可无的调优）：

```powershell
.\.venv\Scripts\python.exe .\evals\run_wiki_rag_eval.py --mode hybrid --ragas `
  --ragas-max-cases 5 --ragas-timeout 300 --ragas-max-tokens 8192 --push-langfuse
```

耗时约 30～40 分钟（5 case × 4 指标，判官是推理模型且每个指标要多轮调用）。RAGAS 这一层现在是容错的：单个指标失败会记录 `error_type` 并继续，检索指标与归档不受影响。

### P1-4 追查 hybrid 的排序退化——已完成，2026-09-07。结论：**不要调权重**

按验收要求先补测试用例再做实验，结果**推翻了这条任务原本的假设**。

**先补的用例**：黄金集 20 → 26 题，新增 6 道精确标识符题（`amount_to_invoice`、`amount_residual`、`bom_line_ids`、`date_approve`、`arch_db`、`ai.openai_key`）。每个符号都经程序核实只出现在唯一一篇 reviewed 笔记里，参考答案照抄原文而非臆造。

**实验一：分阶段定位**（lexical / 只融合不重排 / 完整 hybrid，全量 26 题）

| 指标 | lexical | fusion | hybrid |
| --- | ---: | ---: | ---: |
| mean MRR | 0.7045 | 0.7468 | 0.7724 |
| mean recall | 0.9038 | 0.9615 | 0.9615 |

- **6 道新标识符题在三种配置下全部满分 1.0000。** 原假设"RRF 权重与 reranker 在精确标识符类问题上稀释词法判断"**不成立**——真正的标识符查询完全没被稀释。
- `wiki-external-id` 的特殊之处不在于它是标识符题，而在于 `ref('module.xml_id')` 里的 `module.xml_id` 是**占位符**，不是语料中真实存在的符号。词法排第 1 有偶然成分，而向量提上来的 `Base 元模型深挖.md` 同样在讲外部 ID，主题上并不离谱。
- 退化也不止一例：`wiki-runtime`（1→2）由 **reranker** 造成，`wiki-stock-picking`（6→掉出）由**融合**造成。原记录"是本轮唯一排序退化"在 P0-2 修好词法后已不成立。

**实验二：权重扫描**（全量 26 题，含 reranker）

| lex/vec | MRR | recall | 相对当前的名次变化 |
| --- | ---: | ---: | --- |
| 0.45/0.55（当前） | 0.7724 | 0.9615 | 基准 |
| 0.50/0.50 | 0.7724 | 0.9615 | 完全无变化 |
| 0.55/0.45 | 0.7724 | 0.9615 | 完全无变化 |
| 0.65/0.35 | 0.7724 | 0.9231 | `wiki-product-category` 3→2，但 `wiki-stock-rule` 6→掉出 |
| 0.80/0.20 | 0.7308 | 0.8846 | 再坏两个 case |

`0.45/0.55` 到 `0.55/0.45` 区间内权重**完全不影响结果**，说明当前取值既不敏感也不是问题所在。

**实验三：这一个 case 到底能不能修好**

| 权重 | 不重排 | 重排 |
| --- | ---: | ---: |
| 0.45/0.55 | 第 2 | 第 2 |
| 0.80/0.20 | **第 1** | 第 2 |

融合与 reranker **各自独立**地把它降到第 2：即便把权重推到极端、把融合掰回来，reranker 也会再压一次。要修好这一个 case，得同时接受掉 recall 的权重**加上**为该类查询绕开 reranker。

**结论：保持 `0.45/0.55` 不变，不为这个 case 调参。** 两阶段都是净收益（MRR +0.0423 / +0.0256，recall +0.0577），代价却是确定的。该取舍已固化为离线测试 `backend/tests/test_wiki_knowledge.py::RankFusionTests`（含"RRF 丢弃分数置信度"的直接证据），避免以后被当成 bug 顺手改掉。

**遗留（不再归入 P1-4）**：`wiki-runtime` 的 reranker 降级、`wiki-stock-picking` 在 hybrid 下仍未召回。这两条要动的是 reranker 策略而非融合权重，同样需要先补同类用例再谈调参。

### P2 前端

详见 [19 §3.5](19-current-status-and-open-gaps.md)。架构结论：保持原生 JS 与自托管资源，不引入框架和构建链。

| 优先级 | 项 | 验收 |
| --- | --- | --- |
| ~~P0~~ **已完成 2026-09-07** | 助手回答的 Markdown 渲染 | 见下方说明 |
| ~~P1~~ **已完成 2026-09-07** | 新增“评测与质量”页 | 见下方说明 |
| P2 **部分完成 2026-09-07** | 侧边栏改 JS 注入 ✅；替换手工版本号 ✅；`app.js` 拆为每页 `type="module"` 入口 ⏳ | 见下方说明 |
| P3 | `/api/semantic_audit` 增加界面 | 现有后端能力可在 UI 使用 |

**Markdown 渲染（已完成）**：新增自托管的 `markdown.js`（约 250 行，无依赖），`app.js` 的 `appendChatMessage` 对助手消息调用它；渲染器缺失时回退到原来的纯文本 `<p>`。支持标题、加粗/斜体、行内代码、有序/无序列表（含**混合类型嵌套**）、表格、围栏代码块、引用、分隔线、安全链接。安全性见 [19 §3.5](19-current-status-and-open-gaps.md)。

拆成独立文件而不是塞进 2000 行 IIFE，主要是为了**可测**：`tests/markdown_render_test.js` 用最小 DOM 桩在 Node 里跑 15 项回归（含 4 类注入载荷），由 `backend/tests/test_markdown_rendering.py` 接进 `unittest`，一条命令仍覆盖全部。真实浏览器复核也做了：表格/列表/代码块/链接均正确，注入的 `<script>`、`<img onerror>` 产生 0 个元素。

一处只有真浏览器才暴露的问题：初版实现里“有序列表套无序列表”会断成两个并列列表，`<ul>` 直接挂在 `<ul>` 下（无效 HTML），视觉上却因为缩进看着正常。Node 测试当时写得太宽松放过了它。已改为按缩进维护栈、每层各自决定 `ol`/`ul`，并把测试收紧为精确断言。

新增文件需同步两处，否则前端 404：`backend/app/main.py` 的 `_UI_FILES` 白名单、`chat.html` 的 `<script>`（必须在 `app.js` 之前）。静态资源版本号已从 `v=15` 升到 `v=16`。

**评测与质量页（已完成）**：新增 `quality.html` + `quality.js`（独立文件，与 markdown.js 同样只构造 DOM 不用 `innerHTML`），后端 `GET /api/quality/summary` 由 `backend/app/services/eval_reports.py` 只读汇总 `evals/reports/` 下的归档。展示最近一次语义层 A/B（通过率/结果签名/p50/p95/Token/Cost/Repair + 与 native 的差值 + 延迟门禁结论）、按 `generation_role` 的成本归因、Wiki 检索两种模式对照与差值、RAGAS 指标含成功样本数，以及全部历史运行。

页面**只读不触发评测**：跑评测要花模型额度、要连只读业务库，那是命令行里的显式动作，不该由打开一个页面触发。`evals/reports/` 是 gitignore 的，新克隆必然无数据，此时页面显示空状态并给出复现命令，而不是报错。

两处刻意的取舍：RAGAS「未运行」与「跑了但分低」在接口和页面上严格区分，缺失指标绝不显示成 0；RAGAS 区块始终附带样本量与"判官与被评回答同模型"的解读提醒，避免 0.9075 被当成全量结论。

实现时发现并修掉一个真 bug：最初按目录名字母序取"最近一次"，而真实归档里 `20260903-native-wren-ab-final`（09:58）在字母序上排在 `20260903-product-name-guard`（09:31）之后，页面会把更旧的一次当成最新。改为解析报告内的时间戳排序（缺失时回退到目录名前缀的日期时间），已补两项测试。

**侧边栏与版本号（已完成）**：新增 `sidebar.js`，导航是其中唯一的定义处；6 个页面只保留 `<aside class="sidebar" data-sidebar></aside>` 挂载点。必须在 `app.js` 之前加载（两者都 defer，顺序即执行顺序），否则 `app.js` 找不到侧边栏里的 `[data-db-mini-status]`、`[data-conversation-list]`。会话历史区只在 `data-page="chat"` 时注入。

手工版本号 `?v=N` 已全部删除，改由服务端保证：`/ui/{filename}` 返回 `Cache-Control: no-cache` 加基于 mtime+size 的 `ETag`，命中 `If-None-Match` 时返回 304。改了立刻生效，没改也不必重传，从此不存在"漏改版本号导致改了没生效"。

新增 `backend/tests/test_ui_shell.py` 8 项测试：页面不得硬编码 `nav-link`、必须有挂载点且脚本顺序正确、`sidebar.js` 不得使用 `innerHTML`、页面不得残留 `?v=`、资源必须带 `no-cache` 与 `ETag`、相同 ETag 必须 304。

**`app.js` 拆模块暂缓**：侧边栏抽取已经消掉了最大的一块重复（加导航项从改 6 个文件变成改 1 处）。剩下的拆分收益是"每页不加载无关代码"，但 `app.js` 仍有 2000 行且前端只有 `node --check` 与渲染器测试兜底，大改的回归风险高于收益。**建议先补前端测试再拆**，不要为了拆而拆。

### P2-2 待处理的噪音

- ~~脚本退出时的 `ResourceTracker.__del__` 报错~~：**已确认无害**（2026-09-07 实测）。`run_wiki_rag_eval.py` 跑完退出码为 0，噪音出现在结果打印之后，不影响 CI 接入。
- ~~`.wiki-index/` 的 `.tmp` 残留~~：**已根治**。残留来自进程被强杀（`_rebuild` 的 `replace`/`unlink` 两条路径都没跑到）。现在每次重建会清扫**超过 1 小时**的孤儿 `.tmp`——只删足够旧的，避免误删另一个正在构建的进程的临时文件。那个 8-20 留下的 3.7MB 文件也已删除。
- `run_wiki_rag_eval.py` 的 RAGAS `DeprecationWarning`：**不要直接替换 import**。`ragas.metrics.collections` 的调用签名不同（`.ascore(**kwargs)` 返回带 `.value` 的对象，即该文件 `run_ragas` 中已使用的形式），而当前这段用的是 `single_turn_ascore(sample)`。改则需连调用一起改并补测试。
- 脚本退出时的 `ResourceTracker.__del__` 报错为 multiprocess 在 Python 3.12 下的清理噪音，发生在结果打印之后。接入 CI 前需确认 `$LASTEXITCODE` 仍为 0。
- `.wiki-index/` 下有一个 3.7MB 的 `.tmp` 残留文件可清理。

### P3 已知但本轮不做

- `odoo-stack/compose.yaml` 的 `SEMANTIC_PROVIDER` 默认为 `wren`，与 [18](18-native-wren-ab-benchmark.md) 的实测结论矛盾，应改回 `native`；`LANGFUSE_ENABLED` 默认 `false` 使线上无可观测性。
- 黄金集扩展到退款、税、空值、多币种等至少 60 题，需用户先确认业务口径。
- UAT-01～09 全部待用户签字，UAT-01 销售口径对照是发布前置。

### P1-5 切回 deepseek 并复测全量 A/B——**待用户确认**

[19 §2.4](19-current-status-and-open-gaps.md) 实测：同样 3 道 KPI，deepseek 平均 5.8s 且无 repair，siliconflow 平均 20.2s 且每次都要 repair；未修 thinking 开关前的 siliconflow 更是达到 ~151s。

- 建议：把 `LLM_PROVIDER` 从 `siliconflow` 改回 `deepseek`（两家 key 都已配置，改环境变量或在"数据与模型"页切换即可）。
- 需用户确认的是**费用与合规**，不是技术：deepseek 与 siliconflow 的计费和数据出境策略不同，这属于业务决定。
- 切换后必须重跑全量三轮 A/B，重新测 p50/p95 与通过率，并更新 [18](18-native-wren-ab-benchmark.md)；在那之前不要把"快 26 倍"当成已落地收益。

### P1-6 给新增黄金集用例补参考结果签名

`evals/datasets/sales_golden.jsonl` 里有 14 道数据类用例带 `pending_reference: true`，因为写它们时库里还没有可用数据。现在 275 张订单已覆盖 12 个月，可以为每题写参考 SQL 并生成签名，然后去掉该标记。

- 验收：`test_live_result_references_cover_all_non_interrupt_data_cases` 在没有 pending 例外的情况下通过。

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
| `markdown.js`（新增） | 自托管 Markdown 渲染器，只构造 DOM 不用 `innerHTML` |
| `tests/markdown_render_test.js`（新增） | 15 项渲染回归，含注入载荷；用最小 DOM 桩在 Node 里跑 |
| `backend/tests/test_markdown_rendering.py`（新增） | 把上面的 Node 测试接进 `unittest`，并静态断言渲染器不含 `innerHTML` 等 API |
| `app.js`、`chat.html`、`styles.css`、`backend/app/main.py` | 接入渲染器、白名单、样式，静态资源版本号 `v=15` → `v=16` |
| `evals/run_wiki_rag_eval.py` | P1-2：`summary.md` + 按时间戳归档 + `--push-langfuse`；RAGAS 改为逐指标容错，单次判官超时不再丢弃整轮结果；新增 `--ragas-timeout` |
| `backend/tests/test_wiki_rag_eval.py` | 新增 9 项测试（报告渲染/归档/Score 载荷、RAGAS 部分失败聚合） |
| `docs/19` §2.3、§3.1、§3.5 | 记录 RAGAS 首个基线、报告可比性与 Markdown 渲染的落地状态 |
| `quality.html`、`quality.js`（新增） | 评测与质量页，只读展示归档 |
| `backend/app/services/eval_reports.py`（新增） | 只读汇总 `evals/reports/`，容错且按报告时间戳定序 |
| `backend/app/api/routes/quality.py`（新增）、`api/router.py` | `GET /api/quality/summary` |
| `backend/tests/test_eval_reports.py`、`test_quality_api.py`（新增） | 12 项测试（空状态、定序、损坏归档、RAGAS 区分、接口与页面接线） |
| `evals/datasets/wiki_rag_golden.jsonl` | P1-4：20 → 26 题，新增 6 道精确标识符题 |
| `backend/tests/test_wiki_knowledge.py` | 新增 `RankFusionTests`，固化 RRF 丢弃分数置信度这一已测取舍 |
