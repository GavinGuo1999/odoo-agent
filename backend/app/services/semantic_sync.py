from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.bi.semantic import KNOWN_DOMAINS, SalesSemanticLayer, SemanticLayer
from app.config import DatabaseConfig, SemanticSyncConfig
from app.database import OdooDatabase


ODOO_MODEL_BY_TABLE = {
    "sale_order": "sale.order",
    "sale_order_line": "sale.order.line",
    "res_partner": "res.partner",
    "product_product": "product.product",
    "product_template": "product.template",
    "res_users": "res.users",
    "res_company": "res.company",
    "res_currency": "res.currency",
    "uom_uom": "uom.uom",
    # CRM 域（docs/23 §2.3）
    "crm_lead": "crm.lead",
    "crm_stage": "crm.stage",
    "crm_team": "crm.team",
    "crm_lost_reason": "crm.lost.reason",
    "crm_tag": "crm.tag",
    "utm_source": "utm.source",
    "utm_medium": "utm.medium",
    "utm_campaign": "utm.campaign",
}

# 纯多对多关系表：它们**没有对应的 Odoo 模型**，是 ORM 由 Many2many 字段自动建的。
#
# 四源审计的其中两源（ORM 与源码扫描）都以模型类为单位，对这类表无从下手；
# 硬给它们编一个模型名会让审计报告出现一条永远对不上的差异，
# 把真正的问题淹掉。所以显式排除，并在报告里说明原因，而不是假装审过了。
RELATION_TABLES_WITHOUT_MODEL = frozenset({
    "crm_tag_rel",
    "crm_stage_crm_team_rel",
})

_POSTGRES_TO_WREN = {
    "bigint": "BIGINT",
    "integer": "INTEGER",
    "smallint": "SMALLINT",
    "numeric": "DECIMAL",
    "decimal": "DECIMAL",
    "double precision": "DOUBLE",
    "real": "FLOAT",
    "boolean": "BOOLEAN",
    "character varying": "VARCHAR",
    "character": "VARCHAR",
    "text": "TEXT",
    "json": "JSON",
    "jsonb": "JSON",
    "date": "DATE",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMPTZ",
    "time without time zone": "TIME",
    "uuid": "UUID",
}

_COMPATIBLE_WREN_TYPE_GROUPS = (
    {"SMALLINT", "INTEGER", "BIGINT"},
    {"VARCHAR", "TEXT"},
)


class SemanticSyncError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceScanResult:
    definitions: dict[str, dict[str, list[dict[str, Any]]]]
    python_files_seen: int
    candidate_files_parsed: int
    source_files_matched: int


def _scope() -> dict[str, dict[str, Any]]:
    """审计范围 = 所有已注册业务域开放的表（去掉无模型的关系表）。

    共享表（res_partner 等）在两个域各自声明，这里按表名合并；
    `test_crm_semantics` 已经断言过共享表在两域的字段集合一致，
    所以合并不会掩盖差异。
    """
    tables: dict[str, list[str]] = {}
    descriptions: dict[str, str] = {}
    for domain in KNOWN_DOMAINS:
        layer = SemanticLayer.load(domain)
        for table, columns in layer.table_columns.items():
            if table in RELATION_TABLES_WITHOUT_MODEL:
                continue
            merged = dict.fromkeys(tables.get(table, []))
            merged.update(dict.fromkeys(columns))
            tables[table] = list(merged)
            descriptions.setdefault(table, layer.table_description(table))

    missing_models = set(tables) - set(ODOO_MODEL_BY_TABLE)
    if missing_models:
        raise SemanticSyncError(
            "Odoo model mapping is missing for: " + ", ".join(sorted(missing_models))
        )
    return {
        table: {
            "odoo_model": ODOO_MODEL_BY_TABLE[table],
            "columns": columns,
            "description": descriptions[table],
        }
        for table, columns in tables.items()
    }


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            cursor: ast.AST | None = node
            while isinstance(cursor, ast.Attribute):
                parts.append(cursor.attr)
                cursor = cursor.value
            if isinstance(cursor, ast.Name):
                parts.append(cursor.id)
            return ".".join(reversed(parts))
        return None
    if isinstance(value, (str, int, float, bool, list, tuple, dict)) or value is None:
        return value
    return str(value)


def _assignment(class_node: ast.ClassDef, name: str) -> Any:
    for node in class_node.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return _literal(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return _literal(node.value)
    return None


def _as_model_names(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple)):
        return {str(item) for item in value if isinstance(item, str)}
    return set()


def _field_call(node: ast.AST) -> tuple[str, ast.Call] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "fields":
        return None
    return node.func.attr, node


def _field_definition(
    *,
    field_type: str,
    call: ast.Call,
    source_file: str,
    line: int,
) -> dict[str, Any]:
    keywords = {item.arg: _literal(item.value) for item in call.keywords if item.arg}
    relation = keywords.get("comodel_name")
    if relation is None and field_type in {"Many2one", "One2many", "Many2many"}:
        relation = _literal(call.args[0]) if call.args else None
    return {
        "field_type": field_type,
        "relation": relation,
        "label": keywords.get("string"),
        "help": keywords.get("help"),
        "related": keywords.get("related"),
        "compute": keywords.get("compute"),
        "store": keywords.get("store"),
        "company_dependent": keywords.get("company_dependent"),
        "required": keywords.get("required"),
        "readonly": keywords.get("readonly"),
        "selection": keywords.get("selection"),
        "source_file": source_file,
        "line": line,
    }


def scan_odoo_source(
    source_path: Path,
    scope: dict[str, dict[str, Any]],
    module_hints: set[str] | None = None,
) -> SourceScanResult:
    if not source_path.is_dir():
        raise SemanticSyncError(f"Odoo source directory does not exist: {source_path}")

    target_models = {str(item["odoo_model"]) for item in scope.values()}
    fields_by_model = {
        str(item["odoo_model"]): set(map(str, item["columns"]))
        for item in scope.values()
    }
    result = {
        model: {field: [] for field in sorted(fields)}
        for model, fields in fields_by_model.items()
    }
    target_pattern = re.compile(
        r"_(?:name|inherit)\s*=.{0,400}(?:"
        + "|".join(re.escape(model) for model in sorted(target_models, key=len, reverse=True))
        + r")",
        re.DOTALL,
    )
    python_files_seen = 0
    candidate_files_parsed = 0
    matched_files: set[str] = set()

    addon_roots = [
        path
        for path in (source_path / "odoo" / "addons", source_path / "addons")
        if path.is_dir()
    ]
    candidate_paths: list[Path]
    if module_hints and addon_roots:
        # ``ir.model.fields.modules`` tells us which installed addons actually
        # contribute the allow-listed fields. This avoids opening the complete
        # Enterprise distribution (more than ten thousand Python files).
        module_paths = {
            addon_root / module
            for addon_root in addon_roots
            for module in module_hints
            if (addon_root / module).is_dir()
        }
        candidate_paths = sorted(
            {
                path
                for module_path in module_paths
                for path in module_path.rglob("*.py")
            }
        )
    else:
        candidate_paths = sorted(source_path.rglob("*.py"))

    for path in candidate_paths:
        if any(part in {".venv", "__pycache__"} for part in path.parts):
            continue
        python_files_seen += 1
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if not target_pattern.search(source):
            continue
        try:
            module = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        candidate_files_parsed += 1
        relative = path.relative_to(source_path).as_posix()

        for class_node in (node for node in ast.walk(module) if isinstance(node, ast.ClassDef)):
            declared_name = _assignment(class_node, "_name")
            inherited_names = _as_model_names(_assignment(class_node, "_inherit"))
            class_models: set[str] = set()
            if isinstance(declared_name, str) and declared_name in target_models:
                class_models.add(declared_name)
            elif declared_name is None:
                class_models.update(inherited_names & target_models)
            if not class_models:
                continue

            for node in class_node.body:
                targets: list[str] = []
                value: ast.AST | None = None
                if isinstance(node, ast.Assign):
                    targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                    value = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    targets = [node.target.id]
                    value = node.value
                if len(targets) != 1 or value is None:
                    continue
                parsed_call = _field_call(value)
                if parsed_call is None:
                    continue
                field_name = targets[0]
                field_type, call = parsed_call
                for model in class_models:
                    if field_name not in fields_by_model[model]:
                        continue
                    result[model][field_name].append(
                        _field_definition(
                            field_type=field_type,
                            call=call,
                            source_file=relative,
                            line=node.lineno,
                        )
                    )
                    matched_files.add(relative)

    return SourceScanResult(
        definitions=result,
        python_files_seen=python_files_seen,
        candidate_files_parsed=candidate_files_parsed,
        source_files_matched=len(matched_files),
    )


def _source_module_hints(database_metadata: dict[str, Any]) -> set[str]:
    modules = {"base", "product", "sale", "uom"}
    for fields in database_metadata.get("orm", {}).values():
        for field in fields.values():
            raw_modules = field.get("modules")
            if not isinstance(raw_modules, str):
                continue
            modules.update(
                item.strip()
                for item in raw_modules.split(",")
                if item.strip() and re.fullmatch(r"[a-zA-Z0-9_]+", item.strip())
            )
    return modules


def _localized_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for locale in ("zh_CN", "en_US"):
            candidate = value.get(locale)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _wren_type(physical: dict[str, Any]) -> str:
    data_type = str(physical.get("data_type") or "").casefold()
    udt_name = str(physical.get("udt_name") or "").casefold()
    return _POSTGRES_TO_WREN.get(data_type) or _POSTGRES_TO_WREN.get(udt_name) or "VARCHAR"


def _wren_types_compatible(formal_type: str, physical_type: str) -> bool:
    if formal_type == physical_type:
        return True
    return any(
        formal_type in group and physical_type in group
        for group in _COMPATIBLE_WREN_TYPE_GROUPS
    )


def _load_formal_project(project_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    config_path = project_path / "wren_project.yml"
    if not config_path.exists():
        raise SemanticSyncError(f"Formal Wren project is missing: {config_path}")
    project_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    models: dict[str, dict[str, Any]] = {}
    for metadata_path in sorted((project_path / "models").glob("*/metadata.yml")):
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        name = str(metadata.get("name") or metadata_path.parent.name)
        models[name] = metadata
    return project_config, models


def _formal_columns(model: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not model:
        return {}
    return {
        str(column["name"]): dict(column)
        for column in model.get("columns", [])
        if isinstance(column, dict) and column.get("name")
    }


def _description(
    formal_column: dict[str, Any] | None,
    orm_field: dict[str, Any] | None,
    source_definitions: list[dict[str, Any]],
) -> str | None:
    formal_properties = (formal_column or {}).get("properties") or {}
    candidates = [
        formal_properties.get("description"),
        _localized_text((orm_field or {}).get("help")),
        _localized_text((orm_field or {}).get("field_description")),
    ]
    for definition in reversed(source_definitions):
        candidates.extend([definition.get("help"), definition.get("label")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:1000]
    return None


def _build_snapshot(
    *,
    scope: dict[str, dict[str, Any]],
    database_metadata: dict[str, Any],
    source_scan: SourceScanResult,
    formal_models: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    snapshot_models: dict[str, Any] = {}
    for table, scoped in sorted(scope.items()):
        model_name = str(scoped["odoo_model"])
        formal_model = formal_models.get(table)
        formal_columns = _formal_columns(formal_model)
        physical_columns = database_metadata["physical"].get(table, {})
        orm_fields = database_metadata["orm"].get(model_name, {})
        source_fields = source_scan.definitions.get(model_name, {})
        fields: dict[str, Any] = {}

        if formal_model is None:
            issues.append(
                {
                    "severity": "error",
                    "code": "formal_model_missing",
                    "table": table,
                    "field": None,
                    "message": "正式 Wren 项目缺少该模型。",
                }
            )

        for field_name in scoped["columns"]:
            formal_column = formal_columns.get(field_name)
            physical = physical_columns.get(field_name)
            orm_field = orm_fields.get(field_name)
            source_definitions = source_fields.get(field_name, [])
            if physical is None:
                issues.append(
                    {
                        "severity": "error",
                        "code": "physical_column_missing",
                        "table": table,
                        "field": field_name,
                        "message": "真实 PostgreSQL 表中不存在该开放字段。",
                    }
                )
            if orm_field is None and field_name != "id":
                issues.append(
                    {
                        "severity": "warning",
                        "code": "orm_field_missing",
                        "table": table,
                        "field": field_name,
                        "message": "ir.model.fields 中未找到该字段。",
                    }
                )
            if not source_definitions and field_name != "id":
                issues.append(
                    {
                        "severity": "info",
                        "code": "source_definition_not_found",
                        "table": table,
                        "field": field_name,
                        "message": "静态源码扫描未定位到字段声明，可能来自继承或动态注册。",
                    }
                )
            if formal_column is None:
                issues.append(
                    {
                        "severity": "error",
                        "code": "formal_column_missing",
                        "table": table,
                        "field": field_name,
                        "message": "正式 Wren MDL 中缺少开放字段。",
                    }
                )
            elif physical is not None:
                actual_type = _wren_type(physical)
                formal_type = str(formal_column.get("type") or "").upper()
                if not _wren_types_compatible(formal_type, actual_type):
                    issues.append(
                        {
                            "severity": "warning",
                            "code": "wren_type_drift",
                            "table": table,
                            "field": field_name,
                            "message": f"Wren 类型为 {formal_type}，真实数据库建议类型为 {actual_type}。",
                        }
                    )

            physical_type = _wren_type(physical) if physical else None
            formal_type = (
                str(formal_column.get("type") or "").upper()
                if formal_column
                else None
            )
            suggested_type = physical_type
            if formal_type and physical_type and _wren_types_compatible(
                formal_type,
                physical_type,
            ):
                # Preserve a curated compatible abstraction (for example BIGINT
                # for Odoo IDs backed by int4) to keep drafts reviewable.
                suggested_type = formal_type
            fields[field_name] = {
                "physical": physical,
                "orm": orm_field,
                "source_definitions": source_definitions,
                "formal_wren": formal_column,
                "suggested_wren_type": suggested_type,
                "suggested_description": _description(
                    formal_column,
                    orm_field,
                    source_definitions,
                ),
            }

        snapshot_models[table] = {
            "odoo_model": model_name,
            "description": scoped["description"],
            "fields": fields,
        }

    return {"models": snapshot_models}, issues


def _draft_model(table: str, model: dict[str, Any]) -> dict[str, Any]:
    columns: list[dict[str, Any]] = []
    for field_name, field in model["fields"].items():
        physical = field["physical"]
        if physical is None:
            continue
        column: dict[str, Any] = {
            "name": field_name,
            "type": field["suggested_wren_type"],
        }
        if field_name == "id":
            column["is_primary_key"] = True
        if physical.get("not_null"):
            column["not_null"] = True
        if field.get("suggested_description"):
            column["properties"] = {"description": field["suggested_description"]}
        columns.append(column)
    return {
        "name": table,
        "table_reference": {"schema": "public", "table": table},
        "primary_key": "id" if "id" in model["fields"] else None,
        "columns": columns,
        "cached": False,
        "properties": {"description": model["description"]},
    }


def _draft_relationships(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    table_by_model = {
        model["odoo_model"]: table for table, model in snapshot["models"].items()
    }
    relationships: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for table, model in snapshot["models"].items():
        for field_name, field in model["fields"].items():
            orm = field.get("orm") or {}
            relation = orm.get("relation")
            if orm.get("ttype") != "many2one" or relation not in table_by_model:
                continue
            target_table = table_by_model[str(relation)]
            if field.get("physical") is None:
                continue
            key = (table, field_name, target_table)
            if key in seen:
                continue
            seen.add(key)
            relationships.append(
                {
                    "name": f"{table}_{field_name.removesuffix('_id')}",
                    "models": [table, target_table],
                    "join_type": "MANY_TO_ONE",
                    "condition": f"{table}.{field_name} = {target_table}.id",
                }
            )
    return relationships


def _write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def _write_draft(
    draft_path: Path,
    snapshot: dict[str, Any],
    formal_project_path: Path,
) -> None:
    _write_yaml(
        draft_path / "wren_project.yml",
        {
            "schema_version": 5,
            "name": "odoo_sales_agent_draft",
            "version": datetime.now().astimezone().strftime("%Y.%m.%d"),
            "catalog": "wren",
            "schema": "public",
            "data_source": "postgres",
        },
    )
    _write_yaml(
        draft_path / "relationships.yml",
        {"relationships": _draft_relationships(snapshot)},
    )
    for table, model in snapshot["models"].items():
        draft = _draft_model(table, model)
        if draft.get("primary_key") is None:
            draft.pop("primary_key", None)
        _write_yaml(draft_path / "models" / table / "metadata.yml", draft)

    formal_knowledge = formal_project_path / "knowledge"
    if formal_knowledge.is_dir():
        shutil.copytree(formal_knowledge, draft_path / "knowledge")


def _report_markdown(summary: dict[str, Any], issues: list[dict[str, Any]]) -> str:
    lines = [
        "# Odoo 语义一致性审计",
        "",
        f"- 审计 ID：`{summary['audit_id']}`",
        f"- 生成时间：{summary['generated_at']}",
        f"- 状态：**{summary['status']}**",
        f"- 数据库只读：{summary['database_read_only']}",
        f"- 模型/字段：{summary['model_count']} / {summary['field_count']}",
        f"- 错误/警告/信息：{summary['error_count']} / {summary['warning_count']} / {summary['info_count']}",
        "",
        "> 本报告和 draft 仅供审核，不会覆盖正式 Wren 项目。",
        "",
        "## 差异明细",
        "",
        "| 严重度 | 代码 | 表 | 字段 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not issues:
        lines.append("| - | clean | - | - | 未发现差异 |")
    for issue in issues:
        message = str(issue["message"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {issue['severity']} | {issue['code']} | {issue['table']} | "
            f"{issue.get('field') or '-'} | {message} |"
        )
    return "\n".join(lines) + "\n"


class SemanticSyncService:
    def __init__(
        self,
        database_config: DatabaseConfig,
        sync_config: SemanticSyncConfig,
    ) -> None:
        self._database = OdooDatabase(database_config)
        self._config = sync_config

    async def audit(self) -> dict[str, Any]:
        scope = _scope()
        formal_config, formal_models = _load_formal_project(
            self._config.formal_wren_project_path
        )
        try:
            database_metadata = await self._database.inspect_semantic_metadata(scope)
            source_scan = await asyncio.to_thread(
                scan_odoo_source,
                self._config.source_path,
                scope,
                _source_module_hints(database_metadata),
            )
        except Exception as exc:
            raise SemanticSyncError(type(exc).__name__) from exc
        if not database_metadata.get("read_only"):
            raise SemanticSyncError("Odoo semantic audit requires a read-only connection.")

        snapshot, issues = _build_snapshot(
            scope=scope,
            database_metadata=database_metadata,
            source_scan=source_scan,
            formal_models=formal_models,
        )
        generated_at = datetime.now().astimezone().isoformat()
        digest_payload = json.dumps(
            {
                "snapshot": snapshot,
                "issues": issues,
                "formal_version": formal_config.get("version"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        digest = hashlib.sha256(digest_payload).hexdigest()
        audit_id = (
            datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
            + f"-{digest[:8]}"
        )
        audit_path = self._config.output_path / "audits" / audit_id
        draft_path = audit_path / "wren-draft"
        error_count = sum(issue["severity"] == "error" for issue in issues)
        warning_count = sum(issue["severity"] == "warning" for issue in issues)
        info_count = sum(issue["severity"] == "info" for issue in issues)
        field_count = sum(len(model["fields"]) for model in snapshot["models"].values())
        summary = {
            "audit_id": audit_id,
            "generated_at": generated_at,
            "status": "drift" if error_count or warning_count else "clean",
            "approval_status": "manual_review_required",
            "semantic_version": SalesSemanticLayer.load().version,
            "formal_wren_version": str(formal_config.get("version") or "unknown"),
            "model_count": len(snapshot["models"]),
            "field_count": field_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "database_read_only": True,
            "python_files_seen": source_scan.python_files_seen,
            "candidate_files_parsed": source_scan.candidate_files_parsed,
            "source_files_matched": source_scan.source_files_matched,
            "source_path": str(self._config.source_path),
            "report_path": str(audit_path / "report.md"),
            "snapshot_path": str(audit_path / "semantic-snapshot.json"),
            "draft_path": str(draft_path),
            "issues": issues,
        }

        audit_path.mkdir(parents=True, exist_ok=False)
        (audit_path / "semantic-snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        _write_draft(draft_path, snapshot, self._config.formal_wren_project_path)
        (audit_path / "report.md").write_text(
            _report_markdown(summary, issues),
            encoding="utf-8",
        )
        (audit_path / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._config.output_path.mkdir(parents=True, exist_ok=True)
        latest_temp = self._config.output_path / "latest.json.tmp"
        latest_temp.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        latest_temp.replace(self._config.output_path / "latest.json")
        return summary

    def latest(self) -> dict[str, Any] | None:
        path = self._config.output_path / "latest.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticSyncError(type(exc).__name__) from exc
        return payload if isinstance(payload, dict) else None
