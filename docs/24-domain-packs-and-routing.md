# 业务域包与域路由

> 文档版本：1.0
>
> 日期：2026-09-15
>
> 状态：**已实现并通过门禁**（P2 域包重构 + P3 CRM 域包）
>
> 设计来源：[M2 设计稿](23-m2-crm-multiagent-waza-design.md) §2.5 / §3.3 / §3.4

本文描述**当前代码里真实存在**的域包机制。设计意图与未实现部分见 23，
不要把 23 里的 P4 内容当成已有能力。

## 1. 为什么需要域包

M1 的 Text2SQL 链路（检索语义 → 生成 QueryPlan+SQL → 编译 → 守卫 → 执行 → 画像 →
补充查询 → 展示）与"销售"这件事其实无关。它只依赖四样东西：语义上下文、SQL 守卫、
Prompt、验证过的示例。

加第二个业务域时，硬编码的销售假设会暴露出来。实测一共四处：

| 位置 | 原来写死的 | 加 CRM 后的问题 |
| --- | --- | --- |
| `semantic.py` 的 `retrieve()` | 选表关键词分支、默认指标 | 关键词分支会随域数线性膨胀 |
| `sql_guard.py` 的 `metric_fields` | `{state, display_type}` | CRM 的口径字段是 `type` / `won_status`，全被判成未声明 |
| `sql_guard.py` 的时间列 | `date_order` | CRM 有三个时间列，全被判成未声明 |
| `sql_guard.py` 的公司隔离 | `{sale_order, sale_order_line}` | **CRM 查询完全不受 company_id 约束**（见 §4.3） |

## 2. DomainPack

`backend/app/bi/domain.py`：

```python
@dataclass(frozen=True, slots=True)
class DomainPack:
    domain: str                       # "sales" | "crm"
    semantics: SemanticContextProvider
    guard: ReadOnlySqlGuard           # 本域独立实例
    routing_keywords: tuple[str, ...]
```

`DomainRegistry` 持有全部域包，`SalesAgent` 通过 `self._pack(state)` 按本轮判定的
`state["domain"]` 取包。Text2SQL 的每个节点都从包里取语义和守卫，不再引用
任何单域全局对象。

三条硬约束：

1. **每个域一份独立守卫实例。** 白名单是域包的属性，不是全局常量。
   CRM 域的守卫里根本没有 `sale_order` 这张表——所以不是"提示词让它别查"，
   而是查了就被拒。
2. **共享表在每个域各自声明。** `res_partner` / `res_users` / `res_company` /
   `res_currency` 四张表在两个域的定义文件里都写一遍，允许重复，不做继承。
   继承会让"CRM 到底能看客户的哪些字段"这个问题的答案分散在两个文件里。
   `test_crm_semantics` 断言这四张表在两域的字段集合**完全一致**，防止漂移。
3. **跨域是第三个包，不是两域并集。** 当前尚未实现（P4）。

## 3. 域定义文件

`backend/app/bi/{sales,crm}_semantics.json`。加一个业务域 = 加一个 JSON +
在 `semantic.py` 的 `_DOMAIN_FILES` 登记一行 + 一批黄金题，**不改任何图节点或边**。

判据全部在数据里，不在代码里：

| 键 | 作用 |
| --- | --- |
| `routing_keywords` | 域路由的判据（§4） |
| `base_tables` / `table_selection` | 选表规则，原先硬编码在 `retrieve()` 里 |
| `default_metrics` | 没命中任何指标关键词时的回退 |
| `metric_explanation` | 口径说明怎么措辞（销售说"订单状态"，CRM 说"记录范围"） |
| `business_notes` | 域级硬规则，会作为 `domain_rules` 进 Prompt |
| `sql_profile` | 守卫判据（§5） |

## 4. 域路由

`classify_domain(question, registry)`：数每个域的 `routing_keywords` 命中数，取最高。
不调模型，理由与 `classify_intent` 相同（见 [M1 §2.3](22-milestone-2026-09.md)）。

### 4.1 意图与域是两个独立判定

意图决定走不走取数，域决定用哪套语义层和哪份守卫白名单。两个都是确定性的，
所以都能进**静态**门禁——不花模型额度、不碰数据库，每次提交都能跑。

### 4.2 零命中与并列一律回落默认域

这一点是刻意的：M1 的 76 题黄金集里有大量不含任何域关键词的问题
（"哪个客户买得最多"），严格路由会把它们全部打断。
[23 §3.4](23-m2-crm-multiagent-waza-design.md) 里"域完全无命中就问用户"是
Supervisor 的行为，带自己的门禁，属于 P4；在那之前回落保证 M1 行为一字不变。

各域命中数随 trace 返回——线上排查"这题为什么走错了域"时，光知道结论没用。

### 4.3 意图词表也从域定义合并

`_DATA_WORDS` 和 `_METRIC_WORDS` 原先是手写的销售词表。实测缺陷：
"赢率是多少"里没有一个销售业务词，会被判成 `general`，永远到不了取数。
现在这两个词表**追加**各域的 `routing_keywords` 和指标 `name`（销售域原有的词
一个不动），所以加一个域会自动教会意图路由它的业务词。

### 4.4 两处实测的路由缺陷与修法

| 问题 | 现象 | 修法 |
| --- | --- | --- |
| "输单原因分布是什么样的？" | "是什么"命中知识线索词 → 判成 `knowledge`，去查 Wiki | `_DATA_REQUEST_WORDS` 加 `分布`——它只可能是要按维度聚合的数据 |
| 同一句 | "原因"命中 hybrid 线索词 → 判成 `hybrid`，多花一次检索和模型调用 | 判定线索前先剔掉**维度名**短语（`输单原因` 等）。"为什么输单原因这么集中"仍是解释类 |

第二条值得记住：**CRM 有一个维度就叫"输单原因"**，维度名里正好含着解释线索词。
这类冲突在扩域时还会出现，修法是把维度名从线索文本里剔掉，不是删线索词。

## 5. 守卫域档案

`SqlDomainProfile`（`database/sql_guard.py`）。不传就是销售域原判据，
所以 M1 的全部构造点行为不变。

| 字段 | 销售 | CRM | 作用 |
| --- | --- | --- | --- |
| `metric_rule_fields` | `state`, `display_type` | `type`, `won_status` | 允许标 `source="metric_rule"` 的字段 |
| `time_fields` | `date_order` | `create_date`, `date_closed`, `date_deadline` | 声明时间范围后 WHERE 允许的时间列 |
| `company_scoped_tables` | `sale_order`, `sale_order_line` | `crm_lead` | 必须带 `company_id = N` 的表 |
| `scope_required_metrics` | 空 | 9 个指标 | **R1**：指标声明了口径字段，计划里必须真的声明 |
| `forbidden_filter_fields` | 空 | `active` | **R2**：声明了也不放行 |
| `translated_name_dimensions` | `product` | `stage`, `lost_reason`, `team`, `tag` | JSONB 译名列必须 COALESCE 回退 |
| `metric_expression_fields` | 8 个指标 | 10 个指标 | 放行聚合 `FILTER (WHERE ...)` 里的列 |

**`scope_required_metrics` 与 `metric_expression_fields` 都从指标自己的
`scope` / `expression` 推导**，不手写。手写一份等于给口径开第二处定义，
改了 scope 忘了改这里就会静默失效。

推导的副产品正好避开一类误伤：`crm_conversion_rate` 的口径就是"不限 type"，
推导出来它不需要任何范围声明，所以 R1 不会卡它。

### 5.1 一处补上的隔离漏洞

公司隔离原先写死 `{sale_order, sale_order_line}`，因此 CRM 查询**完全不受
`company_id` 约束**。单公司环境下看不出来，但这是隔离边界上的真实漏洞。
现在它是域档案的一项，`test_sql_guard_crm` 里有对应用例。

### 5.2 一处按 M1 原则收紧的误伤

`COUNT(*) FILTER (WHERE date_conversion IS NOT NULL)` 是转化率的**计算式本身**，
但被既有规则判成"未声明的过滤字段"。

修法沿用 [M1 §2.1](22-milestone-2026-09.md) 的原则——**收紧判据而不是放宽规则**：
聚合 `FILTER` 里的列只在"出现在本轮声明指标的计算式里"时放行，顶层 WHERE 的判据
一步不让。所以 `COUNT(*) FILTER (WHERE partner_id = 5)` 照样被拒——那确实是把指标
限定到了某个客户。

## 6. 门禁

| 检查 | 命令 | 当前结果 |
| --- | --- | --- |
| 后端全量 | `python -m unittest discover -s tests`（在 `backend/`） | 317 全绿 |
| 销售黄金集 | `run_sales_eval.py` | 76/76 |
| CRM 黄金集 | `run_sales_eval.py --dataset evals/datasets/crm_golden.jsonl` | 27/27 |
| 语义审计范围 | `_scope()` | 17 张表（原 9） |

静态黄金集现在同时校验**意图和域**两个确定性判定。CRM 的 27 题里有 5 题是
反向用例：销售域的问题不能被 CRM 抢走，不含域关键词的问题必须回落销售域。

CRM 的 data 类用例一律标 `pending_reference`——还没有结果签名。
编一个签名比没有签名更糟，那会让回归对着假基线跑绿。

## 7. 相关文档

- [M2 设计稿](23-m2-crm-multiagent-waza-design.md) — 设计意图、五个 Odoo 19 实测坑、P4 待做部分
- [数据语义与安全](06-data-and-security.md) — 守卫契约与敏感字段策略
- [LangGraph 工作流](07-langgraph-workflow.md) — 节点与状态
- [项目级 Skills 与 Waza](17-project-skills-and-waza.md) — `$odoo-crm-semantics` 与 `$odoo-multiagent-orchestration`
