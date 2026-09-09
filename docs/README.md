# Odoo Sales Agent 文档中心

> 文档版本：1.0
>
> 适用应用版本：0.2.0
>
> 最后更新：2026-08-30
>
> 文档状态：正式

本目录是 Odoo Sales Agent 的正式文档入口。项目当前面向单用户、单公司、只读销售分析场景，通过自然语言查询 Odoo PostgreSQL 实时数据，并结合已审核的 `learn_odoo` Wiki 提供有引用的 Odoo 业务与源码知识解释。

## 1. 阅读导航

| 读者 | 建议阅读顺序 |
| --- | --- |
| 项目负责人 / 产品 | [产品与范围](01-product-overview.md) → [系统架构](02-system-architecture.md) → [演进路线](11-roadmap.md) |
| 首次使用者 | [安装与配置](03-installation-and-configuration.md) → [用户使用手册](04-user-guide.md) |
| 前端或接口开发者 | [API 参考](05-api-reference.md) → [Agent 工作流](07-langgraph-workflow.md) |
| 数据负责人 | [数据语义与安全](06-data-and-security.md) → [评测与质量](09-evaluation-and-quality.md) |
| 运维 / 开发人员 | [开发与运维](10-development-and-operations.md) → [Langfuse 可观测性](08-langfuse-observability.md) |
| 测试 / 发布负责人 | [TDD 测试与验收红绿灯](16-tdd-test-and-acceptance-matrix.md) → [评测与质量](09-evaluation-and-quality.md) |
| Agent / Skill 开发者 | [项目级 Skills 与 Waza 评测](17-project-skills-and-waza.md) → [开发与运维](10-development-and-operations.md) |

## 2. 文档目录

1. [产品与范围](01-product-overview.md)

   产品目标、适用场景、功能边界、质量目标和术语。

2. [系统架构](02-system-architecture.md)

   组件划分、部署拓扑、数据流、关键技术决策和故障降级。

3. [安装与配置](03-installation-and-configuration.md)

   Windows 本地部署、模型、Odoo、Langfuse、状态库和环境变量。

4. [用户使用手册](04-user-guide.md)

   四个页面的使用方式、提问技巧、中断恢复、反馈和常见场景。

5. [API 参考](05-api-reference.md)

   REST/SSE 接口、请求响应、事件格式、状态码和调用示例。

6. [数据语义与安全](06-data-and-security.md)

   指标口径、开放表字段、SQL Guard、只读连接和敏感信息策略。

7. [LangGraph Agent 工作流](07-langgraph-workflow.md)

   节点、状态、QueryPlan、模型路由、Checkpointer、Interrupt 和快速路径。

8. [Langfuse 可观测性](08-langfuse-observability.md)

   Trace 结构、Session、Token、Cost、Score、Dataset、排错与日常运营。

9. [评测与质量保障](09-evaluation-and-quality.md)

   黄金问题集、静态/真实回归、Langfuse Dataset 和发布门禁。

10. [开发与运维](10-development-and-operations.md)

    项目结构、开发命令、测试、启动停止、故障处理和发布检查清单。

11. [演进路线](11-roadmap.md)

    当前完成度、已知限制、近期优化和中长期触发条件。

12. [核心技术组件状态](12-technology-stack-status.md)

    SQLGlot、Pydantic/Instructor、Langfuse、dbt Core 和 LiteLLM 的用途、落地状态与后续边界。

13. [Wren AI 语义编译集成](13-wren-semantic-integration.md)

    Wren MDL 的职责边界、LangGraph 固定节点、QueryPlan 合同、切换与验证方法。

14. [Odoo 语义同步与一致性审计](14-odoo-semantic-sync-and-audit.md)

    PostgreSQL、ORM、源码和正式 MDL 的只读对照、差异报告、隔离草稿与人工发布流程。

15. [learn_odoo Wiki 与 ChatBI 知识集成](15-wiki-bi-knowledge-integration.md)

    Wiki 只读索引、知识/源码/混合意图、引用协议、LangGraph 与 Langfuse 集成边界。

16. [TDD 测试与验收红绿灯](16-tdd-test-and-acceptance-matrix.md)

    Red-Green-Refactor 流程、Codex 自动测试、协作测试、用户验收和发布门禁。

17. [项目级 Skills 与 Waza 评测](17-project-skills-and-waza.md)

    仓库级 Codex Skills、Waza 兼容配置、离线 Trigger 样例和各评测层职责边界。

18. [Native / Wren 三轮真实 A/B 基准](18-native-wren-ab-benchmark.md)

    同条件三轮基准的通过率、结果签名、延迟、Token 与成本对比。

19. [当前状态与未关闭差异](19-current-status-and-open-gaps.md)

    已验证事实、未关闭差异和证据标注；开始新一轮工作前先读这一篇。

20. [下一步任务队列](20-next-actions.md)
- [人工测试清单](21-manual-test-checklist.md)：带标准答案的人工验收清单，含环境启动与已知现象说明。

    待办、验收标准、执行环境约束和已定结论；接手新会话时与 19 一起读。

## 3. 当前能力快照

| 能力 | 状态 |
| --- | --- |
| LiteLLM + DeepSeek / 硅基流动 | 已支持，LiteLLM SDK 统一调用，可按节点选择供应商和模型 |
| Odoo 实时销售数据 | 已支持，PostgreSQL 强制只读 |
| Text2SQL | 已支持，Pydantic QueryPlan + SQLGlot 校验 + 最多两次修复 |
| Wren AI | 已支持实验切换，使用 MDL 上下文和 dry-plan，不直接执行 Odoo 查询 |
| Odoo 语义审计 | 已支持，四源只读对照并生成版本化报告与隔离 Wren 草稿 |
| learn_odoo Wiki | 已支持，只索引已审核笔记，提供知识引用和实时数据混合解释 |
| 图表 | 已支持，后端白名单 ChartSpec，前端本地 ECharts 渲染 |
| 会话恢复 | 已支持，独立 PostgreSQL Checkpointer |
| 人工确认 | 已支持，LangGraph Interrupt / Resume |
| 实时进度 | 已支持，SSE 阶段事件 |
| 可观测性 | 已支持，Langfuse Trace、Session、Token、Cost、Score、Dataset |
| 自动评测 | 已支持，20 条首批黄金问题和静态/真实回归运行器 |
| Wiki 向量检索 | 已支持，默认 hybrid：FAISS + bge-m3 + bge-reranker-v2-m3，失败自动降级词法 |
| RAG 评测 | 已支持两层 RAGAS；免模型一层已有基线，需 LLM 评审一层尚未运行 |
| 项目级 Skills | 已支持，三个仓库级 Skill 和 Waza Mock 触发评测样例 |
| 用户权限 | 当前不支持；应用按单用户本地工具设计 |
| 写回 Odoo | 当前不支持；第一阶段严格只读 |

## 4. 文档维护规则

- `docs/` 中的文档描述当前正式能力；根目录旧规划文件仅作为兼容入口。
- 功能变更必须同步更新对应文档，尤其是 API、环境变量、指标口径和安全边界。
- 示例中不得出现真实 API Key、数据库密码、Cookie、Token 或个人数据。
- 路线图必须明确区分“已完成”“下一步”和“暂缓”，不得把计划写成已实现。
- 版本号采用 `主版本.次版本`；破坏性 API 变化至少提升次版本并在发布说明中列出。

## 5. 快速入口

```powershell
# 1. 启动本机 Odoo 与 PostgreSQL
Set-Location D:\odoo19e
.\start-odoo19-dev.ps1

# 2. 启动 Agent（也可双击 start-odoo-agent.bat）
Set-Location D:\odoo19e\odoo-agent
.\start-backend.ps1 -NoReload
```

应用入口：<http://127.0.0.1:8090/ui/index.html>

API 文档：<http://127.0.0.1:8090/docs>
