"""消融实验：逐条关掉核心机制，跑同一份需求，看系统退化多少。

想法很简单——机制有没有用，把它拆掉跑一遍就知道：
    python run_ablation.py              # 跑全部 6 组
    python run_ablation.py --only no_anchor,no_contract
    python run_ablation.py --list       # 只看有哪些组

每组一次独立运行（独立日志和归档），结束后在控制台打汇总表，
并写一份 Markdown 报告到 reports/ablation_report.md，方便直接贴进 README。
需要 .env 里有可用的 API Key（每组都要真实调用 LLM，会消耗额度）。
"""
import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# 每个组 = 关掉哪个机制。baseline 什么都不关，作为对照。
GROUPS = {
    "baseline": {},
    "no_anchor": {"rule_anchor": False},          # 评分锚点：LLM 打分不受规则引擎约束
    "no_evidence": {"evidence_gate": False},      # 反驳证据约束：什么反驳都收
    "no_contract": {"contract_check": False},     # 契约校验：输出格式乱也不管
    "no_checkpoint": {"checkpoint_verify": False},  # 检查点验证：修复后不回归
    "no_pingpong": {"anti_pingpong": False},      # 防踢皮球：允许反复反驳同一条
}

GROUP_LABEL = {
    "baseline": "完整系统",
    "no_anchor": "- 评分锚点",
    "no_evidence": "- 反驳证据约束",
    "no_contract": "- 契约校验",
    "no_checkpoint": "- 检查点验证",
    "no_pingpong": "- 防踢皮球",
}


def load_requirement(path: str) -> str:
    if not os.path.exists(path):
        sys.exit(f"[错误] 需求文件不存在：{path}")
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        sys.exit(f"[错误] 需求文件是空的：{path}")
    return text


def run_group(name: str, requirement: str) -> dict:
    """跑一组实验，返回这组的关键指标。组内崩了也算一种结果，记录下来继续下一组。"""
    from main import CHECKPOINT_PATH, build_engine
    from core.logger import WorkflowLogger
    from core.state import WorkflowState

    with open(os.path.join("config", "workflow.json"), encoding="utf-8") as f:
        config = json.load(f)
    config["ablation"].update(GROUPS[name])

    logger = WorkflowLogger(log_dir=os.path.join("logs", "ablation"))
    run_id = f"ablation_{name}_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir = os.path.join("logs", "runs", run_id)

    print("\n" + "#" * 60)
    print(f"# 实验组 [{name}]：{GROUP_LABEL[name]}")
    print("#" * 60)

    state = WorkflowState(requirement=requirement, run_id=run_id)
    started = time.time()
    try:
        engine = build_engine(logger, run_dir, config_override=config)
        state = engine.run(state)
    except Exception as e:  # 实验脚本，任何异常都记下来当结果，不中断后续组
        return {"group": name, "ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        # 清理检查点，别影响下一组
        if os.path.exists(CHECKPOINT_PATH):
            os.remove(CHECKPOINT_PATH)

    duration = time.time() - started
    llm_calls = 0
    archive = os.path.join(run_dir, "llm_calls.jsonl")
    if os.path.exists(archive):
        with open(archive, encoding="utf-8") as f:
            llm_calls = sum(1 for line in f if line.strip())

    # 从日志里数关键事件：锚点纠偏几次、强制采纳几次
    texts = [r.get("text", "") for r in logger.records if r.get("event") == "info"]
    anchored = sum(1 for t in texts if "评分锚点生效" in t)
    forced = sum(1 for t in texts if "强制按采纳处理" in t)

    return {
        "group": name,
        "ok": True,
        "rounds": state.round,
        "score": state.score,
        "best_score": state.best_score,
        "issues": len(state.issue_tracker),
        "llm_calls": llm_calls,
        "duration": round(duration, 1),
        "anchored": anchored,
        "forced": forced,
    }


def print_table(results: list) -> None:
    head = f"{'实验组':<16}{'结果':<8}{'终评':<8}{'最优':<8}{'轮数':<6}{'LLM调用':<10}{'耗时(s)':<10}"
    print("\n" + "=" * len(head))
    print("消融实验汇总")
    print("=" * len(head))
    print(head)
    print("-" * len(head))
    for r in results:
        if r.get("ok"):
            print(f"{r['group']:<16}{'完成':<8}{r['score']:<8}{r['best_score']:<8}"
                  f"{r['rounds']:<6}{r['llm_calls']:<10}{r['duration']:<10}")
        else:
            print(f"{r['group']:<16}{'运行失败':<8}{r['error']}")
    print("=" * len(head))


def write_report(results: list, requirement: str, path: str) -> None:
    lines = [
        "# 机制消融实验报告",
        "",
        f"统一需求：{requirement}",
        "",
        "| 实验组 | 结果 | 终评 | 最优 | 轮数 | LLM调用 | 耗时(s) | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("ok"):
            note = []
            if r["anchored"]:
                note.append(f"锚点纠偏 {r['anchored']} 次")
            if r["forced"]:
                note.append(f"强制采纳 {r['forced']} 次")
            lines.append(f"| {GROUP_LABEL[r['group']]} | 完成 | {r['score']} | {r['best_score']} "
                         f"| {r['rounds']} | {r['llm_calls']} | {r['duration']} | {'；'.join(note)} |")
        else:
            lines.append(f"| {GROUP_LABEL[r['group']]} | 运行失败 | - | - | - | - | - | {r['error']} |")
    lines += [
        "",
        "> 每组 = 同一需求、独立运行一次，只改一个机制开关，其余条件完全相同。",
        "> 完整系统作为对照；其余各组展示去掉该机制后的退化情况。",
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入：{path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="机制消融实验：拆掉一个机制跑一次，看系统退化多少")
    parser.add_argument("--only", default=None,
                        help="只跑指定组，逗号分隔，如 --only no_anchor,no_contract")
    parser.add_argument("--requirement", default=os.path.join("examples", "demo_requirement.txt"),
                        help="统一需求文件，默认 examples/demo_requirement.txt")
    parser.add_argument("--list", action="store_true", help="列出所有实验组后退出")
    args = parser.parse_args()

    if args.list:
        for name in GROUPS:
            print(f"{name:<16} 关闭机制：{GROUP_LABEL[name]}")
        return

    if not os.getenv("LLM_API_KEY") or os.getenv("LLM_API_KEY", "").startswith("sk-在这里"):
        sys.exit("[错误] 未检测到有效 API Key，请先配置 .env。")

    names = list(GROUPS)
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [n for n in wanted if n not in GROUPS]
        if unknown:
            sys.exit(f"[错误] 不存在的实验组：{unknown}（用 --list 查看全部）")
        names = wanted

    requirement = load_requirement(args.requirement)
    print(f"统一需求（{len(requirement)} 字）：{requirement[:60]}{'...' if len(requirement) > 60 else ''}")
    print(f"共 {len(names)} 组实验，预计消耗 {len(names)} 次完整运行的 API 额度。")

    results = []
    for name in names:
        results.append(run_group(name, requirement))
        time.sleep(2)  # 给 API 端点一点喘息，别一口气打满

    print_table(results)
    write_report(results, requirement, os.path.join("reports", "ablation_report.md"))


if __name__ == "__main__":
    main()
