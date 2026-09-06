from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.run_wiki_rag_eval import _retrieval_scores, load_dataset  # noqa: E402


class WikiRagEvaluationTests(unittest.TestCase):
    def test_golden_dataset_has_reviewed_reference_paths(self) -> None:
        items = load_dataset(ROOT / "evals" / "datasets" / "wiki_rag_golden.jsonl")
        self.assertEqual(len(items), 20)
        wiki_root = ROOT.parent / "learn_odoo"
        for item in items:
            self.assertTrue(item.reference_answer)
            for relative_path in item.reference_paths:
                text = (wiki_root / relative_path).read_text(encoding="utf-8")
                self.assertRegex(text, r"status:\s*(reviewed|evergreen)")

    def test_ranked_context_metrics_reward_early_relevant_hits(self) -> None:
        relevant = ("a.md", "b.md")
        early = _retrieval_scores(["a.md", "x.md", "b.md"], relevant)
        late = _retrieval_scores(["x.md", "a.md", "b.md"], relevant)
        self.assertEqual(early["context_recall"], 1.0)
        self.assertGreater(early["context_precision"], late["context_precision"])
        self.assertEqual(early["mrr"], 1.0)


if __name__ == "__main__":
    unittest.main()
