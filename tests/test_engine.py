"""工作流引擎的单元测试：用 Mock 节点验证调度逻辑，不调用 LLM，离线可跑。

运行方式（项目根目录下）：
    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.conditions import ConditionError, evaluate
from core.engine import WorkflowEngine
from core.state import WorkflowState


class MockNode:
    """假节点：按预设行为改状态，记录自己被调用了几次。"""

    def __init__(self, node_id, action=None):
        self.node_id = node_id
        self.name = node_id
        self.calls = 0
        self.action = action or (lambda state: state)

    def run(self, state):
        self.calls += 1
        return self.action(state)


class QuietLogger:
    def node_start(self, *a): pass
    def node_end(self, *a): pass
    def message(self, *a): pass
    def info(self, *a): pass
    def save(self): pass


def make_engine(edges, registry, max_loops=3, start="a"):
    config = {"start": start, "max_loops": max_loops, "edges": edges}
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    return WorkflowEngine(config, registry, QuietLogger(), tmp.name)


class TestSequential(unittest.TestCase):
    def test_sequential_flow(self):
        """顺序执行：a → b → END，两个节点各执行一次。"""
        a, b = MockNode("a"), MockNode("b")
        engine = make_engine(
            [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
            {"a": a, "b": b})
        state = engine.run(WorkflowState())
        self.assertEqual(a.calls, 1)
        self.assertEqual(b.calls, 1)
        self.assertTrue(state.finished)


class TestConditionalBranch(unittest.TestCase):
    def test_branch_taken_when_condition_true(self):
        """score >= 85 成立时直接走向 END，跳过 fixer。"""
        reviewer = MockNode("reviewer", lambda s: WorkflowState.from_dict(
            {**s.to_dict(), "score": 90}))
        fixer = MockNode("fixer")
        engine = make_engine(
            [{"from": "reviewer", "to": "END", "condition": "score >= 85"},
             {"from": "reviewer", "to": "fixer"},
             {"from": "fixer", "to": "reviewer"}],
            {"reviewer": reviewer, "fixer": fixer}, start="reviewer")
        engine.run(WorkflowState())
        self.assertEqual(reviewer.calls, 1)
        self.assertEqual(fixer.calls, 0)  # 条件成立，分支被跳过

    def test_branch_skipped_when_condition_false(self):
        """score < 85 时走向 fixer，修复后回到 reviewer 形成循环。"""
        scores = iter([60, 90])

        def review_action(s):
            d = s.to_dict()
            d["score"] = next(scores)
            d["round"] = d["round"] + 1
            return WorkflowState.from_dict(d)

        reviewer = MockNode("reviewer", review_action)
        fixer = MockNode("fixer")
        engine = make_engine(
            [{"from": "reviewer", "to": "END", "condition": "score >= 85"},
             {"from": "reviewer", "to": "fixer"},
             {"from": "fixer", "to": "reviewer"}],
            {"reviewer": reviewer, "fixer": fixer}, start="reviewer")
        state = engine.run(WorkflowState())
        self.assertEqual(reviewer.calls, 2)  # 审了两次
        self.assertEqual(fixer.calls, 1)     # 修了一次
        self.assertEqual(state.score, 90)


class TestLoopGuard(unittest.TestCase):
    def test_max_loops_prevents_infinite_cycle(self):
        """分数一直不达标时，循环到上限强制结束而不是死循环。"""
        reviewer = MockNode("reviewer", lambda s: WorkflowState.from_dict(
            {**s.to_dict(), "score": 50}))
        fixer = MockNode("fixer")
        engine = make_engine(
            [{"from": "reviewer", "to": "END", "condition": "score >= 85"},
             {"from": "reviewer", "to": "fixer"},
             {"from": "fixer", "to": "reviewer"}],
            {"reviewer": reviewer, "fixer": fixer}, max_loops=3, start="reviewer")
        engine.run(WorkflowState())
        self.assertEqual(reviewer.calls, 3)  # 达到上限即停


class TestCheckpoint(unittest.TestCase):
    def test_checkpoint_saved_and_loadable(self):
        """每个节点完成后保存检查点，可以恢复出完整状态。"""
        a = MockNode("a", lambda s: WorkflowState.from_dict(
            {**s.to_dict(), "requirement": "测试需求"}))
        engine = make_engine([{"from": "a", "to": "END"}], {"a": a})
        engine.run(WorkflowState())
        restored = WorkflowState.load_checkpoint(engine.checkpoint_path)
        self.assertEqual(restored.requirement, "测试需求")
        self.assertTrue(restored.finished)

    def test_missing_node_raises(self):
        """配置引用了没注册的节点：启动校验直接拦下（fail-fast），不等到运行时。"""
        with self.assertRaises(ValueError):
            make_engine([{"from": "a", "to": "ghost"}], {"a": MockNode("a")})


class TestConfigValidation(unittest.TestCase):
    """启动时的配置校验：写错配置立刻报错，而不是跑到一半才崩。"""

    def test_unknown_start_rejected(self):
        with self.assertRaises(ValueError):
            make_engine([{"from": "a", "to": "END"}], {"a": MockNode("a")}, start="ghost")

    def test_bad_max_loops_rejected(self):
        with self.assertRaises(ValueError):
            make_engine([{"from": "a", "to": "END"}], {"a": MockNode("a")}, max_loops=0)

    def test_missing_edges_rejected(self):
        with self.assertRaises(ValueError):
            WorkflowEngine({"start": "a"}, {"a": MockNode("a")}, QuietLogger(),
                           tempfile.mktemp(suffix=".json"))

    def test_node_without_out_edge_rejected(self):
        """注册了但没有任何出边的节点，流程走到它会卡死，启动时就要拦。"""
        with self.assertRaises(ValueError):
            make_engine([{"from": "a", "to": "END"}],
                        {"a": MockNode("a"), "b": MockNode("b")})

    def test_valid_config_accepted(self):
        engine = make_engine([{"from": "a", "to": "END"}], {"a": MockNode("a")})
        self.assertEqual(engine.max_loops, 3)


class TestConditionParser(unittest.TestCase):
    def test_operators(self):
        state = WorkflowState(score=85, round=2)
        self.assertTrue(evaluate("score >= 85", state))
        self.assertFalse(evaluate("score > 85", state))
        self.assertTrue(evaluate("round == 2", state))
        self.assertTrue(evaluate("score != 100", state))

    def test_rejects_non_numeric(self):
        with self.assertRaises(ConditionError):
            evaluate("score >= high", WorkflowState())

    def test_rejects_unknown_field(self):
        with self.assertRaises(ConditionError):
            evaluate("unknown >= 1", WorkflowState())


if __name__ == "__main__":
    unittest.main()
