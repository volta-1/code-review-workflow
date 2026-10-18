"""工具2：规则引擎，基于 AST 遍历确定性扫描常见低级 bug。复用自项目1。

内置 9 条规则：eval/exec、裸 except、except-pass、可变默认参数、== None、
通配符导入、while True 死循环、函数过长、参数过多、嵌套过深、
未使用变量、return 后不可达代码、覆盖内置函数名。
"""
import ast
import builtins

_BUILTIN_NAMES = set(dir(builtins))


class _RuleVisitor(ast.NodeVisitor):
    """遍历 AST 记录命中规则，每条: (行号, 等级, 规则名, 描述)。"""

    def __init__(self) -> None:
        self.findings: list[tuple] = []

    def _add(self, node: ast.AST, severity: str, rule: str, desc: str) -> None:
        self.findings.append((getattr(node, "lineno", "?"), severity, rule, desc))

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            self._add(node, "high", "dangerous-call",
                      f"使用了 {node.func.id}()，存在代码注入风险，建议用 ast.literal_eval 或重构逻辑")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.type is None:
            self._add(node, "medium", "bare-except",
                      "裸 except 会吞掉包括 KeyboardInterrupt 在内的所有异常，应指定具体异常类型")
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            self._add(node, "medium", "except-pass",
                      "except 块中直接 pass，异常被静默吞掉，排查问题将非常困难")
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        for default in node.args.defaults:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self._add(node, "high", "mutable-default-arg",
                          f"函数 {node.name} 使用了可变对象作为默认参数，"
                          "多次调用间会共享同一对象，应改为 None 再在函数体内初始化")
        # 函数过长：超过 40 行难以测试和维护
        if hasattr(node, "end_lineno") and node.end_lineno:
            length = node.end_lineno - node.lineno
            if length > 40:
                self._add(node, "medium", "function-too-long",
                          f"函数 {node.name} 长达 {length} 行，建议拆分为多个职责单一的函数")
        # 参数过多：超过 5 个参数说明职责可能不清
        n_args = len(node.args.args) + len(node.args.kwonlyargs)
        if n_args > 5:
            self._add(node, "low", "too-many-params",
                      f"函数 {node.name} 有 {n_args} 个参数，建议用配置对象或 dataclass 封装")
        self.generic_visit(node)

    def visit_Compare(self, node):
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comparator, ast.Constant) \
                    and comparator.value is None:
                self._add(node, "low", "eq-none",
                          "与 None 比较应使用 is / is not，而不是 == / !=")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if any(a.name == "*" for a in node.names):
            self._add(node, "low", "wildcard-import",
                      f"from {node.module} import * 会污染命名空间，建议显式导入")
        self.generic_visit(node)

    def visit_While(self, node):
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            has_break = any(isinstance(n, ast.Break) for n in ast.walk(node))
            has_return = any(isinstance(n, ast.Return) for n in ast.walk(node))
            if not has_break and not has_return:
                self._add(node, "high", "infinite-loop",
                          "while True 循环内没有 break 或 return，疑似死循环")
        self.generic_visit(node)

    def generic_visit(self, node):
        # 嵌套过深：if/for/while/try/with 嵌套超过 4 层
        depth = self._nesting_depth(node)
        if depth > 4 and isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
            # 只在最外层报一次（深度刚好超标的节点）
            self._add(node, "medium", "deep-nesting",
                      f"代码嵌套达到 {depth} 层，建议用提前返回（early return）或拆函数降低嵌套")
        super().generic_visit(node)

    @staticmethod
    def _nesting_depth(node, _types=(ast.If, ast.For, ast.While, ast.Try, ast.With)):
        """计算以该节点为根的控制结构最大嵌套层数（含节点自身）。"""
        def dive(n, d):
            best = d
            for child in ast.iter_child_nodes(n):
                best = max(best, dive(child, d + 1 if isinstance(child, _types) else d))
            return best
        start = 1 if isinstance(node, _types) else 0
        return max(start, dive(node, start))


def _scan_unreachable_and_unused(tree: ast.AST, visitor: _RuleVisitor) -> None:
    """需要跨语句/作用域视角的三条规则，用整体遍历实现。"""
    # 覆盖内置函数名：list = [] 之后内置 list 就被遮蔽了
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) \
                and node.id in _BUILTIN_NAMES:
            visitor._add(node, "medium", "shadow-builtin",
                         f"变量名 {node.id} 覆盖了 Python 内置函数，后续无法再使用内置 {node.id}()")

    # return/raise 之后的不可达代码
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            for idx, stmt in enumerate(stmts[:-1]):
                if isinstance(stmt, (ast.Return, ast.Raise)):
                    nxt = stmts[idx + 1]
                    visitor._add(nxt, "medium", "unreachable-code",
                                 "该语句位于 return/raise 之后，永远不会被执行")
                    break

    # 未使用变量：函数内赋值后再也没被读取（下划线开头的惯例性忽略）
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        assigned, used = set(), set()
        args = {a.arg for a in node.args.args + node.args.kwonlyargs}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                if isinstance(sub.ctx, ast.Store):
                    assigned.add(sub.id)
                elif isinstance(sub.ctx, ast.Load):
                    used.add(sub.id)
        for name in sorted(assigned - used - args):
            if not name.startswith("_"):
                visitor._add(node, "low", "unused-variable",
                             f"函数 {node.name} 中的变量 {name} 赋值后从未被使用")


def scan_findings(code: str) -> list[tuple]:
    """结构化扫描结果：[(行号, 等级, 规则名, 描述), ...]，供评分锚点等程序化使用。

    语法错误或扫描失败时返回空列表（文本版 rule_based_scan 会给出可读说明）。
    """
    try:
        tree = ast.parse(code)
    except Exception:  # 语法错误 / 其他解析失败：文本版会给出可读说明
        return []
    visitor = _RuleVisitor()
    visitor.visit(tree)
    _scan_unreachable_and_unused(tree, visitor)
    return visitor.findings


# 确定性评分锚点：按严重等级扣分，给 LLM 主观分设一个不可逾越的上限
SEVERITY_PENALTY = {"high": 15, "medium": 8, "low": 3}


def score_ceiling(code: str) -> int:
    """规则引擎推出的分数上限：100 减去命中规则的扣分，下限 0。"""
    penalty = sum(SEVERITY_PENALTY.get(sev, 0) for _, sev, _, _ in scan_findings(code))
    return max(0, 100 - penalty)


def rule_based_scan(code: str) -> str:
    """用内置规则引擎扫描 Python 代码中的常见低级 bug，返回命中规则列表。"""
    try:
        ast.parse(code)
    except SyntaxError:
        return "代码存在语法错误，无法扫描。请先调用 ast_structure_parser 定位语法问题。"
    except Exception as e:
        return f"【扫描失败】{type(e).__name__}: {e}"

    findings = scan_findings(code)
    if not findings:
        return "===== 规则引擎扫描结果 =====\n未命中任何规则，未发现低级 bug。"

    lines = ["===== 规则引擎扫描结果 =====", f"共命中 {len(findings)} 条规则："]
    for lineno, severity, rule, desc in findings:
        lines.append(f"[{severity:6s}] 第 {lineno} 行 | {rule}: {desc}")
    return "\n".join(lines)
