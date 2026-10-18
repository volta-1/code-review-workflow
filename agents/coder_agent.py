"""编码Agent：根据需求生成代码，三重自检全部通过后才交付。

三重自检（任一不过就把失败原因喂回 LLM 重来）：
1. 能跑——子进程真实执行，退出码为 0
2. 自查门——规则引擎扫描，不命中 high 级规则（低级 bug 源头拦截，不甩给下游）
3. 检查点自验——结构化检查点由确定性模板编译成断言脚本真实执行，
   断言强度不依赖 LLM 自觉（不允许手写脚本混进恒真式）
"""
from agents.base import AgentNode
from agents.prompts import CODER_FIX_TEMPLATE, CODER_SYSTEM, CODER_TEMPLATE
from core.llm import extract_json
from core.schema import SchemaError, validate_coder_output
from core.state import WorkflowState
from tools.checkpoint_runner import build_verify_script
from tools.code_runner import python_code_executor, python_module_executor
from tools.rule_scanner import scan_findings

MAX_SELF_TEST = 2  # 生成失败最多自我修正 1 次（共尝试 2 次）


class CoderAgent(AgentNode):
    """职责：需求 → 能跑、自查干净、检查点已验证的代码。"""

    node_id = "coder"
    name = "编码Agent"

    def run(self, state: WorkflowState) -> WorkflowState:
        code, description, checkpoints = self._generate_with_self_test(state)
        state.code = code
        state.checkpoints = checkpoints
        self.send(state, "审查Agent", "code",
                  f"已交付代码（{len(code.splitlines())} 行）：{description}；"
                  f"{len(checkpoints)} 条功能检查点已通过自验")
        self.logger.info(f"交付代码 {len(code.splitlines())} 行，三重自检全部通过，"
                         f"功能检查点 {len(checkpoints)} 条（已验证）")
        return state

    def _generate_with_self_test(self, state: WorkflowState) -> tuple[str, str, list]:
        """生成代码并跑三重自检；失败则带失败原因让 LLM 修正一次。"""
        user_msg = CODER_TEMPLATE.format(requirement=state.requirement)
        for attempt in range(1, MAX_SELF_TEST + 1):
            data = extract_json(self.llm.chat(CODER_SYSTEM, user_msg))
            try:
                parsed = validate_coder_output(data)  # 契约校验：不合约直接判失败
            except SchemaError as e:
                raise RuntimeError(f"编码Agent 输出不符合契约（{e}），请重试。")
            code = parsed["code"]

            failure = self._self_check(code, parsed["checkpoints"],
                                       state.ablation.get("checkpoint_verify", True))
            if failure is None:
                return code, parsed["description"], parsed["checkpoints"]
            self.logger.info(f"自检未通过（{failure.splitlines()[0]}），"
                             f"{'带回原因自我修正' if attempt < MAX_SELF_TEST else '自检次数用尽'}")
            user_msg = CODER_FIX_TEMPLATE.format(
                requirement=state.requirement, code=code, error=failure)
        raise RuntimeError("编码Agent 两次生成的代码都未通过三重自检，请调整需求描述后重试。")

    def _self_check(self, code: str, checkpoints: list, verify_checkpoints: bool = True) -> str | None:
        """三重自检：能跑 → 无 high 级规则命中 → 检查点断言全过。通过返回 None，否则返回失败原因。
        verify_checkpoints=False 时跳过第三重（消融实验 no_checkpoint 组）。"""
        # 第一重：能跑
        run_result = python_code_executor(code)
        self.logger.info(f"自检 1/3 运行验证：{run_result.splitlines()[1]}")
        if "退出码：0" not in run_result:
            return f"代码运行失败：\n{run_result}"

        # 第二重：自查门——high 级问题在源头拦截
        highs = [(line, rule, desc) for line, sev, rule, desc in scan_findings(code)
                 if sev == "high"]
        if highs:
            detail = "\n".join(f"第 {line} 行 [{rule}] {desc}" for line, rule, desc in highs)
            self.logger.info(f"自检 2/3 规则自查：命中 {len(highs)} 条 high 级规则，打回修正")
            return f"规则引擎自查发现 high 级问题，请改用安全写法：\n{detail}"
        self.logger.info("自检 2/3 规则自查：无 high 级问题")

        # 第三重：检查点自验——确定性模板编译检查点，真实执行
        if checkpoints and verify_checkpoints:
            script = build_verify_script(checkpoints)
            verify_result = python_module_executor(code, script)
            self.logger.info(f"自检 3/3 检查点自验：{verify_result.splitlines()[1]}")
            if "退出码：0" not in verify_result:
                return f"功能检查点自验失败，请修正代码或检查点：\n{verify_result}"
        return None
