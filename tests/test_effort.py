"""Unit tests for the configurable effort rate-card + estimator (v2.10)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from usma.effort import (
    DEFAULT_CARD,
    build_estimates,
    build_rollup,
    days_from_hours,
    default_card,
    dump_card,
    load_card,
    merge_with_default,
)
from usma.modules.fabric_mapping.models import (
    Recommendation,
    RunbookStep,
)


def test_default_card_loads_and_round_trips(tmp_path: Path) -> None:
    card = default_card()
    assert card.version == 1
    assert card.team_velocity == 1.0
    assert "dedicated_pools.tables" in card.rules
    out = tmp_path / "card.json"
    dump_card(card, out)
    reloaded = load_card(out)
    assert reloaded.to_dict() == card.to_dict()


def test_merge_with_default_keeps_other_rules_intact() -> None:
    merged = merge_with_default({
        "team_velocity": 2.0,
        "rules": {
            "dedicated_pools.tables": {"hours_per_table": 0.5},
        },
    })
    assert merged.team_velocity == 2.0
    # Overridden coefficient
    assert merged.rules["dedicated_pools.tables"].units["hours_per_table"] == 0.5
    # Unrelated rule remained at its shipped value
    assert "hours_per_blocker" in merged.rules["dedicated_pools.tsql_surface"].units


def test_load_card_rejects_unsupported_version(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_card(p)


def test_load_card_rejects_non_object(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_card(p)


def test_load_card_env_var_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "custom.json"
    p.write_text(json.dumps({"team_velocity": 3.0}), encoding="utf-8")
    monkeypatch.setenv("SMA_EFFORT_CARD", str(p))
    monkeypatch.chdir(tmp_path)
    card = load_card(None)
    assert card.team_velocity == 3.0


def _rec(rec_id: str, area: str, severity: str = "warning") -> Recommendation:
    return Recommendation(
        id=rec_id, area=area, title=f"t-{rec_id}", severity=severity,
        effort="medium", detail="d", target=None, fabric_action="a",
    )


def _step(order: int, phase: str, rec_id: str, effort: str = "medium") -> RunbookStep:
    return RunbookStep(
        phase=phase, order=order, title="t", detail="d",
        severity="warning", effort=effort, source_recommendation_id=rec_id,
    )


def test_estimator_applies_per_table_coefficient() -> None:
    rec = _rec("r1", "dedicated_pools.tables")
    step = _step(1, "data_plane_prep", "r1")
    loaded = {"dedicated_pools": {"pools": [{"tables": [{}] * 20}]}}
    card = default_card()
    estimates = build_estimates([step], [rec], loaded, card)
    # 20 tables × 0.25 h = 5 h. No phase steps (only 1 in this phase): + 16 / 1
    # for the data_plane_prep base; team_velocity is 1.
    assert estimates[0].p50_hours == pytest.approx(5.0 + 16.0, rel=1e-3)
    assert estimates[0].p90_hours == pytest.approx(
        (5.0 + 16.0) * card.confidence_p50_to_p90_multiplier, rel=1e-3,
    )
    assert estimates[0].breakdown.area == "dedicated_pools.tables"
    assert any("tables" in c for c in estimates[0].breakdown.components)


def test_estimator_caps_runaway_totals() -> None:
    rec = _rec("r1", "dedicated_pools.tables")
    step = _step(1, "data_plane_prep", "r1")
    # 10_000 tables × 0.25 h = 2500 h; cap is 80 h.
    loaded = {"dedicated_pools": {"pools": [{"tables": [{}] * 10000}]}}
    card = default_card()
    estimates = build_estimates([step], [rec], loaded, card)
    # cap (80) + phase base (16)
    assert estimates[0].p50_hours == pytest.approx(80.0 + 16.0, rel=1e-3)
    assert estimates[0].breakdown.capped is True


def test_estimator_qualitative_fallback_for_unknown_area() -> None:
    rec = _rec("r1", "completely.unknown.area")
    step = _step(1, "data_plane_prep", "r1", effort="high")
    card = default_card()
    estimates = build_estimates([step], [rec], loaded={}, card=card)
    # No rule, no phase step share for an unknown phase; phase IS data_plane_prep
    # which has a base of 16 h split across 1 step -> 16 h, no fallback needed.
    # But area_share is 0 and phase_share 16 -> p50 = 16. Fallback won't fire.
    assert estimates[0].p50_hours > 0
    # Now a step in a phase with no base hours and unknown area triggers fallback.
    card2 = default_card()
    card2.phases.clear()
    estimates2 = build_estimates([step], [rec], loaded={}, card=card2)
    assert estimates2[0].breakdown.fallback_used is True
    assert estimates2[0].p50_hours == pytest.approx(
        DEFAULT_CARD["qualitative"]["high"], rel=1e-3,
    )


def test_team_velocity_divides_p50() -> None:
    rec = _rec("r1", "dedicated_pools.tables")
    step = _step(1, "data_plane_prep", "r1")
    loaded = {"dedicated_pools": {"pools": [{"tables": [{}] * 10}]}}
    base = default_card()
    fast = merge_with_default({"team_velocity": 2.0})
    e_base = build_estimates([step], [rec], loaded, base)[0].p50_hours
    e_fast = build_estimates([step], [rec], loaded, fast)[0].p50_hours
    assert e_fast == pytest.approx(e_base / 2.0, rel=1e-3)


def test_rollup_sums_per_phase_and_total() -> None:
    steps = [
        _step(1, "foundation", "r1"),
        _step(2, "data_plane_prep", "r2"),
        _step(3, "data_plane_prep", "r3"),
    ]
    recs = [_rec("r1", "monitoring.dwu"), _rec("r2", "dedicated_pools.tables"),
            _rec("r3", "dedicated_pools.indexes")]
    loaded = {
        "dedicated_pools": {"pools": [{"tables": [{}] * 4, "indexes": [{}] * 2}]},
        "monitoring": {},
    }
    card = default_card()
    estimates = build_estimates(steps, recs, loaded, card)
    rollup = build_rollup(steps, estimates, card_source="default", card_version=1)
    assert len(rollup.per_phase) == 2
    assert rollup.per_phase[0].phase == "foundation"
    assert rollup.per_phase[1].phase == "data_plane_prep"
    assert rollup.per_phase[1].step_count == 2
    assert rollup.total_p50_hours == pytest.approx(
        sum(e.p50_hours for e in estimates), rel=1e-3,
    )


def test_days_from_hours_uses_8_hours_and_15_pct_spillage() -> None:
    assert days_from_hours(None) is None
    assert days_from_hours(0) == 0
    # 8 h => (8/8)*1.15 = 1.15 -> ceil -> 2 days
    assert days_from_hours(8) == 2
    # 16 h => 2.3 -> 3 days
    assert days_from_hours(16) == 3
    # 80 h => 11.5 -> 12 days
    assert days_from_hours(80) == 12
    # any positive sub-day amount rounds up to 1
    assert days_from_hours(0.5) == 1
