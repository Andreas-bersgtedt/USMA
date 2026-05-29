"""Tests for SourceDescriptor / Credentials (Phase 0)."""
from __future__ import annotations

import pytest

from usma.sources import (
    Credentials,
    SourceDescriptor,
    SourceType,
    databricks_platform,
)


def test_descriptor_minimum_fields():
    d = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="/subscriptions/00000000/.../workspaces/foo",
        display_name="foo",
    )
    assert d.type == SourceType.SYNAPSE_WORKSPACE
    assert d.short_id() == "foo"
    assert d.subscription_id is None
    assert d.extras == {}


def test_descriptor_short_id_from_id_tail():
    d = SourceDescriptor(
        type=SourceType.ADF,
        id="/subscriptions/000/resourceGroups/rg/providers/Microsoft.DataFactory/factories/My Factory",
        display_name="My Factory",
    )
    assert d.short_id() == "my-factory"


def test_descriptor_short_id_override_via_extras():
    d = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="adb-1234567890.42.azuredatabricks.net",
        display_name="prod",
        extras={"short_id": "prod-east"},
    )
    assert d.short_id() == "prod-east"


def test_descriptor_is_frozen():
    d = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="x",
        display_name="x",
    )
    try:
        d.display_name = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("SourceDescriptor must be frozen")


def test_credentials_extras_default_empty():
    c = Credentials(tenant_id="t", client_id="c", client_secret="s")
    assert c.extras == {}


def test_credentials_extras_carry_per_source_secrets():
    c = Credentials(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        extras={"DATABRICKS_TOKEN": "abc"},
    )
    assert c.extras["DATABRICKS_TOKEN"] == "abc"


def test_source_type_string_values_are_stable():
    # These values appear in manifests/artifact names — never rename without
    # a manifest schema bump (ADR-0003).
    assert SourceType.SYNAPSE_WORKSPACE.value == "synapse_workspace"
    assert SourceType.ADF.value == "adf"
    assert SourceType.DATABRICKS.value == "databricks"
    assert SourceType.SAP_BW.value == "sap_bw"
    assert SourceType.SQL_SERVER.value == "sql_server"
    assert SourceType.SNOWFLAKE.value == "snowflake"


# ---------------------------------------------------------------------------
# Phase 4.7 — Databricks multi-cloud platform discriminator
# ---------------------------------------------------------------------------


def _databricks_descriptor(**extras_kwargs):
    return SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="adb-1.42.azuredatabricks.net",
        display_name="ws",
        extras=dict(extras_kwargs),
    )


def test_databricks_platform_defaults_to_azure_for_pre_4_7_descriptors():
    # Pre-4.7 descriptors (no `platform` key) must resolve to "azure"
    # so legacy manifests and config files keep working unchanged.
    d = _databricks_descriptor()
    assert databricks_platform(d) == "azure"


def test_databricks_platform_round_trips_aws():
    d = _databricks_descriptor(platform="aws")
    assert databricks_platform(d) == "aws"


def test_databricks_platform_round_trips_gcp():
    d = _databricks_descriptor(platform="gcp")
    assert databricks_platform(d) == "gcp"


def test_databricks_platform_normalises_case():
    d = _databricks_descriptor(platform="AWS")
    assert databricks_platform(d) == "aws"


def test_databricks_platform_rejects_unknown_value():
    d = _databricks_descriptor(platform="oracle")
    with pytest.raises(ValueError, match="extras\\['platform'\\]"):
        databricks_platform(d)


def test_databricks_platform_rejects_non_string():
    d = _databricks_descriptor(platform=42)
    with pytest.raises(ValueError, match="must be a string"):
        databricks_platform(d)


def test_databricks_platform_rejects_non_databricks_descriptor():
    d = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="ws",
        display_name="ws",
        extras={"platform": "aws"},
    )
    with pytest.raises(ValueError, match="non-Databricks"):
        databricks_platform(d)
