# 项目级 Skills 与 Waza 评测

> 文档版本：1.0
>
> 最后更新：2026-08-30
>
> 状态：项目级 Skills 和离线评测样例已落地；Waza CLI 尚未安装

## 1. 目标与边界

项目使用 Codex 仓库级 Skills 固化高价值、可复用的开发决策，并使用 Waza 兼容资产检查 Skill 的发现信息、触发边界和 Token 预算。

Waza 不替代应用运行时，也不替代现有质量工具：

| 层 | 负责内容 |
| --- | --- |
| Codex Skill | 开发或测试任务应该遵循的项目工作流与安全边界 |
| Waza | Skill 触发、规范、行为和多模型对比 |
| Agent 黄金集 | LangGraph、QueryPlan、SQL、数据访问、Interrupt 和图表行为 |
| RAGAS | Wiki 检索上下文和知识回答质量 |
| Langfuse | Trace、Observation、Token、Cost、延迟和反馈 |

Waza 当前官方执行器是 `copilot-sdk` 或 `mock`，不是 Codex 运行时。因此 Waza 结果只能证明通用 Skill 资产和对应执行器下的行为，不能单独证明 Codex 或 `odoo-agent` HTTP 链路正确。

## 2. 目录

```text
.agents/skills/
├─ odoo-agent-development/
│  ├─ SKILL.md
│  └─ agents/openai.yaml
├─ odoo-readonly-testing/
│  ├─ SKILL.md
│  └─ agents/openai.yaml
└─ odoo-rag-evaluation/
   ├─ SKILL.md
   └─ agents/openai.yaml

evals/skills/
├─ odoo-agent-development/
├─ odoo-readonly-testing/
└─ odoo-rag-evaluation/

.waza.yaml
```

Codex 从仓库根目录的 `.agents/skills` 自动发现这些 Skill。若当前已打开的旧任务没有刷新列表，新建任务或重启 Codex 后即可看到。

## 3. Skill 分工

| Skill | 适用请求 | 不负责 |
| --- | --- | --- |
| `$odoo-agent-development` | LangGraph、QueryPlan、Text2SQL、SQL Repair、Wren、Chart Planner 开发或评审 | 无关的 Odoo addon 开发和业务验收 |
| `$odoo-readonly-testing` | 单测、黄金集、只读真实回归、浏览器 smoke、TDD 红绿灯证据 | 自动授权付费调用、数据库写入或代替用户签字 |
| `$odoo-rag-evaluation` | Wiki RAG、LlamaIndex、FAISS、Embedding、Reranker、RAGAS 与 Langfuse 评测 | 用 Wiki 猜测实时 Odoo 数据或绕过 SQL |

可以通过 `$skill-name` 显式调用；描述匹配时 Codex 也可以自动选择。

## 4. 当前离线门禁

项目不安装 Waza 也可以运行下面的本地契约测试：

```powershell
Set-Location D:\odoo19e\odoo-agent
& '.\.venv\Scripts\python.exe' -m unittest backend.tests.test_project_skills -v
```

该测试检查：

- `SKILL.md` 名称、描述和 UI 元数据；
- Skill 中维护文档链接是否存在；
- `.waza.yaml` 是否保持仓库级目录和 `mock` 默认值；
- 每个 Skill 是否至少有一个正触发和一个负触发样例；
- Trigger grader 是否引用真实 Skill 文件。

`.waza-cache/` 和 `.waza-results/` 是生成物，已加入 Git 忽略。

## 5. 安装 Waza 后可执行的检查

安装第三方二进制前需要用户明确批准版本和来源。安装后从仓库根目录运行：

```powershell
waza check .agents/skills/odoo-agent-development
waza check .agents/skills/odoo-readonly-testing
waza check .agents/skills/odoo-rag-evaluation

waza run evals/skills/odoo-agent-development/eval.yaml
waza run evals/skills/odoo-readonly-testing/eval.yaml
waza run evals/skills/odoo-rag-evaluation/eval.yaml
```

仓库默认配置使用 `mock`，不会调用外部模型。真实评测必须显式选择受支持执行器和模型，并重新取得当前任务对账号、网络和额度的授权；结果写入被忽略的 `.waza-results/`。

## 6. RAG 下一阶段

`$odoo-rag-evaluation` 固化的是升级和评测方法，不代表 FAISS、LlamaIndex、SiliconFlow Embedding/Reranker 或 RAGAS 已经接入生产代码。实施前先建立带相关文档/Chunk 标注的 Wiki 数据集，并保存当前 SQLite FTS/字符检索基线；之后按 LlamaIndex、FAISS、Embedding、Hybrid、Reranker 的顺序逐项 A/B，避免同时改动后无法归因。

至少记录：Recall/Hit@K、MRR 或 nDCG、Context Precision、Context Recall、Faithfulness、Answer Relevancy、引用覆盖、延迟、成本和失败回退率。

## 7. 参考

- [OpenAI：Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Microsoft Waza](https://github.com/microsoft/waza)
- [Waza 配置 Schema](https://github.com/microsoft/waza/blob/main/schemas/config.schema.json)
