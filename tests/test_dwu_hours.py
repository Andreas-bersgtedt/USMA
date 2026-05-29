"""Tests for monitoring DWU-hours derivation."""
from datetime import datetime, timezone

from usma.modules.monitoring import dwu_hours


def _ts(h):
    return datetime(2025, 1, 1, h, 0, 0, tzinfo=timezone.utc)


def test_single_pool_single_day():
    series = [
        {
            "resource_name": "p1", "metric_name": "DWUUsedPercent",
            "points": [(_ts(h), 50.0) for h in range(0, 4)],
        },
        {
            "resource_name": "p1", "metric_name": "DWULimit",
            "points": [(_ts(h), 1000.0) for h in range(0, 4)],
        },
    ]
    out = dwu_hours.derive_dwu_days(series, interval="PT1H", active_threshold_pct=5.0)
    assert len(out) == 1
    d = out[0]
    assert d.pool_name == "p1"
    assert d.active_hours == 4.0
    # 4h * 1000 DWU * 50% = 2000 DWU-hours
    assert d.active_dwu_hours == 2000.0


def test_threshold_filters_inactive_intervals():
    series = [
        {
            "resource_name": "p1", "metric_name": "DWUUsedPercent",
            "points": [(_ts(0), 1.0), (_ts(1), 50.0)],
        },
        {
            "resource_name": "p1", "metric_name": "DWULimit",
            "points": [(_ts(0), 1000.0), (_ts(1), 1000.0)],
        },
    ]
    out = dwu_hours.derive_dwu_days(series, interval="PT1H", active_threshold_pct=5.0)
    assert out[0].active_hours == 1.0
