# learn_odoo Wiki 与 ChatBI 知识集成

> 文档版本：1.0  
> 适用应用版本：0.2.0  
> 最后更新：2026-08-20  
> 状态：已实现第一阶段

## 1. 目标

本集成把 `D:\odoo19e\learn_odoo` 作为 Odoo 业务知识和源码学习笔记来源，与 Odoo Agent 的实时数据分析组合起来。它解决两个不同问题：

1. **数据事实**：当前 Odoo 数据库中真实发生了什么；
2. **业务解释**：字段、模型、业务流程和源码机制通常如何工作。

系统不会让 Wiki 替代数据库，也不会让模型仅凭 Wiki 猜测实时业务数据。最终形成三层事实体系：

| 层 | 来源 | 回答的问题 | 可信边界 |
| --- | --- | --- | --- |
| 实时事实层 | Odoo PostgreSQL 只读查询 | 金额、数量、客户、订单、趋势 | 当前数据库事实 |
| 计算语义层 | `sales_semantics.json` / Wren MDL | 指标定义、开放字段、关系、过滤规则 | Text2SQL 的正式合同 |
| 解释知识层 | `learn_odoo` 已审核笔记 | 模型、字段、流程、源码与机制 | 通用解释和排查线索 |

## 2. 核心边界

### 2.1 Wiki 是只读来源

- Agent 只扫描 `learn_odoo/01_Odoo/**/*.md`；
- 不修改 Markdown、Frontmatter、Obsidian 链接或 `.codex-index`；
- 本地检索索引写入 `odoo-agent/.wiki-index/wiki.db`；
- `.wiki-index/` 已加入 Git 忽略；
- 重建索引采用临时数据库完成后原子替换，避免读到半成品。

### 2.2 只有审核内容能进入回答

当前允许 Frontmatter：

```yaml
status: reviewed
```

或：

```yaml
status: evergreen
```

`draft` 等未审核状态不会进入索引。`type: moc`、`dashboard`、`guide` 默认不作为回答证据，以减少目录页、Dataview 汇总和使用说明对检索的干扰。

### 2.3 Wiki 绝不进入 SQL Prompt

混合问题先检索 Wiki，再进入数据查询路径，但 `generate_sales_sql` 只能看到正式销售语义层和数据库发现字段。Wiki 内容只会进入查询完成后的 `synthesize_sales_answer`。

这条隔离规则用于避免：

- 学习笔记中的旧字段污染 SQL；
- 非正式推测变成查询过滤条件；
- Markdown 示例被误当成可执行 SQL；
- Wiki 与当前数据库版本不一致时破坏查询安全。

自动测试会断言 Wiki 原文没有出现在 SQL Generation Prompt 中。

## 3. 意图与 LangGraph 路由

入口使用确定性分类，不额外调用模型。

| Intent | 示例 | Graph 路径 | 是否查询 Odoo |
| --- | --- | --- | --- |
| `general` | “今天几号？” | `answer_general` | 否 |
| `semantic` | “销售额口径是什么？” | `explain_metric` | 否 |
| `knowledge` | “qty_to_invoice 怎么计算？” | Wiki 检索 → 知识回答 | 否 |
| `source` | “查看 sale.order._action_confirm 的源码” | Wiki 检索 → 源码知识回答 | 否 |
| `data` | “本月销售额是多少？” | 语义检索 → Text2SQL → 查询 | 是 |
| `hybrid` | “本月哪些订单已交付但不能开票，为什么？” | Wiki 检索 → Text2SQL → 混合解释 | 是 |

简化流程：

```text
classify_intent
├─ general   -> answer_general
├─ semantic  -> explain_metric
├─ knowledge -> retrieve_wiki_context -> answer_knowledge
├─ source    -> retrieve_wiki_context -> answer_knowledge
├─ data      -> retrieve_sales_context -> Text2SQL pipeline
└─ hybrid    -> retrieve_wiki_context
                -> retrieve_sales_context
                -> Text2SQL pipeline
                -> synthesize_sales_answer
```

`hybrid` 不使用确定性快速摘要，因为最终回答需要明确区分“数据库事实”和“Wiki 业务解释”。

## 4. 索引实现

### 4.1 数据源与分块

索引器读取 YAML Frontmatter 和 Markdown 正文，以二级/三级标题为主要分块边界。以下章节默认不索引：

- 图谱关系；
- 相关笔记；
- 面试可讲表达；
- 我还没搞懂的地方。

Dataview 代码块、图片链接和纯导航内容会被清理。超长章节按段落切分，避免把整个笔记塞入一次模型调用。

### 4.2 元数据

检索可使用并返回：

- 标题和章节；
- 相对路径；
- `type/status/module/topic/updated`；
- `tags/aliases`；
- `odoo_models/odoo_fields/bi_metrics`；
- Obsidian Wiki 链接关系。

### 4.3 检索方式

> 2026-09-06 更新：本节描述的“第一阶段只用本地词法检索”已经演进。`WikiConfig.retrieval_mode` 的默认值现为 `hybrid`：在下列词法检索之上叠加 SiliconFlow `bge-m3` 向量检索（经 LlamaIndex 持久化到 FAISS）与 `bge-reranker-v2-m3` 重排，任一环节失败都会记录 `fallback_reason` 并降级回纯词法。实测 hybrid recall 0.95、lexical 0.80，详见 [当前状态与未关闭差异](19-current-status-and-open-gaps.md)。

词法检索层（也是降级时的兜底）：

1. SQLite FTS5；
2. 中文二元/三元字符匹配；
3. 英文字段、模型名和方法名 Token 匹配；
4. 标题、章节、元数据和正文分级加权；
5. 精确标题加分；
6. 在结果有余量时补充一篇显式 Obsidian Wiki 链接邻居。

这样可以零外部成本地检索 `qty_to_invoice`、`stock.move` 等技术词，也便于先通过黄金问题观察是否真的需要向量检索。

### 4.4 自动更新

每次状态读取或搜索都会计算源文件指纹。指纹基于相对路径、文件大小和修改时间；源文件变化后自动重建索引。也可以在 Wiki 页面点击“重新建立索引”强制更新。

## 5. 回答与引用协议

### 5.1 知识回答

知识模型只能使用以下格式的上下文：

```text
[知识来源 1]
标题：Sale 源码主链路
章节：qty_to_invoice 的计算
路径：01_Odoo/...
内容：...
```

回答的重要结论必须使用 `[知识来源 N]` 标记。API 同时返回结构化 `citations`，前端展示标题、章节、摘要、相对路径和 Obsidian 打开链接。

如果没有命中，系统直接返回“没有足够依据”，不调用模型凭记忆补写。

### 5.2 混合回答

混合回答必须分成：

1. **数据事实**：只引用本轮 SQL 结果；
2. **Wiki 业务解释**：只作为通用机制和排查方向，并引用来源。

Wiki 规则不能证明某一条订单的具体原因。例如，笔记可以说明 `qty_to_invoice` 的一般计算机制，但只有查询到该订单行的策略、交付量和已开票量后，才可以判断具体阻塞条件。

## 6. API

### 6.1 `GET /api/wiki/status`

读取索引状态；索引过期时自动更新。

主要返回：

```json
{
  "available": true,
  "root_path": "D:\\odoo19e\\learn_odoo",
  "index_path": "D:\\odoo19e\\odoo-agent\\.wiki-index\\wiki.db",
  "note_count": 41,
  "chunk_count": 605,
  "indexed_at": "2026-08-20T10:00:00+08:00",
  "source_fingerprint": "...",
  "tokenizer": "trigram",
  "allowed_statuses": ["reviewed", "evergreen"]
}
```

计数随 Wiki 内容变化，示例不构成固定合同。

### 6.2 `GET /api/wiki/search?q=...&limit=6`

- `q`：2～500 字符；
- `limit`：1～12；
- 返回结构化命中，不返回完整 Markdown 正文；
- `absolute_path` 和 `obsidian_uri` 仅用于本机单用户界面。

### 6.3 `POST /api/wiki/reindex`

强制重建索引。它只删除和替换 BI 项目的索引数据库，不写 Wiki。

### 6.4 ChatResponse 扩展

新增：

```json
{
  "phase": "knowledge-base",
  "intent": "knowledge",
  "answer_mode": "knowledge",
  "citations": [
    {
      "title": "Sale 源码主链路",
      "heading": "qty_to_invoice 的计算",
      "relative_path": "01_Odoo/...",
      "obsidian_uri": "obsidian://open?..."
    }
  ]
}
```

`intent=hybrid` 的 `phase` 保持 `text2sql`，并同时返回数据结果和引用。

## 7. 页面使用

打开：<http://127.0.0.1:8090/ui/wiki.html>

页面提供：

- 索引状态、笔记数、片段数和索引时间；
- 关键词检索；
- 强制重建索引；
- 结果相关度、标题、章节和摘要；
- “在 Obsidian 打开”。

聊天页会在回答下方显示“知识依据 N 条”。该区域与 SQL 结果卡片相互独立，因此审核者能明确知道一段内容来自数据库还是 Wiki。

## 8. 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WIKI_PATH` | `D:\odoo19e\learn_odoo` | Wiki 根目录 |
| `WIKI_INDEX_PATH` | `D:\odoo19e\odoo-agent\.wiki-index\wiki.db` | 本地检索索引 |
| `WIKI_MAX_RESULTS` | `6` | Agent 默认检索条数，1～12 |

当前审核状态白名单由应用固定为 `reviewed/evergreen`，不通过网页放宽，以免误把草稿接入回答。

## 9. Langfuse 可观测性

知识问题新增：

```text
Retriever: retrieve-odoo-wiki-context
Generation: answer-odoo-knowledge-question
```

混合问题：

```text
Retriever: retrieve-odoo-wiki-context
Tool: check-odoo-readonly-database
Retriever: retrieve-sales-semantic-context
Generation: generate-sales-sql
Tool: validate-readonly-sales-sql
Tool: execute-readonly-sales-sql
Generation: explain-sales-result
```

Retriever Output 记录：命中数量、标题、章节和索引指纹，不记录完整 Wiki 正文。根 Observation 记录 `citation_count`，便于筛选“知识回答但没有引用”等质量问题。

知识正文会作为模型 Generation Prompt 的一部分，因此在当前 Langfuse 配置下可能出现在该 Generation 的 Input 中。若未来 Wiki 包含敏感内容，应在接入前增加字段级脱敏，或关闭相关 Prompt/Trace 内容采集。

建议建立 Saved View：

| 目的 | 条件 |
| --- | --- |
| Wiki 知识调用 | Observation name = `retrieve-odoo-wiki-context` |
| 知识回答 | Generation name = `answer-odoo-knowledge-question` |
| 混合问题 | Trace output.intent = `hybrid` |
| 疑似缺证据 | Trace output.intent in `knowledge/source/hybrid` 且 citation_count = 0 |

## 10. 质量验证

自动测试覆盖：

- `reviewed/evergreen` 进入索引，`draft` 不进入；
- 缺失 Wiki 时安全降级；
- 知识问题不访问数据库；
- 知识回答返回结构化引用；
- 混合问题同时访问 Wiki 和 Odoo；
- Wiki 原文不进入 SQL Prompt；
- 普通、指标、数据、知识、源码、混合意图分类。

建议下一批加入黄金问题：

```text
销售订单确认为什么会产生交货单？
qty_to_invoice 怎么计算？
Stock Move 和 Picking 有什么区别？
查看 sale.order._action_confirm 的源码入口。
本月哪些订单已交付但不能开票，为什么？
为什么 SO001 不能开票？
```

评测维度应至少包含：路由正确、证据命中、引用正确、无无依据结论、SQL 安全和端到端延迟。

## 11. 已知限制与下一阶段

第一阶段检索的是人工整理 Wiki，不是 Odoo 全量源码 AST。因此：

- Wiki 未记录的方法无法保证回答；
- 笔记可能落后于当前 Odoo 构建版本；
- 当前引用定位到 Markdown 章节，不定位到源码精确行号；
- FTS 对业务同义词的召回弱于高质量 Embedding；
- `source` 表示“以源码学习笔记为依据”，不是实时扫描整个 Odoo 源码。

推荐演进顺序：

1. 用知识黄金集积累检索失败样本；
2. 把 `.codex-index` 中模型、字段、文件、类和方法 Manifest 作为第二 Retriever；
3. 源码问题返回“Wiki 解释 + 源文件/行号证据”；
4. 只在关键词检索不足时增加 Embedding 和 reranker；
5. 增加 Wiki 新鲜度审计：笔记记录的 Odoo 版本、字段和源码路径与当前源码对照；
6. 将点踩样本进入 Langfuse Dataset，做知识检索自动回归。

不建议当前立刻引入独立向量数据库、知识图数据库或全量源码 RAG。先用可审计的本地检索和真实反馈确认瓶颈，能降低维护成本和错误面。
