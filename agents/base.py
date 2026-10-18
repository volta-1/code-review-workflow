"""Agent 节点基类：所有工作流节点的统一接口。

新增一个 Agent 只需三步：
1. 继承 AgentNode，实现 run(state)
2. 在 main.py 的注册表里登记
3. 在 config/workflow.json 里给它接边
"""
from abc import ABC, abstractmethod

from core.llm import LLMClient
from core.state import WorkflowState


class AgentNode(ABC):
    """工作流节点：接收共享状态，完成自己的职责，写回状态并发消息。"""

    node_id: str = "node"   # 配置文件中引用的标识（英文）
    name: str = "节点"       # 日志与消息中显示的名字（中文）

    def __init__(self, llm: LLMClient, logger) -> None:
        self.llm = llm
        self.logger = logger

    @abstractmethod
    def run(self, state: WorkflowState) -> WorkflowState:
        """执行节点职责，返回更新后的状态。"""

    def send(self, state: WorkflowState, receiver: str, msg_type: str, content: str) -> None:
        """向消息流追加一条消息并写日志。"""
        state.add_message(self.name, receiver, msg_type, content)
        self.logger.message(self.name, receiver, msg_type)
