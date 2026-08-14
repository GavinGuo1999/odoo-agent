from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import DatabaseConfig, SemanticSyncConfig  # noqa: E402
from app.services.semantic_sync import (  # noqa: E402
    SemanticSyncService,
    scan_odoo_source,
)


def _scope() -> dict[str, dict[str, object]]:
    return {
        "sale_order": {
            "odoo_model": "sale.order",
            "columns": ["id", "name"],
            "description": "销售订单",
        }
    }


def _database_metadata() -> dict[str, object]:
    return {
        "read_only": True,
        "physical": {
            "sale_order": {
                "id": {
                    "data_type": "integer",
                    "udt_name": "int4",
                    "not_null": True,
                    "ordinal_position": 1,
                },
                "name": {
                    "data_type": "character varying",
                    "udt_name": "varchar",
                    "not_null": True,
                    "ordinal_position": 2,
                },
            }
        },
        "orm": {
            "sale.order": {
                "name": {
                    "ttype": "char",
                    "relation": None,
                    "field_description": {"en_US": "Order Reference"},
                    "help": None,
                    "selection": [],
                }
            }
        },
    }


def _write_source(source_path: Path) -> None:
    path = source_path / "addons" / "sale" / "models" / "sale_order.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        """from odoo import fields, models

class SaleOrder(models.Model):
    _name = "sale.order"

    name = fields.Char(string="Order Reference", required=True)
""",
        encoding="utf-8",
    )


def _write_formal_project(project_path: Path, *, name_type: str = "VARCHAR") -> None:
    project_path.mkdir(parents=True)
    (project_path / "wren_project.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 5,
                "name": "test",
                "version": "1.0",
                "catalog": "wren",
                "schema": "public",
                "data_source": "postgres",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    metadata_path = project_path / "models" / "sale_order" / "metadata.yml"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        yaml.safe_dump(
            {
                "name": "sale_order",
                "table_reference": {"schema": "public", "table": "sale_order"},
                "primary_key": "id",
                "columns": [
                    {"name": "id", "type": "BIGINT", "is_primary_key": True},
                    {"name": "name", "type": name_type},
                ],
                "cached": False,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


class SemanticSourceScannerTests(unittest.TestCase):
    def test_scans_business_addons_outside_the_odoo_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir)
            _write_source(source_path)

            result = scan_odoo_source(source_path, _scope())

        definitions = result.definitions["sale.order"]["name"]
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0]["field_type"], "Char")
        self.assertEqual(definitions[0]["label"], "Order Reference")
        self.assertEqual(result.source_files_matched, 1)


class SemanticSyncServiceTests(unittest.IsolatedAsyncioTestCase):
    def _database_config(self) -> DatabaseConfig:
        return DatabaseConfig(
            host="127.0.0.1",
            port=55432,
            database="odoo19_dev",
            user="codex_readonly",
            password=None,
            company_id=1,
            statement_timeout_ms=15000,
            max_rows=500,
        )

    async def test_audit_writes_versioned_draft_without_modifying_formal_mdl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "odoo-source"
            formal_path = root / "formal-wren"
            output_path = root / "audit-output"
            _write_source(source_path)
            _write_formal_project(formal_path)
            formal_before = (formal_path / "models" / "sale_order" / "metadata.yml").read_bytes()
            config = SemanticSyncConfig(
                source_path=source_path,
                output_path=output_path,
                formal_wren_project_path=formal_path,
            )

            with (
                patch("app.services.semantic_sync._scope", return_value=_scope()),
                patch(
                    "app.services.semantic_sync.OdooDatabase.inspect_semantic_metadata",
                    new=AsyncMock(return_value=_database_metadata()),
                ),
            ):
                result = await SemanticSyncService(
                    self._database_config(),
                    config,
                ).audit()

            self.assertEqual(result["status"], "clean")
            self.assertTrue(result["database_read_only"])
            self.assertEqual(
                (formal_path / "models" / "sale_order" / "metadata.yml").read_bytes(),
                formal_before,
            )
            self.assertTrue(Path(result["report_path"]).is_file())
            self.assertTrue(
                (Path(result["draft_path"]) / "models" / "sale_order" / "metadata.yml").is_file()
            )
            draft = yaml.safe_load(
                (Path(result["draft_path"]) / "models" / "sale_order" / "metadata.yml").read_text(
                    encoding="utf-8"
                )
            )
            id_column = next(column for column in draft["columns"] if column["name"] == "id")
            self.assertEqual(id_column["type"], "BIGINT")
            latest = json.loads((output_path / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["audit_id"], result["audit_id"])

    async def test_audit_reports_formal_type_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "odoo-source"
            formal_path = root / "formal-wren"
            _write_source(source_path)
            _write_formal_project(formal_path, name_type="JSON")
            config = SemanticSyncConfig(
                source_path=source_path,
                output_path=root / "audit-output",
                formal_wren_project_path=formal_path,
            )

            with (
                patch("app.services.semantic_sync._scope", return_value=_scope()),
                patch(
                    "app.services.semantic_sync.OdooDatabase.inspect_semantic_metadata",
                    new=AsyncMock(return_value=_database_metadata()),
                ),
            ):
                result = await SemanticSyncService(
                    self._database_config(),
                    config,
                ).audit()

        self.assertEqual(result["status"], "drift")
        self.assertEqual(result["warning_count"], 1)
        self.assertEqual(result["issues"][0]["code"], "wren_type_drift")


if __name__ == "__main__":
    unittest.main()
