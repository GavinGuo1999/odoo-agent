from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.bi.time_series import complete_year_months  # noqa: E402


class TimeSeriesTests(unittest.TestCase):
    def test_current_year_monthly_query_fills_missing_buckets_through_today(self) -> None:
        rows, added = complete_year_months(
            question="今年每个月的销售趋势怎么样？",
            columns=["month", "sales_amount"],
            rows=[
                {"month": "2026-06-01", "sales_amount": 100.0},
                {"month": "2026-07-01", "sales_amount": 200.0},
            ],
            today=date(2026, 8, 6),
        )

        self.assertEqual(len(rows), 8)
        self.assertEqual(added, 6)
        self.assertEqual(rows[0], {"month": "2026-01-01", "sales_amount": 0})
        self.assertEqual(rows[5]["sales_amount"], 100.0)
        self.assertEqual(rows[6]["sales_amount"], 200.0)
        self.assertEqual(rows[7]["sales_amount"], 0)

    def test_unrelated_query_is_not_changed(self) -> None:
        original = [{"month": "2026-07-01", "sales_amount": 200.0}]

        rows, added = complete_year_months(
            question="销售额最高的客户是谁？",
            columns=["month", "sales_amount"],
            rows=original,
            today=date(2026, 8, 6),
        )

        self.assertIs(rows, original)
        self.assertEqual(added, 0)


if __name__ == "__main__":
    unittest.main()
