"""LLM 输出契约校验与评分锚点的单元测试。离线可跑。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.schema import (
    SchemaError,
    validate_coder_output,
    validate_fixer_output,
    validate_review_output,
)
from tools.rule_scanner import score_ceiling


class TestCoderSchema(unittest.TestCase):
    def test_valid_output(self):
        parsed = validate_coder_output({
            "code": "print(1)", "description": "演示",
            "checkpoints": [{"call": "m.add(1, 2)", "expect": "m.add(1, 2) == 3", "desc": "加法"}]})
        self.assertEqual(parsed["code"], "print(1)")
        self.assertEqual(parsed["checkpoints"][0]["expect"], "m.add(1, 2) == 3")

    def test_missing_code_rejected(self):
        with self.assertRaises(SchemaError):
            validate_coder_output({"description": "没有代码"})

    def test_defaults_filled(self):
        parsed = validate_coder_output({"code": "print(1)"})
        self.assertEqual(parsed["description"], "")
        self.assertEqual(parsed["checkpoints"], [])

    def test_invalid_checkpoints_dropped(self):
        """缺 expect 的检查点（弱断言/散文）被丢弃，不混入验证链。"""
        parsed = validate_coder_output({
            "code": "print(1)",
            "checkpoints": [{"expect": "m.x() == 1"}, {"call": "m.x()"}, "添加任务后应有1项"]})
        self.assertEqual(len(parsed["checkpoints"]), 1)


class TestReviewSchema(unittest.TestCase):
    def test_valid_output(self):
        parsed = validate_review_output({
            "score": 75, "summary": "尚可",
            "issues": [{"description": "用了 eval", "severity": "high"}],
            "verifications": [{"id": 1, "verdict": "resolved"}]})
        self.assertEqual(parsed["score"], 75)
        self.assertEqual(parsed["issues"][0]["severity"], "high")

    def test_score_out_of_range_rejected(self):
        with self.assertRaises(SchemaError):
            validate_review_output({"score": 150, "issues": []})

    def test_score_missing_rejected(self):
        with self.assertRaises(SchemaError):
            validate_review_output({"summary": "没给分"})

    def test_bad_severity_normalized(self):
        """severity 乱写时归一为中危，而不是整个判死。"""
        parsed = validate_review_output({
            "score": 60, "issues": [{"description": "x", "severity": "严重"}]})
        self.assertEqual(parsed["issues"][0]["severity"], "medium")

    def test_invalid_verdict_filtered(self):
        parsed = validate_review_output({
            "score": 60, "issues": [],
            "verifications": [{"id": 1, "verdict": "maybe"}, {"id": 2, "verdict": "resolved"}]})
        self.assertEqual(len(parsed["verifications"]), 1)


class TestFixerSchema(unittest.TestCase):
    def test_valid_output(self):
        parsed = validate_fixer_output({
            "code": "print(2)", "changes": "改了", "decisions": [{"id": 1, "action": "fixed"}]})
        self.assertEqual(len(parsed["decisions"]), 1)

    def test_invalid_action_filtered(self):
        parsed = validate_fixer_output({
            "code": "print(2)",
            "decisions": [{"id": 1, "action": "ignored"}, {"id": 2, "action": "rejected",
                          "reason": "第 3 行的 `add()` 本来就做了参数校验，这条意见是误报"}]})
        self.assertEqual(len(parsed["decisions"]), 1)

    def test_missing_code_rejected(self):
        with self.assertRaises(SchemaError):
            validate_fixer_output({"changes": "只有说明没有代码"})

    def test_rejected_without_evidence_dropped(self):
        """反驳理由空泛（太短或无代码引用）被丢弃，fixed 决策不受限。"""
        parsed = validate_fixer_output({
            "code": "print(2)",
            "decisions": [
                {"id": 1, "action": "rejected", "reason": "这条不对"},  # 太短
                {"id": 2, "action": "rejected",
                 "reason": "这条意见不成立，因为需求里就是这么要求的，不需要改"},  # 无代码引用
                {"id": 3, "action": "rejected",
                 "reason": "第 5 行 `validate()` 已经处理了空值，这条是误报，不需要修改"},  # 合格
                {"id": 4, "action": "fixed", "reason": "好"},
            ]})
        kept = [d["id"] for d in parsed["decisions"]]
        self.assertEqual(kept, [3, 4])


class TestScoreCeiling(unittest.TestCase):
    def test_clean_code_full_ceiling(self):
        self.assertEqual(score_ceiling("def add(a, b):\n    return a + b\n"), 100)

    def test_high_severity_caps_score(self):
        """命中 high 级规则时上限被压到 85，LLM 无法给出 95 分。"""
        self.assertEqual(score_ceiling("eval('1+1')\n"), 85)

    def test_multiple_findings_stack(self):
        code = "eval('1')\nlist = [1]\nx = 1\ndef f():\n    return 1\n    print(2)\n"
        self.assertLessEqual(score_ceiling(code), 100 - 15 - 8 - 8)

    def test_syntax_error_zero_ceiling_not_crash(self):
        """语法错误时扫描为空，上限保持 100（审查Agent 有语法错误单独通道）。"""
        self.assertEqual(score_ceiling("def broken(:\n"), 100)


if __name__ == "__main__":
    unittest.main()
