---
name: odoo-agent-development
description: Implement or review odoo-agent LangGraph, QueryPlan, Text2SQL repair, semantic-layer, and Chart Planner changes. Use for work in the odoo-agent analytics workflow; do not use for unrelated Odoo addon development.
---

# Odoo Agent Development

Make scoped workflow changes without weakening the project's read-only and evidence boundaries.

## Load the relevant contract

- Read [LangGraph workflow](../../../docs/07-langgraph-workflow.md) before changing graph state, nodes, edges, retry, checkpoint, interrupt, or chart planning.
- Read [data and security](../../../docs/06-data-and-security.md) before changing SQL generation, QueryPlan, semantic fields, database access, or SQL Guard.
- Read [evaluation and quality](../../../docs/09-evaluation-and-quality.md) and the [TDD matrix](../../../docs/16-tdd-test-and-acceptance-matrix.md) before changing prompts, models, routing, or answer contracts.
- Read the [Wren boundary](../../../docs/13-wren-semantic-integration.md) only for semantic-provider work, and the [Wiki boundary](../../../docs/15-wiki-bi-knowledge-integration.md) only for knowledge or hybrid routing.

## Preserve these invariants

- Keep LangGraph as the only orchestration layer. Express dynamic behavior through explicit state, nodes, and conditional edges.
- Treat model output as untrusted. Parse structured contracts with Pydantic and validate SQL with SQLGlot before execution.
- Send every business query through the SQL Guard and the Odoo read-only transaction. Do not add a bypass or execute model SQL directly.
- Keep Wiki text out of SQL-generation and repair prompts. Wiki evidence may only support knowledge answers or post-query explanations.
- Interrupt before a query when the user must resolve ambiguity. Resume with the same session and keep pre-interrupt operations idempotent.
- Classify SQL failures before repair. Never repair unsafe operations; cap repair attempts and stop repeated SQL/plan fingerprints.
- Build chart plans only from returned result fields. Invalid plans must fall back safely without losing the data answer.
- Keep stable metric and dimension identifiers across QueryPlan, SQL result contracts, answers, charts, and golden data.

## Development loop

1. Inspect the dirty worktree and preserve unrelated user changes.
2. Add a failing test for the requested behavior or reproduced defect.
3. Make the smallest graph, schema, prompt, or guard change that turns it green.
4. Run the affected tests, then the full backend suite, frontend syntax check, and static golden set.
5. Run live database/model regression only when the user has authorized the read-only database, local services, and model quota.
6. Update the workflow, security, evaluation, or TDD documentation when a contract or gate changes.

Use the standard verification commands in [development and operations](../../../docs/10-development-and-operations.md). Report what changed, what passed, and which business decisions still require user UAT.

## USE FOR / DO NOT USE FOR

**USE FOR:**

- "改 LangGraph 的节点 / 边 / 状态" / change a graph node, edge, or state field
- "QueryPlan 协议或 SQL 修复循环出问题了" / fix the QueryPlan contract or the SQL repair loop
- "Text2SQL 生成的语句不对" / the generated SQL is wrong
- "图表规划 / ChartPlan 回退" / chart planning and its deterministic fallback
- "Wren 语义编译节点" / the Wren semantic-compilation node
- "Interrupt 后恢复不了" / resume after an interrupt

**DO NOT USE FOR:**

- CRM 指标口径（归 `$odoo-crm-semantics`）
- 域包、域路由、跨域编排（归 `$odoo-multiagent-orchestration`）
- Wiki 检索与 RAG 评测（归 `$odoo-rag-evaluation`）
- 与本工作流无关的 Odoo addon 开发
