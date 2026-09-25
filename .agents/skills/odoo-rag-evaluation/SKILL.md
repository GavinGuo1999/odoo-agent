---
name: odoo-rag-evaluation
description: Design, implement, or evaluate learn_odoo Wiki retrieval using LlamaIndex, FAISS, embeddings, rerankers, RAGAS, and Langfuse. Use for odoo-agent knowledge RAG work; do not use it to answer live Odoo data questions without SQL.
---

# Odoo RAG Evaluation

Improve Wiki retrieval with measured before/after evidence while preserving the separation between knowledge explanations and live database facts.

## Establish the baseline

Read the [Wiki integration contract](../../../docs/15-wiki-bi-knowledge-integration.md) and inspect `backend/app/services/wiki_knowledge.py` and `backend/app/services/wiki_vector.py` before proposing changes.

Implemented pipeline, verified 2026-09-06:

- `lexical`: SQLite FTS5 with the trigram tokenizer plus character-gram scoring over title, heading, metadata, and content, then one Obsidian link neighbour when room remains.
- `hybrid` (the `WikiConfig` default): SiliconFlow `bge-m3` embeddings persisted through the LlamaIndex FAISS adapter, fused with the lexical ranking, then `bge-reranker-v2-m3`. Any embedding or rerank failure records `fallback_reason` and degrades to `lexical`.
- RAGAS 0.4.3 in `evals/run_wiki_rag_eval.py`, in two tiers: ID-based context precision/recall that needs no model call, and LLM-judged Faithfulness, Answer Relevancy, Context Precision, and Context Recall behind `--ragas`.

Measured on `evals/datasets/wiki_rag_golden.jsonl` (20 cases, top-6), recorded in `evals/reports/wiki-rag-latest.json`:

| mode | recall | hit@6 | MRR | ID context precision |
| --- | --- | --- | --- | --- |
| lexical | 0.8000 | 0.8000 | 0.5367 | 0.1334 |
| hybrid + rerank, 2026-09-06 | 0.9500 | 0.9500 | 0.7292 | 0.1584 |

Hybrid ran with an empty `fallback_reasons` list and reranking active on every case. It recovered the three Chinese-question / English-title concept notes (`wiki-sale-order`, `wiki-stock-rule`, `wiki-manifest`) that lexical ranked out of the top 6.

Still open, and not to be described as solved: `wiki-stock-picking` misses under both modes; `wiki-external-id` MRR falls from 1.00 to 0.50 under hybrid; the LLM-judged RAGAS tier behind `--ragas` has never run. ID-based context precision is top-k sensitive — most cases carry one reference note against top-6 — so never quote it as a quality verdict.

Preserve these boundaries:

- Treat `learn_odoo` as a read-only source. Index only reviewed or evergreen notes allowed by the existing contract.
- Keep Wiki chunks out of SQL generation and SQL repair prompts.
- Keep structured citations and index fingerprints. If retrieval has insufficient evidence, do not answer from model memory.
- Do not send secrets or unrelated Odoo records to embedding, reranking, judging, or observability services.
- Require current authorization before using external model quota. Keep provider/model choices configurable rather than hard-coded.

## Evaluate before replacing the baseline

1. Build a versioned dataset containing the question, relevant note/chunk IDs, reference answer or claims, and slice tags such as field, model, process, source, Chinese, and exact identifier.
2. Record the current lexical baseline on the unchanged corpus and chunking policy.
3. Add one candidate at a time: LlamaIndex ingestion, FAISS vector retrieval, SiliconFlow embedding, hybrid fusion, then reranking.
4. Compare candidates on the same dataset, corpus fingerprint, top-k, and answer prompt. Record provider/model versions and cost.
5. Promote only when gains are real and exact Odoo identifiers, citations, latency, and offline fallback do not regress.

## Required measurements

- **Retrieval:** hit rate/recall at k, MRR or nDCG, RAGAS Context Precision, and Context Recall.
- **Generation:** RAGAS Faithfulness and Answer Relevancy, plus deterministic citation coverage and unsupported-claim checks.
- **Operations:** indexing time, search latency percentiles, embedding/reranker calls, tokens, cost, index size, and failure/fallback rate.
- Report per-slice results and actual before/after values. Do not present a single aggregate score as sufficient evidence.

RAGAS and LLM judges supplement deterministic retrieval checks; they do not replace source review, citation validation, SQL Guard, or user acceptance. Use Langfuse for trace/cost diagnostics, the existing HTTP golden runner for application behavior, and Waza only for this Skill's trigger and workflow conformance.

## USE FOR / DO NOT USE FOR

**USE FOR:**

- "Wiki 检索漏了笔记" / wiki retrieval misses the right note
- "换 embedding 或 reranker" / swap the embedding model or reranker
- "跑一次 RAGAS 评测" / run a RAGAS evaluation
- "词法 vs 混合检索对比" / compare lexical and hybrid retrieval
- "知识回答有没有编造" / check answer faithfulness

**DO NOT USE FOR:**

- 用 Wiki 猜测实时 Odoo 业务数据（必须走 SQL）
- 绕过 SQL 守卫回答取数问题
- 销售或 CRM 的指标口径
