"""Slice F — standalone Dedicated SQL pool (formerly SQL DW) monitoring.

Covers:
- ``MonitoringClient.list_standalone_dwu_resource_ids`` filters to the
  ``DataWarehouse`` SKU tier and honours the single-pool override.
- ``fetch_metrics(metric_name_map=...)`` rewrites snake_case standalone
  metric names to the workspace PascalCase set, leaving unmapped names
  untouched.
- ``MonitoringAnalyzer`` dispatches to the standalone listing path +
  metric set when the primary scope is ``SYNAPSE_DEDICATED_SQL``, so
  the resulting ``MonitoringAnalysis.series`` keys on ``DWULimit`` /
  ``DWUUsedPercent`` regardless of source topology — which is what
  ``fabric_mapping.cu_projection`` consumes.
- ``MODULE_SPECS["monitoring"].supports`` now includes
  ``SYNAPSE_DEDICATED_SQL`` so the SPA + planner expose the module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pytest

from usma.config import AppConfig, AzureConfig, SourceDescriptor, SqlConfig
from usma.modules.monitoring import monitor_client as mc
from usma.modules.monitoring.analyzer import MonitoringAnalyzer
from usma.modules.monitoring.monitor_client import (
    STANDALONE_DWU_POOL_METRICS,
    STANDALONE_TO_WORKSPACE_METRIC,
    MonitoringClient,
)
from usma.modules.spec import MODULE_SPECS
from usma.sources import SourceType


# --- listing path ------------------------------------------------------


class _Sku:
    def __init__(self, tier: str | None) -> None:
        self.tier = tier


class _Db:
    def __init__(self, name: str, tier: str | None, rid: str | None = None) -> None:
        self.name = name
        self.sku = _Sku(tier) if tier is not None else None
        self.id = rid if rid is not None else f"/subscriptions/s/rg/r/db/{name}"


class _FakeSqlDatabasesApi:
    def __init__(self, dbs: list[_Db]) -> None:
        self._dbs = dbs
        self.calls: list[tuple[str, str]] = []

    def list_by_server(self, rg: str, server: str) -> Iterable[_Db]:
        self.calls.append((rg, server))
        return iter(self._dbs)


class _FakeSqlMgmt:
    def __init__(self, dbs: list[_Db]) -> None:
        self.databases = _FakeSqlDatabasesApi(dbs)


def _make_client(dbs: list[_Db], *, pool_filter: str | None = None) -> MonitoringClient:
    client = MonitoringClient.__new__(MonitoringClient)
    client._azure = AzureConfig(  # type: ignore[attr-defined]
        tenant_id="t",
        client_id="c",
        client_secret="s",
        subscription_id="sub",
        resource_group="rg",
        workspace_name="srv",  # repurposed as SQL server name
        dedicated_pool=pool_filter,
    )
    fake = _FakeSqlMgmt(dbs)
    client._MonitoringClient__sql = fake  # type: ignore[attr-defined]
    return client


def test_list_standalone_dwu_filters_to_datawarehouse_tier() -> None:
    dbs = [
        _Db("master", tier=None),
        _Db("regular_db", tier="GeneralPurpose"),
        _Db("testdedicatedpool", tier="DataWarehouse"),
        _Db("anotherdw", tier="DataWarehouse"),
    ]
    client = _make_client(dbs)
    out = client.list_standalone_dwu_resource_ids()
    names = [n for n, _ in out]
    assert names == ["testdedicatedpool", "anotherdw"]
    # Resource IDs are passed through as-is from the SDK.
    assert all(rid.endswith(f"/db/{n}") for n, rid in out)


def test_list_standalone_dwu_honours_single_pool_filter() -> None:
    dbs = [
        _Db("poolA", tier="DataWarehouse"),
        _Db("poolB", tier="DataWarehouse"),
    ]
    client = _make_client(dbs, pool_filter="poolB")
    out = client.list_standalone_dwu_resource_ids()
    assert [n for n, _ in out] == ["poolB"]


def test_list_standalone_dwu_skips_dbs_without_id() -> None:
    dbs = [
        _Db("dw_with_id", tier="DataWarehouse"),
        _Db("dw_missing_id", tier="DataWarehouse", rid=""),
    ]
    client = _make_client(dbs)
    out = client.list_standalone_dwu_resource_ids()
    assert [n for n, _ in out] == ["dw_with_id"]


# --- metric-name normalization in fetch_metrics ------------------------


class _MetricName:
    def __init__(self, v: str) -> None:
        self.value = v


class _Datum:
    def __init__(self, ts: datetime, avg: float | None) -> None:
        self.time_stamp = ts
        self.average = avg


class _Timeseries:
    def __init__(self, data: list[_Datum]) -> None:
        self.data = data


class _Metric:
    def __init__(self, name: str, unit: str, points: list[_Datum]) -> None:
        self.name = _MetricName(name)
        self.unit = unit
        self.timeseries = [_Timeseries(points)]


class _MetricsResult:
    def __init__(self, metrics: list[_Metric]) -> None:
        self.value = metrics


class _RecordingMetricsApi:
    def __init__(self, returned_names: list[str]) -> None:
        self._returned = returned_names
        self.calls: list[list[str]] = []

    def list(self, resource_id, timespan, interval, metricnames, aggregation):
        self.calls.append(metricnames.split(","))
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        return _MetricsResult([
            _Metric(n, "Percent", [_Datum(ts, 42.0)]) for n in self._returned
        ])


class _RecordingMonitor:
    def __init__(self, returned_names: list[str]) -> None:
        self.metrics = _RecordingMetricsApi(returned_names)


def test_fetch_metrics_rewrites_standalone_names_when_map_provided() -> None:
    client = MonitoringClient.__new__(MonitoringClient)
    client._monitor = _RecordingMonitor(  # type: ignore[attr-defined]
        ["dwu_consumption_percent", "dwu_limit", "cache_hit_percent"],
    )

    out = client.fetch_metrics(
        resource_id="/subscriptions/x/y",
        metric_names=list(STANDALONE_DWU_POOL_METRICS),
        window_start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 7, tzinfo=timezone.utc),
        metric_name_map=STANDALONE_TO_WORKSPACE_METRIC,
    )
    names = [n for n, _, _ in out]
    # Mapped names get rewritten to the workspace PascalCase set.
    assert "DWUUsedPercent" in names
    assert "DWULimit" in names
    # Names with no alias (e.g. cache_hit_percent) pass through.
    assert "cache_hit_percent" in names


def test_fetch_metrics_without_map_passes_names_through() -> None:
    client = MonitoringClient.__new__(MonitoringClient)
    client._monitor = _RecordingMonitor(["DWULimit", "DWUUsedPercent"])  # type: ignore[attr-defined]

    out = client.fetch_metrics(
        resource_id="/subscriptions/x/y",
        metric_names=["DWULimit", "DWUUsedPercent"],
        window_start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 7, tzinfo=timezone.utc),
    )
    assert [n for n, _, _ in out] == ["DWULimit", "DWUUsedPercent"]


# --- analyzer scope dispatch ------------------------------------------


def _standalone_cfg() -> AppConfig:
    azure = AzureConfig(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        subscription_id="sub",
        resource_group="rg",
        workspace_name="srv",
        dedicated_pool="testdedicatedpool",
    )
    scope = SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id=(
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.Sql/servers/srv/databases/testdedicatedpool"
        ),
        display_name="testdedicatedpool",
        subscription_id="sub",
        resource_group="rg",
    )
    return AppConfig(
        azure=azure, sql=SqlConfig(), output_dir=Path("./output"), scopes=[scope],
    )


class _FakeMonitoringClient:
    """Records which listing method + metric set the analyzer used."""

    def __init__(self) -> None:
        self.list_standalone_called = False
        self.list_workspace_called = False
        self.fetch_calls: list[tuple[tuple[str, ...], dict | None]] = []

    def list_standalone_dwu_resource_ids(self) -> list[tuple[str, str]]:
        self.list_standalone_called = True
        return [(
            "testdedicatedpool",
            "/subscriptions/sub/.../databases/testdedicatedpool",
        )]

    def list_dedicated_pool_resource_ids(self) -> list[tuple[str, str]]:
        self.list_workspace_called = True
        return []

    def fetch_metrics(
        self, resource_id, metric_names, start, end,
        interval, aggregation, metric_name_map=None,
    ):
        names = tuple(metric_names)
        self.fetch_calls.append((names, metric_name_map))
        # Simulate the rewritten names — what the real client would return
        # for a standalone DWU pool.
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        rewritten = [
            (metric_name_map.get(n, n) if metric_name_map else n, "Percent",
             [(ts, 42.0)])
            for n in names
        ]
        return rewritten


def test_analyzer_uses_standalone_path_for_synapse_dedicated_sql_scope() -> None:
    analyzer = MonitoringAnalyzer(_standalone_cfg())
    fake = _FakeMonitoringClient()
    analyzer._client = fake  # type: ignore[attr-defined]

    result = analyzer.run()

    assert fake.list_standalone_called is True
    assert fake.list_workspace_called is False
    assert fake.fetch_calls, "expected at least one fetch_metrics call"
    names, name_map = fake.fetch_calls[0]
    assert names == STANDALONE_DWU_POOL_METRICS
    assert name_map is STANDALONE_TO_WORKSPACE_METRIC
    # The MetricSeries the analyzer assembled should already key on the
    # workspace PascalCase names — that's what cu_projection consumes.
    metric_names = {s.metric_name for s in result.series}
    assert "DWULimit" in metric_names
    assert "DWUUsedPercent" in metric_names


# --- module spec --------------------------------------------------------


def test_module_spec_monitoring_supports_synapse_dedicated_sql() -> None:
    spec = MODULE_SPECS["monitoring"]
    assert spec.supports_source(SourceType.SYNAPSE_DEDICATED_SQL)
    # Workspace must still be supported.
    assert spec.supports_source(SourceType.SYNAPSE_WORKSPACE)
