"""命令行入口：python main.py run [需求描述]

流程：编码Agent → 审查Agent →（分数<85）→ 修复Agent → 回到审查Agent ……
      直到评分达标或达到循环上限，输出最终报告。
"""
import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

CHECKPOINT_PATH = os.path.join("logs", "checkpoint.json")
DEFAULT_REQUIREMENT_FILE = os.path.join("examples", "demo_requirement.txt")


def build_engine(logger, run_dir: str | None = None, config_override: dict | None = None) -> "WorkflowEngine":
    """组装工作流：注册节点 + 加载流程配置。新增 Agent 在这里登记。
    config_override 传入时用它替代配置文件（消融实验改机制开关用）。"""
    from agents.coder_agent import CoderAgent
    from agents.fixer_agent import FixerAgent
    from agents.reviewer_agent import ReviewerAgent
    from core import schema
    from core.engine import WorkflowEngine
    from core.llm import LLMClient

    if config_override is not None:
        config = config_override
    else:
        with open(os.path.join("config", "workflow.json"), encoding="utf-8") as f:
            config = json.load(f)
    # 契约校验是全局开关（schema 模块级），跑消融 no_contract 组时在这里关掉
    schema.set_strict(config.get("ablation", {}).get("contract_check", True))

    llm = LLMClient(run_dir=run_dir)
    registry = {}
    for node_cls in (CoderAgent, ReviewerAgent, FixerAgent):
        node = node_cls(llm, logger)
        registry[node.node_id] = node
    return WorkflowEngine(config, registry, logger, CHECKPOINT_PATH)


def cmd_run(requirement: str, resume: bool, threshold: int, max_loops: int | None) -> None:
    import time

    from core.logger import WorkflowLogger
    from core.state import WorkflowState

    logger = WorkflowLogger()
    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join("logs", "runs", run_id)  # 本次运行的完整归档目录

    if resume:
        if not os.path.exists(CHECKPOINT_PATH):
            sys.exit("[错误] 没有找到检查点文件 logs/checkpoint.json，无法续跑。")
        state = WorkflowState.load_checkpoint(CHECKPOINT_PATH)
        print(f"===== 从检查点恢复：下一步执行 [{state.current_node}]，已完成 {state.round} 轮审查 =====")
    else:
        state = WorkflowState(requirement=requirement, run_id=run_id)
        print(f"===== 多 Agent 代码审查工作流启动 =====")
        print(f"需求：{requirement}")
        print(f"达标分数线：{threshold}（低于此分数将进入修复-复审循环）")

    try:
        engine = build_engine(logger, run_dir)
    except ValueError as e:
        sys.exit(f"[配置错误] 工作流配置不合法：{e}\n请检查 config/workflow.json 后重试。")
    except RuntimeError as e:
        print(f"[配置错误] {e}")
        print("操作步骤：")
        print("  1. 复制配置模板：cp .env.example .env")
        print("  2. 编辑 .env，把 LLM_API_KEY 改成你的 Key")
        print("  3. 重新运行：python main.py run \"你的需求\"")
        sys.exit(1)

    if max_loops is not None:
        engine.max_loops = max_loops  # 命令行覆盖配置里的循环上限，不用改配置文件

    # 条件分支的阈值从命令行注入流程配置
    for edge in engine.config["edges"]:
        if edge.get("condition"):
            field = edge["condition"].split(">=")[0].strip()
            edge["condition"] = f"{field} >= {threshold}"

    try:
        state = engine.run(state)
    except RuntimeError as e:
        sys.exit(f"[错误] 工作流执行失败：{e}\n提示：可运行 python main.py run --resume 从检查点续跑")

    # ---- 汇总输出 ----
    print(f"\n{'=' * 56}")
    print("===== 工作流执行完毕 =====")
    print(f"评分变化：{' → '.join(str(s) for s in state.score_history)}")
    print(f"最终评分：{state.score} / 100")
    print(f"最终评价：{state.review.get('summary', '')}")
    print(f"Agent 间消息：共 {len(state.messages)} 条")

    from report.report_generator import generate_workflow_report
    report_path = generate_workflow_report(state, run_dir=run_dir)
    print(f"\n✅ Markdown 工作流报告已生成：{report_path}")
    print(f"📋 执行日志：{logger.log_path}")
    if os.path.exists(os.path.join(run_dir, "llm_calls.jsonl")):
        print(f"🗂  运行归档（LLM 原始调用记录）：{run_dir}/llm_calls.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="多 Agent 代码审查工作流：编码 → 审查 ⇄ 修复")
    sub = parser.add_subparsers(dest="command")
    p_run = sub.add_parser("run", help="启动工作流")
    p_run.add_argument("requirement", nargs="?", default=None,
                       help="需求描述（缺省使用内置示例需求）")
    p_run.add_argument("--resume", action="store_true", help="从上次检查点断点续跑")
    p_run.add_argument("--threshold", type=int, default=85, help="达标分数线，默认 85")
    p_run.add_argument("--max-loops", type=int, default=None,
                       help="单个节点的循环上限（覆盖 config/workflow.json 的 max_loops）")
    args = parser.parse_args()

    if args.command == "run":
        if args.max_loops is not None and args.max_loops < 1:
            sys.exit("[错误] --max-loops 必须是正整数。")
        requirement = args.requirement
        if not requirement:
            if not os.path.exists(DEFAULT_REQUIREMENT_FILE):
                sys.exit(f"[错误] 未提供需求，且示例文件不存在：{DEFAULT_REQUIREMENT_FILE}")
            with open(DEFAULT_REQUIREMENT_FILE, encoding="utf-8") as f:
                requirement = f.read().strip()
        if not requirement:
            sys.exit("[错误] 需求描述不能为空。")
        cmd_run(requirement, args.resume, args.threshold, args.max_loops)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
