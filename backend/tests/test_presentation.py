from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.presentation import presentation_metadata  # noqa: E402


class PresentationTests(unittest.TestCase):
    def test_returns_chinese_labels_and_formats(self) -> None:
        labels, formats, metric_labels = presentation_metadata(
            ["month", "customer", "sales_amount", "order_count"],
            ["sales_amount"],
        )

        self.assertEqual(labels["month"], "月份")
        self.assertEqual(labels["customer"], "客户")
        self.assertEqual(labels["sales_amount"], "销售额")
        self.assertEqual(formats["sales_amount"], "currency")
        self.assertEqual(formats["order_count"], "integer")
        self.assertEqual(formats["month"], "date")
        self.assertEqual(metric_labels["sales_amount"], "销售额")


if __name__ == "__main__":
    unittest.main()
