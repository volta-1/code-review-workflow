"""LLM 结构化输出的契约校验：不信任模型返回，校验不过直接抛错让节点重试。

刻意不用 pydantic：校验逻辑很浅，手写零依赖，离线跑单元测试不需要装任何包。
每个 validate_* 函数返回「归一化后的字典」（补齐可选字段的默认值），
字段缺失或类型不对时抛 SchemaError。
"""
import re

from tools.checkpoint_runner import validate_checkpoint


class SchemaError(ValueError):
    """LLM 输出不符合约定结构。"""


_STRICT = True  # 契约校验开关：消融实验里关掉它，模拟一个不做校验的系统


def set_strict(on: bool) -> None:
    """打开/关闭契约校验（全局开关，消融实验 no_contract 组用）。"""
    global _STRICT
    _STRICT = on


def _require_str(data: dict, key: str, allow_empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        if _STRICT:
            raise SchemaError(f"字段 {key!r} 缺失或不是非空字符串")
        return ""  # 宽松模式：缺字段就按空处理，不判死
    return value


def _optional_str(data: dict, key: str) -> str:
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def validate_coder_output(data: dict) -> dict:
    """编码Agent 输出：{code, description?, checkpoints?: [{call?, expect, desc?}, ...]}"""
    if not isinstance(data, dict):
        if _STRICT:
            raise SchemaError("编码Agent 输出不是 JSON 对象")
        data = {}
    code = _require_str(data, "code")
    raw_checkpoints = data.get("checkpoints") or []
    if not isinstance(raw_checkpoints, list):
        raise SchemaError("checkpoints 必须是数组")
    # 检查点是结构化断言语句（call/expect），交给模板编译成验证脚本；
    # 不合规的条目直接丢弃，不让弱断言混进来
    checkpoints = [cp for cp in (validate_checkpoint(c) for c in raw_checkpoints) if cp]
    return {
        "code": code,
        "description": _optional_str(data, "description"),
        "checkpoints": checkpoints,
    }


_SEVERITIES = ("high", "medium", "low")


def _validate_issue(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise SchemaError("issue 条目不是对象")
    desc = raw.get("description")
    if not isinstance(desc, str) or not desc.strip():
        raise SchemaError("issue 缺少 description")
    issue = dict(raw)
    if issue.get("severity") not in _SEVERITIES:
        issue["severity"] = "medium"  # 等级缺失/乱写时归一为中危，不直接判死
    issue.setdefault("fix", "")
    return issue


def validate_review_output(data: dict) -> dict:
    """审查Agent 输出：{score: 0-100, summary?, issues: [...], verifications?: [...], round_feedback?}"""
    if not isinstance(data, dict):
        if _STRICT:
            raise SchemaError("审查Agent 输出不是 JSON 对象")
        data = {}
    score = data.get("score")
    bad_score = isinstance(score, bool) or not isinstance(score, (int, float))
    if bad_score or not 0 <= score <= 100:
        if _STRICT:
            if bad_score:
                raise SchemaError("score 缺失或不是数字")
            raise SchemaError(f"score={score} 超出 0-100 范围")
        score = 0  # 宽松模式：分数乱写就按 0 分算
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        if _STRICT:
            raise SchemaError("issues 必须是数组")
        issues = []
    verifications = data.get("verifications") or []
    if not isinstance(verifications, list):
        if _STRICT:
            raise SchemaError("verifications 必须是数组")
        verifications = []
    valid_verdicts = ("resolved", "unresolved", "rejected_ok")
    return {
        "score": int(score),
        "summary": _optional_str(data, "summary"),
        "issues": [_validate_issue(i) for i in issues],
        "verifications": [v for v in verifications
                          if isinstance(v, dict) and v.get("verdict") in valid_verdicts],
        "round_feedback": _optional_str(data, "round_feedback"),
    }


def validate_fixer_output(data: dict) -> dict:
    """修复Agent 输出：{code, changes?, decisions?: [...]}

    反驳证据约束：action="rejected" 的决策，reason 必须 ≥20 字且包含代码引用
    （行号、函数名或代码片段），空泛的"这条不对"式反驳直接丢弃。
    回归验证脚本不再由 LLM 手写：由 checkpoint_runner 从结构化检查点确定性编译。
    """
    if not isinstance(data, dict):
        if _STRICT:
            raise SchemaError("修复Agent 输出不是 JSON 对象")
        data = {}
    code = _require_str(data, "code")
    decisions = data.get("decisions") or []
    if not isinstance(decisions, list):
        if _STRICT:
            raise SchemaError("decisions 必须是数组")
        decisions = []
    valid_actions = ("fixed", "rejected")
    if _STRICT:
        gate = _decision_has_evidence  # 反驳必须带证据
    else:
        gate = lambda d: True          # 宽松模式：什么反驳都收
    return {
        "code": code,
        "changes": _optional_str(data, "changes"),
        "decisions": [d for d in decisions
                      if isinstance(d, dict) and d.get("action") in valid_actions
                      and gate(d)],
    }


_CODE_REF = re.compile(r"(`[^`]+`|第\s*\d+\s*行|\w+\s*\(|\bline\s*\d+)", re.IGNORECASE)


def _decision_has_evidence(decision: dict) -> bool:
    """rejected 决策必须有充分理由 + 代码引用；fixed 决策不设门槛。"""
    if decision.get("action") != "rejected":
        return True
    reason = decision.get("reason", "")
    if not isinstance(reason, str) or len(reason.strip()) < 20:
        return False
    return bool(_CODE_REF.search(reason))
