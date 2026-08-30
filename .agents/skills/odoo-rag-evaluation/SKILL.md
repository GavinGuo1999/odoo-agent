---
name: odoo-rag-evaluation
description: Design, implement, or evaluate learn_odoo Wiki retrieval using LlamaIndex, FAISS, embeddings, rerankers, RAGAS, and Langfuse. Use for odoo-agent knowledge RAG work; do not use it to answer live Odoo data questions without SQL.
---

# Odoo RAG Evaluation

Improve Wiki retrieval with measured before/after evidence while preserving the separation between knowledge explanations and live database facts.

## Establish the baseline

Read the [Wiki integration contract](../../../docs/15-wiki-bi-knowledge-integration.md) and inspect `backend/app/services/wiki_knowledge.py` before proposing changes. The current baseline uses SQLite FTS5 plus character/token matching; FAISS, LlamaIndex, external embeddings, reranking, and RAGAS are not yet production capabilities.

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
