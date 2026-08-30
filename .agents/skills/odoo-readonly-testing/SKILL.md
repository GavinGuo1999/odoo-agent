---
name: odoo-readonly-testing
description: Run and report odoo-agent unit, golden-set, local HTTP, browser, and UAT checks under read-only database rules. Use for odoo-agent verification and release evidence; it never authorizes database writes or paid live tests by itself.
---

# Odoo Read-only Testing

Produce reproducible red/yellow/green evidence while protecting local Odoo data, credentials, and model quota.

## Choose the test tier

Read the [TDD and acceptance matrix](../../../docs/16-tdd-test-and-acceptance-matrix.md) first. Use [evaluation and quality](../../../docs/09-evaluation-and-quality.md) for golden-set contracts.

- **Green / automatic:** backend unit tests, frontend syntax, static golden set, schema parsing, and `git diff --check`.
- **Yellow / authorized:** starting local Odoo or Agent services, querying `odoo19_dev` through `codex_readonly`, calling configured paid models, browser smoke tests, and Langfuse trace inspection.
- **Red / user acceptance:** business metric reconciliation, chart usefulness, multi-session UX, model cost/quality acceptance, permissions, and every Odoo write action.

Prior authorization applies only to the stated environment and operation. A read-only test approval never permits Odoo writes, destructive database commands, production access, or exposing credentials.

## Execute safely

1. Run the narrow failing test first, then the full automatic gates documented in the TDD matrix.
2. Before live testing, confirm the target is local, the account is read-only, and model/service use is authorized in the current task.
3. Use unique session IDs for independent cases and the same session ID only for deliberate checkpoint or interrupt-resume tests.
4. Assert `data_accessed`, intent/phase, QueryPlan semantics, read-only SQL shape, result structure, interrupts, and error categories. Do not treat a plausible answer as sufficient evidence.
5. Never print API keys, database passwords, raw connection strings, sensitive rows, or full unredacted prompts/traces.
6. Stop only the exact services started for the test, then verify their expected ports are no longer listening.

## Report the result

- Record the command or case ID, environment, provider/model, pass/fail result, and a redacted failure summary.
- Keep automated correctness separate from user UAT. A green test cannot approve business meaning or visual usefulness.
- When a live batch is interrupted by the test host, distinguish infrastructure interruption from a failed case and resume only the unexecuted cases.
- Update the TDD matrix when the gate, test count, or real before/after evidence changes.
