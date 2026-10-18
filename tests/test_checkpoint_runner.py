"""检查点模板编译器与反驳防踢皮球机制的测试。离线可跑。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.fixer_agent import FixerAgent
from core.state import WorkflowState
from tools.checkpoint_runner import (
    build_verify_script,
    format_checkpoints,
    validate_checkpoint,
)
from tools.code_runner import python_module_executor


MODULE = "def add(a, b):\n    return a + b\n\nTASKS = []\ndef add_task(t):\n    TASKS.append(t)\n"


class TestValidateCheckpoint(unittest.TestCase):
    def test_valid_full(self):
        cp = validate_checkpoint({"call": "m.add(1, 2)", "expect": "m.add(1, 2) == 3", "desc": "加法"})
        self.assertEqual(cp["call"], "m.add(1, 2)")

    def test_call_optional(self):
        cp = validate_checkpoint({"expect": "m.add(0, 0) == 0"})
        self.assertEqual(cp["call"], "")

    def test_missing_expect_rejected(self):
        self.assertIsNone(validate_checkpoint({"call": "m.add(1, 2)"}))
        self.assertIsNone(validate_checkpoint("添加任务后应有 1 项"))  # 散文不收
        self.assertIsNone(validate_checkpoint({"expect": ""}))


class TestBuildVerifyScript(unittest.TestCase):
    def test_script_passes_when_assertions_hold(self):
        cps = [{"call": "m.add_task('买菜')", "expect": "len(m.TASKS) == 1", "desc": "添加任务"},
               {"call": "", "expect": "m.add(2, 3) == 5", "desc": "加法"}]
        result = python_module_executor(MODULE, build_verify_script(cps))
        self.assertIn("退出码：0", result)
        self.assertIn("ALL CHECKS PASSED", result)

    def test_script_fails_when_assertion_breaks(self):
        cps = [{"call": "", "expect": "m.add(2, 3) == 6", "desc": "故意错的断言"}]
        result = python_module_executor(MODULE, build_verify_script(cps))
        self.assertIn("退出码：1", result)
        self.assertIn("检查点 1 失败", result)

    def test_script_fails_when_call_raises(self):
        cps = [{"call": "m.not_exist()", "expect": "True", "desc": "调用不存在的函数"}]
        result = python_module_executor(MODULE, build_verify_script(cps))
        self.assertIn("退出码：1", result)

    def test_format_checkpoints(self):
        text = format_checkpoints([{"call": "m.add_task('x')", "expect": "len(m.TASKS) == 1",
                                    "desc": "添加"}])
        self.assertIn("m.add_task('x')", text)
        self.assertIn("len(m.TASKS) == 1", text)
        self.assertEqual(format_checkpoints([]), "（编码Agent 未提供）")


class QuietLogger:
    def node_start(self, *a): pass
    def node_end(self, *a): pass
    def message(self, *a): pass
    def info(self, *a): pass
    def save(self): pass


class TestRejectionFailed(unittest.TestCase):
    def test_rejection_failed_marked(self):
        """rejected 被裁定为 unresolved 时打上 rejection_failed 标记。"""
        state = WorkflowState()
        state.register_issues([{"id": 1, "description": "x"}])
        state.apply_decisions([{"id": 1, "action": "rejected"}])
        state.apply_verifications([{"id": 1, "verdict": "unresolved"}])
        self.assertEqual(state.rejection_failed_ids(), {1})

    def test_rejected_ok_not_marked(self):
        state = WorkflowState()
        state.register_issues([{"id": 1, "description": "x"}])
        state.apply_decisions([{"id": 1, "action": "rejected"}])
        state.apply_verifications([{"id": 1, "verdict": "rejected_ok"}])
        self.assertEqual(state.rejection_failed_ids(), set())

    def test_fixer_forces_fix_on_failed_rejection(self):
        """修复Agent 对被驳回过反驳的 issue，再次反驳会被强制改为 fixed。"""
        state = WorkflowState()
        state.register_issues([{"id": 1, "description": "x"}])
        state.apply_decisions([{"id": 1, "action": "rejected"}])
        state.apply_verifications([{"id": 1, "verdict": "unresolved"}])

        fixer = FixerAgent.__new__(FixerAgent)
        fixer.logger = QuietLogger()
        decisions = fixer._enforce_no_reject(state, [
            {"id": 1, "action": "rejected", "reason": "我还是觉得这条不成立，因为它和需求冲突"}])
        self.assertEqual(decisions[0]["action"], "fixed")

    def test_fixer_allows_first_time_rejection(self):
        """首次反驳（无驳回记录）不受影响。"""
        state = WorkflowState()
        state.register_issues([{"id": 1, "description": "x"}])
        fixer = FixerAgent.__new__(FixerAgent)
        fixer.logger = QuietLogger()
        decisions = fixer._enforce_no_reject(state, [
            {"id": 1, "action": "rejected", "reason": "第 3 行 `add()` 已有校验，误报"}])
        self.assertEqual(decisions[0]["action"], "rejected")


class TestRunArchive(unittest.TestCase):
    def test_archive_appends_jsonl(self):
        """LLM 原始调用归档：每次成功调用追加一行 JSON，失败不阻断。"""
        from core.llm import LLMClient
        with tempfile.TemporaryDirectory() as run_dir:
            client = LLMClient.__new__(LLMClient)  # 不触发惰性导入，只测归档逻辑
            client._archive_path = os.path.join(run_dir, "llm_calls.jsonl")
            client._archive("sys1", "user1", "reply1")
            client._archive("sys2", "user2", "reply2")
            with open(client._archive_path, encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)
            record = json.loads(lines[0])
            self.assertEqual(record["reply"], "reply1")
            self.assertIn("ts", record)

    def test_archive_disabled_when_no_path(self):
        from core.llm import LLMClient
        client = LLMClient.__new__(LLMClient)
        client._archive_path = None
        client._archive("s", "u", "r")  # 不应抛错


if __name__ == "__main__":
    unittest.main()
