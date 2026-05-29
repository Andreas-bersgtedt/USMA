"""Phase 1.5 — tests for the ``--scope`` flag parser on ``analyze-all``.

We import the private ``_apply_scope_flags`` helper directly so the test
can exercise the parser without spinning the entire Click command tree
(which transitively imports every analyzer + the rich CLI surface).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from usma.cli import _apply_scope_flags
from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.sources import SourceType


@pytest.fixture
def base_cfg() -> AppConfig:
    return AppConfig(
        azure=AzureConfig(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            subscription_id="sub-default",
            resource_group="rg-default",
            workspace_name="ws-default",
        ),
        sql=SqlConfig(),
        output_dir=Path("./output"),
    )


def test_apply_scope_flags_synapse_short_form(base_cfg: AppConfig) -> None:
    """``--scope synapse_workspace:ws-eu`` reuses the default subscription
    + resource group from .env."""
    new = _apply_scope_flags(base_cfg, ("synapse_workspace:ws-eu",))
    assert len(new.scopes) == 1
    s = new.scopes[0]
    assert s.type is SourceType.SYNAPSE_WORKSPACE
    assert s.display_name == "ws-eu"
    assert s.subscription_id == "sub-default"
    assert s.resource_group == "rg-default"
    assert "Microsoft.Synapse/workspaces/ws-eu" in s.id


def test_apply_scope_flags_adf_long_form(base_cfg: AppConfig) -> None:
    """ADF scope with explicit ``@<sub>/<rg>`` override."""
    new = _apply_scope_flags(
        base_cfg,
        ("adf:adf-prod@sub-other/rg-data",),
    )
    s = new.scopes[0]
    assert s.type is SourceType.ADF
    assert s.subscription_id == "sub-other"
    assert s.resource_group == "rg-data"
    assert "Microsoft.DataFactory/factories/adf-prod" in s.id


def test_apply_scope_flags_repeatable(base_cfg: AppConfig) -> None:
    """Multiple ``--scope`` values land in order on ``cfg.scopes``."""
    new = _apply_scope_flags(
        base_cfg,
        (
            "synapse_workspace:ws-a",
            "adf:adf-b",
            "synapse_workspace:ws-c",
        ),
    )
    assert tuple(s.display_name for s in new.scopes) == ("ws-a", "adf-b", "ws-c")
    assert tuple(s.type for s in new.scopes) == (
        SourceType.SYNAPSE_WORKSPACE,
        SourceType.ADF,
        SourceType.SYNAPSE_WORKSPACE,
    )


def test_apply_scope_flags_rejects_missing_colon(base_cfg: AppConfig) -> None:
    import click
    with pytest.raises(click.UsageError):
        _apply_scope_flags(base_cfg, ("ws-no-colon",))


def test_apply_scope_flags_rejects_unknown_type(base_cfg: AppConfig) -> None:
    import click
    with pytest.raises(click.UsageError):
        _apply_scope_flags(base_cfg, ("not_a_source:ws",))


def test_apply_scope_flags_rejects_empty_display(base_cfg: AppConfig) -> None:
    import click
    with pytest.raises(click.UsageError):
        _apply_scope_flags(base_cfg, ("synapse_workspace:",))


def test_apply_scope_flags_bigquery_uses_project_id_not_arm(base_cfg: AppConfig) -> None:
    """``--scope bigquery:<project-id>`` produces a descriptor whose ``id``
    is the GCP project id (no ARM URL) and leaves Azure coords unset."""
    new = _apply_scope_flags(base_cfg, ("bigquery:my-gcp-project",))
    assert len(new.scopes) == 1
    s = new.scopes[0]
    assert s.type is SourceType.BIGQUERY
    assert s.id == "my-gcp-project"
    assert s.display_name == "my-gcp-project"
    assert s.subscription_id is None
    assert s.resource_group is None


def test_apply_scope_flags_mixes_azure_and_bigquery(base_cfg: AppConfig) -> None:
    """A single ``analyze-all`` invocation may mix Azure + GCP scopes."""
    new = _apply_scope_flags(
        base_cfg,
        ("synapse_workspace:ws-a", "bigquery:proj-b"),
    )
    assert tuple(s.type for s in new.scopes) == (
        SourceType.SYNAPSE_WORKSPACE,
        SourceType.BIGQUERY,
    )
    assert new.scopes[0].subscription_id == "sub-default"
    assert new.scopes[1].subscription_id is None


# ---------------------------------------------------------------------------
# Phase 4.7 — ``databricks-aws:<host>`` shortcut
# ---------------------------------------------------------------------------

def test_apply_scope_flags_databricks_aws_bare_host(base_cfg: AppConfig) -> None:
    """``--scope databricks-aws:<host>`` builds a DATABRICKS descriptor
    with ``extras['platform']='aws'`` and no Azure coords."""
    new = _apply_scope_flags(
        base_cfg, ("databricks-aws:acme-prod.cloud.databricks.com",),
    )
    assert len(new.scopes) == 1
    s = new.scopes[0]
    assert s.type is SourceType.DATABRICKS
    assert s.extras.get("platform") == "aws"
    assert s.subscription_id is None
    assert s.resource_group is None
    assert "acme-prod" in s.display_name


def test_apply_scope_flags_databricks_aws_url_form(base_cfg: AppConfig) -> None:
    """The shortcut also accepts a full ``https://`` URL."""
    new = _apply_scope_flags(
        base_cfg, ("databricks-aws:https://acme-prod.cloud.databricks.com/",),
    )
    s = new.scopes[0]
    assert s.type is SourceType.DATABRICKS
    assert s.extras.get("platform") == "aws"


def test_apply_scope_flags_databricks_aws_ignores_at_segment(base_cfg: AppConfig) -> None:
    """AWS workspaces have no Azure sub/RG; any ``@..`` segment is dropped."""
    new = _apply_scope_flags(
        base_cfg,
        ("databricks-aws:acme.cloud.databricks.com@sub-x/rg-y",),
    )
    s = new.scopes[0]
    assert s.subscription_id is None
    assert s.resource_group is None


def test_apply_scope_flags_databricks_aws_rejects_empty_host(base_cfg: AppConfig) -> None:
    import click
    with pytest.raises(click.UsageError):
        _apply_scope_flags(base_cfg, ("databricks-aws:",))


# ---------------------------------------------------------------------------
# Phase 7 Slice 7-B — ``snowflake:<account>[@<platform>]`` shortcut
# ---------------------------------------------------------------------------

def test_apply_scope_flags_snowflake_bare_account_defaults_to_aws(base_cfg: AppConfig) -> None:
    """``--scope snowflake:<account>`` defaults to ``extras['platform']='aws'``
    when no ``@<platform>`` suffix is provided."""
    new = _apply_scope_flags(base_cfg, ("snowflake:acme-prod",))
    assert len(new.scopes) == 1
    s = new.scopes[0]
    assert s.type is SourceType.SNOWFLAKE
    assert s.id == "acme-prod"
    assert s.display_name == "acme-prod"
    assert s.extras.get("platform") == "aws"
    assert s.subscription_id is None
    assert s.resource_group is None


def test_apply_scope_flags_snowflake_explicit_platform(base_cfg: AppConfig) -> None:
    """``snowflake:<account>@<platform>`` pins ``extras['platform']``."""
    new = _apply_scope_flags(base_cfg, ("snowflake:acme-eu@azure",))
    s = new.scopes[0]
    assert s.extras.get("platform") == "azure"
    new = _apply_scope_flags(base_cfg, ("snowflake:acme-apac@gcp",))
    s = new.scopes[0]
    assert s.extras.get("platform") == "gcp"


def test_apply_scope_flags_snowflake_rejects_empty_account(base_cfg: AppConfig) -> None:
    import click
    with pytest.raises(click.UsageError, match="missing account"):
        _apply_scope_flags(base_cfg, ("snowflake:",))


def test_apply_scope_flags_snowflake_rejects_unknown_platform(base_cfg: AppConfig) -> None:
    import click
    with pytest.raises(click.UsageError, match="unknown"):
        _apply_scope_flags(base_cfg, ("snowflake:acme-prod@oracle",))


def test_apply_scope_flags_mixes_snowflake_and_azure(base_cfg: AppConfig) -> None:
    """A single ``analyze-all`` invocation may mix Snowflake + Azure scopes."""
    new = _apply_scope_flags(
        base_cfg, ("snowflake:acme-prod", "synapse_workspace:ws-eu"),
    )
    assert tuple(s.type for s in new.scopes) == (
        SourceType.SNOWFLAKE,
        SourceType.SYNAPSE_WORKSPACE,
    )
