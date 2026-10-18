"""工具4：把结构化功能检查点编译成可执行的验证脚本。

设计动机：不让 LLM 手写验证脚本（可能写出 assert True 这种弱断言）。
LLM 只负责用结构化形式描述"测什么"（call / expect），
"怎么测"由这个模板确定性生成——断言强度不再依赖模型自觉。

检查点格式：
{"call": "m.add_task('买菜')（可选，先执行的操作）",
 "expect": "len(m.list_tasks()) == 1（必须为真的表达式）",
 "desc": "一句话说明（可选）"}
其中 m 是被测模块（脚本以 import module_under_test as m 开头）。
"""

_HEADER = "import module_under_test as m\n\n_failures = []\n"

_CHECKPOINT_TEMPLATE = """
# --- 检查点 {idx}: {desc} ---
try:
{call_line}    if not ({expect}):
        raise AssertionError("断言不成立: {expect}")
except Exception as _e:
    _failures.append("检查点 {idx} 失败: " + str(_e))
"""

_FOOTER = """
if _failures:
    print("\\n".join(_failures))
    raise SystemExit(1)
print("ALL CHECKS PASSED")
"""


def validate_checkpoint(item: object) -> dict | None:
    """校验单条检查点，合法返回归一化字典，不合法返回 None。"""
    if not isinstance(item, dict):
        return None
    expect = item.get("expect")
    if not isinstance(expect, str) or not expect.strip():
        return None
    call = item.get("call", "")
    desc = item.get("desc", "")
    return {
        "call": call.strip() if isinstance(call, str) else "",
        "expect": expect.strip(),
        "desc": desc.strip() if isinstance(desc, str) else "",
    }


def build_verify_script(checkpoints: list) -> str:
    """把检查点列表编译成完整验证脚本（供 python_module_executor 执行）。"""
    parts = [_HEADER]
    for idx, cp in enumerate(checkpoints, 1):
        call_line = f"    {cp['call']}\n" if cp["call"] else ""
        parts.append(_CHECKPOINT_TEMPLATE.format(
            idx=idx, desc=cp["desc"] or cp["expect"],
            call_line=call_line, expect=cp["expect"]))
    parts.append(_FOOTER)
    return "".join(parts)


def format_checkpoints(checkpoints: list) -> str:
    """渲染成给人看的清单（prompt 与报告共用）。"""
    if not checkpoints:
        return "（编码Agent 未提供）"
    lines = []
    for idx, cp in enumerate(checkpoints, 1):
        step = f"先执行 {cp['call']}，然后" if cp["call"] else ""
        lines.append(f"{idx}. {step}应满足：{cp['expect']}"
                     + (f"（{cp['desc']}）" if cp["desc"] else ""))
    return "\n".join(lines)
