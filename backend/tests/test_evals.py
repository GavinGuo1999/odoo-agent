from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from evals.run_sales_eval import DEFAULT_DATASET, load_dataset, validate_static  # noqa: E402


class GoldenDatasetTests(unittest.TestCase):
    def test_sales_golden_dataset_has_twenty_valid_static_cases(self) -> None:
        items = load_dataset(DEFAULT_DATASET)
        results = validate_static(items)

        self.assertEqual(len(items), 20)
        self.assertTrue(all(item["passed"] for item in results))
        self.assertTrue(any(item.expected_output.interrupt for item in items))
        self.assertTrue(any(item.id == "safety-prompt-injection" for item in items))


if __name__ == "__main__":
    unittest.main()
