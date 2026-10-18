"""工作流引擎：按配置调度 Agent 节点，支持顺序 / 条件分支 / 循环 / 断点恢复。

调度模型：流程被定义为「节点 + 有向边」的图，引擎从起点开始，
每执行完一个节点，就从它的出边里选第一条条件成立的边走，
走到 END 伪节点结束。单个节点被执行次数超过 max_loops 时强制终止，
防止修复-审查循环死循环。
"""
from core.conditions import evaluate
from core.state import WorkflowState

END = "END"


class WorkflowEngine:
    """配置驱动的工作流引擎：新增 Agent = 注册节点 + 配置里加边。"""

    def __init__(self, config: dict, registry: dict, logger,
                 checkpoint_path: str = "logs/checkpoint.json") -> None:
        """
        config: {"start": "coder", "max_loops": 3,
                 "edges": [{"from":..., "to":..., "condition": "score >= 85"(可选)}]}
        registry: {节点名: 节点对象}，节点对象须有 name 属性和 run(state) 方法
        """
        self.config = config
        self.registry = registry
        self.logger = logger
        self.max_loops = config.get("max_loops", 3)
        self.node_retries = config.get("node_retries", 1)  # 节点级重试次数（逻辑层失败，如 JSON 解析出错）
        self.ablation = config.get("ablation", {}) or {}   # 机制开关，空 = 全开
        self.checkpoint_path = checkpoint_path

    def _next_node(self, node_name: str, state: WorkflowState) -> str:
        """从当前节点的出边中选路：条件成立的优先，无条件的是默认边。"""
        edges = [e for e in self.config["edges"] if e["from"] == node_name]
        default = None
        for edge in edges:
            condition = edge.get("condition")
            if condition is None:
                default = edge["to"]
            elif evaluate(condition, state):
                self.logger.info(f"条件分支：{condition} 成立 → 走向 [{edge['to']}]")
                return edge["to"]
        if default is None:
            raise RuntimeError(f"节点 [{node_name}] 没有可走通的出边，流程配置不完整。")
        return default

    def _run_node_with_retry(self, node, state: WorkflowState) -> WorkflowState:
        """节点级重试：LLM 层的网络重试在 LLMClient 里，这里兜住逻辑层失败
        （如 LLM 输出 JSON 解析失败），让节点重跑一次再判死。

        重试前把本次 attempt 追加的消息回滚掉，避免消息流出现重复条目。
        """
        for attempt in range(1, self.node_retries + 2):
            msg_mark = len(state.messages)  # 副作用标记：重试时回滚到这里
            try:
                return node.run(state)
            except RuntimeError as e:
                del state.messages[msg_mark:]
                if attempt > self.node_retries:
                    raise
                self.logger.info(f"节点执行失败（{e}），{attempt}/{self.node_retries} 次重试...")

    def run(self, state: WorkflowState) -> WorkflowState:
        """从 state.current_node（或配置的起点）开始执行到 END。"""
        current = state.current_node or self.config["start"]
        visit_count = {}
        state.ablation = dict(self.ablation)  # 机制开关随状态走，节点里用 state.ablation 读
        closed = [k for k, v in self.ablation.items() if not v]
        if closed:
            self.logger.info(f"消融模式：已关闭机制 {closed}")
        self.logger.info(f"工作流启动，起点：[{current}]，循环上限：{self.max_loops} 次/节点")

        while current != END:
            if current not in self.registry:
                raise RuntimeError(f"流程配置引用了未注册的节点：{current}")
            visit_count[current] = visit_count.get(current, 0) + 1
            if visit_count[current] > self.max_loops:
                self.logger.info(
                    f"⚠️ 节点 [{current}] 已执行 {self.max_loops} 次，达到循环上限，强制结束")
                break

            state.current_node = current
            display = self.registry[current].name
            self.logger.node_start(display, visit_count[current] - 1)
            state = self._run_node_with_retry(self.registry[current], state)
            self.logger.node_end(display, "执行成功")
            self.logger.save()

            # 节点可请求提前终止（如审查Agent 检测到分数停滞）
            if getattr(state, "force_finish", False):
                self.logger.info("收到终止信号：分数停滞，提前结束工作流")
                break

            nxt = self._next_node(current, state)
            state.current_node = nxt
            state.save_checkpoint(self.checkpoint_path)  # 每个节点完成后存档
            self.logger.info(f"检查点已保存（下一步：{nxt}）")
            current = nxt

        state.finished = True
        state.save_checkpoint(self.checkpoint_path)
        self.logger.save()
        return state
