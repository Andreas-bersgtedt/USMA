"""Verify CostAnalyzer skips Azure Cost Management for non-Azure Databricks.

Azure Cost Management requires an Azure subscription scope. Databricks
workspaces on AWS / GCP have none, and historically the analyzer would
crash with ``client_id should be the id of a Microsoft Entra
application`` because it passed Databricks SP creds to Azure SDK clients.

This module locks in the bypass: when the primary scope is a Databricks
descriptor with ``extras['platform']`` of ``"aws"`` or ``"gcp"``, the
analyzer must:

* skip CostClient / GcpCostClient construction (``_cc`` is None);
* report ``collection_status == "skipped"``;
* emit a single explanatory finding (``cost.skipped_non_azure``);
* leave ``rows`` / ``monthly_totals`` empty;
* not raise.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules.cost.analyzer import CostAnalyzer
from usma.sources import SourceDescriptor, SourceType


def _aws_databricks_cfg(tmp_path: Path) -> AppConfig:
    azure = AzureConfig(
        tenant_id="",
        client_id="",
        client_secret="",
        subscription_id="",
        resource_group="",
        workspace_name="dbc-8ca30c2f-419d.cloud.databricks.com",
    )
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="dbc-8ca30c2f-419d.cloud.databricks.com",
        display_name="dbc-8ca30c2f-419d.cloud.databricks.com",
        extras={"platform": "aws", "workspace_url": "dbc-8ca30c2f-419d.cloud.databricks.com"},
    )
    return AppConfig(
        azure=azure,
        sql=SqlConfig(),
        output_dir=tmp_path,
        scopes=(descriptor,),
    )


def test_cost_analyzer_skips_aws_databricks(tmp_path):
    analyzer = CostAnalyzer(_aws_databricks_cfg(tmp_path))
    assert analyzer._cc is None
    assert analyzer._is_non_azure_databricks is True

    result = analyzer.run()

    assert result.collection_status == "skipped"
    assert result.rows == []
    assert result.monthly_totals == {}
    assert result.errors == []
    assert result.subscription_id == ""
    assert result.resource_group == ""
    rule_ids = [f.rule_id for f in result.findings]
    assert "cost.skipped_non_azure" in rule_ids
    assert "cost.collection_error" not in rule_ids
    skipped = next(f for f in result.findings if f.rule_id == "cost.skipped_non_azure")
    assert "AWS" in skipped.title
    assert skipped.severity == "info"


def test_cost_analyzer_skips_gcp_databricks(tmp_path):
    cfg = _aws_databricks_cfg(tmp_path)
    # Swap platform extras to GCP.
    original = cfg.scopes[0]
    gcp_descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id=original.id,
        display_name=original.display_name,
        extras={"platform": "gcp"},
    )
    cfg = AppConfig(
        azure=cfg.azure,
        sql=cfg.sql,
        output_dir=cfg.output_dir,
        scopes=(gcp_descriptor,),
    )
    analyzer = CostAnalyzer(cfg)
    assert analyzer._is_non_azure_databricks is True
    result = analyzer.run()
    assert result.collection_status == "skipped"
    skipped = next(f for f in result.findings if f.rule_id == "cost.skipped_non_azure")
    assert "GCP" in skipped.title


def test_cost_analyzer_azure_databricks_unchanged(tmp_path, monkeypatch):
    """Legacy Azure-Databricks descriptors (no extras) must keep the
    Azure Cost Management path."""
    monkeypatch.setenv("SMA_COST_DISABLE_LIVE", "1")
    azure = AzureConfig(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        subscription_id="sub",
        resource_group="rg",
        workspace_name="adb-1234.azuredatabricks.net",
    )
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/ws",
        display_name="ws",
        subscription_id="sub",
        resource_group="rg",
        # No extras['platform'] — legacy descriptor, must resolve to Azure.
    )
    cfg = AppConfig(
        azure=azure, sql=SqlConfig(), output_dir=tmp_path, scopes=(descriptor,)
    )
    analyzer = CostAnalyzer(cfg)
    assert analyzer._is_non_azure_databricks is False
    assert analyzer._cc is not None
    result = analyzer.run()
    # SMA_COST_DISABLE_LIVE → "live_disabled", not "skipped"
    assert result.collection_status == "live_disabled"
