"""工作流共享状态：Agent 之间通过它传递消息、共享数据。

设计要点：
- 状态共享：所有 Agent 读写同一个 WorkflowState（代码、评分、审查结论等）
- 消息传递：每个 Agent 的产出都会追加一条 AgentMessage，形成可追溯的消息流
- 断点恢复：state 可整体序列化为 JSON，崩溃后从最近检查点续跑
"""
import json
import os
import time
from dataclasses import asdict, dataclass, field

from core.lifecycle import IssueTracker, VersionLedger


@dataclass
class AgentMessage:
    """一条 Agent 间消息：谁发给谁、什么类型、内容摘要。"""
    sender: str
    receiver: str
    msg_type: str        # code / review / fixed_code / notice
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class WorkflowState:
    """贯穿整个工作流的共享状态。"""
    requirement: str = ""        # 用户的需求描述
    run_id: str = ""             # 本次运行编号（logs/runs/<run_id>/ 下归档 LLM 原始调用）
    ablation: dict = field(default_factory=dict)  # 机制开关（消融实验用），空字典 = 全开
    language: str = "python"
    code: str = ""               # 当前版本的代码（编码产出 / 修复后更新）
    review: dict = field(default_factory=dict)   # 最近一次审查结论（score/summary/issues）
    score: int = 0               # 最近一次评分
    round: int = 0               # 已完成的审查轮次
    score_history: list = field(default_factory=list)  # 每轮评分，画分数曲线用
    review_history: list = field(default_factory=list)  # 每轮完整审查结论（逐轮对比用）
    history_summary: str = ""    # 超过 3 轮后，旧轮次由 LLM 压缩成摘要存这里（上下文预算管理）
    checkpoints: list = field(default_factory=list)     # 编码Agent 交付的功能检查点（回归验证用）
    decisions: list = field(default_factory=list)       # 修复Agent 对审查意见的采纳/反驳记录
    issue_tracker: list = field(default_factory=list)   # issue 生命周期：open→fixed/rejected→resolved/...
    best_code: str = ""          # 历史最优版本的代码（回滚用）
    best_score: int = 0          # 历史最高分
    best_round: int = 0          # 最优版本出自第几轮
    force_finish: bool = False   # 节点可置位请求引擎提前终止（如分数停滞）
    messages: list = field(default_factory=list)       # AgentMessage 消息流
    current_node: str = ""       # 引擎调度用：下一个要执行的节点
    finished: bool = False

    def add_message(self, sender: str, receiver: str, msg_type: str, content: str) -> None:
        self.messages.append(AgentMessage(sender, receiver, msg_type, content))

    # ---- 版本回滚与 issue 生命周期：规则在 core/lifecycle.py，这里只做委托 ----
    # 数据（issue_tracker / best_* 字段）仍由本 dataclass 持有并参与序列化，
    # 行为挪出去后，state 只剩"存数据"，加新规则不用改状态结构。
    def record_version(self) -> bool:
        """记录当前版本评分；分数从历史最高回落时回滚到最优版本。返回是否发生了回滚。"""
        return VersionLedger(self).record()

    def register_issues(self, issues: list) -> None:
        """登记本轮提出的问题；已在跟踪表中的（编号相同）不重复登记。"""
        IssueTracker(self.issue_tracker).register(issues)

    def apply_decisions(self, decisions: list) -> None:
        """修复Agent 的决策写入生命周期：采纳→fixed，反驳→rejected。"""
        IssueTracker(self.issue_tracker).apply_decisions(decisions)

    def apply_verifications(self, verifications: list) -> None:
        """审查Agent 复审核销：resolved（已修好）/ unresolved（没修好）/ rejected_ok（反驳成立）。"""
        IssueTracker(self.issue_tracker).apply_verifications(verifications)

    def rejection_failed_ids(self) -> set:
        """曾被反驳且反驳被驳回的 issue 编号（修复Agent 对这些只能修不能再驳）。"""
        return IssueTracker(self.issue_tracker).rejection_failed_ids()

    # ---- 断点恢复：序列化 / 反序列化 ----
    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowState":
        data = dict(data)
        data["messages"] = [AgentMessage(**m) for m in data.get("messages", [])]
        return cls(**data)

    def save_checkpoint(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)

    @classmethod
    def load_checkpoint(cls, path: str) -> "WorkflowState":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
