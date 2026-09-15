"""执行链路：阶段记录与耗时归属。

最容易出错的是**归属方向**。阶段事件在动手之前发射，所以某一步的耗时是它到
下一步之间的间隔。用反了会把时间记到后一个节点头上——实测时把答案合成的
66 秒记成了"回答已完成"花了 66 秒，正好指错瓶颈，而这个功能的全部意义就是指出瓶颈。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.bi.agent import _begin_turn_trace, _collect_turn_trace, _emit_stage


class TurnTraceTests(unittest.TestCase):
    def test_nothing_is_recorded_before_a_turn_starts(self) -> None:
        from app.bi.agent import _TURN_TRACE

        token = _TURN_TRACE.set(None)
        try:
            _emit_stage("orphan", "不该被记录")
            self.assertEqual(_collect_turn_trace(), [])
        finally:
            _TURN_TRACE.reset(token)

    def test_stages_are_recorded_in_order_with_cumulative_offsets(self) -> None:
        _begin_turn_trace()
        with patch("app.bi.agent.time.monotonic", side_effect=[0.0, 1.5, 2.0]):
            _emit_stage("classify", "判断问题类型")
            _emit_stage("sql-generation", "生成 SQL")
            _emit_stage("complete", "完成")

        steps = _collect_turn_trace()
        self.assertEqual([s["stage"] for s in steps],
                         ["classify", "sql-generation", "complete"])
        # at_ms 是距本轮开始的累计毫秒，首步恒为 0。
        self.assertEqual([s["at_ms"] for s in steps], [0.0, 1500.0, 2000.0])

    def test_duration_belongs_to_the_stage_that_precedes_the_gap(self) -> None:
        """这条是这个文件存在的理由。"""

        _begin_turn_trace()
        with patch("app.bi.agent.time.monotonic", side_effect=[0.0, 0.1, 30.1]):
            _emit_stage("chart-planning", "规划图表")
            _emit_stage("answer-synthesis", "整理结论")   # 在模型调用**之前**发射
            _emit_stage("complete", "完成")               # 模型调用结束后发射

        steps = _collect_turn_trace()
        durations = {
            step["stage"]: (steps[i + 1]["at_ms"] - step["at_ms"]) if i + 1 < len(steps) else 0.0
            for i, step in enumerate(steps)
        }
        # 30 秒花在合成上，不是花在"完成"上。
        self.assertAlmostEqual(durations["answer-synthesis"], 30000.0, places=1)
        self.assertEqual(durations["complete"], 0.0)
        self.assertAlmostEqual(durations["chart-planning"], 100.0, places=1)

    def test_internal_timestamp_is_not_exposed(self) -> None:
        _begin_turn_trace()
        _emit_stage("classify", "判断问题类型")
        # _t 是内部用来算间隔的，不该出现在 artifact 或 API 响应里。
        from app.bi.agent import _turn_artifact

        artifact = _turn_artifact({"answer": "x", "intent": "general", "rows": [{"a": 1}]})
        for step in artifact["trace_steps"]:
            self.assertNotIn("_t", step)


if __name__ == "__main__":
    unittest.main()
