"""Regression — non-Azure scopes must not inherit AZURE_* identity on RunMeta.

Prior to the fix, a BigQuery (or Snowflake-on-AWS, Databricks-on-AWS/GCP)
run inherited ``AZURE_TENANT_ID`` / ``AZURE_SUBSCRIPTION_ID`` from the
service-principal credentials sitting in ``.env``, causing the Estate
Overview to render e.g. ``Google Cloud · Tenant · Subscription: <azure
tenant guid> · <azure sub guid>``. The cloud-aware helper now zeroes
those fields out for non-Azure scopes.
"""
from __future__ import annotations

from usma.web.jobs import _scope_is_azure
from usma.web.schemas import ScopeRef


def _scope(source_type: str, extras: dict | None = None) -> ScopeRef:
    return ScopeRef(
        source_type=source_type,  # type: ignore[arg-type]
        id="x",
        display_name="x",
        extras=extras or {},
    )


def test_bigquery_scope_is_not_azure() -> None:
    assert _scope_is_azure(_scope("bigquery")) is False


def test_snowflake_aws_scope_is_not_azure() -> None:
    assert _scope_is_azure(_scope("snowflake", {"platform": "aws"})) is False


def test_snowflake_azure_scope_is_azure() -> None:
    assert _scope_is_azure(_scope("snowflake", {"platform": "azure"})) is True


def test_databricks_aws_scope_is_not_azure() -> None:
    assert _scope_is_azure(_scope("databricks", {"platform": "aws"})) is False


def test_databricks_gcp_scope_is_not_azure() -> None:
    assert _scope_is_azure(_scope("databricks", {"platform": "gcp"})) is False


def test_databricks_without_platform_defaults_azure() -> None:
    # Legacy single-scope path leaves ``extras`` empty; Databricks
    # historically meant Databricks-on-Azure in that case.
    assert _scope_is_azure(_scope("databricks")) is True


def test_synapse_scope_is_azure() -> None:
    assert _scope_is_azure(_scope("synapse_workspace")) is True


def test_adf_scope_is_azure() -> None:
    assert _scope_is_azure(_scope("adf")) is True


def test_no_scope_is_not_azure() -> None:
    assert _scope_is_azure(None) is False
