"""Tests for the Fabric capacity (CU) projection."""
from datetime import datetime, timezone

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

    Sizing is driven by the busiest UTC day inside the window (Fabric's
    capacity smoothing window is 24h)."""
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
    # peak day 1200 CU-hr / 24h = 50 CU sustained. * 1.3 → 65 CU → F128.
    assert proj.spark_cu_contribution == 65.0
    assert proj.dwu_cu_contribution == 0.0
    assert proj.recommended_sku == "F128"


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
    """Pre-v2.6.3 spark_pools.json has no ``peak_day_cu_hours``. The
    projection should still produce a sensible (conservative) number by
    estimating the peak day as roughly twice the window average."""
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
    # Fallback peak-day = 1680 / (7/2) = 480 CU-hr → 20 CU → *1.3 = 26 CU → F32.
    assert proj.spark_cu_contribution == 26.0
    assert proj.recommended_sku == "F32"


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
