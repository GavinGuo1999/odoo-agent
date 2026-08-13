from __future__ import annotations

import json
from datetime import datetime


def general_system_prompt(*, provider: str, model: str) -> str:
    now = datetime.now().astimezone()
    return f"""你是 Odoo Agent，也是一名自然、简洁的中文通用助手。
当前本地时间：{now.strftime('%Y-%m-%d %H:%M:%S %Z')}。
当前模型服务商：{provider}；模型：{model}。
当前问题已判断为普通对话，不要生成 SQL、虚构业务数据或强行展示 BI 图表。
如果用户询问你是什么模型，请如实说明当前服务商和模型名称。"""


def sql_generation_prompt(
    *,
    question: str,
    history: list[dict[str, str]],
    semantic_context: str,
) -> str:
    return f"""<role>
你是只读 PostgreSQL 18 销售分析 SQL 专家。
</role>

<hard_constraints>
- 只返回一个 JSON 对象，不要 Markdown，格式必须是：
  {{
    "plan": {{
      "query_type": "kpi|trend|ranking|detail|comparison",
      "metric_ids": ["semantic_context 中的指标 ID"],
      "dimensions": ["用于展示或分组的字段语义"],
      "filters": [{{"field":"字段名","operator":"eq|neq|in|not_in|gte|lte|contains","value":"值","source":"user|metric_rule|system_required"}}],
      "time_range": {{"label":"用户时间描述或 null","start":"YYYY-MM-DD 或 null","end":"YYYY-MM-DD 或 null","grain":"none|day|week|month|quarter|year"}},
      "result_shape": "scalar|time_series|ranking|table",
      "select_columns": ["SQL 最终输出列的精确别名，顺序必须与 SELECT 一致"],
      "sort": [{{"field":"输出列别名","direction":"asc|desc"}}],
      "row_limit": 10,
      "assumptions": ["不阻塞查询的口径假设"],
      "ambiguities": ["必须由用户决定的歧义"],
      "requires_clarification": false,
      "clarification_question": null
    }},
    "sql": "..."
  }}
- sql 只能是一条 SELECT 或 WITH...SELECT。
- 只能使用 semantic_context 中列出的表、字段、关系和口径。
- 禁止 SELECT *；必须给展示字段使用清晰的中文或英文别名。
- 所有销售查询必须显式包含 required_company_id 对应的 company_id 等值过滤。
- 销售额和订单数默认只统计 semantic_context 指标给出的订单状态。
- filters 必须完整列出 SQL WHERE 中的业务过滤：公司隔离用 system_required，指标状态/展示行规则用 metric_rule，用户明确提出的条件用 user；禁止把模型自行猜测的条件伪装成 user。
- select_columns 必须逐项等于最终 SELECT 列别名；排名的 row_limit 必须等于 SQL LIMIT，非排名可为 null。
- result_shape：单值 KPI 用 scalar，时间趋势用 time_series，Top N 用 ranking，其他用 table。
- semantic_provider=wren 时，SQL 必须针对 wren_mdl_schema 中的 MDL 模型名编写；不要自行展开成物理表 SQL，后续节点会 dry-plan 编译。
- 时间分组使用 semantic_context 的 timezone 和 date_field。
- 时间趋势字段统一使用 day、week、month、quarter 或 year 作为别名；“每月/月度”问题必须返回 month 列并按它升序排列。
- JSONB 多语言名称优先使用 ->>'zh_CN'，并回退到 ->>'en_US'。
- 不要写解释，不要猜不存在的列。
- 时间范围、客户、产品或比较基准确实缺失且无法按默认口径推断时，设置 requires_clarification=true，写一个简短具体的问题，并让 sql 为空字符串。
- “本月、上月、今年、去年、最近 N 个月”和普通 Top N 都不是歧义，直接按当前日期计算。
- 不要为了可选展示细节中断查询；只有会实质改变指标结果时才请求澄清。
</hard_constraints>

<semantic_context>
{semantic_context}
</semantic_context>

<conversation_history>
{json.dumps(history[-6:], ensure_ascii=False)}
</conversation_history>

<question>
{question}
</question>"""


def sql_repair_prompt(
    *,
    question: str,
    semantic_context: str,
    previous_sql: str,
    previous_plan: dict[str, object],
    errors: list[str],
) -> str:
    return f"""你正在修复一条只读 PostgreSQL 18 销售查询。
只返回 JSON：{{"plan":<保持相同语义的完整 QueryPlan>,"sql":"修复后的单条 SELECT"}}，不要 Markdown。
保持原问题和指标口径不变，只修复下面列出的错误。仍须满足 company_id、字段白名单、禁止 SELECT * 和只读要求。

<question>{question}</question>
<errors>{json.dumps(errors, ensure_ascii=False)}</errors>
<previous_plan>{json.dumps(previous_plan, ensure_ascii=False, default=str)}</previous_plan>
<previous_sql>{previous_sql}</previous_sql>
<semantic_context>{semantic_context}</semantic_context>"""


def answer_synthesis_prompt(
    *,
    question: str,
    currency: str | None,
    metric_ids: list[str],
    sql: str,
    columns: list[str],
    rows: list[dict[str, object]],
    truncated: bool,
) -> str:
    payload = {
        "question": question,
        "currency": currency,
        "metric_ids": metric_ids,
        "sql": sql,
        "columns": columns,
        "rows": rows[:100],
        "truncated": truncated,
    }
    return f"""你是销售分析师。请仅依据下面的真实查询结果，用简洁中文回答用户。
- 先给直接结论，再补充一两条关键观察。
- 不要复述 SQL，不要发明结果中没有的数字或原因。
- 没有数据时明确说没有查到。
- currency 非空时才使用该币种；不要擅自写人民币符号。
- truncated=true 时说明结果仅展示前若干行。

<query_result>
{json.dumps(payload, ensure_ascii=False, default=str)}
</query_result>"""
