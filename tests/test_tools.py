"""三个工具的单元测试（复用自项目1，改为直接调用普通函数）。离线可跑。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.ast_parser import ast_structure_parser
from tools.code_runner import python_code_executor, python_module_executor
from tools.rule_scanner import rule_based_scan


class TestAstParser(unittest.TestCase):
    def test_valid_code(self):
        code = "import os\n\nclass Foo:\n    pass\n\ndef bar(a, b):\n    return a + b\n"
        result = ast_structure_parser(code)
        self.assertIn("bar", result)
        self.assertIn("Foo", result)

    def test_syntax_error(self):
        result = ast_structure_parser("def broken(:\n    pass")
        self.assertIn("语法错误", result)


class TestRuleScanner(unittest.TestCase):
    def test_detects_eval(self):
        self.assertIn("eval", rule_based_scan("eval('1+1')\n"))

    def test_detects_mutable_default(self):
        self.assertIn("可变对象", rule_based_scan("def f(items=[]):\n    return items\n"))

    def test_clean_code(self):
        self.assertIn("未命中任何规则", rule_based_scan("def add(a, b):\n    return a + b\n"))

    def test_too_many_params(self):
        result = rule_based_scan("def f(a, b, c, d, e, f):\n    return a\n")
        self.assertIn("too-many-params", result)

    def test_function_too_long(self):
        body = "\n".join(f"    x{i} = {i}" for i in range(45))
        result = rule_based_scan(f"def long_fn():\n{body}\n")
        self.assertIn("function-too-long", result)

    def test_deep_nesting(self):
        code = "if a:\n" + "".join(f"{'    ' * i}if b:\n" for i in range(1, 5)) + " " * 20 + "pass\n"
        self.assertIn("deep-nesting", rule_based_scan(code))

    def test_shadow_builtin(self):
        self.assertIn("shadow-builtin", rule_based_scan("list = [1, 2]\nprint(list)\n"))

    def test_unreachable_code(self):
        code = "def f():\n    return 1\n    print('永远跑不到')\n"
        self.assertIn("unreachable-code", rule_based_scan(code))

    def test_unused_variable(self):
        code = "def f():\n    temp = 42\n    return 1\n"
        self.assertIn("unused-variable", rule_based_scan(code))


class TestModuleExecutor(unittest.TestCase):
    def test_regression_pass(self):
        module = "def add(a, b):\n    return a + b\n"
        test = "import module_under_test as m\nassert m.add(1, 2) == 3\nprint('ALL CHECKS PASSED')\n"
        result = python_module_executor(module, test)
        self.assertIn("退出码：0", result)
        self.assertIn("ALL CHECKS PASSED", result)

    def test_regression_fail(self):
        module = "def add(a, b):\n    return a - b\n"  # 功能被改坏
        test = "import module_under_test as m\nassert m.add(1, 2) == 3\n"
        result = python_module_executor(module, test)
        self.assertNotIn("退出码：0", result)
        self.assertIn("AssertionError", result)


class TestCodeRunner(unittest.TestCase):
    def test_run_success(self):
        result = python_code_executor("print('hello')")
        self.assertIn("退出码：0", result)
        self.assertIn("hello", result)

    def test_run_failure(self):
        result = python_code_executor("raise ValueError('boom')")
        self.assertNotIn("退出码：0", result)
        self.assertIn("ValueError", result)

    def test_timeout(self):
        self.assertIn("超时", python_code_executor("while True:\n    pass"))


if __name__ == "__main__":
    unittest.main()
