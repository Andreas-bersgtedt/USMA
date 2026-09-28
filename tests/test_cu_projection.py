"""Tests for the Fabric capacity (CU) projection."""
from datetime import datetime, timezone

import pytest

from usma.modules.fabric_mapping import cu_projection


def _ts(h):
    return datetime(2025, 1, 1, h, 0, 0, tzinfo=timezone.utc)


def test_returns_none_with_missing_data():
    assert cu_projection.project_capacity([]) is None


def test_picks_smallest_sku_covering_peak():
    series = [
        {
            "resource_name": "p1", "metric_name": "DWUUsedPercent",
            "points": [(_ts(0), 80.0)],
        },
        {
            "resource_name": "p1", "metric_name": "DWULimit",
            "points": [(_ts(0), 1000.0)],
        },
    ]
    proj = cu_projection.project_capacity(series, headroom_pct=30)
    assert proj is not None
    # peak active DWU = 1000 * 0.8 = 800. With 30% headroom → 1040. *0.020 → 20.8 CU.
    # Smallest F-SKU covering 20.8 CU is F32.
    assert proj.peak_dwu == 800.0
    assert proj.recommended_sku == "F32"
    # DW-only projection: spark + pipelines contributions are zero.
    assert proj.spark_cu_contribution == 0.0
    assert proj.pipelines_cu_contribution == 0.0


def test_returns_projection_from_spark_alone():
    """When the dedicated SQL pool isn't migrated yet but Spark Livy
    history is collected, the recommended SKU should still cover Spark.

    Steady state uses the average of all days, including zero-usage days."""
    spark = {
        "run_stats": [{
            "pool": "p1", "kind": "scheduled",
            "windows": [{
                "window_days": 7,
                "est_cu_hours_fabric_spark": 1200.0,  # window total
                "peak_day_cu_hours": 1200.0,           # all on one day
            }],
        }],
    }
    proj = cu_projection.project_capacity([], spark_payload=spark, headroom_pct=30)
    assert proj is not None
    assert proj.spark_steady_state_cu == pytest.approx(1200 / 7 / 24)
    assert proj.spark_cu_contribution == 9.29
    assert proj.dwu_cu_contribution == 0.0
    assert proj.recommended_sku == "F16"


def test_combines_dwu_spark_and_pipelines():
    """The recommended SKU must cover the SUM of all three workload
    classes that share a Fabric capacity, not just the largest."""
    series = [
        {"resource_name": "p1", "metric_name": "DWUUsedPercent", "points": [(_ts(0), 50.0)]},
        {"resource_name": "p1", "metric_name": "DWULimit", "points": [(_ts(0), 1000.0)]},
    ]
    spark = {
        "run_stats": [{
            "pool": "p1", "kind": "scheduled",
            "windows": [{
                "window_days": 7,
                "est_cu_hours_fabric_spark": 1680.0,
                "peak_day_cu_hours": 240.0,  # busiest day = 240 CU-hr
            }],
        }],
    }
    pipelines = {
        "run_history": {"by_pipeline": [{
            "windows": [{
                "window_days": 7,
                "est_cu_hours_from_diu": 168.0,
                "est_cu_hours_from_vcore": 168.0,
                "est_cu_hours_from_orchestration": 0.0,
                "peak_day_cu_hours": 48.0,  # busiest day = 48 CU-hr total
            }],
        }]},
    }
    proj = cu_projection.project_capacity(
        series, spark_payload=spark, pipelines_payload=pipelines, headroom_pct=30,
    )
    assert proj is not None
    # DW: 500 * 0.020 = 10 CU (pre-headroom).
    # Spark: 240 / 24 = 10 CU sustained.
    # Pipelines: 48 / 24 = 2 CU sustained.
    # Total pre-headroom = 22 CU. * 1.3 = 28.6 CU → F32.
    assert proj.dwu_cu_contribution == 13.0   # 10 * 1.3
    assert proj.spark_cu_contribution == 13.0  # 10 * 1.3
    assert proj.pipelines_cu_contribution == 2.6  # 2 * 1.3
    assert proj.estimated_cu == 28.6
    assert proj.recommended_sku == "F32"


def test_legacy_payload_without_peak_day_falls_back():
    """Historical totals use the same average, with an explicit caveat."""
    spark = {
        "run_stats": [{
            "pool": "p1", "kind": "scheduled",
            "windows": [{
                "window_days": 7,
                "est_cu_hours_fabric_spark": 1680.0,
                # no peak_day_cu_hours — legacy artefact
            }],
        }],
    }
    proj = cu_projection.project_capacity([], spark_payload=spark, headroom_pct=30)
    assert proj is not None
    assert proj.spark_cu_contribution == 13.0
    assert proj.recommended_sku == "F16"
    assert any("Historical" in warning for warning in proj.spark_accounting_warnings)


def _spark_entry(pool, kind="scheduled", days=7, total=16):
    return {
        "pool": pool, "kind": kind,
        "windows": [{
            "window_days": days, "est_cu_hours_fabric_spark": total,
            "known_usage_run_count": 1, "unknown_usage_run_count": 0,
        }],
    }


def test_spark_sums_pools_and_trigger_kinds_with_one_common_denominator():
    spark = {"run_stats": [
        _spark_entry("p1"), _spark_entry("p1", "interactive"), _spark_entry("p2"),
    ]}
    proj = cu_projection.project_capacity([], spark_payload=spark, headroom_pct=0)
    assert proj.spark_daily_cu_hours == pytest.approx(48 / 7)
    assert proj.spark_steady_state_cu == pytest.approx(48 / 7 / 24)
    assert proj.spark_peak_day_cu_hours is None


def test_spark_does_not_sum_overlapping_windows():
    entry = _spark_entry("p1")
    entry["windows"].append({"window_days": 28, "est_cu_hours_fabric_spark": 64})
    proj = cu_projection.project_capacity([], spark_payload={"run_stats": [entry]}, headroom_pct=0)
    assert proj.spark_steady_state_cu == pytest.approx(16 / 7 / 24)


def test_spark_incompatible_windows_do_not_produce_a_mixed_average(caplog):
    spark = {"run_stats": [_spark_entry("p1"), _spark_entry("p2", days=28)]}
    assert cu_projection.project_capacity([], spark_payload=spark) is None
    assert "no common observation window" in caplog.text


def test_spark_uses_shortest_common_window_when_seven_days_unavailable():
    spark = {"run_stats": [_spark_entry("p1", days=3), _spark_entry("p2", days=3)]}
    proj = cu_projection.project_capacity([], spark_payload=spark, headroom_pct=0)
    assert proj.spark_window_days == 3
    assert proj.spark_steady_state_cu == pytest.approx(32 / 3 / 24)


def test_spark_peak_is_separate_and_combines_matching_dates_only():
    spark = {
        "run_stats": [_spark_entry("p1"), _spark_entry("p2")],
        "observation_end": "2026-09-28T00:00:00Z",
        "collection_complete": True,
        "daily_usage": [
            {"day": "2026-09-21", "est_cu_hours_fabric_spark": 8},
            {"day": "2026-09-21", "est_cu_hours_fabric_spark": 16},
            {"day": "2026-09-22", "est_cu_hours_fabric_spark": 8},
            {"day": "2026-09-20", "est_cu_hours_fabric_spark": 1000},
            {"day": "2026-09-28", "est_cu_hours_fabric_spark": 1000},
        ],
    }
    proj = cu_projection.project_capacity([], spark_payload=spark, headroom_pct=30)
    assert proj.spark_peak_day_cu_hours == 24
    assert proj.spark_steady_state_cu == pytest.approx(32 / 7 / 24)
    assert proj.spark_cu_contribution == round(32 / 7 / 24 * 1.3, 2)


def test_incomplete_and_unknown_usage_are_exposed():
    entry = _spark_entry("p1")
    entry["windows"][0]["unknown_usage_run_count"] = 2
    proj = cu_projection.project_capacity(
        [], pipelines_payload={"run_history": {"by_pipeline": [{
            "windows": [{"window_days": 7, "peak_day_cu_hours": 24}],
        }]}}, spark_payload={
        "run_stats": [entry], "collection_complete": False,
        "accounting_warnings": ["Fixed-shape estimate, not measured allocation."],
    })
    assert proj.spark_cu_contribution == 0
    assert proj.estimated_cu == proj.pipelines_cu_contribution
    assert any("incomplete" in w for w in proj.spark_accounting_warnings)
    assert any("unknown consumption" in w for w in proj.spark_accounting_warnings)
    assert any("Fixed-shape" in w for w in proj.spark_accounting_warnings)


@pytest.mark.parametrize("incomplete,unknown", [(True, 0), (False, 1)])
def test_partial_spark_only_cannot_produce_a_capacity_recommendation(incomplete, unknown, caplog):
    entry = _spark_entry("p1")
    entry["windows"][0]["unknown_usage_run_count"] = unknown
    assert cu_projection.project_capacity([], spark_payload={
        "run_stats": [entry], "collection_complete": not incomplete,
    }) is None
    assert "Spark steady-state sizing unavailable" in caplog.text


@pytest.mark.parametrize("total", [-1, float("nan"), float("inf")])
def test_invalid_spark_totals_raise(total):
    with pytest.raises(ValueError, match="finite and non-negative"):
        cu_projection.project_capacity([], spark_payload={"run_stats": [_spark_entry("p1", total=total)]})


@pytest.mark.parametrize("missing", ["coverage", "average", "steady_state", "unknown_count"])
def test_new_contract_requires_coverage_and_available_averages(missing):
    entry = _spark_entry("p1")
    window = entry["windows"][0]
    window.update(avg_daily_cu_hours=16 / 7, steady_state_cu=16 / 7 / 24)
    payload = {
        "accounting_basis": "fixed_shape_estimate",
        "collection_complete": True, "run_stats": [entry],
    }
    assert cu_projection.project_capacity([], spark_payload=payload) is not None
    if missing == "coverage":
        payload["collection_complete"] = None
    else:
        key = {
            "average": "avg_daily_cu_hours", "steady_state": "steady_state_cu",
            "unknown_count": "unknown_usage_run_count",
        }[missing]
        window[key] = None
    assert cu_projection.project_capacity([], spark_payload=payload) is None


def test_returns_none_when_all_components_zero():
    proj = cu_projection.project_capacity(
        [],
        spark_payload={"run_stats": []},
        pipelines_payload={"run_history": {"by_pipeline": []}},
    )
    assert proj is None


def test_serverless_payload_contributes_cu():
    """Heuristic: 0.02 CU per 60 GB scanned, integrated over duration.

    Pick a day with ``mb_seconds = 259_200_000 * 1024`` so the arithmetic
    is round:
        gb_seconds = 259_200_000
        cu_seconds = gb_seconds * (0.02 / 60) = 86_400
        cu_hours   = 86_400 / 3600 = 24 CU-hr that day
        sustained  = 24 / 24 = 1.0 CU
    With 30 % headroom and no other component:
        serverless_cu_contribution = 1.3 CU, estimated_cu = 1.3 CU → F2.
    """
    serverless = {
        "daily_usage": [
            {"day": "2025-01-01", "mb_seconds": 259_200_000 * 1024,
             "data_processed_mb": 0, "request_count": 1},
            {"day": "2025-01-02", "mb_seconds": 0,
             "data_processed_mb": 0, "request_count": 0},
        ],
    }
    proj = cu_projection.project_capacity(
        [], serverless_payload=serverless, headroom_pct=30,
    )
    assert proj is not None
    assert proj.dwu_cu_contribution == 0.0
    assert proj.serverless_cu_contribution == 1.3
    assert proj.serverless_peak_day_cu_hours == 24.0
    assert proj.estimated_cu == 1.3
    assert proj.recommended_sku == "F2"


def test_serverless_with_zero_mb_seconds_is_ignored():
    """Pre-v2.7 serverless artifacts have no mb_seconds; the projection
    should treat them as zero contribution (no CU added)."""
    serverless = {
        "daily_usage": [
            {"day": "2025-01-01", "data_processed_mb": 1024 * 1024, "request_count": 100},
        ],
    }
    proj = cu_projection.project_capacity(
        [], serverless_payload=serverless, headroom_pct=30,
    )
    assert proj is None
