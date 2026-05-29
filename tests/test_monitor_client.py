"""Tests for monitor_client metric-name resilience."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from usma.modules.monitoring import monitor_client as mc
from usma.modules.monitoring.monitor_client import (
    _iso_z,
    _parse_invalid_metric_name,
)


def test_iso_z_strips_offset_and_microseconds() -> None:
    dt = datetime(2026, 4, 27, 19, 0, 0, 123456, tzinfo=timezone.utc)
    assert _iso_z(dt) == "2026-04-27T19:00:00Z"


def test_parse_invalid_metric_name_extracts_offender() -> None:
    msg = (
        "(BadRequest) Failed to find metric configuration for provider: "
        "Microsoft.Synapse, resource Type: workspaces/sqlPools, metric: "
        "FailedConnections, Valid metrics: DWULimit,DWUUsed,..."
    )
    assert _parse_invalid_metric_name(msg) == "FailedConnections"


def test_parse_invalid_metric_name_returns_none_for_unrelated() -> None:
    assert _parse_invalid_metric_name("(BadRequest) something else") is None


class _FakeMetricsApi:
    """Fakes azure.mgmt.monitor's metrics.list — fails on FailedConnections."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def list(self, resource_id, timespan, interval, metricnames, aggregation):
        names = metricnames.split(",")
        self.calls.append(names)
        if "FailedConnections" in names:
            raise RuntimeError(
                "(BadRequest) Failed to find metric configuration for provider: "
                "Microsoft.Synapse, resource Type: workspaces/sqlPools, metric: "
                "FailedConnections, Valid metrics: DWULimit,Connections"
            )

        class _Result:
            value = []  # empty timeseries; we only assert on retry behaviour
        return _Result()


class _FakeMonitor:
    def __init__(self) -> None:
        self.metrics = _FakeMetricsApi()


def test_fetch_metrics_drops_unsupported_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = mc.MonitoringClient.__new__(mc.MonitoringClient)  # bypass __init__
    fake_monitor = _FakeMonitor()
    client._monitor = fake_monitor  # type: ignore[attr-defined]

    out = client.fetch_metrics(
        resource_id="/subscriptions/x/y",
        metric_names=["DWULimit", "FailedConnections", "Connections"],
        window_start=datetime(2026, 4, 20, tzinfo=timezone.utc),
        window_end=datetime(2026, 4, 27, tzinfo=timezone.utc),
    )

    assert out == []
    assert len(fake_monitor.metrics.calls) == 2
    assert "FailedConnections" in fake_monitor.metrics.calls[0]
    assert "FailedConnections" not in fake_monitor.metrics.calls[1]
