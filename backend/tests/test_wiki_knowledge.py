from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings, WikiConfig  # noqa: E402
from app.services.wiki_knowledge import WikiKnowledgeService  # noqa: E402


class FakeEmbeddingClient:
    model = "test-embedding"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            if text == "物流":
                vectors.append([0.0, 1.0])
            elif "交货单" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([1.0, 0.0])
        return vectors


class FailingEmbeddingClient:
    model = "failing-embedding"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("provider unavailable")


class FakeRerankerClient:
    model = "test-reranker"

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
    ) -> list[tuple[int, float]]:
        ranked = sorted(
            enumerate(documents),
            key=lambda item: "交货单" not in item[1],
        )
        return [
            (index, 1.0 - position * 0.1)
            for position, (index, _) in enumerate(ranked[:top_n])
        ]


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

    def test_hybrid_search_uses_faiss_and_reranks_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "learn_odoo"
            source = root / "01_Odoo" / "03_源码"
            source.mkdir(parents=True)
            (source / "invoice.md").write_text(
                """---
type: source
status: reviewed
module: account
---
# 发票流程

## 开票

销售订单行满足开票策略后创建客户发票，并累计已经开票的数量。
业务人员可以从订单行检查待开票数量、已开票数量和相关发票状态。
""",
                encoding="utf-8",
            )
            (source / "delivery.md").write_text(
                """---
type: source
status: reviewed
module: stock
---
# 库存履约

## 交付

确认销售订单后会按路线和仓库规则创建交货单，用于后续拣货和出库。
仓库人员随后完成分配、拣货、验证和实际库存移动等履约步骤。
""",
                encoding="utf-8",
            )
            index_path = Path(directory) / "agent-index" / "wiki.db"
            vector_path = Path(directory) / "agent-index" / "wiki.faiss"
            service = WikiKnowledgeService(
                WikiConfig(
                    root_path=root,
                    index_path=index_path,
                    allowed_statuses=("reviewed",),
                    max_results=2,
                    retrieval_mode="hybrid",
                    vector_index_path=vector_path,
                ),
                embedding_client=FakeEmbeddingClient(),
                reranker_client=FakeRerankerClient(),
            )

            status = service.status()
            result = service.search("物流", limit=1)

            self.assertTrue(status.vector_available)
            self.assertEqual(status.retrieval_mode, "hybrid")
            self.assertTrue(vector_path.exists())
            self.assertEqual(result.retrieval_mode, "hybrid")
            self.assertTrue(result.reranked)
            self.assertEqual(result.hits[0].title, "库存履约")

    def test_hybrid_failure_falls_back_to_local_lexical_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "learn_odoo"
            source = root / "01_Odoo" / "03_源码"
            source.mkdir(parents=True)
            (source / "sale.md").write_text(
                """---
type: source
status: reviewed
module: sale
---
# 销售开票

## 待开票数量

qty_to_invoice 会结合开票策略、交付数量和已开票数量计算。
它用于判断销售订单行是否仍有数量需要创建客户发票并进入开票流程。
""",
                encoding="utf-8",
            )
            service = WikiKnowledgeService(
                WikiConfig(
                    root_path=root,
                    index_path=Path(directory) / "agent-index" / "wiki.db",
                    allowed_statuses=("reviewed",),
                    max_results=2,
                    retrieval_mode="hybrid",
                    vector_index_path=Path(directory) / "agent-index" / "wiki.faiss",
                ),
                embedding_client=FailingEmbeddingClient(),
            )

            result = service.search("qty_to_invoice", limit=1)

            self.assertEqual(result.retrieval_mode, "lexical")
            self.assertEqual(result.fallback_reason, "RuntimeError")
            self.assertEqual(result.hits[0].title, "销售开票")

    def test_lexical_search_surfaces_short_concept_note_over_long_source_notes(
        self,
    ) -> None:
        # docs/19 §3.2: BM25 加成饱和到 5.00 + 字段命中未按字段长度归一，使短概念
        # 笔记被更长的源码阅读笔记挤出纯词法 top-6。用真实 learn_odoo 语料复现该
        # 缺陷。Stock Picking.md 修复前两种模式均未召回（docs/19 §3.1），是本次两处
        # 修复能在纯词法路径下真正拿下的 case——它标题/正文用词与提问直接重合。
        # Sale Order.md / Stock Rule.md 不在断言范围内：它们的提问是英文标题概念笔
        # 记的中文意译，trigram 精确匹配和字符 gram 重叠天然不足，docs/19 §2.1 记录
        # 这两条本就是靠 hybrid 的向量检索找回的，词法打分公式无法、也不应该被调参
        # 到覆盖这类根本性的复述失配（会变成对着 20 题过拟合）。
        question = "仓库一次收货或发货由什么单据组织，它和库存移动是什么关系？"
        expected_relative_path = "01_Odoo/06_Concepts/Stock Picking.md"
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                Settings().wiki(),
                retrieval_mode="lexical",
                index_path=Path(directory) / "wiki.db",
                vector_index_path=Path(directory) / "wiki.faiss",
            )
            service = WikiKnowledgeService(config)
            status = service.status()
            self.assertTrue(
                status.available,
                "learn_odoo 语料不可用（预期路径：workspace/learn_odoo），无法复现真实排序问题",
            )

            result = service.search(question, limit=6)
            retrieved = [hit.relative_path for hit in result.hits]
            self.assertIn(
                expected_relative_path,
                retrieved,
                f"期望 {expected_relative_path} 进入纯词法 top-6，实际命中 {retrieved}",
            )


if __name__ == "__main__":
    unittest.main()
