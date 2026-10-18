"""用真实引擎 + Mock 节点生成一份演示日志，供可视化页面内嵌。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.engine import WorkflowEngine
from core.state import WorkflowState


class MockAgent:
    """模拟真实 Agent 的行为序列：干活、写状态、发消息。"""
    def __init__(self, node_id, name, script):
        self.node_id = node_id
        self.name = name
        self.script = script  # list of fn(state) -> state

    def run(self, state):
        fn = self.script.pop(0) if self.script else (lambda s: s)
        return fn(state)


def coder_run(logger):
    def run(s):
        s.add_message("编码Agent", "审查Agent", "code",
                      "已交付代码（42 行）：TODO 管理器；3 条功能检查点已通过自验")
        logger.message("编码Agent", "审查Agent", "code")
        logger.info("交付代码 42 行，三重自检全部通过，功能检查点 3 条（已验证）")
        return s
    return run


def reviewer_round(logger, scores, issues_log):
    def run(s):
        s.round += 1
        raw = scores[s.round - 1]
        logger.info(f"开始第 {s.round} 轮审查，调用 AST 解析与规则扫描工具...")
        if s.round == 2:
            # 演示评分锚点：LLM 想给 93，被规则引擎上限压回 91
            logger.info("评分锚点生效：LLM 给出 93 分，但规则引擎扣分上限为 91 分，按上限计")
        s.score = raw
        s.score_history.append(s.score)
        s.add_message("审查Agent", "工作流", "review",
                      f"第 {s.round} 轮审查完成：{s.score} 分，发现 {issues_log[s.round - 1]} 个问题")
        logger.message("审查Agent", "工作流", "review")
        logger.info(f"评分 {s.score}/100，问题 {issues_log[s.round - 1]} 个")
        return s
    return run


def fixer_run(logger):
    def run(s):
        logger.info("意见 #1：✅ 采纳——改用参数化查询，字符串拼接有注入风险")
        logger.info("意见 #2：🚫 反驳——第 18 行的下标判断写反了，left > right 时不会触发，"
                    "建议先补一个边界用例再定级")
        s.add_message("修复Agent", "审查Agent", "fix_decisions",
                      "修复完成：改用参数化查询；采纳 2 条，反驳 1 条（附代码引用）；"
                      "运行验证与回归验证均通过")
        logger.message("修复Agent", "审查Agent", "fix_decisions")
        logger.info("运行验证：退出码：0")
        logger.info("回归验证：退出码：0（3 条功能检查点全过）")
        return s
    return run


def main():
    from core.logger import WorkflowLogger
    logger = WorkflowLogger(log_dir="/tmp/vizlog")
    with open("config/workflow.json", encoding="utf-8") as f:
        config = json.load(f)

    registry = {
        "coder": MockAgent("coder", "编码Agent", [coder_run(logger)]),
        "reviewer": MockAgent("reviewer", "审查Agent",
                              [reviewer_round(logger, [62, 91], [4, 1])] * 2),
        "fixer": MockAgent("fixer", "修复Agent", [fixer_run(logger)]),
    }
    state = WorkflowState(requirement="写一个命令行 TODO 管理器，支持添加/列出/完成任务")
    engine = WorkflowEngine(config, registry, logger, checkpoint_path="/tmp/vizlog/checkpoint.json")
    state = engine.run(state)
    logger.save()

    # 打包成页面需要的完整数据：日志事件 + 最终状态摘要
    payload = {
        "records": logger.records,
        "requirement": state.requirement,
        "score_history": state.score_history,
        "final_score": state.score,
        "rounds": state.round,
        "messages": [
            {"sender": m.sender, "receiver": m.receiver, "msg_type": m.msg_type, "content": m.content}
            for m in state.messages
        ],
    }
    with open("/tmp/vizlog/demo_run.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("OK", len(logger.records), "records")


if __name__ == "__main__":
    main()
