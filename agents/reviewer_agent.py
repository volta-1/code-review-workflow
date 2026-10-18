"""审查Agent：双引擎审查 + issue 生命周期核销 + 版本回滚 + 停滞检测。

第 2 轮起的额外职责：
- 收到修复Agent 的采纳/反驳决策，逐条核销 issue（resolved/unresolved/rejected_ok）
- 分数回落时触发版本回滚，始终保留历史最优代码
- 连续两轮分数不提升时置 force_finish，请引擎提前终止
- 轮次记录超过 3 轮时把早期记录压缩成摘要，控制上下文长度
"""
import json

from agents.base import AgentNode
from agents.prompts import (
    REVIEW_FEEDBACK_CONTEXT,
    REVIEW_HISTORY_CONTEXT,
    REVIEW_TRACKER_CONTEXT,
    REVIEWER_SYSTEM,
    REVIEWER_TEMPLATE,
    SUMMARIZE_SYSTEM,
    SUMMARIZE_TEMPLATE,
)
from core.lifecycle import IssueTracker, VersionLedger, detect_stagnation
from core.llm import extract_json
from core.schema import SchemaError, validate_review_output
from core.state import WorkflowState
from tools.ast_parser import ast_structure_parser
from tools.rule_scanner import rule_based_scan, score_ceiling

KEEP_FULL_ROUNDS = 2   # 保留最近几轮的完整记录
COMPRESS_AFTER = 3     # 超过这个轮次开始压缩历史


class ReviewerAgent(AgentNode):
    """职责：代码 → 结构化审查结论 + issue 核销 + 版本管理。"""

    node_id = "reviewer"
    name = "审查Agent"

    def run(self, state: WorkflowState) -> WorkflowState:
        self._compress_history(state)
        self.logger.info(f"开始第 {state.round + 1} 轮审查，调用 AST 解析与规则扫描工具...")

        ast_report = ast_structure_parser(state.code)
        rule_report = rule_based_scan(state.code)
        if "【语法错误】" in ast_report:
            # 语法错误时规则引擎无法工作，直接给最低分让修复Agent处理
            review = {"score": 0, "summary": "代码存在语法错误，无法运行。",
                      "issues": [{"id": 1, "line": None, "severity": "high",
                                  "description": ast_report, "fix": "修复语法错误"}],
                      "verifications": [], "round_feedback": ""}
        else:
            data = extract_json(self.llm.chat(REVIEWER_SYSTEM, REVIEWER_TEMPLATE.format(
                code=state.code, ast_report=ast_report, rule_report=rule_report,
                feedback_context=self._decisions_context(state),
                tracker_context=self._tracker_context(state),
                history_context=self._history_context(state))))
            try:
                review = validate_review_output(data)  # 契约校验：不合约直接判失败
            except SchemaError as e:
                raise RuntimeError(f"审查Agent 输出不符合契约（{e}），请重试。")

        # 确定性评分锚点：LLM 主观分不得超过规则引擎推出的上限，
        # 避免"扫出 high 级 bug 却给 95 分"这类分数漂移
        ceiling = score_ceiling(state.code)
        if state.ablation.get("rule_anchor", True) and review["score"] > ceiling:
            self.logger.info(
                f"评分锚点生效：LLM 给出 {review['score']} 分，但规则引擎扣分上限为 "
                f"{ceiling} 分，按上限计")
            review["score"] = ceiling

        state.round += 1
        issues = review.get("issues") or []
        for idx, issue in enumerate(issues, 1):  # LLM 没给编号时兜底补齐
            issue.setdefault("id", idx)

        # issue 生命周期与版本台账：规则在 core/lifecycle.py，这里只串流程
        tracker = IssueTracker(state.issue_tracker)
        tracker.apply_verifications(review.get("verifications") or [])
        tracker.register(issues)

        state.review = review
        state.score = int(review.get("score", 0))
        state.score_history.append(state.score)
        rolled_back = VersionLedger(state).record()
        if rolled_back:
            self.send(state, "工作流", "notice",
                      f"第 {state.round} 轮分数回落至 {state.score}，"
                      f"已回滚到第 {state.best_round} 轮的最优版本（{state.best_score} 分）")
            self.logger.info(f"↩️ 分数回落，回滚到第 {state.best_round} 轮最优版本")

        state.review_history.append({
            "round": state.round, "score": state.score,
            "summary": review.get("summary", ""),
            "issue_count": len(issues),
            "round_feedback": review.get("round_feedback", ""),
            "rolled_back": rolled_back,
        })

        # 停滞检测：连续两轮分数没有提升（且本轮未回滚），请求引擎提前终止
        if not rolled_back and detect_stagnation(state.score_history, state.score):
            state.force_finish = True
            self.logger.info("检测到分数停滞（连续两轮未提升），建议提前终止")

        self.send(state, "工作流", "review",
                  f"第 {state.round} 轮审查完成：{state.score} 分，发现 {len(issues)} 个问题")
        self.logger.info(f"评分 {state.score}/100，问题 {len(issues)} 个：{review.get('summary', '')}")
        return state

    # ---- 上下文组装 ----
    def _decisions_context(self, state: WorkflowState) -> str:
        if not state.decisions:
            return ""
        return REVIEW_FEEDBACK_CONTEXT.format(
            decisions=json.dumps(state.decisions, ensure_ascii=False, indent=1))

    def _tracker_context(self, state: WorkflowState) -> str:
        pending = [t for t in state.issue_tracker if t["status"] in ("fixed", "rejected")]
        if not pending:
            return ""
        return REVIEW_TRACKER_CONTEXT.format(
            tracker=json.dumps(pending, ensure_ascii=False, indent=1))

    def _history_context(self, state: WorkflowState) -> str:
        if not state.history_summary:
            return ""
        return REVIEW_HISTORY_CONTEXT.format(summary=state.history_summary)

    def _compress_history(self, state: WorkflowState) -> None:
        """上下文预算管理：轮次记录超过阈值时，早期轮次压缩成摘要，只留最近几轮完整记录。"""
        if len(state.review_history) <= COMPRESS_AFTER:
            return
        old_rounds = state.review_history[:-KEEP_FULL_ROUNDS]
        summary = self.llm.chat(SUMMARIZE_SYSTEM, SUMMARIZE_TEMPLATE.format(
            history=json.dumps(old_rounds, ensure_ascii=False)))
        state.history_summary = (state.history_summary + "\n" + summary).strip()
        state.review_history = state.review_history[-KEEP_FULL_ROUNDS:]
        self.logger.info(f"早期 {len(old_rounds)} 轮记录已压缩为摘要（上下文预算管理）")
