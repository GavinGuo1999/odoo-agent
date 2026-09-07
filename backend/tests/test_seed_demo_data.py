from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from tools.seed_demo_data import build_plan, month_starts  # noqa: E402


class SeedPlanTests(unittest.TestCase):
    """演示数据的分布逻辑必须可验证。

    造出来的数据会直接影响趋势、排名、同比这些问题的答案，分布错了会让评测
    结论跟着错，所以铺月逻辑单独测，不依赖数据库。
    """

    TODAY = date(2026, 9, 7)

    def test_months_end_at_the_current_month(self) -> None:
        starts = month_starts(self.TODAY, 12)

        self.assertEqual(len(starts), 12)
        self.assertEqual(starts[-1], date(2026, 9, 1))
        self.assertEqual(starts[0], date(2025, 10, 1))

    def test_month_starts_cross_the_year_boundary(self) -> None:
        starts = month_starts(date(2026, 2, 15), 4)

        self.assertEqual(starts, [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)])

    def test_every_month_gets_orders_including_the_current_one(self) -> None:
        plan = build_plan(
            today=self.TODAY, total_orders=240, months=12, customer_count=12, product_count=8
        )
        counts = plan.month_counts()

        self.assertEqual(len(counts), 12)
        self.assertTrue(all(count > 0 for count in counts.values()), counts)
        self.assertIn("2026-09", counts, "当月必须有数据")
        self.assertEqual(sum(counts.values()), 240)

    def test_current_month_is_scaled_down_by_elapsed_days(self) -> None:
        plan = build_plan(
            today=self.TODAY, total_orders=240, months=12, customer_count=12, product_count=8
        )
        counts = plan.month_counts()

        # 当月只过了 7 天。若与整月同量，趋势图上会出现假的放量信号。
        self.assertLess(counts["2026-09"], counts["2026-08"])

    def test_no_order_is_dated_in_the_future(self) -> None:
        plan = build_plan(
            today=self.TODAY, total_orders=240, months=12, customer_count=12, product_count=8
        )

        latest = max(order.order_date for order in plan.orders)
        self.assertLessEqual(latest, self.TODAY, "不能造出未来日期的订单")

    def test_states_cover_confirmed_draft_and_cancelled(self) -> None:
        plan = build_plan(
            today=self.TODAY, total_orders=240, months=12, customer_count=12, product_count=8
        )
        states = plan.state_counts()

        # 全是已确认单的话，“未确认订单”“取消率”这类问题就没有数据可查。
        for expected in ("sale", "draft", "cancel"):
            self.assertIn(expected, states)
            self.assertGreater(states[expected], 0)
        self.assertGreater(states["sale"], states["cancel"])

    def test_plan_is_reproducible_for_a_given_seed(self) -> None:
        first = build_plan(
            today=self.TODAY, total_orders=60, months=12, customer_count=5, product_count=4, seed=7
        )
        second = build_plan(
            today=self.TODAY, total_orders=60, months=12, customer_count=5, product_count=4, seed=7
        )

        self.assertEqual(
            [(o.order_date, o.customer_index, o.state) for o in first.orders],
            [(o.order_date, o.customer_index, o.state) for o in second.orders],
        )

    def test_lines_reference_products_within_range(self) -> None:
        plan = build_plan(
            today=self.TODAY, total_orders=80, months=12, customer_count=3, product_count=4
        )

        for order in plan.orders:
            self.assertTrue(order.lines)
            self.assertLess(order.customer_index, 3)
            indexes = [line.product_index for line in order.lines]
            self.assertEqual(len(indexes), len(set(indexes)), "同一张单不应重复同一个产品")
            for line in order.lines:
                self.assertLess(line.product_index, 4)
                self.assertGreaterEqual(line.quantity, 1)

    def test_too_few_orders_for_the_month_span_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_plan(
                today=self.TODAY, total_orders=5, months=12, customer_count=3, product_count=3
            )


if __name__ == "__main__":
    unittest.main()
