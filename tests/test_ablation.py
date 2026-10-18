"""消融开关的离线测试：不开 API，直接验证各机制的开关逻辑。"""
import pytest

import core.schema as schema
from core import schema as schema_mod
from core.engine import WorkflowEngine
from core.state import WorkflowState


class FakeLogger:
    """测试用的哑 logger，只记录 info 内容。"""
    def __init__(self):
        self.infos = []

    def info(self, text):
        self.infos.append(text)

    def node_start(self, *a):
        pass

    def node_end(self, *a):
        pass

    def message(self, *a):
        pass

    def save(self):
        pass


@pytest.fixture(autouse=True)
def reset_strict():
    yield
    schema.set_strict(True)  # 每个用例结束恢复严格模式，别影响其他测试


def test_contract_strict_on_raises():
    schema.set_strict(True)
    with pytest.raises(schema.SchemaError):
        schema.validate_review_output({"issues": []})  # 缺 score


def test_contract_lenient_tolerates():
    schema.set_strict(False)
    out = schema.validate_review_output({"score": 250, "issues": "不是数组"})
    assert out["score"] == 0  # 乱写的分数按 0 算，不抛错
    assert out["issues"] == []


def test_evidence_gate_on_filters_bare_rejection():
    schema.set_strict(True)
    data = {"code": "x = 1", "decisions": [
        {"id": 1, "action": "rejected", "reason": "不对"},  # 无理由，应被丢弃
        {"id": 2, "action": "rejected",
         "reason": "第 3 行的判断写反了，left > right 时才会触发，建议改为 >= 后重测"},
    ]}
    out = schema.validate_fixer_output(data)
    assert [d["id"] for d in out["decisions"]] == [2]


def test_evidence_gate_off_keeps_all():
    schema.set_strict(False)
    data = {"code": "x = 1", "decisions": [
        {"id": 1, "action": "rejected", "reason": "不对"},
    ]}
    out = schema.validate_fixer_output(data)
    assert len(out["decisions"]) == 1


def test_engine_injects_ablation_into_state():
    config = {"start": "END", "ablation": {"rule_anchor": False}}
    engine = WorkflowEngine(config, {}, FakeLogger())
    state = engine.run(WorkflowState())
    assert state.ablation == {"rule_anchor": False}
    assert any("消融模式" in t for t in engine.logger.infos)


def test_engine_empty_ablation_keeps_all_on():
    config = {"start": "END"}
    engine = WorkflowEngine(config, {}, FakeLogger())
    state = engine.run(WorkflowState())
    assert state.ablation == {}
    assert not any("消融模式" in t for t in engine.logger.infos)


def test_ablation_block_in_config_file():
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "workflow.json")
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    assert config["ablation"]["rule_anchor"] is True
    assert config["ablation"]["anti_pingpong"] is True
