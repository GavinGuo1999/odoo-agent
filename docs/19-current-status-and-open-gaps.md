# 当前状态与未关闭差异

> 状态日期：2026-09-07
>
> 适用应用版本：0.2.0
>
> 用途：给下一次开发或评审一个不必重新扫描仓库的入口。本文只记录**已验证的事实**和**未关闭的差异**，不记录计划。
>
> 证据标注：`实测` 表示有可复现的运行记录；`静态核对` 表示读代码得出；`推断` 表示尚无证据，必须先验证再引用。

## 1. 五个核心判断

1. **RAG 不需要引入 RAGFlow。** 语料为 41 篇笔记、605 个 chunk、33.4 万字符（`实测`，读 `.wiki-index/wiki.db`）。这个量级下向量库不是瓶颈，而 RAGFlow 的 OCR、版面解析、多租户能力本项目都用不上，且会丢掉当前已在利用的 frontmatter 状态过滤、标题分级加权和 Obsidian 双链邻居扩展。
2. **Langfuse 的记录层已经完整，缺的是对比层。** Trace 分层、Session、成本、脱敏、点踩原因分类、Prompt 版本管理、Dataset 与 Experiment 代码均已存在（`静态核对`）；但基准的 p50/p95/Token/Cost 仍只落在本地 JSON，Langfuse 内没有版本间横向对比视图。
3. **Wiki 对 ChatBI 取数准确率的贡献为零。** `data` 意图不经过 Wiki，SQL 生成只读语义层；`hybrid` 意图下 Wiki 内容也只进入 `synthesize_sales_answer`，不进入 QueryPlan 与 SQL（`静态核对`，见 `backend/app/bi/agent.py`）。Wiki 是并行的知识问答产品，不是 BI 增强。
4. **Wren 当前不适合作为默认语义层。** 三轮全量 A/B 显示其更慢更贵且通过率略低（`实测`，见 [18](18-native-wren-ab-benchmark.md)）。它的真实价值是作为对抗性 SQL 生成器，曾暴露 Guard 的两个缺陷。
5. **Skill 资产的主要风险是内容漂移，不是缺少 Waza。** 已发生过一次 SKILL.md 否认已落地能力的实例，现已由 `test_project_skills.py` 的断言防护。

## 2. 已验证的能力与数据

### 2.1 Wiki 检索（`实测` 2026-09-06）

命令：`python evals\run_wiki_rag_eval.py --mode both --limit 6`，数据集 `evals/datasets/wiki_rag_golden.jsonl`，20 个 case。

| 检索模式 | recall | hit@6 | MRR | ID context precision |
| --- | ---: | ---: | ---: | ---: |
| lexical | 0.8000 | 0.8000 | 0.5367 | 0.1334 |
| hybrid + rerank | 0.9500 | 0.9500 | 0.7292 | 0.1584 |

- hybrid 的 `fallback_reasons` 为空，20 个 case 全部 `reranked: true`，说明 FAISS 索引、SiliconFlow Embedding 与 Reranker 均已实际生效。
- hybrid 相对 lexical：recall +0.15、MRR +0.1925、hit@6 +0.15。
- hybrid 修复的三条全部是“中文提问 / 英文标题概念笔记”：`wiki-sale-order`、`wiki-stock-rule`、`wiki-manifest`。这是稠密检索的典型适用场景。
- ID-based context precision 对 top-k 敏感（多数 case 只有 1～2 篇参考笔记却取 top6），**不能单独作为质量结论**。

### 2.2 销售 Text2SQL（`实测` 2026-09-03）

见 [18](18-native-wren-ab-benchmark.md)。20 题 × 3 轮 × 2 语义层：

| 语义层 | 总通过率 | 结果签名 | p50 | Token | Cost USD | Repair |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 100.00% | 100.00% | 6.27s | 203,370 | 0.107130 | 4 |
| wren | 98.33% | 100.00% | 11.66s | 275,026 | 0.140127 | 8 |

Wren 相对 native：p50 +5.39s、Token +71,656、Cost +$0.033、Repair +4，通过率 -1.67 个百分点。

### 2.3 RAGAS 首个基线（`实测` 2026-09-07）

命令：`run_wiki_rag_eval.py --mode hybrid --ragas --ragas-max-cases 5 --ragas-timeout 300 --ragas-max-tokens 8192`。判官与作答模型均为 SiliconFlow `deepseek-ai/DeepSeek-V4-Pro`，embedding 为 `BAAI/bge-m3`，样本 5 个 case。

| 指标 | 值 |
| --- | ---: |
| Faithfulness | 0.9075 |
| Answer Relevancy | 0.8376 |
| Context Precision | 0.8820 |
| Context Recall | 1.0000 |

四项全部评分成功（`status: completed`），归档于 `evals/reports/20260907-095649-wiki-rag/`，并已推送 Langfuse（trace `638540427c…`，8 个 `wiki-*` NUMERIC Score，含检索侧 4 项）。

**只有 5 个样本**，且判官与被评作答用的是同一个模型（自评偏高的风险已知未消除）。这是基线不是结论，扩样本与换判官模型后需复验。

跑通前踩到两个坑，都已在代码中处理：判官默认 90s 会超时（`--ragas-timeout`，现 300s）；RAGAS 的 `InstructorModelArgs.max_tokens` 默认 1024，推理模型的思考 token 会先吃光预算导致结构化输出被截断、`faithfulness` 每个 case 抛 `IncompleteOutputException`（`--ragas-max-tokens`，现 8192）。

### 2.4 自动化门禁（`实测` 2026-09-03 基线）

后端 `unittest` 115/115、静态黄金集 20/20、`node --check app.js` 通过、Wren 9 模型构建通过。

## 3. 未关闭的差异

### 3.1 检索与 RAG

| 项 | 状态 | 说明 |
| --- | --- | --- |
| `wiki-external-id` 排序退化 | **已调查，判定不修**（2026-09-07） | 融合与 reranker 各自独立把它降到第 2；权重在 0.45/0.55～0.55/0.45 区间对 26 题结果完全相同，再偏词法会掉 recall。详见 [20 P1-4](20-next-actions.md) |
| `wiki-runtime`、`wiki-stock-picking` 在 hybrid 下的名次 | 未处理 | 前者被 reranker 从第 1 压到第 2，后者被融合挤出 top-6。要动的是 reranker 策略，需先补同类用例 |
| 需 LLM 评审的 RAGAS 一层 | **已建立基线**（`实测` 2026-09-07） | 见 §2.3 |
| 报告不可比 | **已解决**（2026-09-07） | `run_wiki_rag_eval.py` 现在按 `evals/reports/<时间戳>-wiki-rag/` 归档 `report.json` + `summary.md`，同时仍刷新 `wiki-rag-latest.json`；`--push-langfuse` 把检索与 RAGAS 指标作为 Score 挂到一条 trace 上 |

### 3.2 词法打分的两处缺陷（`实测`，已修复 2026-09-06）

在真实 `learn_odoo` 语料上复现并修复了 `_search_locked` 的 lexical 路径两处缺陷（[20 P0-2](20-next-actions.md) 记录改动细节与代码）：

1. **BM25 加成饱和。** 原 `2.0 + max(0.0, min(3.0, -rank))` 使前排候选一律取到 5.00，该项不再参与区分。改为按同批次命中的最大幅值做相对缩放：`2.0 + 3.0 * (magnitude / peak_magnitude)`。
2. **字段命中未按字段长度归一。** `title×4 + heading×3 + metadata×2 + content×1` 原使用原始交集计数，长笔记天然累积更多 content 命中。改为除以该字段 gram 数的平方根。

两处同时修复后的效果（`--mode both --limit 6`，2026-09-06 实测）：

| 方案 | recall | hit@6 | MRR |
| --- | ---: | ---: | ---: |
| 修复前 | 0.8000 | 0.8000 | 0.5367 |
| 修复后 | 0.8750 | 0.9000 | 0.6158 |

`wiki-stock-picking`（此前 lexical 与 hybrid 均未召回）现在纯词法可召回 `Stock Picking.md`，是本次修复实际找回的唯一 case。`wiki-sale-order`、`wiki-stock-rule`、`wiki-manifest` 修复后仍是纯词法 miss——它们的中文提问是英文标题概念笔记的意译，trigram 精确匹配在 FTS 层面完全无法命中（`Stock Rule.md` 的两个 chunk 甚至不在 100 条候选之内），这类复述失配只能靠 hybrid 的向量检索解决（见 §2.1），词法打分公式改不动。

hybrid 的 recall 修复后仍为 0.9500，未升到 1.00：`wiki-stock-picking` 在 hybrid 模式下仍是唯一 miss（RRF 融合 + rerank 后未采纳词法层新排名）。此前记录的"待验证假设：hybrid 可能升至 1.00"已证伪。

该实验样本已从 20 题扩到 26 题（新增 6 道精确标识符题，见 [20 P1-4](20-next-actions.md)）。

### 3.3 可观测性（`实测` 2026-09-06 更新）

| 项 | 状态 |
| --- | --- |
| Langfuse environment 映射 | 已实现（`configure_langfuse_environment`） |
| Prompt Management | 已实现（production label、300s 缓存、2s 超时、代码 fallback、版本回链 generation） |
| Dataset 与 Experiment | **已实际执行**（`实测`）。数据集 `odoo-agent/sales-golden-v1` 存在且有 20 个 item；2026-09-06 回填 6 次 run（native/wren 各 3 轮），每次 run 带 60 个 item 级分数（20 题 × sql-safe / metric-correct / answer-grounded）与 6 个 run 级分数 |
| 点踩原因分类 | 已实现（`user-thumbs` BOOLEAN + `user-feedback-reason` CATEGORICAL） |
| 版本间成本/延迟对比 | **已实现**（`实测`）：`run_semantic_benchmark.py --langfuse-experiment` 把每轮的 p50/p95/Token/Cost/pass-rate 作为 run 级 Score 推上去，run 名含 `provider + model + git sha + 轮次` |
| 延迟门禁 | **已实现**：`p50 ≤ 18s` 成为会失败的断言（超预算时进程退出码为 1，`--no-latency-gate` 可关） |
| 按 `generation_role` 的成本报表 | **已实现**：`_usage_fields` 按 role 分桶累加，`summary.json` 的 `providers.<p>.by_role` 与 `summary.md` 的归因表给出每个 role 的调用数/Token/Cost 及占比 |
| LLM Judge | 未接入（BI 侧） |
| 生产环境可观测性 | `odoo-stack/compose.yaml` 中 `LANGFUSE_ENABLED` 默认 `false` |

Langfuse 上现有的可横比基线（`实测`，回填自 [18](18-native-wren-ab-benchmark.md) 的 9-03 三轮 A/B）：

| provider | 轮次 | p50 | p95 | pass_rate | cost USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| native | 1 / 2 / 3 | 6148.91 / 6402.97 / 5914.23 ms | 45218 / 32227 / 45214 ms | 1.0000 | 0.107130 |
| wren | 1 / 2 / 3 | 10411.28 / 11900.31 / 10089.37 ms | 44581 / 47015 / 35058 ms | 0.9833 | 0.140127 |

两条使用时必须知道的实测约束：

1. **数据集名里的 `/` 会打断 Langfuse 的路径型 REST 接口。** `odoo-agent/sales-golden-v1` 使 SDK 的 `api.datasets.get_run` / `delete_run` 返回 404（请求打到了 Web 应用而非 API）。写入路径不受影响。程序化读取或删除 run 时必须把名字 URL 编码为 `odoo-agent%2Fsales-golden-v1`。
2. **Score 摄取是异步的。** 推送后立即查询会看不到 run 级分数，约 1～2 分钟后才可见。**不要据此判断推送失败**——run 本体和 metadata 是立即可见的。

p95 仍在 32～47s，远高于 p50 门禁线；当前门禁只约束 p50（docs/20 P1-1 定义），p95 未设门禁。

### 3.4 语义层与配置

- `odoo-stack/compose.yaml` 的 `SEMANTIC_PROVIDER` 默认为 `wren`，与 2.2 的实测结论矛盾，应改回 `native`。
- 语义层仍限销售域；发票、收款、库存、毛利、销售团队等主题未开放。

### 3.5 前端（`静态核对`）

现有 5 个静态页（工作台 / 智能助手 / 销售看板 / Odoo Wiki / 数据与模型），单个约 2,030 行 IIFE `app.js`（另有独立的 `markdown.js`） 在每页全量加载，靠元素缺失隐式分页。已具备：SSE 阶段进度、ECharts 白名单渲染、CSV 导出、每条消息的成本显示、Langfuse Trace 链接、点踩原因下拉、移动端菜单、`aria` 标注。

未处理：

| 项 | 影响 |
| --- | --- |
| ~~助手回答用 `paragraph.textContent` 直接写入单个 `<p>`~~ | **已解决**（2026-09-07）：新增自托管渲染器 `markdown.js`，支持标题、加粗/斜体、行内代码、有序/无序列表（含混合嵌套）、表格、围栏代码块、引用、分隔线与安全链接 |
| ~~侧边栏在各页面各有一份副本~~ | **已解决**（2026-09-07）：`sidebar.js` 单点生成导航，页面只留 `data-sidebar` 挂载点 |
| ~~手工版本号~~ | **已解决**（2026-09-07）：改为服务端 `Cache-Control: no-cache` + mtime/size `ETag` + 304。新增静态文件仍需同步 `backend/app/main.py` 的 `_UI_FILES` 白名单 |
| ~~无评测与质量页面~~ | **已解决**（2026-09-07）：新增 `quality.html`，读 `GET /api/quality/summary`，展示最近一次 A/B、按 role 的成本归因、Wiki 检索对照、RAGAS 指标与历史运行 |
| `/api/semantic_audit` 无界面 | 后端能力已存在但无法在 UI 使用 |

架构建议：保持原生 JS 与自托管资源，不引入框架与构建链；按需拆为 `type="module"` 的每页入口即可。

**Markdown 渲染的安全约定（`实测`）**：`markdown.js` 全程只用 `document.createElement` / `createTextNode` 构造 DOM，**不使用 `innerHTML`**，因此模型输出里的任何标签只会成为文本节点。链接只放行 `http(s)`，`javascript:` / `data:` / `vbscript:` 一律降级为纯文本。这两点由 `backend/tests/test_markdown_rendering.py`（静态断言渲染器不含 `innerHTML` 等 API）与 `tests/markdown_render_test.js`（15 项行为回归，含 4 类注入载荷）共同守住。仓库没有前端构建链，所以 JS 测试用最小 DOM 桩在 Node 里跑，并由 Python 测试接进统一的 `unittest` 门禁。

### 3.6 交付与验收

- 9-03 之后存在一批未提交改动（Wiki 向量检索、RAGAS 评测、Prompt Management、Dataset Experiment、settings 前端），[16](16-tdd-test-and-acceptance-matrix.md) 的 115/115 基线不代表当前工作树。
- 黄灯未执行：Checkpointer 重启恢复、SiliconFlow 模块安装与 9 项 TransactionCase、SiliconFlow 真实 API smoke。
- 红灯 UAT-01～09 全部待用户签字，其中 UAT-01 销售口径对照是发布前置。

## 4. 文档与代码漂移

本项目已实际发生两类漂移，均需在流程上防护：

1. **否认已落地的能力。** `odoo-rag-evaluation/SKILL.md` 曾长期声明 FAISS、LlamaIndex、RAGAS “尚未进入生产”，而对应代码已存在。已由 `backend/tests/test_project_skills.py::test_skill_bodies_do_not_deny_shipped_capabilities` 断言防护。
2. **把推断写成事实。** 曾因 `.wiki-index/` 下没有 `wiki.faiss` 而记录“hybrid 因额度不足无法运行”；实际该索引由 `_ensure_vector_index` 在首次 hybrid 检索时按需构建，Embedding 与 Reranker 额度一直可用，2026-09-06 一次运行即得到 0.95 的 recall。**结论：缺少产物只能证明未执行，不能证明不可执行。** 本文的证据标注制度即为此设立。

## 5. 复现命令

```powershell
Set-Location D:\odoo19e\odoo-agent

# Wiki 检索对照（无模型调用）
.\.venv\Scripts\python.exe .\evals\run_wiki_rag_eval.py --mode both --limit 6

# 带 LLM 评审的 RAGAS（消耗 answer 模型与 embedding 额度）
.\.venv\Scripts\python.exe .\evals\run_wiki_rag_eval.py --mode hybrid --ragas --ragas-max-cases 5

# Skill 与 Waza 资产契约（离线）
.\.venv\Scripts\python.exe -m unittest backend.tests.test_project_skills -v

# 销售语义层 A/B（需只读数据库与模型额度授权）
.\.venv\Scripts\python.exe .\evals\run_semantic_benchmark.py --runs 3 --providers native,wren --model-provider deepseek
```

## 6. 相关文档

- [评测与质量保障](09-evaluation-and-quality.md)
- [演进路线](11-roadmap.md)
- [核心技术组件状态](12-technology-stack-status.md)
- [learn_odoo Wiki 与 ChatBI 知识集成](15-wiki-bi-knowledge-integration.md)
- [TDD 测试与验收红绿灯](16-tdd-test-and-acceptance-matrix.md)
- [Native / Wren 三轮真实 A/B 基准](18-native-wren-ab-benchmark.md)
