"""core/lifecycle.py 的离线测试：issue 状态机、版本回滚、停滞判定。"""
from core.lifecycle import IssueTracker, VersionLedger, detect_stagnation
from core.state import WorkflowState


def _state_with(code, score, round_no, best_score=0, best_code="", best_round=0):
    return WorkflowState(code=code, score=score, round=round_no,
                         best_score=best_score, best_code=best_code, best_round=best_round)


class TestIssueTracker:
    def test_register_no_duplicate(self):
        items = []
        t = IssueTracker(items)
        t.register([{"id": 1, "description": "a"}])
        t.register([{"id": 1, "description": "a 又来了"}])
        assert len(items) == 1  # 同编号不重复登记

    def test_decision_and_verification_flow(self):
        items = []
        t = IssueTracker(items)
        t.register([{"id": 1}, {"id": 2}])
        t.apply_decisions([{"id": 1, "action": "fixed"}, {"id": 2, "action": "rejected"}])
        assert items[0]["status"] == "fixed"
        assert items[1]["status"] == "rejected"
        # 反驳被驳回 → 打 rejection_failed 标记
        t.apply_verifications([{"id": 2, "verdict": "unresolved"}])
        assert items[1]["rejection_failed"] is True
        assert t.rejection_failed_ids() == {2}

    def test_rejected_ok_clears_nothing(self):
        items = []
        t = IssueTracker(items)
        t.register([{"id": 1}])
        t.apply_decisions([{"id": 1, "action": "rejected"}])
        t.apply_verifications([{"id": 1, "verdict": "rejected_ok"}])
        assert items[0]["status"] == "rejected_ok"
        assert t.rejection_failed_ids() == set()

    def test_invalid_verdict_ignored(self):
        items = []
        t = IssueTracker(items)
        t.register([{"id": 1}])
        t.apply_verifications([{"id": 1, "verdict": "随便写"}])
        assert items[0]["status"] == "open"


class TestVersionLedger:
    def test_new_record_becomes_best(self):
        s = _state_with(code="v2", score=80, round_no=2, best_score=70,
                        best_code="v1", best_round=1)
        assert VersionLedger(s).record() is False
        assert s.best_code == "v2" and s.best_score == 80

    def test_drop_rolls_back(self):
        s = _state_with(code="v3", score=60, round_no=3, best_score=85,
                        best_code="v2", best_round=2)
        assert VersionLedger(s).record() is True
        assert s.code == "v2"  # 回滚到最优版本

    def test_same_score_no_rollback(self):
        s = _state_with(code="v2改", score=85, round_no=3, best_score=85,
                        best_code="v2", best_round=2)
        assert VersionLedger(s).record() is False
        assert s.code == "v2改"  # 同分不回落，保留新版本


class TestStagnation:
    def test_stagnant_when_not_improving(self):
        assert detect_stagnation([80, 80], 80) is True
        assert detect_stagnation([90, 85], 85) is True

    def test_not_stagnant_when_improving_or_perfect(self):
        assert detect_stagnation([60, 85], 85) is False
        assert detect_stagnation([100, 100], 100) is False  # 满分不终止
        assert detect_stagnation([85], 85) is False          # 不足两轮不判
