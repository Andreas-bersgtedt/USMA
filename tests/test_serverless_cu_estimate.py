"""Tests for the serverless SQL → Fabric CU heuristic."""
from usma.modules.serverless_pools import cu_estimate


def test_cu_seconds_for_query_matches_user_baseline():
    """120 GB query running 100 s → 4 CU-seconds (per the agreed heuristic)."""
    assert cu_estimate.cu_seconds_for_query(
        data_scanned_gb=120.0, duration_seconds=100.0,
    ) == 4.0


def test_cu_seconds_for_query_handles_non_positive():
    assert cu_estimate.cu_seconds_for_query(data_scanned_gb=0, duration_seconds=10) == 0.0
    assert cu_estimate.cu_seconds_for_query(data_scanned_gb=10, duration_seconds=0) == 0.0
    assert cu_estimate.cu_seconds_for_query(data_scanned_gb=-1, duration_seconds=10) == 0.0


def test_constants_are_documented_baseline():
    assert cu_estimate.SERVERLESS_GB_PER_CU == 60.0
    assert cu_estimate.SERVERLESS_CU_PER_GB == 0.02


def test_project_serverless_cu_picks_peak_day():
    """Peak-day CU-hours should reflect the worst single day, not the sum."""
    # Day A: 120 GB-seconds → cu_s = 120 * 0.02/60 = 0.04 CU-s, cu_h ≈ 1.11e-5
    # Day B: 1024 MB-seconds * 3600 = 1 GB-hour, mb_seconds = 1024*3600
    #   gb_seconds = 3600, cu_s = 3600 * 0.02/60 = 1.2 CU-s, cu_h = 1.2/3600
    daily = [
        {"day": "2025-01-01", "mb_seconds": 120 * 1024},  # 120 GB-seconds
        {"day": "2025-01-02", "mb_seconds": 1024 * 3600},  # 1 GB-hour
    ]
    proj = cu_estimate.project_serverless_cu(daily)
    assert proj is not None
    assert proj.peak_day == "2025-01-02"
    assert proj.window_days == 2
    # cu_hours day B = (1024*3600/1024) * (0.02/60) / 3600 = 3600 * 0.000333 / 3600
    #                = 0.000333...
    assert abs(proj.peak_day_cu_hours - (0.02 / 60.0)) < 1e-9


def test_project_serverless_cu_returns_none_for_empty():
    assert cu_estimate.project_serverless_cu(None) is None
    assert cu_estimate.project_serverless_cu([]) is None
    assert cu_estimate.project_serverless_cu([{"day": "2025-01-01"}]) is None
