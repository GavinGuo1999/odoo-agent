from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_deepseek_defaults_use_current_api(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            provider = settings.provider("deepseek")

        self.assertEqual(provider.base_url, "https://api.deepseek.com")
        self.assertEqual(provider.model, "deepseek-v4-pro")
        self.assertFalse(provider.configured)
        self.assertEqual(provider.max_retries, 1)
        self.assertEqual(settings.database().explain_total_cost_limit, 1_000_000.0)

    def test_provider_key_is_loaded_without_being_exposed(self) -> None:
        with patch.dict(
            os.environ,
            {"SILICONFLOW_API_KEY": "sensitive-value"},
            clear=True,
        ):
            settings = Settings()
            provider = settings.provider("siliconflow")

        self.assertTrue(provider.configured)
        self.assertNotIn("sensitive-value", repr(settings.siliconflow_api_key))

    def test_wiki_hybrid_retrieval_uses_siliconflow_without_exposing_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SILICONFLOW_API_KEY": "wiki-sensitive-value",
                "WIKI_RETRIEVAL_MODE": "hybrid",
                "WIKI_EMBEDDING_MODEL": "BAAI/bge-m3",
                "WIKI_RERANKER_MODEL": "BAAI/bge-reranker-v2-m3",
            },
            clear=True,
        ):
            wiki = Settings().wiki()

        self.assertEqual(wiki.retrieval_mode, "hybrid")
        self.assertEqual(wiki.embedding_model, "BAAI/bge-m3")
        self.assertEqual(wiki.reranker_model, "BAAI/bge-reranker-v2-m3")
        self.assertNotIn("wiki-sensitive-value", repr(wiki))


if __name__ == "__main__":
    unittest.main()
