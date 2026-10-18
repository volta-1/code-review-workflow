"""工作流执行日志：终端实时显示 + JSON 文件落盘，支撑状态追踪。"""
import json
import os
import time


class WorkflowLogger:
    """记录每个节点的执行过程：开始/结束/耗时/结果摘要。"""

    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, f"workflow_{time.strftime('%Y%m%d_%H%M%S')}.json")
        self.records = []

    def node_start(self, node_name: str, round_no: int) -> None:
        print(f"\n{'=' * 56}")
        print(f"▶ 节点 [{node_name}] 开始执行（第 {round_no + 1} 轮）")
        print(f"{'=' * 56}")
        self._current = {"node": node_name, "round": round_no + 1,
                         "start": time.strftime("%H:%M:%S")}

    def node_end(self, node_name: str, summary: str) -> None:
        duration = ""
        if hasattr(self, "_current"):
            self._current["summary"] = summary
            self.records.append(self._current)
        print(f"◀ 节点 [{node_name}] 完成：{summary}{duration}")

    def message(self, sender: str, receiver: str, msg_type: str) -> None:
        print(f"  📨 消息：{sender} → {receiver}（{msg_type}）")
        self.records.append({"event": "message", "from": sender,
                             "to": receiver, "type": msg_type})

    def info(self, text: str) -> None:
        print(f"  {text}")
        self.records.append({"event": "info", "text": text})

    def save(self) -> None:
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=1)
