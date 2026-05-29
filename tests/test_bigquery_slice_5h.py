"""Tests for Slice 5-H — GCP cost client + cost.supports widened to BIGQUERY.

The actual ``google.cloud.bigquery`` SDK is not exercised here; we
patch ``bigquery.Client`` so the parser, env-var dispatch, and
``CostAnalyzer`` BigQuery branch all run under pytest without any GCP
credentials.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules.cost.gcp_cost_client import (
    GcpCostClient,
    _classify_service,
    _parse_billing_rows,
    _resolve_billing_table,
)
from usma.modules.spec import MODULE_SPECS
from usma.sources import SourceDescriptor, SourceType


# ---------------------------------------------------------------------------
# MODULE_SPECS
# ---------------------------------------------------------------------------


def test_cost_supports_bigquery():
    assert SourceType.BIGQUERY in MODULE_SPECS["cost"].supports


# ---------------------------------------------------------------------------
# _resolve_billing_table
# ---------------------------------------------------------------------------


def test_resolve_billing_table_missing(monkeypatch):
    monkeypatch.delenv("SMA_GCP_BILLING_DATASET", raising=False)
    monkeypatch.delenv("SMA_GCP_BILLING_TABLE", raising=False)
    monkeypatch.delenv("SMA_GCP_BILLING_ACCOUNT", raising=False)
    assert _resolve_billing_table("my-proj") is None


def test_resolve_billing_table_dataset_unqualified(monkeypatch):
    monkeypatch.setenv("SMA_GCP_BILLING_DATASET", "billing_export")
    monkeypatch.setenv("SMA_GCP_BILLING_TABLE", "gcp_billing_export_v1_ABCDEF_012345_678901")
    monkeypatch.delenv("SMA_GCP_BILLING_ACCOUNT", raising=False)
    assert _resolve_billing_table("my-proj") == (
        "my-proj.billing_export",
        "gcp_billing_export_v1_ABCDEF_012345_678901",
    )


def test_resolve_billing_table_derived_from_account(monkeypatch):
    monkeypatch.setenv("SMA_GCP_BILLING_DATASET", "billing-host.exports")
    monkeypatch.delenv("SMA_GCP_BILLING_TABLE", raising=False)
    monkeypatch.setenv("SMA_GCP_BILLING_ACCOUNT", "ABCDEF-012345-678901")
    dataset, table = _resolve_billing_table("my-proj")
    assert dataset == "billing-host.exports"
    assert table == "gcp_billing_export_v1_ABCDEF_012345_678901"


# ---------------------------------------------------------------------------
# _classify_service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "service, expected",
    [
        ("BigQuery", "bigquery_project"),
        ("BigQuery Reservation API", "bigquery_project"),
        ("Cloud Dataflow", "dataflow_job"),
        ("Cloud Dataproc", "dataproc_cluster"),
        ("Cloud Storage", "storage"),
        ("Compute Engine", "compute"),
        ("Cloud Pub/Sub", "other"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_classify_service(service, expected):
    assert _classify_service(service) == expected


# ---------------------------------------------------------------------------
# _parse_billing_rows
# ---------------------------------------------------------------------------


def test_parse_billing_rows_basic():
    rows = [
        {
            "month": "2026-04",
            "service": "BigQuery",
            "sku": "Analysis",
            "project_id": "my-proj",
            "currency": "USD",
            "cost": 123.45,
            "usage_quantity": 100.0,
            "usage_unit": "byte-seconds",
        },
        {
            "month": "2026-05",
            "service": "Cloud Storage",
            "sku": "Standard Storage",
            "project_id": "my-proj",
            "currency": "USD",
            "cost": 10.0,
            "usage_quantity": 50.0,
            "usage_unit": "byte-month",
        },
    ]
    parsed = _parse_billing_rows(rows)
    assert len(parsed) == 2
    assert parsed[0].month == "2026-04"
    assert parsed[0].resource_kind == "bigquery_project"
    assert parsed[0].resource_name == "my-proj"
    assert parsed[0].cost == pytest.approx(123.45)
    assert parsed[0].currency == "USD"
    assert parsed[1].resource_kind == "storage"


def test_parse_billing_rows_falls_back_to_service_when_sku_blank():
    rows = [{"month": "2026-04", "service": "BigQuery", "sku": "", "project_id": "p",
             "currency": "USD", "cost": 1.0, "usage_quantity": 0.0, "usage_unit": None}]
    parsed = _parse_billing_rows(rows)
    assert parsed[0].sku == "BigQuery"


# ---------------------------------------------------------------------------
# GcpCostClient — live-disabled + missing-config short circuits
# ---------------------------------------------------------------------------


def _descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        type=SourceType.BIGQUERY,
        id="my-proj",
        display_name="my-proj",
    )


def test_gcp_cost_client_live_disabled(monkeypatch):
    monkeypatch.setenv("SMA_COST_DISABLE_LIVE", "1")
    rows, status = GcpCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rows == []
    assert status == "live_disabled"


def test_gcp_cost_client_missing_config(monkeypatch):
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.delenv("SMA_GCP_BILLING_DATASET", raising=False)
    monkeypatch.delenv("SMA_GCP_BILLING_TABLE", raising=False)
    monkeypatch.delenv("SMA_GCP_BILLING_ACCOUNT", raising=False)
    rows, status = GcpCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rows == []
    assert status == "missing_config"


def test_gcp_cost_client_requires_project_id():
    with pytest.raises(ValueError):
        GcpCostClient(SourceDescriptor(type=SourceType.BIGQUERY, id="", display_name=""))


# ---------------------------------------------------------------------------
# GcpCostClient — live path with mocked bigquery module
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_bigquery_module(monkeypatch):
    """Inject a fake ``google.cloud.bigquery`` so the live path runs."""
    fake_bq = types.SimpleNamespace()

    captured: dict = {}

    class _ScalarParam:
        def __init__(self, name, type_, value):
            self.name = name
            self.type_ = type_
            self.value = value

    class _JobConfig:
        def __init__(self, query_parameters=None):
            self.query_parameters = query_parameters or []

    class _Job:
        def __init__(self, rows):
            self._rows = rows

        def result(self):
            return iter(self._rows)

    class _Client:
        def __init__(self, project=None):
            captured["project"] = project

        def query(self, sql, job_config=None):
            captured["sql"] = sql
            captured["params"] = {p.name: p.value for p in job_config.query_parameters}
            return _Job(captured.get("rows", []))

    fake_bq.Client = _Client
    fake_bq.QueryJobConfig = _JobConfig
    fake_bq.ScalarQueryParameter = _ScalarParam

    fake_cloud = types.ModuleType("google.cloud")
    fake_cloud.bigquery = fake_bq  # type: ignore[attr-defined]
    fake_google = types.ModuleType("google")
    fake_google.cloud = fake_cloud  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", fake_bq)

    return captured


def test_gcp_cost_client_live_query(monkeypatch, fake_bigquery_module):
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.setenv("SMA_GCP_BILLING_DATASET", "billing-host.exports")
    monkeypatch.setenv("SMA_GCP_BILLING_TABLE", "gcp_billing_export_v1_ABC_DEF_GHI")

    fake_bigquery_module["rows"] = [
        {"month": "2026-04", "service": "BigQuery", "sku": "Analysis",
         "project_id": "my-proj", "currency": "USD",
         "cost": 42.0, "usage_quantity": 1.0, "usage_unit": "byte-seconds"},
    ]

    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, tzinfo=timezone.utc)
    rows, status = GcpCostClient(_descriptor()).fetch_monthly_breakdown_with_status(start, end)

    assert status == "ok"
    assert len(rows) == 1
    assert rows[0].cost == pytest.approx(42.0)
    assert rows[0].resource_kind == "bigquery_project"
    assert fake_bigquery_module["project"] == "billing-host"
    assert "billing-host.exports.gcp_billing_export_v1_ABC_DEF_GHI" in fake_bigquery_module["sql"]
    assert fake_bigquery_module["params"]["project_id"] == "my-proj"
    assert fake_bigquery_module["params"]["window_start"] == start


def test_gcp_cost_client_live_query_empty(monkeypatch, fake_bigquery_module):
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.setenv("SMA_GCP_BILLING_DATASET", "host.exports")
    monkeypatch.setenv("SMA_GCP_BILLING_TABLE", "tbl")
    fake_bigquery_module["rows"] = []
    rows, status = GcpCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rows == []
    assert status == "empty_window"


# ---------------------------------------------------------------------------
# CostAnalyzer dispatch — BigQuery scopes route to GcpCostClient
# ---------------------------------------------------------------------------


def _bigquery_cfg(tmp_path: Path) -> AppConfig:
    azure = AzureConfig(
        tenant_id="", client_id="", client_secret="",
        subscription_id="my-proj", resource_group="",
        workspace_name="my-proj", gcp_project_id="my-proj",
    )
    descriptor = SourceDescriptor(
        type=SourceType.BIGQUERY, id="my-proj", display_name="my-proj",
    )
    return AppConfig(
        azure=azure,
        sql=SqlConfig(),
        output_dir=tmp_path,
        scopes=(descriptor,),
    )


def test_cost_analyzer_routes_bigquery_to_gcp_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_COST_DISABLE_LIVE", "1")
    from usma.modules.cost.analyzer import CostAnalyzer

    analyzer = CostAnalyzer(_bigquery_cfg(tmp_path))
    assert isinstance(analyzer._cc, GcpCostClient)

    result = analyzer.run()
    assert result.collection_status == "live_disabled"
    assert result.workspace_name == "my-proj"
    assert result.subscription_id == "my-proj"
    assert result.resource_group == ""
    assert result.rows == []


def test_cost_analyzer_azure_path_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_COST_DISABLE_LIVE", "1")
    from usma.modules.cost.analyzer import CostAnalyzer
    from usma.modules.cost.cost_client import CostClient

    azure = AzureConfig(
        tenant_id="t", client_id="c", client_secret="s",
        subscription_id="sub", resource_group="rg", workspace_name="ws",
    )
    descriptor = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id=f"/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Synapse/workspaces/ws",
        display_name="ws", subscription_id="sub", resource_group="rg",
    )
    cfg = AppConfig(azure=azure, sql=SqlConfig(), output_dir=tmp_path, scopes=(descriptor,))
    analyzer = CostAnalyzer(cfg)
    assert isinstance(analyzer._cc, CostClient)
    result = analyzer.run()
    assert result.workspace_name == "ws"
