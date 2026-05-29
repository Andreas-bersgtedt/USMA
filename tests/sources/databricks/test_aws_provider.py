"""Tests for DatabricksAwsProvider (Phase 4.7)."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from usma.sources import (
    Credentials,
    SourceDescriptor,
    SourceType,
)
from usma.sources.databricks import (
    DatabricksAwsProvider,
    DatabricksClientBundle,
    DatabricksProvider,
    aws_host_to_descriptor,
    provider_for_descriptor,
    provider_for_platform,
)


CREDS_HOST_ONLY = Credentials(
    tenant_id="", client_id="", client_secret="",
    extras={
        "DATABRICKS_HOST": "acme-prod.cloud.databricks.com",
    },
)
CREDS_OAUTH = Credentials(
    tenant_id="", client_id="", client_secret="",
    extras={
        "DATABRICKS_HOST": "acme-prod.cloud.databricks.com",
        "DATABRICKS_CLIENT_ID": "sp-client-id",
        "DATABRICKS_CLIENT_SECRET": "sp-client-secret",
    },
)
# Phase 4.7.5 — a single Databricks SP authenticates against both the
# account API (workspace discovery) and each workspace it can reach.
CREDS_ACCOUNT = Credentials(
    tenant_id="", client_id="", client_secret="",
    extras={
        "DATABRICKS_ACCOUNT_ID": "acct-123",
        "DATABRICKS_CLIENT_ID": "sp-client-id",
        "DATABRICKS_CLIENT_SECRET": "sp-client-secret",
    },
)


@pytest.fixture
def fake_databricks_sdk(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``databricks.sdk`` with WorkspaceClient + AccountClient stubs."""
    ws_client_mock = MagicMock(name="WorkspaceClient")
    ws_client_instance = MagicMock(name="ws_client_instance")
    ws_client_instance.current_user.me.return_value = types.SimpleNamespace(
        user_name="sp-aws", display_name=None,
    )
    ws_client_mock.return_value = ws_client_instance

    account_client_mock = MagicMock(name="AccountClient")
    account_instance = MagicMock(name="account_instance")
    account_client_mock.return_value = account_instance

    databricks_pkg = types.ModuleType("databricks")
    databricks_sdk = types.ModuleType("databricks.sdk")
    databricks_sdk.WorkspaceClient = ws_client_mock  # type: ignore[attr-defined]
    databricks_sdk.AccountClient = account_client_mock  # type: ignore[attr-defined]
    databricks_pkg.sdk = databricks_sdk  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "databricks", databricks_pkg)
    monkeypatch.setitem(sys.modules, "databricks.sdk", databricks_sdk)

    return ws_client_mock, ws_client_instance, account_client_mock, account_instance


# ---------------------------------------------------------------------------
# aws_host_to_descriptor()
# ---------------------------------------------------------------------------


def test_host_to_descriptor_bare_host():
    d = aws_host_to_descriptor("acme-prod.cloud.databricks.com")
    assert d.type == SourceType.DATABRICKS
    assert d.id == "acme-prod.cloud.databricks.com"
    assert d.display_name == "acme-prod"
    assert d.subscription_id is None
    assert d.resource_group is None
    assert d.extras["platform"] == "aws"
    assert d.extras["workspace_url"] == "acme-prod.cloud.databricks.com"


def test_host_to_descriptor_with_scheme_and_trailing_slash():
    d = aws_host_to_descriptor("https://acme-prod.cloud.databricks.com/")
    assert d.id == "acme-prod.cloud.databricks.com"
    assert d.display_name == "acme-prod"


def test_host_to_descriptor_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        aws_host_to_descriptor("   ")


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_discover_requires_env_vars():
    provider = DatabricksAwsProvider()
    empty = Credentials(tenant_id="", client_id="", client_secret="")
    with pytest.raises(ValueError, match="DATABRICKS_ACCOUNT_ID|DATABRICKS_HOST"):
        provider.discover(empty)


def test_discover_explicit_host_returns_single_descriptor():
    provider = DatabricksAwsProvider()
    out = provider.discover(CREDS_HOST_ONLY)
    assert len(out) == 1
    d = out[0]
    assert d.type == SourceType.DATABRICKS
    assert d.display_name == "acme-prod"
    assert d.extras["platform"] == "aws"


def test_discover_account_api_enumerates_workspaces(fake_databricks_sdk):
    _, _, account_client_mock, account_instance = fake_databricks_sdk
    ws1 = types.SimpleNamespace(
        deployment_name="acme-prod", workspace_name="acme-prod",
        workspace_id=1111, aws_region="us-east-1",
    )
    ws2 = types.SimpleNamespace(
        deployment_name="acme-dev", workspace_name="acme-dev",
        workspace_id=2222, aws_region="us-west-2",
    )
    account_instance.workspaces.list.return_value = iter([ws1, ws2])

    provider = DatabricksAwsProvider()
    out = provider.discover(CREDS_ACCOUNT)

    assert len(out) == 2
    account_client_mock.assert_called_once()
    call_kwargs = account_client_mock.call_args.kwargs
    assert call_kwargs["account_id"] == "acct-123"
    # Phase 4.7.5 — the same Databricks SP feeds the account client.
    assert call_kwargs["client_id"] == "sp-client-id"
    assert call_kwargs["client_secret"] == "sp-client-secret"
    assert call_kwargs["host"].startswith("https://accounts.cloud.databricks.com")

    prod = next(d for d in out if d.display_name == "acme-prod")
    assert prod.extras["platform"] == "aws"
    assert prod.extras["workspace_url"] == "acme-prod.cloud.databricks.com"
    assert prod.extras["workspace_id"] == "1111"
    assert prod.extras["account_id"] == "acct-123"
    assert prod.location == "us-east-1"


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_ok_with_oauth_sp(fake_databricks_sdk):
    ws_client_mock, _, _, _ = fake_databricks_sdk
    provider = DatabricksAwsProvider()
    descriptor = aws_host_to_descriptor("acme-prod.cloud.databricks.com")
    checks = provider.validate(descriptor, CREDS_OAUTH)

    assert len(checks) == 1
    assert checks[0].ok is True
    assert checks[0].category == "Data plane"
    assert "sp-aws" in checks[0].detail
    # Single-SP path: WorkspaceClient(host=..., client_id=..., client_secret=...)
    call_kwargs = ws_client_mock.call_args.kwargs
    assert call_kwargs["client_id"] == "sp-client-id"
    assert call_kwargs["client_secret"] == "sp-client-secret"
    assert "token" not in call_kwargs


def test_validate_reports_failure(fake_databricks_sdk):
    _, ws_client_instance, _, _ = fake_databricks_sdk
    ws_client_instance.current_user.me.side_effect = RuntimeError("401 Unauthorized")
    provider = DatabricksAwsProvider()
    descriptor = aws_host_to_descriptor("acme-prod.cloud.databricks.com")
    checks = provider.validate(descriptor, CREDS_OAUTH)

    assert len(checks) == 1
    assert checks[0].ok is False
    assert "401" in checks[0].detail


# ---------------------------------------------------------------------------
# make_clients()
# ---------------------------------------------------------------------------


def test_make_clients_builds_aws_bundle():
    provider = DatabricksAwsProvider()
    descriptor = aws_host_to_descriptor("acme-prod.cloud.databricks.com")
    bundle = provider.make_clients(descriptor, CREDS_OAUTH)

    assert isinstance(bundle, DatabricksClientBundle)
    assert bundle.platform == "aws"
    assert bundle.subscription_id is None
    assert bundle.resource_group is None
    assert bundle.credential is None
    assert bundle.workspace_url == "acme-prod.cloud.databricks.com"
    assert bundle.workspace_name == "acme-prod"
    assert bundle.oauth_client_id == "sp-client-id"
    assert bundle.oauth_client_secret == "sp-client-secret"
    # Phase 4.7.5 — PAT path is no longer wired through the AWS bundle.
    assert bundle.pat_token is None


def test_make_clients_missing_workspace_url_raises():
    provider = DatabricksAwsProvider()
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="",
        display_name="empty",
        extras={"platform": "aws"},
    )
    with pytest.raises(ValueError, match="missing"):
        provider.make_clients(descriptor, CREDS_OAUTH)


# ---------------------------------------------------------------------------
# provider_for_platform / provider_for_descriptor dispatch
# ---------------------------------------------------------------------------


def test_provider_for_platform_azure():
    assert isinstance(provider_for_platform("azure"), DatabricksProvider)


def test_provider_for_platform_aws():
    assert isinstance(provider_for_platform("aws"), DatabricksAwsProvider)


def test_provider_for_platform_unknown_raises():
    with pytest.raises(KeyError, match="gcp"):
        provider_for_platform("gcp")


def test_provider_for_descriptor_dispatches_by_extras():
    azure = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="adb-1.42.azuredatabricks.net",
        display_name="contoso-prod",
        subscription_id="sub-x",
        resource_group="rg-1",
    )
    aws = aws_host_to_descriptor("acme-prod.cloud.databricks.com")
    assert isinstance(provider_for_descriptor(azure), DatabricksProvider)
    assert isinstance(provider_for_descriptor(aws), DatabricksAwsProvider)
