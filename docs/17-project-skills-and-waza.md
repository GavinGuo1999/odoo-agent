# 项目级 Skills 与 Waza 评测

> 文档版本：1.2
>
> 最后更新：2026-09-17
>
> 状态：5 个项目级 Skills 已落地；**Waza CLI 已安装（v0.38.7）并已首次跑通**，
> `check` 与 `run`（mock）已进绿灯门禁；需真实执行器的 behavior eval 仍未运行

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
├─ odoo-agent-development/          SKILL.md + agents/openai.yaml
├─ odoo-readonly-testing/           同上
├─ odoo-rag-evaluation/             同上
├─ odoo-crm-semantics/              同上（2026-09-15 新增）
└─ odoo-multiagent-orchestration/   同上（2026-09-15 新增）

evals/skills/<同名目录>/             eval.yaml + tasks/{positive,negative}.yaml

.waza.yaml
```

Codex 从仓库根目录的 `.agents/skills` 自动发现这些 Skill。若当前已打开的旧任务没有刷新列表，新建任务或重启 Codex 后即可看到。

## 3. Skill 分工

| Skill | 适用请求 | 不负责 |
| --- | --- | --- |
| `$odoo-agent-development` | LangGraph、QueryPlan、Text2SQL、SQL Repair、Wren、Chart Planner 开发或评审 | 无关的 Odoo addon 开发和业务验收 |
| `$odoo-readonly-testing` | 单测、黄金集、只读真实回归、浏览器 smoke、TDD 红绿灯证据 | 自动授权付费调用、数据库写入或代替用户签字 |
| `$odoo-rag-evaluation` | Wiki RAG、LlamaIndex、FAISS、Embedding、Reranker、RAGAS 与 Langfuse 评测 | 用 Wiki 猜测实时 Odoo 数据或绕过 SQL |
| `$odoo-crm-semantics` | CRM 语义层、指标口径、CRM 守卫规则（`crm.lead`、阶段、团队、输单原因、赢率、漏斗、转化率） | 销售订单分析、Odoo CRM 最终用户培训、未经确认改已拍板的口径 |
| `$odoo-multiagent-orchestration` | 域包、域路由、每域守卫、Supervisor 拓扑、轮次预算、跨域合并 | 单域 Text2SQL 内部、CRM 口径本身 |

可以通过 `$skill-name` 显式调用；描述匹配时 Codex 也可以自动选择。

新增两个 Skill 的原因不是"多一点覆盖"，而是这两块的错误**不报错**：CRM 口径写错
只会给出好看的错数字（赢率 100%），多 Agent 编排写错会悄悄引入第二编排层或
让简单问题也付并行的成本。这类规则必须写下来，否则一定会被重新引入。
判据见 [CRM 设计 §2.2/§2.4](23-m2-crm-multiagent-waza-design.md) 与
[域包与域路由](24-domain-packs-and-routing.md)。

## 4. 当前离线门禁

项目不安装 Waza 也可以运行下面的本地契约测试：

```powershell
Set-Location D:\odoo19e\odoo-agent
& '.\.venv\Scripts\python.exe' -m unittest backend.tests.test_project_skills -v
```

该测试检查：

- `SKILL.md` 名称、描述和 UI 元数据；
- Skill 中维护文档链接是否存在；
- **Skill 正文引用的 `§N.M` 章节在它链接的文档里是否真的存在**；
- **`SKILL.md` 是否越过会被截断的硬线**（粗上界，精确预算归 Waza，见下）；
- **`eval.yaml` 的 `skill_directories` 与 grader 的 `skill_path` 各自按正确基准解析**（2026-09-17 新增，见 §5.1）；
- Skill 是否声称了仓库已经具备的能力不存在；
- `.waza.yaml` 是否保持仓库级目录和 `mock` 默认值；
- 每个 Skill 是否至少有一个正触发和一个负触发样例；
- Trigger grader 是否引用真实 Skill 文件。

### 4.1 和 Waza 的分工（2026-09-17 划清）

同一件事不留两处定义。实测对照后的分工：

| 检查 | 归谁 | 为什么 |
| --- | --- | --- |
| 精确 token 预算（含 1200 提示线） | **Waza** | 它有自己的分词器，且会读 `.waza.yaml`；本地 `tiktoken` 系统性偏高 19～266 |
| 是否越过 3000 截断硬线 | 自研（粗上界） | 本地计数偏高，所以"本地没超"是保守结论，不会漏放超长 Skill |
| 文档链接指向的**文件是否存在** | **自研** | Waza 按设计拒绝跟随 Skill 目录之外的链接（见 §5.3） |
| 引用的**章节号是否存在** | **自研** | Waza 不做这项；章节漂移比链接 404 更难发现——链接没坏但指向错误段落，开发者会照着错的段落做 |
| 触发边界评分 | **Waza** | 需要它的 trigger grader |
| 评测 spec 的路径基准 | 自研 | 钉住 §5.1 的问题 1，防回归 |

原先自研测试也判 1200 提示线，并为两份 Skill 维护了一份"已知例外名单"。
对照 Waza 的权威计数后发现那两份根本没超——例外名单是本地多算造出来的，已删除。

`.waza-cache/` 和 `.waza-results/` 是生成物，已加入 Git 忽略。

## 5. Waza CLI：安装与实测结果

### 5.0 安装（2026-09-17 完成）

安装第三方二进制前需要用户明确批准版本和来源。本次已批准并完成：

| 项 | 值 |
| --- | --- |
| 来源 / 版本 | `github.com/microsoft/waza`，`v0.38.7`（2026-08-19） |
| 资产 | `waza-windows-amd64.exe`，135.3 MB |
| 校验 | SHA-256 `451d05c5…5076c9`，与 release 的 `checksums.txt` 一致 |
| 位置 | `%LOCALAPPDATA%\Microsoft\Waza\waza.exe`（仓库外） |

**限流那条待办可以关了。** 官方 `install.ps1` 走未认证 GitHub API，在本机出口地址上
确实 403（已复现：`rate limit exceeded for 79.127.245.215`）。但本机的 `gh` 已认证，
**认证请求不受该限制**，所以用 `gh release download` 直接取资产即可，
既绕开限流，也不必把远程脚本管给 shell 执行：

```powershell
gh release download v0.38.7 --repo microsoft/waza `
  --pattern 'waza-windows-amd64.exe' --pattern 'checksums.txt' --dir <临时目录>
# 校验通过后再复制到 %LOCALAPPDATA%\Microsoft\Waza\waza.exe
```

### 5.1 首次运行找出的三个问题

| # | 问题 | 为什么自研测试发现不了 |
| --- | --- | --- |
| 1 | **5 份 eval.yaml 的 `skill_directories` 全是错的**，Waza 报 `required skills not found`——这 5 份评测从写下来那天起就跑不了 | 自研测试只能断言那个字符串长得对，无法知道 Waza 按哪个基准解析它 |
| 2 | `odoo-multiagent-orchestration` **正例触发失败**（0.40 < 0.55 阈值） | 触发评分需要 Waza 自己的评分器 |
| 3 | 自研的 token 计数**系统性偏高** 19～266，曾据此误判两份 Skill 超标并加了例外名单 | 本地 `tiktoken` 与 Waza 的分词器口径不同，只有对照才知道 |

**问题 1 的关键细节：同一个 spec 里两个字段的基准目录不一样。**

| 字段 | 基准 | 正确写法 |
| --- | --- | --- |
| `config.skill_directories` | **eval.yaml 所在目录** | `../../../.agents/skills/<name>` |
| `graders[].config.skill_path` | **仓库根**（进程工作目录） | `.agents/skills/<name>/SKILL.md` |

把后者也改成 `../../../` 会解析到 `D:\.agents\...`。
`test_project_skills.py::test_skill_eval_specs_resolve_skill_directories_against_the_spec_file`
同时钉住两个方向，任一边写错都会红（已验证会红）。

**问题 2 的归因实验。** 同一份 Skill、同一个语义的请求，只换动词形态：

| 提示词写法 | 得分 | 结果 |
| --- | ---: | --- |
| `routes` / `merges` / `turn budget` | 0.40 | ❌ |
| `routing` / `merging` / `turn budgets` | **1.00** | ✅ |

结论：**trigger grader 量的是词汇重叠，不是语义。** 所以失败不代表真实助手会漏掉
这份 Skill，但描述确实该覆盖真人会用的词形——真实开发者会说 "route"，
不会说 "topology"。修描述后该项 0.40 → 0.80，通过。
同时也说明：0.55 这个阈值是从 2026-08 的文件里沿用的，它量的是词汇重叠，不是准确率。

### 5.2 当前成绩单（2026-09-17）

| Skill | 合规等级 | token | 规范项 | 触发测试 |
| --- | --- | ---: | ---: | ---: |
| odoo-agent-development | Medium-High | 842 | 9/9 | 2/2 |
| odoo-crm-semantics | Medium-High | 1,199 | 9/9 | 2/2 |
| odoo-multiagent-orchestration | Medium-High | 1,146 | 9/9 | 2/2 |
| odoo-rag-evaluation | Medium-High | 1,197 | 9/9 | 2/2 |
| odoo-readonly-testing | Medium-High | 707 | 9/9 | 2/2 |

合规等级从最初全部 `Low` 提升到全部 `Medium-High`，办法是给每份 Skill 补一段
`USE FOR / DO NOT USE FOR` 触发短语清单，并删掉与之重复的旧「不负责」小节
（同一件事不留两处定义）。crm 与 rag 两份贴着 1200 提示线（1199 / 1197），
再加内容前要先看 `waza check`。

### 5.3 刻意不追的两项

**链接全部 0/N 有效。** Waza 的判据是"链接不得指向 Skill 目录之外"，
这是为**单独发布到公共市场**的 Skill 设计的。本项目 5 份是仓库内部规范，
故意指向 `docs/`——Skill 只放判据、明细留在文档。反过来，自研测试要求这些链接
**必须**指向真实存在的文件和真实存在的章节号，这是 Waza 按设计不做的。

**合规等级没冲 High。** 剩余差距要求加入 `**UTILITY SKILL**`、`INVOKES:` 这类
路由标记，那是公共市场的 Skill 编排图用的，硬塞进仓库内部的中文 Skill 属于生搬硬套。

**Waza 的评分按"发布到公共技能市场"校准。它的检查很有用，但它的评级不是本项目的目标。**

### 5.4 复现命令

从仓库根目录运行（均为离线、不花模型额度）：

```powershell
foreach ($s in @(
    'odoo-agent-development','odoo-readonly-testing','odoo-rag-evaluation',
    'odoo-crm-semantics','odoo-multiagent-orchestration')) {
    waza check ".agents/skills/$s"
    waza run "evals/skills/$s/eval.yaml"
}
```

### 5.5 触发评测的负例选法

新增两个 Skill 的负例刻意选**同一仓库里的邻域工作**，而不是"做个 PPT"这类显然无关的
请求：`odoo-crm-semantics` 的负例是销售订单分析，`odoo-multiagent-orchestration`
的负例是单域 QueryPlan 修复。相邻技能之间的边界才是真正会判错的地方；
用显然无关的负例只能证明分类器没坏，证明不了边界画对了。

### 5.6 behavior eval 的诚实限制

触发评测只验"路由到哪个 Skill"。真正想验的是"给了 Skill 之后模型的行为"——
写 `crm_lead` 查询时是否显式声明 `won_status`、是否会自己拼 `probability = 100`、
被要求加 Agent 框架时是否引用 roadmap §6.3 拒绝。

这类断言**需要真实执行器，`mock` 判不了内容**，所以它落在需要授权的黄灯层。
不要把写完的 behavior eval 当成门禁已经变强——没跑过的 eval 不是门禁。

仓库默认配置使用 `mock`，不会调用外部模型。真实评测必须显式选择受支持执行器和模型，并重新取得当前任务对账号、网络和额度的授权；结果写入被忽略的 `.waza-results/`。

## 6. RAG 下一阶段

`$odoo-rag-evaluation` 固化的是升级和评测方法。截至 2026-09-06，FAISS、LlamaIndex、SiliconFlow Embedding/Reranker 和 RAGAS 均已进入代码：`backend/app/services/wiki_vector.py` 提供向量检索与重排，`evals/run_wiki_rag_eval.py` 提供两层 RAGAS 评测，`evals/datasets/wiki_rag_golden.jsonl` 提供 20 条带参考路径的知识黄金集。

2026-09-06 已完成 lexical 与 hybrid 的同条件对照（`--mode both --limit 6`，20 题）：

| 检索模式 | recall | hit@6 | MRR | ID context precision |
| --- | ---: | ---: | ---: | ---: |
| lexical | 0.8000 | 0.8000 | 0.5367 | 0.1334 |
| hybrid + rerank | 0.9500 | 0.9500 | 0.7292 | 0.1584 |

hybrid 的 `fallback_reasons` 为空、20 个 case 全部经过 reranker，说明 FAISS 索引、SiliconFlow Embedding 与 Reranker 均已实际生效。相对 lexical：recall +0.15、MRR +0.1925、hit@6 +0.15。

hybrid 修复的正是 lexical 中“中文提问 / 英文标题概念笔记”的三条：`wiki-sale-order`、`wiki-stock-rule`、`wiki-manifest`。

仍未关闭的问题：

- `wiki-stock-picking` 在两种模式下都未召回 `Stock Picking.md` 与 `Stock Move.md`；
- `wiki-external-id` 的 MRR 在 hybrid 下从 1.00 降到 0.50，是本轮唯一的排序退化；
- 需要 LLM 评审的 RAGAS 一层（Faithfulness、Answer Relevancy）仍未运行，报告中 `ragas` 为 `null`。

ID-based context precision 对 top-k 敏感（多数 case 只有 1～2 篇参考笔记却取 top6），不能单独作为质量结论。后续继续按 Embedding、Hybrid 融合权重、Reranker 逐项 A/B，避免同时改动后无法归因。

至少记录：Recall/Hit@K、MRR 或 nDCG、Context Precision、Context Recall、Faithfulness、Answer Relevancy、引用覆盖、延迟、成本和失败回退率。

## 7. 参考

- [OpenAI：Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Microsoft Waza](https://github.com/microsoft/waza)
- [Waza 配置 Schema](https://github.com/microsoft/waza/blob/main/schemas/config.schema.json)
