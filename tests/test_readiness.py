"""Tests for the fabric_mapping readiness scorer."""
from usma.modules.fabric_mapping import readiness
from usma.modules.fabric_mapping.models import Recommendation


def _rec(severity, idx=0, effort="medium"):
    return Recommendation(
        id=f"r{idx}", area="x", title=f"t{idx}",
        severity=severity, effort=effort, detail="d",
    )


def test_all_info_is_ready():
    s = readiness.score_recommendations([_rec("info", i) for i in range(3)])
    assert s.score == 100
    assert s.bucket == "ready"


def test_three_blockers_drop_to_ready_with_effort():
    s = readiness.score_recommendations([_rec("blocker", i) for i in range(3)])
    # 100 - 30 = 70 → ready-with-effort
    assert s.score == 70
    assert s.bucket == "ready-with-effort"


def test_top_k_limits_blockers_returned():
    recs = [_rec("blocker", i, "high") for i in range(8)]
    s = readiness.score_recommendations(recs, top_k=5)
    assert len(s.top_blockers) == 5
