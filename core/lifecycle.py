"""issue 生命周期与版本管理：从 WorkflowState 拆出来的协作规则。

WorkflowState 只存数据（issue 跟踪表、最优版本字段），
"什么时候算修好、什么时候算反驳成立、分数回落怎么办"这些规则
集中放在这里，Agent 只调用不实现——状态层变薄，规则一处维护。

- IssueTracker：issue 从提出到关闭的完整状态机
- VersionLedger：始终保留历史最优代码，分数回落时回滚
- detect_stagnation：连续两轮分数不提升 → 建议引擎提前终止
"""


class IssueTracker:
    """包装 state.issue_tracker 列表，封装 issue 状态机规则。"""

    def __init__(self, items: list) -> None:
        self.items = items  # 直接引用 state 里的列表，改动即生效，不用回写

    def register(self, issues: list) -> None:
        """登记本轮提出的问题；已在跟踪表中的（编号相同）不重复登记。"""
        for issue in issues:
            iid = issue.get("id")
            if not any(t["id"] == iid for t in self.items):
                self.items.append({
                    "id": iid,
                    "description": str(issue.get("description", "")),
                    "status": "open",
                })

    def apply_decisions(self, decisions: list) -> None:
        """修复Agent 的决策写入生命周期：采纳→fixed，反驳→rejected。"""
        for d in decisions:
            for t in self.items:
                if t["id"] == d.get("id"):
                    t["status"] = "fixed" if d.get("action") == "fixed" else "rejected"

    def apply_verifications(self, verifications: list) -> None:
        """审查Agent 复审核销：resolved（已修好）/ unresolved（没修好）/ rejected_ok（反驳成立）。

        反驳被驳回（rejected → unresolved）的 issue 打上 rejection_failed 标记，
        修复Agent 不得再反驳同一 issue——防止两个 Agent 互相踢皮球。
        """
        for v in verifications:
            verdict = v.get("verdict")
            if verdict not in ("resolved", "unresolved", "rejected_ok"):
                continue
            for t in self.items:
                if t["id"] == v.get("id"):
                    if t["status"] == "rejected" and verdict == "unresolved":
                        t["rejection_failed"] = True
                    t["status"] = verdict

    def rejection_failed_ids(self) -> set:
        """曾被反驳且反驳被驳回的 issue 编号（修复Agent 对这些只能修不能再驳）。"""
        return {t["id"] for t in self.items if t.get("rejection_failed")}


class VersionLedger:
    """版本台账：始终跟着最高分走，分数回落就回滚到最优版本。"""

    def __init__(self, state) -> None:
        self.state = state  # 只读写 best_code / best_score / best_round 三个字段

    def record(self) -> bool:
        """记录当前版本评分；分数从历史最高回落时回滚到最优版本。返回是否发生了回滚。"""
        s = self.state
        if s.score > s.best_score:
            s.best_score, s.best_code, s.best_round = s.score, s.code, s.round
            return False
        if s.score < s.best_score and s.best_code and s.code != s.best_code:
            s.code = s.best_code
            return True
        return False


def detect_stagnation(score_history: list, score: int) -> bool:
    """连续两轮分数没有提升（且未满分）→ 再修也难有起色，建议提前终止。"""
    return (len(score_history) >= 2
            and score_history[-1] <= score_history[-2]
            and score < 100)
