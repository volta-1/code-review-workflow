"""条件表达式求值器：用于工作流条件分支，如 "score >= 85"。

刻意不用 eval()：只允许 "状态字段 比较运算符 数字" 这一种形式，
防止配置文件里的表达式执行任意代码。
"""
import re
import operator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.state import WorkflowState

_OPERATORS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}

_PATTERN = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+)\s*$")


class ConditionError(ValueError):
    """条件表达式不合法。"""


def evaluate(condition: str, state: "WorkflowState") -> bool:
    """评估 "score >= 85" 形式的条件，字段取自 state 对象属性。"""
    match = _PATTERN.match(condition)
    if not match:
        raise ConditionError(
            f"不支持的条件表达式：{condition!r}（只支持 '字段 比较符 数字'，如 score >= 85）"
        )
    field_name, op, raw_value = match.groups()
    if not hasattr(state, field_name):
        raise ConditionError(f"状态中不存在字段：{field_name}")
    return _OPERATORS[op](getattr(state, field_name), int(raw_value))
