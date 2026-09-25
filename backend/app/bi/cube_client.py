"""Cube 语义层的最小客户端：编译 SQL、取模型元数据、回填参数。

只用标准库 urllib，并由调用方放进 `asyncio.to_thread`——与 WrenSemanticProvider
用线程跑子进程的方式一致，不引入新依赖。

为什么单独成一个模块：`inline_cube_params` 会进入**安全路径**。守卫靠 SQL 里的
字面值判断公司隔离，而 Cube 返回的是参数化语句，所以这个函数的输出直接决定
守卫看到什么。它值得独立的测试文件和独立的阅读位置。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Sequence

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

__all__ = [
    "CubeClientError",
    "compile_cube_sql",
    "describe_cube_meta",
    "fetch_cube_meta",
    "inline_cube_params",
    "restore_cube_aliases",
]

# Cube 把输出列别名硬截断到这个长度。实测确认：
#   sales_amount        (12) -> sales_amount
#   delivered_quantity  (18) -> delivered_quanti
#   average_order_value (19) -> average_order_va
CUBE_ALIAS_MAX_LENGTH = 16


class CubeClientError(RuntimeError):
    """Cube 服务不可用、返回了非预期结构，或参数回填无法安全完成。"""


_PLACEHOLDER = re.compile(r"\$(\d+)")


def _sql_literal(value: Any, *, bare_integer: bool) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)

    text = str(value)
    if bare_integer:
        # 只还原**整数**。Cube 即便对整数列也用字符串传参（company_id 传成 '1'），
        # 而守卫校验公司隔离时要求 `literal.is_int`，所以这一步是必需的。
        #
        # 刻意不还原浮点和日期：'2026-01-01T00:00:00.000Z' 必须保持带引号，
        # 裸化会变成非法 SQL 或被解析成减法表达式。宁可少还原也不能错还原。
        stripped = text.strip()
        if re.fullmatch(r"[+-]?\d+", stripped):
            return stripped

    return "'" + text.replace("'", "''") + "'"


def inline_cube_params(sql: str, params: Sequence[Any]) -> str:
    """把 Cube 返回的 `$1/$2/...` 占位符回填成字面值。

    这不是绕过安全机制，而是守卫的工作方式决定的必需步骤：守卫会**改写** SQL
    （例如把 Cube 默认的 `LIMIT 50000` 压到 max_rows）并返回规范化结果交给执行层，
    所以参数化形式本来就到不了执行那一步。

    两条必须守住的性质：

    1. **单趟替换。** 用一次 `re.sub` 扫完，替换进去的文本不会被再次扫描。
       如果分多趟从 $n 往 $1 替换，某个参数值里若含有 `$1` 这样的文本，
       就会在后续轮次里被当占位符二次替换——那是一个真实的注入面。
    2. **数量必须对齐。** 占位符编号越界或有参数没被用到，一律抛错，
       绝不静默产出一条语义不同的 SQL。
    """
    seen: set[int] = set()
    problems: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index < 1 or index > len(params):
            problems.append(f"占位符 ${index} 越界（共 {len(params)} 个参数）")
            return match.group(0)
        seen.add(index)
        return _sql_literal(params[index - 1], bare_integer=True)

    result = _PLACEHOLDER.sub(_replace, sql)

    if problems:
        raise CubeClientError("；".join(problems))
    missing = sorted(set(range(1, len(params) + 1)) - seen)
    if missing:
        raise CubeClientError(
            f"参数未被使用：{missing}（Cube 返回了 {len(params)} 个参数，"
            f"但 SQL 里只出现了 {len(seen)} 个占位符）"
        )
    return result


def _outer_select(sql: str) -> exp.Select | None:
    try:
        statement = parse_one(sql, dialect="postgres")
    except ParseError:
        return None
    if isinstance(statement, exp.Select):
        return statement
    inner = statement.find(exp.Select)
    return inner if isinstance(inner, exp.Select) else None


def _realign_order_by(select: exp.Select) -> bool:
    """把外层 ORDER BY 对内层别名的引用，改写成对应输出列的别名。

    SELECT 里一旦出现表达式，Cube 会把查询包进子查询，外层列名是对的，
    但 ORDER BY 引用的是内层生成名：

        SELECT "v"."cast_datetrunc_u" AS "month", ... FROM ( ... ) AS "v"
        ORDER BY "v"."cast_datetrunc_u" ASC

    守卫按名字比对 ORDER BY 与 QueryPlan.sort，于是判 sort_mismatch。

    判据是**表达式完全相等**：ORDER BY 项与某个带别名的输出列，其底层表达式
    逐字相同时才改写成该别名。不做任何模糊匹配——排序键改错就是结果顺序改错。
    """
    order = select.args.get("order")
    if order is None:
        return False

    aliased: list[tuple[str, str]] = []
    for projection in select.expressions:
        if isinstance(projection, exp.Alias):
            aliased.append((projection.this.sql(dialect="postgres"), projection.alias))

    if not aliased:
        return False

    changed = False
    for ordered in order.find_all(exp.Ordered):
        target = ordered.this
        if target is None:
            continue
        rendered = target.sql(dialect="postgres")
        for expression_sql, alias in aliased:
            if rendered == expression_sql and rendered != alias:
                ordered.set("this", exp.column(exp.to_identifier(alias, quoted=True)))
                changed = True
                break
    return changed


def restore_cube_aliases(compiled_sql: str, requested_sql: str) -> str:
    """把被 Cube 截断的输出列别名还原成模型请求的名字。

    为什么需要：项目的守卫**按名字**校验——最终输出列要与 QueryPlan.select_columns
    逐一对齐。而 Cube 把超过 16 字符的别名截断，于是 `delivered_quantity` 变成
    `delivered_quanti`，8 个指标里有 5 个中招，凡用到它们的查询一律被守卫拒掉。

    做法是**按位置**还原，不是按名字猜：编译产物外层 SELECT 的第 i 列，对应请求
    SQL 的第 i 个别名。只有在下列条件全部满足时才改写，否则原样返回——
    宁可让守卫拒绝，也不能悄悄改出一条语义不同的 SQL：

    1. 两侧都能解析出外层 SELECT，且输出列数量相同；
    2. 请求的别名互不重复；
    3. 编译出的别名确实是请求别名的前缀截断（而不是另一个无关的名字）。

    ORDER BY 里对旧别名的引用一并改写，否则改完名字反而产生悬空引用。
    """
    compiled = _outer_select(compiled_sql)
    requested = _outer_select(requested_sql)
    if compiled is None or requested is None:
        return compiled_sql

    wanted = [item.alias_or_name for item in requested.expressions]
    if not wanted or len(wanted) != len(compiled.expressions):
        return compiled_sql
    if len(set(wanted)) != len(wanted):
        return compiled_sql

    renames: dict[str, str] = {}
    for item, target in zip(compiled.expressions, wanted):
        current = item.alias_or_name
        if not current or not target or current == target:
            continue
        # 只认「前缀截断」这一种情况。
        if len(current) >= len(target) or not target.startswith(current):
            return compiled_sql
        if len(current) != CUBE_ALIAS_MAX_LENGTH:
            return compiled_sql
        if isinstance(item, exp.Alias):
            item.set("alias", exp.to_identifier(target, quoted=True))
        else:
            return compiled_sql
        renames[current] = target

    if not renames:
        # 即便没有别名截断，ORDER BY 仍可能引用内层生成名，要单独处理。
        return compiled.sql(dialect="postgres") if _realign_order_by(compiled) else compiled_sql

    # ORDER BY / GROUP BY 里若按名字引用了旧别名，同步改掉。
    for clause in (compiled.args.get("order"), compiled.args.get("group")):
        if clause is None:
            continue
        for column in clause.find_all(exp.Column):
            new_name = renames.get(column.name)
            if new_name and column.table == "":
                column.set("this", exp.to_identifier(new_name, quoted=True))

    _realign_order_by(compiled)
    return compiled.sql(dialect="postgres")


def _cube_get(
    base_url: str,
    path: str,
    query: dict[str, str],
    *,
    timeout_seconds: float,
    api_token: str | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    headers = {"Authorization": api_token or "cube-local"}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise CubeClientError(f"Cube 返回 HTTP {exc.code}：{detail}") from exc
    except (OSError, ValueError) as exc:
        raise CubeClientError(f"Cube 请求失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CubeClientError("Cube 返回了非对象响应。")
    return payload


def fetch_cube_meta(
    base_url: str,
    *,
    timeout_seconds: float,
    api_token: str | None = None,
) -> dict[str, Any]:
    """取 /v1/meta：编译后的模型元数据，用来生成给模型看的 schema 说明。"""
    return _cube_get(base_url, "meta", {}, timeout_seconds=timeout_seconds, api_token=api_token)


def compile_cube_sql(
    base_url: str,
    cube_sql: str,
    *,
    timeout_seconds: float,
    api_token: str | None = None,
) -> str:
    """把一段写给 Cube 视图的 SQL 编译成物理 SQL，**不执行**。

    走 /v1/sql?format=sql，等价于 Wren 的 dry-plan。返回值已完成参数回填，
    可以直接交给守卫。
    """
    statement = cube_sql.strip().rstrip(";")
    if not statement:
        raise CubeClientError("待编译的 SQL 为空。")

    payload = _cube_get(
        base_url,
        "sql",
        {"format": "sql", "query": statement},
        timeout_seconds=timeout_seconds,
        api_token=api_token,
    )

    node = payload.get("sql")
    if not isinstance(node, dict):
        raise CubeClientError(f"Cube /v1/sql 响应缺少 sql 字段：{json.dumps(payload)[:300]}")

    compiled = node.get("sql")
    if isinstance(compiled, str):
        return restore_cube_aliases(compiled, statement)
    if isinstance(compiled, list) and compiled:
        physical = str(compiled[0])
        params = compiled[1] if len(compiled) > 1 and isinstance(compiled[1], list) else []
        return restore_cube_aliases(inline_cube_params(physical, params), statement)

    raise CubeClientError(f"Cube /v1/sql 未返回可用 SQL：{json.dumps(node)[:300]}")


def describe_cube_meta(meta: dict[str, Any]) -> str:
    """把 /v1/meta 整理成给模型看的 schema 说明。

    只列视图（SQL API 面向视图），cube 本身作为实现细节不暴露给模型，
    这样提示词里呈现的就是"一张宽表"，与 Wren 暴露 MDL 模型名的层级一致。
    """
    cubes = meta.get("cubes")
    if not isinstance(cubes, list):
        raise CubeClientError("Cube /v1/meta 响应缺少 cubes 列表。")

    lines: list[str] = []
    for entry in cubes:
        if not isinstance(entry, dict) or entry.get("type") != "view":
            continue
        name = entry.get("name", "?")
        title = entry.get("description") or entry.get("title") or ""
        lines.append(f"视图 {name}{'：' + title if title else ''}")

        for kind, label in (("measures", "指标"), ("dimensions", "维度")):
            members = entry.get(kind)
            if not isinstance(members, list) or not members:
                continue
            rendered = []
            for member in members:
                if not isinstance(member, dict):
                    continue
                short = str(member.get("name", "")).split(".")[-1]
                desc = member.get("shortTitle") or member.get("title") or member.get("description")
                # Cube 把连接过来的维度扁平化成 `res_partner_name`，但编译出的 SQL
                # 里是物理列 `name`。守卫按 QueryPlan.filters 声明的字段名比对 WHERE
                # 里的列名，所以必须把物理列名一并告诉模型，否则过滤类查询一律被判
                # "未声明的过滤字段"。physical 由 Cube 自己的 aliasMember 推出，不靠猜。
                physical = str(member.get("aliasMember") or "").split(".")[-1]
                label_text = f"{short}（{desc}）" if desc else short
                if physical and physical != short:
                    label_text += f"[物理列 {physical}]"
                rendered.append(label_text)
            if rendered:
                lines.append(f"  {label}：{', '.join(rendered)}")

    if not lines:
        raise CubeClientError("Cube 模型里没有可用视图，SQL API 无法查询。")
    return "\n".join(lines)
