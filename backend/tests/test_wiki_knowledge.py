from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import WikiConfig  # noqa: E402
from app.services.wiki_knowledge import WikiKnowledgeService  # noqa: E402


class WikiKnowledgeServiceTests(unittest.TestCase):
    def test_indexes_only_reviewed_notes_and_returns_citations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "learn_odoo"
            source = root / "01_Odoo" / "03_源码"
            source.mkdir(parents=True)
            (source / "sale.md").write_text(
                """---
type: source
status: reviewed
module: sale
topic: invoice
odoo_fields: [qty_to_invoice]
---
# Sale 源码主链路

## qty_to_invoice 的计算

qty_to_invoice 会结合开票策略、交付数量和已开票数量计算。
这是订单行是否待开票的关键字段。
""",
                encoding="utf-8",
            )
            (source / "draft.md").write_text(
                """---
type: source
status: draft
module: sale
---
# 未审核草稿

## qty_to_invoice

这段未审核内容不应进入索引，也不应作为聊天依据。
""",
                encoding="utf-8",
            )
            index_path = Path(directory) / "agent-index" / "wiki.db"
            service = WikiKnowledgeService(
                WikiConfig(
                    root_path=root,
                    index_path=index_path,
                    allowed_statuses=("reviewed", "evergreen"),
                    max_results=6,
                )
            )

            status = service.status()
            result = service.search("qty_to_invoice 怎么计算")

            self.assertTrue(status.available)
            self.assertEqual(status.note_count, 1)
            self.assertGreaterEqual(status.chunk_count, 1)
            self.assertEqual(len(result.hits), 1)
            self.assertEqual(result.hits[0].title, "Sale 源码主链路")
            self.assertEqual(result.hits[0].status, "reviewed")
            self.assertTrue(result.hits[0].obsidian_uri.startswith("obsidian://open"))
            self.assertIn("[知识来源 1]", result.context())
            self.assertTrue(index_path.exists())

    def test_missing_wiki_is_reported_without_creating_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "missing"
            service = WikiKnowledgeService(
                WikiConfig(
                    root_path=root,
                    index_path=Path(directory) / "index" / "wiki.db",
                    allowed_statuses=("reviewed",),
                    max_results=6,
                )
            )

            status = service.status()

            self.assertFalse(status.available)
            self.assertEqual(status.error_type, "WikiDirectoryNotFound")
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
