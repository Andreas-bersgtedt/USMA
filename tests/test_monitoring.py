"""Tests for monitoring reporting and series aggregation."""
from datetime import datetime, timezone
from pathlib import Path

from usma.modules.monitoring.models import MetricSeries, MonitoringAnalysis
from usma.modules.monitoring.monitor_client import build_series, default_window
from usma.modules.monitoring.reporting import write_reports


def test_build_series_aggregates_min_max_avg_p95() -> None:
    points = [
        (datetime(2025, 1, 1, h, tzinfo=timezone.utc), float(h * 10))
        for h in range(10)
    ]
    s = build_series(
        resource_id="/sub/.../dw1",
        pool_name="dw1",
        metric_name="DWUUsedPercent",
        unit="Percent",
        aggregation="Average",
        interval="PT1H",
        points=points,
    )
    assert s.min_value == 0
    assert s.max_value == 90
    assert s.avg_value == 45
    assert s.p95_value is not None and s.p95_value > 80


def test_default_window_returns_pair() -> None:
    start, end = default_window(7)
    assert end > start
    assert (end - start).days == 7


def test_write_reports_produces_expected_files(tmp_path: Path) -> None:
    series = MetricSeries(
        resource_id="/sub/.../dw1",
        resource_kind="dedicated_pool",
        resource_name="dw1",
        metric_name="DWUUsedPercent",
        unit="Percent",
        aggregation="Average",
        interval="PT1H",
        points=[(datetime(2025, 1, 1, tzinfo=timezone.utc), 50.0)],
        min_value=50.0, max_value=50.0, avg_value=50.0, p95_value=50.0,
    )
    result = MonitoringAnalysis(
        workspace_name="ws",
        subscription_id="sub",
        resource_group="rg",
        generated_at=datetime.now(timezone.utc),
        window_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2025, 1, 8, tzinfo=timezone.utc),
        interval="PT1H",
        series=[series],
    )
    paths = write_reports(result, tmp_path, formats=["json", "csv", "markdown"])
    names = {p.name for p in paths}
    assert {"monitoring.json", "monitoring_summary.csv", "monitoring.md"} <= names
