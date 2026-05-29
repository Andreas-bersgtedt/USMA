"""Hyperscaler bucketing for the Estate Overview."""
from __future__ import annotations

from usma.web.estate import _cloud_for


def test_synapse_and_adf_route_to_azure() -> None:
    assert _cloud_for("synapse_workspace", None) == "azure"
    assert _cloud_for("adf", None) == "azure"


def test_bigquery_routes_to_gcp() -> None:
    assert _cloud_for("bigquery", None) == "gcp"


def test_sap_bw_routes_to_on_prem() -> None:
    assert _cloud_for("sap_bw", None) == "on_prem"


def test_databricks_uses_explicit_platform_when_present() -> None:
    assert _cloud_for("databricks", "aws") == "aws"
    assert _cloud_for("databricks", "gcp") == "gcp"
    assert _cloud_for("databricks", "azure") == "azure"


def test_databricks_falls_back_to_host_suffix_for_legacy_runs() -> None:
    # Pre-5.1.2 Databricks runs persisted neither a top-level
    # `platform` nor `extras["platform"]`. The scope id is the
    # workspace host; the suffix uniquely identifies the cloud.
    assert (
        _cloud_for("databricks", None, "dbc-8ca30c2f-419d.cloud.databricks.com")
        == "aws"
    )
    assert (
        _cloud_for("databricks", None, "adb-1234567890123456.5.azuredatabricks.net")
        == "azure"
    )
    assert (
        _cloud_for("databricks", None, "12345.gcp.databricks.com")
        == "gcp"
    )


def test_databricks_defaults_to_azure_when_host_is_unrecognised() -> None:
    # Conservative default: Azure has been the only Databricks cloud
    # supported until 5.1, so an unrecognised host is most likely
    # Azure with a stale / placeholder id.
    assert _cloud_for("databricks", None, None) == "azure"
    assert _cloud_for("databricks", None, "private.example.com") == "azure"


def test_returns_none_for_legacy_runs_without_source_type() -> None:
    assert _cloud_for(None, None) is None
    assert _cloud_for("", None) is None
