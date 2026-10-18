"""编码Agent 三重自检的测试：Mock LLM，离线可跑。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.coder_agent import CoderAgent
from core.state import WorkflowState


class QuietLogger:
    def node_start(self, *a): pass
    def node_end(self, *a): pass
    def message(self, *a): pass
    def info(self, *a): pass
    def save(self): pass


def coder_payload(code, checkpoints=None):
    return json.dumps({
        "code": code,
        "description": "演示代码",
        "checkpoints": checkpoints or [],
    }, ensure_ascii=False)


GOOD_CODE = "def add(a, b):\n    return a + b\n\nif __name__ == '__main__':\n    print(add(1, 2))\n"
BAD_CODE_EVAL = "def calc(expr):\n    return eval(expr)\n\nif __name__ == '__main__':\n    print(calc('1+1'))\n"

PASS_CP = [{"call": "", "expect": "m.add(1, 1) == 2", "desc": "1+1=2"}]
FAIL_CP = [{"call": "", "expect": "m.add(1, 1) == 3", "desc": "故意错的断言"}]


class MockLLM:
    """按脚本依次返回预设回复的 LLM 替身。"""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        return self.replies.pop(0)


class TestCoderSelfCheck(unittest.TestCase):
    def make_agent(self, replies):
        return CoderAgent(MockLLM(replies), QuietLogger())

    def test_clean_code_passes_first_try(self):
        agent = self.make_agent([coder_payload(GOOD_CODE)])
        state = agent.run(WorkflowState(requirement="加法器"))
        self.assertEqual(state.code, GOOD_CODE)
        self.assertEqual(agent.llm.calls, 1)

    def test_high_rule_hit_bounced_back(self):
        """含 eval 的代码被自查门打回，LLM 第二次交出干净代码才交付。"""
        agent = self.make_agent([coder_payload(BAD_CODE_EVAL), coder_payload(GOOD_CODE)])
        state = agent.run(WorkflowState(requirement="计算器"))
        self.assertEqual(state.code, GOOD_CODE)
        self.assertEqual(agent.llm.calls, 2)  # 第一次被打回

    def test_broken_code_bounced_back(self):
        broken = "def f(:\n"
        agent = self.make_agent([coder_payload(broken), coder_payload(GOOD_CODE)])
        state = agent.run(WorkflowState(requirement="任意"))
        self.assertEqual(state.code, GOOD_CODE)

    def test_checkpoint_verify_failure_bounced_back(self):
        """检查点自验失败（断言不成立）也会打回。"""
        agent = self.make_agent([
            coder_payload(GOOD_CODE, checkpoints=FAIL_CP),
            coder_payload(GOOD_CODE),
        ])
        state = agent.run(WorkflowState(requirement="加法器"))
        self.assertEqual(agent.llm.calls, 2)
        self.assertEqual(state.code, GOOD_CODE)

    def test_checkpoint_verify_passes(self):
        """结构化检查点断言成立时一次通过，检查点随代码交付。"""
        agent = self.make_agent([coder_payload(GOOD_CODE, checkpoints=PASS_CP)])
        state = agent.run(WorkflowState(requirement="加法器"))
        self.assertEqual(agent.llm.calls, 1)
        self.assertEqual(state.checkpoints[0]["expect"], "m.add(1, 1) == 2")

    def test_two_failures_raise(self):
        """两次都不过自检：抛错交给节点级重试/上层处理。"""
        agent = self.make_agent([coder_payload(BAD_CODE_EVAL), coder_payload(BAD_CODE_EVAL)])
        with self.assertRaises(RuntimeError):
            agent.run(WorkflowState(requirement="计算器"))


if __name__ == "__main__":
    unittest.main()
