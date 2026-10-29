"""工作流报告生成器：把整个多 Agent 协作过程渲染为 Markdown 报告。

与项目1的区别：报告对象从「单次审查输出」变为「完整工作流状态」，
包含需求、每轮评分变化、最终审查结论、最终代码与 Agent 间消息流。
"""
import json
import os
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.state import WorkflowState

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

SEVERITY_LABEL = {"high": "🔴 高", "medium": "🟡 中", "low": "🔵 低"}


def _score_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def _run_stats(run_dir: str | None) -> str | None:
    """从运行归档的 llm_calls.jsonl 统计 LLM 调用次数与耗时。

    归档一行就是一次真实调用，首末两条的时间差即本次工作流的实际耗时——
    报告里带上这行，被问"一次运行调几次 LLM、花多久"时直接有据可查。
    """
    if not run_dir:
        return None
    path = os.path.join(run_dir, "llm_calls.jsonl")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError):
        return None  # 归档读不出来不阻断报告生成
    if not records:
        return None
    calls = len(records)
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        start = datetime.strptime(records[0]["ts"], fmt)
        end = datetime.strptime(records[-1]["ts"], fmt)
        seconds = max(0, int((end - start).total_seconds()))
        return f"LLM 调用 {calls} 次，耗时约 {seconds} 秒"
    except (KeyError, ValueError):
        return f"LLM 调用 {calls} 次"


def generate_workflow_report(state: "WorkflowState", run_dir: str | None = None) -> str:
    """根据 WorkflowState 生成报告，返回文件路径。

    run_dir 传入本次运行归档目录时，报告头部会附带 LLM 调用统计。"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    run_id = getattr(state, "run_id", "") or datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORT_DIR, f"workflow_report_{run_id}.md")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    review = state.review or {}
    issues = review.get("issues") or []
    score = int(state.score)
    high = sum(1 for i in issues if i.get("severity") == "high")
    medium = sum(1 for i in issues if i.get("severity") == "medium")
    low = sum(1 for i in issues if i.get("severity") == "low")

    # 每轮评分变化
    progression = " → ".join(str(s) for s in state.score_history)

    parts = [
        "# 多 Agent 代码审查工作流报告\n",
        f"- 生成时间：{now}",
        f"- 需求：{state.requirement}",
        f"- 协作 Agent：编码Agent → 审查Agent ⇄ 修复Agent",
        f"- 审查轮次：{state.round} 轮（评分变化：{progression}）",
    ]
    stats = _run_stats(run_dir)
    if stats:
        parts.append(f"- 运行统计：{stats}")
    parts += [
        "",
        "## 最终质量评分卡\n",
        "| 总分 | 评分 | 高危 | 中危 | 低危 |",
        "|------|------|------|------|------|",
        f"| **{score}** / 100 | {_score_bar(score)} | {high} | {medium} | {low} |\n",
        f"**总体评价**：{review.get('summary', '（无）')}\n",
    ]

    # 功能检查点（编码Agent 交付、修复Agent 回归验证的依据）
    if state.checkpoints:
        from tools.checkpoint_runner import format_checkpoints
        parts += ["## 功能检查点（回归验证依据）\n",
                  "```", format_checkpoints(state.checkpoints), "```\n"]

    # 逐轮对比：每轮评分、问题数、审查裁定、回滚标记
    if state.review_history:
        parts += ["## 逐轮审查对比\n",
                  "| 轮次 | 评分 | 问题数 | 评价 / 裁定 |",
                  "|------|------|--------|-------------|"]
        for r in state.review_history:
            note = r.get("round_feedback") or r.get("summary", "")
            note = str(note).replace("|", "\\|").replace("\n", " ")[:60]
            mark = " ↩️回滚" if r.get("rolled_back") else ""
            parts.append(f"| 第 {r['round']} 轮 | {r['score']}{mark} | {r['issue_count']} | {note} |")
        parts.append("")

    # Issue 生命周期跟踪表
    if state.issue_tracker:
        status_label = {"open": "🔵 待处理", "fixed": "🟡 已修复待核销",
                        "rejected": "🟣 被反驳待裁定", "resolved": "✅ 已核销",
                        "unresolved": "🔴 未修好", "rejected_ok": "⚪ 反驳成立"}
        parts += ["## Issue 生命周期跟踪\n",
                  "| 编号 | 状态 | 问题 |",
                  "|------|------|------|"]
        for t in state.issue_tracker:
            desc = str(t.get("description", "")).replace("|", "\\|").replace("\n", " ")[:50]
            label = status_label.get(t.get("status", ""), t.get("status", "?"))
            parts.append(f"| #{t.get('id')} | {label} | {desc} |")
        parts.append("")

    # 修复决策：采纳 / 反驳记录
    if state.decisions:
        adopted = sum(1 for d in state.decisions if d.get("action") == "fixed")
        rejected = sum(1 for d in state.decisions if d.get("action") == "rejected")
        parts += [f"## 修复Agent 决策记录（采纳 {adopted} 条 / 反驳 {rejected} 条）\n",
                  "| 意见编号 | 决策 | 理由 |",
                  "|----------|------|------|"]
        for d in state.decisions:
            mark = "✅ 采纳修复" if d.get("action") == "fixed" else "🚫 反驳"
            reason = str(d.get("reason", "")).replace("|", "\\|").replace("\n", " ")[:80]
            parts.append(f"| #{d.get('id', '?')} | {mark} | {reason} |")
        parts.append("")

    parts.append("## 最终问题清单\n")

    if issues:
        parts.append("| 行号 | 严重等级 | 问题描述 | 修复建议 |")
        parts.append("|------|----------|----------|----------|")
        for i in issues:
            sev = SEVERITY_LABEL.get(i.get("severity", ""), i.get("severity", "?"))
            line = i.get("line") if i.get("line") is not None else "-"
            desc = str(i.get("description", "")).replace("|", "\\|")
            fix = str(i.get("fix", "")).replace("|", "\\|")
            parts.append(f"| {line} | {sev} | {desc} | {fix} |")
    else:
        parts.append("未发现问题。🎉")

    parts += ["\n## 最终代码（已经过多轮审查修复与运行验证）\n",
              "```python", state.code, "```\n",
              "## Agent 间消息流\n",
              "| 时间 | 发送方 | 接收方 | 类型 | 内容 |",
              "|------|--------|--------|------|------|"]
    for m in state.messages:
        ts = datetime.fromtimestamp(m.timestamp).strftime("%H:%M:%S")
        content = str(m.content).replace("|", "\\|").replace("\n", " ")[:60]
        parts.append(f"| {ts} | {m.sender} | {m.receiver} | {m.msg_type} | {content} |")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts) + "\n")
    return report_path
