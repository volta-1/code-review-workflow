"""版本回滚、issue 生命周期、引擎停滞终止与节点级重试的测试。离线可跑。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import WorkflowEngine
from core.state import WorkflowState


class TestVersionRollback(unittest.TestCase):
    def test_rollback_when_score_drops(self):
        """分数回落时回滚到历史最优版本。"""
        state = WorkflowState(code="v1代码", score=90, round=1)
        self.assertFalse(state.record_version())          # 新高分，不回滚
        state.code = "v2代码（改差了）"
        state.score = 60
        state.round = 2
        self.assertTrue(state.record_version())           # 回落，触发回滚
        self.assertEqual(state.code, "v1代码")             # 恢复到最优版本
        self.assertEqual(state.best_round, 1)

    def test_no_rollback_when_improving(self):
        state = WorkflowState(code="v1", score=60, round=1)
        state.record_version()
        state.code = "v2"
        state.score = 90
        state.round = 2
        self.assertFalse(state.record_version())
        self.assertEqual(state.code, "v2")


class TestIssueLifecycle(unittest.TestCase):
    def test_full_lifecycle(self):
        """open → fixed → resolved / rejected → rejected_ok 全流程。"""
        state = WorkflowState()
        state.register_issues([{"id": 1, "description": "eval 风险"},
                               {"id": 2, "description": "缺类型注解"}])
        self.assertEqual([t["status"] for t in state.issue_tracker], ["open", "open"])

        state.apply_decisions([{"id": 1, "action": "fixed"},
                               {"id": 2, "action": "rejected"}])
        self.assertEqual([t["status"] for t in state.issue_tracker], ["fixed", "rejected"])

        state.apply_verifications([{"id": 1, "verdict": "resolved"},
                                   {"id": 2, "verdict": "rejected_ok"}])
        self.assertEqual([t["status"] for t in state.issue_tracker],
                         ["resolved", "rejected_ok"])

    def test_duplicate_registration_ignored(self):
        state = WorkflowState()
        state.register_issues([{"id": 1, "description": "老问题"}])
        state.register_issues([{"id": 1, "description": "同一问题再次出现"}])
        self.assertEqual(len(state.issue_tracker), 1)


class MockNode:
    def __init__(self, node_id, action=None):
        self.node_id = node_id
        self.name = node_id
        self.calls = 0
        self.action = action or (lambda s: s)

    def run(self, state):
        self.calls += 1
        return self.action(state)


class QuietLogger:
    def node_start(self, *a): pass
    def node_end(self, *a): pass
    def message(self, *a): pass
    def info(self, *a): pass
    def save(self): pass


def make_engine(edges, registry, max_loops=5, start="a"):
    config = {"start": start, "max_loops": max_loops, "edges": edges}
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    return WorkflowEngine(config, registry, QuietLogger(), tmp.name)


class TestForceFinish(unittest.TestCase):
    def test_force_finish_stops_early(self):
        """节点置 force_finish 后引擎提前结束，不再走后续节点。"""
        def stop_action(s):
            d = s.to_dict()
            d["force_finish"] = True
            return WorkflowState.from_dict(d)

        a, b = MockNode("a", stop_action), MockNode("b")
        engine = make_engine([{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
                             {"a": a, "b": b})
        engine.run(WorkflowState())
        self.assertEqual(a.calls, 1)
        self.assertEqual(b.calls, 0)  # b 被跳过


class TestNodeRetry(unittest.TestCase):
    def test_node_retry_on_runtime_error(self):
        """节点第一次抛 RuntimeError，重试后成功，流程继续。"""
        attempts = {"n": 0}

        def flaky(s):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("模拟 LLM 输出解析失败")
            return s

        a = MockNode("a", flaky)
        engine = make_engine([{"from": "a", "to": "END"}], {"a": a})
        engine.run(WorkflowState())
        self.assertEqual(attempts["n"], 2)  # 失败 1 次 + 重试成功

    def test_node_retry_exhausted_raises(self):
        """重试次数用完后仍然失败，错误要抛出来而不是吞掉。"""
        def always_fail(s):
            raise RuntimeError("一直失败")

        a = MockNode("a", always_fail)
        engine = make_engine([{"from": "a", "to": "END"}], {"a": a})
        with self.assertRaises(RuntimeError):
            engine.run(WorkflowState())

    def test_retry_rolls_back_messages(self):
        """节点失败重试时，本次 attempt 追加的消息要回滚，不能重复入流。"""
        attempts = {"n": 0}

        def flaky(s):
            attempts["n"] += 1
            s.add_message("测试节点", "工作流", "notice", f"第 {attempts['n']} 次尝试")
            if attempts["n"] == 1:
                raise RuntimeError("发完消息后失败")
            return s

        a = MockNode("a", flaky)
        engine = make_engine([{"from": "a", "to": "END"}], {"a": a})
        state = engine.run(WorkflowState())
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(len(state.messages), 1)  # 失败那次的消息被回滚
        self.assertEqual(state.messages[0].content, "第 2 次尝试")


if __name__ == "__main__":
    unittest.main()
