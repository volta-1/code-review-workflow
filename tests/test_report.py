"""JSON 提取与报告生成的单元测试。离线可跑。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm import extract_json
from core.state import WorkflowState
from report.report_generator import generate_workflow_report


class TestExtractJson(unittest.TestCase):
    def test_fenced(self):
        data = extract_json('前言\n```json\n{"score": 80}\n```\n后记')
        self.assertEqual(data["score"], 80)

    def test_with_comments(self):
        data = extract_json('{"score": 60, // 注释\n "issues": []}')
        self.assertEqual(data["score"], 60)

    def test_url_in_string_not_treated_as_comment(self):
        """字符串值里的 https:// 不能被当成行内注释剥掉。"""
        data = extract_json('{"fix": "参考 https://example.com/docs 处理", "score": 70}')
        self.assertEqual(data["fix"], "参考 https://example.com/docs 处理")
        self.assertEqual(data["score"], 70)

    def test_garbage(self):
        self.assertIsNone(extract_json("这不是 JSON"))


class TestWorkflowReport(unittest.TestCase):
    def test_report_contains_key_sections(self):
        state = WorkflowState(requirement="测试需求", code="print(1)")
        state.score = 88
        state.score_history = [60, 88]
        state.review = {"score": 88, "summary": "良好", "issues": []}
        state.add_message("编码Agent", "审查Agent", "code", "交付代码")
        path = generate_workflow_report(state)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("测试需求", content)
        self.assertIn("60 → 88", content)
        self.assertIn("编码Agent", content)
        os.unlink(path)


class TestRunStats(unittest.TestCase):
    def test_report_includes_llm_stats(self):
        """传入运行归档目录时，报告头部应包含 LLM 调用次数与耗时。"""
        import json
        import tempfile
        run_dir = tempfile.mkdtemp()
        with open(os.path.join(run_dir, "llm_calls.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-10-18 13:00:00", "system": "", "user": "", "reply": ""}) + "\n")
            f.write(json.dumps({"ts": "2026-10-18 13:02:30", "system": "", "user": "", "reply": ""}) + "\n")
        state = WorkflowState(requirement="统计测试", code="print(1)")
        state.score = 90
        state.score_history = [90]
        state.review = {"score": 90, "summary": "好", "issues": []}
        path = generate_workflow_report(state, run_dir=run_dir)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("LLM 调用 2 次", content)
        self.assertIn("150 秒", content)
        os.unlink(path)

    def test_report_without_run_dir_unchanged(self):
        """不传 run_dir 时报告照常生成，不含运行统计行（兼容旧调用）。"""
        state = WorkflowState(requirement="兼容测试", code="print(1)")
        state.score = 80
        state.score_history = [80]
        state.review = {"score": 80, "summary": "好", "issues": []}
        path = generate_workflow_report(state)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("运行统计", content)
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
