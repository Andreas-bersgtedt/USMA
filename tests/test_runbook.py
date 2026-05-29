"""Tests for the migration runbook builder."""
from usma.modules.fabric_mapping import runbook
from usma.modules.fabric_mapping.models import Recommendation


def _rec(area, severity="warning"):
    return Recommendation(id=f"x.{area}", area=area, title="t",
                          severity=severity, effort="medium", detail="d")


def test_phase_ordering():
    recs = [
        _rec("pipelines.activities"),
        _rec("dedicated_pools.tables"),
        _rec("monitoring.dwu"),
        _rec("spark_pools.notebooks"),
        _rec("serverless_pools.cost"),
    ]
    steps = runbook.build_runbook(recs)
    phases = [s.phase for s in steps]
    # Foundation should appear before data_plane_prep, etc.
    assert phases.index("foundation") < phases.index("data_plane_prep")
    assert phases.index("data_plane_prep") < phases.index("ingest_shortcuts")
    assert phases.index("ingest_shortcuts") < phases.index("compute_migration")
    assert phases.index("compute_migration") < phases.index("orchestration_migration")


def test_verification_step_always_present():
    steps = runbook.build_runbook([])
    assert steps and steps[-1].phase == "verification"


def test_verification_has_rollback_hint():
    steps = runbook.build_runbook([])
    assert steps[-1].rollback
