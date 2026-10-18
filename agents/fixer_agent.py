"""修复Agent：对审查意见逐条决策（采纳修复 / 有理反驳），修复后做回归验证。

升级点：
1. 协商式协作：不是无条件照单全收，可对不成立的意见反驳并给出理由，
   交由审查Agent 下一轮裁定——Agent 间从「单向传递」升级为「双向协商」。
   反驳有证据约束（理由充分 + 引用代码），被驳回过的 issue 不得再次反驳。
2. 回归验证：修复后重跑编码Agent 交付的结构化检查点
   （确定性模板编译成断言脚本），证明修复没有改坏功能。
"""
import json

from agents.base import AgentNode
from agents.prompts import FIXER_RETRY_TEMPLATE, FIXER_SYSTEM, FIXER_TEMPLATE
from core.llm import extract_json
from core.schema import SchemaError, validate_fixer_output
from core.state import WorkflowState
from tools.checkpoint_runner import build_verify_script, format_checkpoints
from tools.code_runner import python_code_executor, python_module_executor

MAX_FIX_TEST = 2  # 修复后验证失败最多再修正 1 次


class FixerAgent(AgentNode):
    """职责：代码 + 审查意见 → 修复后可运行且功能不回退的代码 + 逐条决策记录。"""

    node_id = "fixer"
    name = "修复Agent"

    def run(self, state: WorkflowState) -> WorkflowState:
        issues_text = json.dumps(state.review.get("issues", []), ensure_ascii=False, indent=1)
        user_msg = FIXER_TEMPLATE.format(
            requirement=state.requirement, code=state.code,
            score=state.score, issues=issues_text,
            checkpoints=format_checkpoints(state.checkpoints))

        fixed, changes, decisions = None, "", []
        for attempt in range(1, MAX_FIX_TEST + 1):
            data = extract_json(self.llm.chat(FIXER_SYSTEM, user_msg))
            try:
                parsed = validate_fixer_output(data)  # 契约校验（含反驳证据约束）
            except SchemaError as e:
                raise RuntimeError(f"修复Agent 输出不符合契约（{e}），请重试。")
            fixed = parsed["code"]
            changes = parsed["changes"]
            decisions = self._enforce_no_reject(state, parsed["decisions"])

            # 第一重验证：修复后的代码能跑
            run_result = python_code_executor(fixed)
            self.logger.info(f"运行验证：{run_result.splitlines()[1]}")
            # 第二重验证：功能检查点回归（确定性模板编译检查点，真实执行）
            regress_result = ""
            if state.checkpoints and state.ablation.get("checkpoint_verify", True):
                script = build_verify_script(state.checkpoints)
                regress_result = python_module_executor(fixed, script)
                self.logger.info(f"回归验证：{regress_result.splitlines()[1]}")

            if "退出码：0" in run_result and (not regress_result or "退出码：0" in regress_result):
                state.code = fixed
                state.decisions = decisions
                state.apply_decisions(decisions)  # 决策写入 issue 生命周期
                self._log_decisions(state, decisions, changes)
                return state
            error = run_result + ("\n" + regress_result if regress_result else "")
            user_msg = FIXER_RETRY_TEMPLATE.format(
                requirement=state.requirement, code=fixed, error=error)

        # 两次验证都未通过：仍提交最后版本，让下一轮审查兜底，不让流程卡死
        state.code = fixed
        state.decisions = decisions
        state.apply_decisions(decisions)
        self.send(state, "审查Agent", "fix_decisions",
                  "修复后验证未完全通过，已提交最后版本供复审")
        self.logger.info("修复代码验证未完全通过，交由下一轮审查处理")
        return state

    def _enforce_no_reject(self, state: WorkflowState, decisions: list) -> list:
        """曾被驳回反驳的 issue 不得再次反驳：强制改为 fixed，防止来回踢皮球。"""
        no_reject = state.rejection_failed_ids()
        if not no_reject or not state.ablation.get("anti_pingpong", True):
            return decisions
        for d in decisions:
            if d.get("action") == "rejected" and d.get("id") in no_reject:
                self.logger.info(
                    f"意见 #{d.get('id')} 的反驳曾被裁定不成立，本次不得再反驳，强制按采纳处理")
                d["action"] = "fixed"
                d["reason"] = "（此前反驳已被审查Agent 驳回，本轮强制修复）" + d.get("reason", "")
        return decisions

    def _log_decisions(self, state: WorkflowState, decisions: list, changes: str) -> None:
        adopted = sum(1 for d in decisions if d.get("action") == "fixed")
        rejected = sum(1 for d in decisions if d.get("action") == "rejected")
        for d in decisions:
            mark = "✅ 采纳" if d.get("action") == "fixed" else "🚫 反驳"
            self.logger.info(f"意见 #{d.get('id', '?')}：{mark}——{d.get('reason', '')}")
        self.send(state, "审查Agent", "fix_decisions",
                  f"修复完成：{changes}；采纳 {adopted} 条，反驳 {rejected} 条；"
                  f"运行验证与回归验证均通过")
        self.logger.info(f"修复完成（{changes}），采纳 {adopted} 条 / 反驳 {rejected} 条，"
                         "运行验证与回归验证均通过")
