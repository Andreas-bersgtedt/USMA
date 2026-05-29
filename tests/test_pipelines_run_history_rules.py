"""Smoke tests for run-history-derived Fabric mapping recommendations."""
from __future__ import annotations

from usma.modules.fabric_mapping.rules import rules_for_pipelines


def _hist(by_pipeline: list[dict]) -> dict:
    return {
        "by_pipeline": by_pipeline,
        "fetched_run_count": sum(
            (w.get("run_count", 0) for p in by_pipeline for w in p.get("windows", [])),
            0,
        ),
    }


def _w(days: int, **kwargs) -> dict:
    base = {
        "window_days": days, "run_count": 0, "succeeded": 0, "failed": 0,
        "other": 0, "success_rate": None, "avg_duration_ms": None,
        "p95_duration_ms": None, "avg_data_moved_mb_per_run": None,
        "total_data_moved_mb": None,
    }
    base.update(kwargs)
    return base


def test_idle_pipeline_emits_recommendation() -> None:
    payload = {
        "pipelines": [{"name": "idle"}],
        "run_history": _hist([{
            "pipeline": "idle",
            "has_data_movement": False,
            "windows": [_w(7), _w(14), _w(28), _w(90)],
        }]),
    }
    recs = rules_for_pipelines(payload)
    ids = {r.id for r in recs}
    assert "pl.runs.idle" in ids


def test_low_success_rate_emits_recommendation() -> None:
    payload = {
        "pipelines": [{"name": "flaky"}],
        "run_history": _hist([{
            "pipeline": "flaky",
            "has_data_movement": False,
            "windows": [
                _w(7), _w(14),
                _w(28, run_count=10, succeeded=8, failed=2, success_rate=0.8),
                _w(90, run_count=10, succeeded=8, failed=2, success_rate=0.8),
            ],
        }]),
    }
    recs = rules_for_pipelines(payload)
    ids = {r.id for r in recs}
    assert "pl.runs.low_success.flaky" in ids


def test_heavy_data_mover_emits_recommendation() -> None:
    payload = {
        "pipelines": [{"name": "big"}],
        "run_history": _hist([{
            "pipeline": "big",
            "has_data_movement": True,
            "windows": [
                _w(7), _w(14),
                _w(28, run_count=5, succeeded=5, success_rate=1.0,
                   avg_data_moved_mb_per_run=4096.0,
                   total_data_moved_mb=20480.0),
                _w(90, run_count=5, succeeded=5, success_rate=1.0,
                   avg_data_moved_mb_per_run=4096.0,
                   total_data_moved_mb=20480.0),
            ],
        }]),
    }
    recs = rules_for_pipelines(payload)
    ids = {r.id for r in recs}
    assert "pl.runs.heavy_data_movement" in ids


def test_healthy_pipeline_emits_no_runtime_recommendations() -> None:
    payload = {
        "pipelines": [{"name": "good"}],
        "run_history": _hist([{
            "pipeline": "good",
            "has_data_movement": False,
            "windows": [
                _w(7, run_count=10, succeeded=10, success_rate=1.0),
                _w(14, run_count=20, succeeded=20, success_rate=1.0),
                _w(28, run_count=40, succeeded=40, success_rate=1.0),
                _w(90, run_count=120, succeeded=120, success_rate=1.0),
            ],
        }]),
    }
    recs = rules_for_pipelines(payload)
    ids = {r.id for r in recs}
    assert not any(rid.startswith("pl.runs.") for rid in ids)
