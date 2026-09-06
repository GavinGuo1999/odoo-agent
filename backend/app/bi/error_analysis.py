from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.schemas.analysis import SqlErrorAnalysis, SqlErrorCategory, SqlErrorStage


MAX_SQL_REPAIR_ATTEMPTS = 2


def sql_fingerprint(sql: str) -> str | None:
    normalized = re.sub(r"\s+", " ", sql).strip().rstrip(";").casefold()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def query_fingerprint(sql: str, plan: dict[str, Any] | None) -> str | None:
    sql_hash = sql_fingerprint(sql)
    if not sql_hash:
        return None
    plan_contract = json.dumps(plan or {}, ensure_ascii=False, sort_keys=True, default=str)
    material = f"{sql_hash}\n{plan_contract}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _category(stage: SqlErrorStage, message: str) -> SqlErrorCategory:
    normalized = message.casefold()
    if "修复产生了重复查询" in message or "repairloop" in normalized:
        return "repair_loop"
    if "歧义" in message or "需要用户补充" in message:
        return "ambiguous_request"
    if any(
        marker in normalized
        for marker in ("querycanceled", "querycancelederror", "statementtimeout", "timeout")
    ) or "超时" in message:
        return "timeout"
    if "querycostexceeded" in normalized or "计划成本" in message:
        return "cost_limit"
    if any(
        marker in normalized
        for marker in ("insufficientprivilege", "permissiondenied", "permission")
    ) or "权限" in message:
        return "permission"
    if any(
        marker in normalized
        for marker in (
            "connectionrefused",
            "connectionfailure",
            "operationalerror",
            "serverclosed",
            "databaseconnectionerror",
        )
    ) or "连接" in message:
        return "connection"
    if any(
        marker in message
        for marker in (
            "写操作",
            "只允许 SELECT 查询",
            "不允许访问 schema",
            "不允许访问数据表",
            "字段未开放",
            "不允许调用函数",
        )
    ):
        return "unsafe_operation"
    if any(marker in normalized for marker in ("undefinedcolumn", "undefined_column")):
        return "unknown_column"
    if any(marker in normalized for marker in ("undefinedtable", "undefined_table")):
        return "unknown_table"
    if any(
        marker in normalized
        for marker in ("datatype", "typemismatch", "invalidtextrepresentation")
    ) or "类型" in message:
        return "type_mismatch"
    if "语法" in message or "syntax" in normalized:
        return "syntax"
    if any(
        marker in message
        for marker in (
            "company_id",
            "SELECT *",
            "QueryPlan",
            "输出列",
            "未声明",
            "排名查询",
            "SQL LIMIT",
            "SQL ORDER BY",
            "没有授权过滤字段",
            "JOIN 数量",
            "CTE 数量",
            "子查询",
            "笛卡尔积",
            "明细 SQL",
        )
    ):
        return "contract_violation"
    if stage == "planning" or "查询计划格式无效" in message or "模型没有返回可用 SQL" in message:
        return "invalid_plan"
    if stage == "semantic_compilation" or "语义 SQL 编译失败" in message:
        return "semantic_compilation"
    return "unknown"


def analyze_sql_errors(
    *,
    stage: SqlErrorStage,
    errors: list[str],
    sql: str,
) -> SqlErrorAnalysis:
    """Classify sanitized errors so graph routing is explicit and testable."""

    message = "；".join(item.strip() for item in errors if item.strip()) or "未知 SQL 错误。"
    category = _category(stage, message)
    repairable = category in {
        "invalid_plan",
        "semantic_compilation",
        "syntax",
        "contract_violation",
        "unknown_column",
        "unknown_table",
        "type_mismatch",
        "cost_limit",
    } or (category == "unknown" and stage == "execution")
    needs_user_input = category == "ambiguous_request"
    hints = {
        "invalid_plan": "重新生成完整 QueryPlan 和 SQL JSON。",
        "semantic_compilation": "仅使用当前 Wren MDL 暴露的模型、字段和关系。",
        "syntax": "保持业务语义不变并修复 PostgreSQL 语法。",
        "contract_violation": "按 QueryPlan、公司隔离和结果契约修复查询。",
        "unknown_column": "改用语义上下文中实际存在且已开放的字段。",
        "unknown_table": "改用语义上下文中实际存在且已开放的模型或表。",
        "type_mismatch": "修正字段类型、比较值或显式转换。",
        "cost_limit": "减少扫描、连接、子查询或结果范围，同时保持 QueryPlan 业务口径。",
    }
    clarification_question = (
        "这个查询存在会改变结果的歧义，请补充具体时间范围、对象或比较口径。"
        if needs_user_input
        else None
    )
    return SqlErrorAnalysis(
        stage=stage,
        category=category,
        repairable=repairable,
        needs_user_input=needs_user_input,
        summary=message[:500],
        repair_hint=hints.get(category),
        clarification_question=clarification_question,
        sql_fingerprint=sql_fingerprint(sql),
    )
