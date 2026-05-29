"""Tests for DatabricksProvider (Phase 4)."""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from usma.sources import (
    Credentials,
    SourceDescriptor,
    SourceType,
)
from usma.sources.databricks.provider import (
    DATABRICKS_AAD_RESOURCE,
    DatabricksClientBundle,
    DatabricksProvider,
    _workspace_to_descriptor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


CREDS = Credentials(tenant_id="t", client_id="c", client_secret="s")
CREDS_PAT = Credentials(
    tenant_id="t",
    client_id="c",
    client_secret="s",
    extras={"DATABRICKS_TOKEN": "dapi-fake"},
)


def _fake_workspace(
    *,
    name: str,
    rg: str,
    location: str = "westeurope",
    workspace_url: str | None = "adb-1234567890123456.7.azuredatabricks.net",
    workspace_id: str | None = "1234567890123456",
    sku: str | None = "premium",
    managed_rg: str | None = "/subscriptions/s/resourceGroups/databricks-rg-managed",
) -> Any:
    """Build a fake azure-mgmt-databricks Workspace-like object.

    Mirrors the snake-case shape ``azure-mgmt-databricks`` 2.x returns
    (``workspace.properties.workspace_url`` etc.).
    """
    props = types.SimpleNamespace()
    if workspace_url is not None:
        props.workspace_url = workspace_url
    if workspace_id is not None:
        props.workspace_id = workspace_id
    if managed_rg is not None:
        props.managed_resource_group_id = managed_rg

    ws = types.SimpleNamespace()
    ws.id = (
        f"/subscriptions/00000000-0000-0000-0000-000000000000"
        f"/resourceGroups/{rg}/providers/Microsoft.Databricks/workspaces/{name}"
    )
    ws.name = name
    ws.location = location
    ws.properties = props
    if sku is not None:
        ws.sku = types.SimpleNamespace(name=sku)
    else:
        ws.sku = None
    return ws


@pytest.fixture
def fake_azure_sdks(monkeypatch: pytest.MonkeyPatch):
    """Install fake ``azure.identity`` + ``azure.mgmt.databricks`` modules."""
    cred_mock = MagicMock(name="ClientSecretCredential")
    cred_instance = MagicMock(name="cred_instance")
    cred_instance.get_token = MagicMock(
        return_value=types.SimpleNamespace(token="fake-token", expires_on=0),
    )
    cred_mock.return_value = cred_instance

    mgmt_mock = MagicMock(name="AzureDatabricksManagementClient")
    mgmt_instance = MagicMock(name="mgmt_instance")
    mgmt_mock.return_value = mgmt_instance

    azure_identity = types.ModuleType("azure.identity")
    azure_identity.ClientSecretCredential = cred_mock  # type: ignore[attr-defined]
    azure_mgmt_db = types.ModuleType("azure.mgmt.databricks")
    azure_mgmt_db.AzureDatabricksManagementClient = mgmt_mock  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setitem(sys.modules, "azure.mgmt.databricks", azure_mgmt_db)

    return cred_mock, mgmt_mock, mgmt_instance


@pytest.fixture
def fake_databricks_sdk(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``databricks.sdk`` module with a WorkspaceClient stub."""
    ws_client_mock = MagicMock(name="WorkspaceClient")
    ws_client_instance = MagicMock(name="ws_client_instance")
    me_obj = types.SimpleNamespace(user_name="sp-app", display_name=None)
    ws_client_instance.current_user.me.return_value = me_obj
    ws_client_mock.return_value = ws_client_instance

    databricks_pkg = types.ModuleType("databricks")
    databricks_sdk = types.ModuleType("databricks.sdk")
    databricks_sdk.WorkspaceClient = ws_client_mock  # type: ignore[attr-defined]
    databricks_pkg.sdk = databricks_sdk  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "databricks", databricks_pkg)
    monkeypatch.setitem(sys.modules, "databricks.sdk", databricks_sdk)

    return ws_client_mock, ws_client_instance


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_discover_requires_subscription_id():
    provider = DatabricksProvider()
    with pytest.raises(ValueError, match="subscription_id is required"):
        provider.discover(CREDS)


def test_discover_returns_descriptors(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.workspaces.list_by_subscription.return_value = iter([
        _fake_workspace(name="dbx-prod", rg="rg-1"),
        _fake_workspace(
            name="dbx-dev", rg="rg-2", sku="standard",
            workspace_url="adb-9999999999999999.0.azuredatabricks.net",
            workspace_id="9999999999999999",
        ),
    ])

    provider = DatabricksProvider()
    out = provider.discover(CREDS, subscription_id="sub-x")

    assert len(out) == 2
    assert all(isinstance(d, SourceDescriptor) for d in out)
    assert all(d.type == SourceType.DATABRICKS for d in out)
    assert {d.display_name for d in out} == {"dbx-prod", "dbx-dev"}

    prod = next(d for d in out if d.display_name == "dbx-prod")
    assert prod.resource_group == "rg-1"
    assert prod.subscription_id == "sub-x"
    assert prod.extras["workspace_url"] == "adb-1234567890123456.7.azuredatabricks.net"
    assert prod.extras["workspace_id"] == "1234567890123456"
    assert prod.extras["sku"] == "premium"
    assert prod.extras["managed_resource_group_id"].endswith("databricks-rg-managed")

    dev = next(d for d in out if d.display_name == "dbx-dev")
    assert dev.extras["sku"] == "standard"
    assert dev.extras["workspace_id"] == "9999999999999999"


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_ok_when_arm_and_dataplane_succeed(
    fake_azure_sdks, fake_databricks_sdk,
):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.workspaces.get.return_value = _fake_workspace(
        name="dbx-prod", rg="rg-1",
    )

    provider = DatabricksProvider()
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="/subscriptions/sub-x/resourceGroups/rg-1/providers/Microsoft.Databricks/workspaces/dbx-prod",
        display_name="dbx-prod",
        subscription_id="sub-x",
        resource_group="rg-1",
        location="westeurope",
        extras={"workspace_url": "adb-1234567890123456.7.azuredatabricks.net"},
    )
    checks = provider.validate(descriptor, CREDS)

    assert len(checks) == 2
    arm, dp = checks
    assert arm.ok is True
    assert arm.category == "Control plane"
    assert dp.ok is True
    assert dp.category == "Data plane"
    assert "sp-app" in dp.detail


def test_validate_short_circuits_on_arm_failure(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.workspaces.get.side_effect = RuntimeError("403 Forbidden")

    provider = DatabricksProvider()
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="x", display_name="dbx-prod",
        subscription_id="sub-x", resource_group="rg-1",
    )
    checks = provider.validate(descriptor, CREDS)

    # ARM failed → data-plane check is skipped (only one row returned).
    assert len(checks) == 1
    assert checks[0].ok is False
    assert "403" in checks[0].detail


def test_validate_reports_missing_workspace_url(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.workspaces.get.return_value = _fake_workspace(
        name="dbx-prov", rg="rg-1", workspace_url=None,
    )

    provider = DatabricksProvider()
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="x", display_name="dbx-prov",
        subscription_id="sub-x", resource_group="rg-1",
    )
    checks = provider.validate(descriptor, CREDS)

    assert len(checks) == 2
    arm, dp = checks
    assert arm.ok is True
    assert dp.ok is False
    assert "workspaceUrl" in dp.detail


def test_validate_pat_token_path(
    fake_azure_sdks, fake_databricks_sdk,
):
    """When DATABRICKS_TOKEN is set, WorkspaceClient receives ``token=`` and no AAD callable."""
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.workspaces.get.return_value = _fake_workspace(
        name="dbx-prod", rg="rg-1",
    )
    ws_client_mock, _ = fake_databricks_sdk

    provider = DatabricksProvider()
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="x", display_name="dbx-prod",
        subscription_id="sub-x", resource_group="rg-1",
        extras={"workspace_url": "adb-1234567890123456.7.azuredatabricks.net"},
    )
    checks = provider.validate(descriptor, CREDS_PAT)

    assert all(c.ok for c in checks)
    call_kwargs = ws_client_mock.call_args.kwargs
    assert call_kwargs.get("token") == "dapi-fake"
    assert "credentials_provider" not in call_kwargs


# ---------------------------------------------------------------------------
# make_clients()
# ---------------------------------------------------------------------------


def test_make_clients_returns_bundle(fake_azure_sdks):
    provider = DatabricksProvider()
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="/subscriptions/sub-x/resourceGroups/rg-1/.../workspaces/dbx-prod",
        display_name="dbx-prod",
        subscription_id="sub-x",
        resource_group="rg-1",
        location="westeurope",
        extras={
            "workspace_url": "adb-1234567890123456.7.azuredatabricks.net",
            "workspace_id": "1234567890123456",
        },
    )
    bundle = provider.make_clients(descriptor, CREDS)
    assert isinstance(bundle, DatabricksClientBundle)
    assert bundle.subscription_id == "sub-x"
    assert bundle.resource_group == "rg-1"
    assert bundle.workspace_name == "dbx-prod"
    assert bundle.workspace_url == "adb-1234567890123456.7.azuredatabricks.net"
    assert bundle.workspace_id == "1234567890123456"
    assert bundle.location == "westeurope"
    assert bundle.pat_token is None
    assert bundle.credential is not None


def test_make_clients_pat_token_propagates(fake_azure_sdks):
    provider = DatabricksProvider()
    descriptor = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="x", display_name="dbx-prod",
        subscription_id="sub-x", resource_group="rg-1",
        extras={"workspace_url": "adb-x.0.azuredatabricks.net"},
    )
    bundle = provider.make_clients(descriptor, CREDS_PAT)
    assert bundle.pat_token == "dapi-fake"


def test_make_clients_validates_descriptor(fake_azure_sdks):
    provider = DatabricksProvider()
    bad_sub = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="x", display_name="x", subscription_id=None, resource_group="rg",
        extras={"workspace_url": "adb-x.0.azuredatabricks.net"},
    )
    with pytest.raises(ValueError, match="subscription_id"):
        provider.make_clients(bad_sub, CREDS)

    bad_rg = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="x", display_name="x", subscription_id="s", resource_group=None,
        extras={"workspace_url": "adb-x.0.azuredatabricks.net"},
    )
    with pytest.raises(ValueError, match="resource_group"):
        provider.make_clients(bad_rg, CREDS)

    no_url = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="x", display_name="x", subscription_id="s", resource_group="rg",
    )
    with pytest.raises(ValueError, match="workspace_url"):
        provider.make_clients(no_url, CREDS)


# ---------------------------------------------------------------------------
# _workspace_to_descriptor()
# ---------------------------------------------------------------------------


def test_workspace_to_descriptor_parses_arm_id():
    ws = _fake_workspace(name="dbx-prod", rg="rg-prod")
    out = _workspace_to_descriptor(ws, subscription_id="sub-x")
    assert out.type == SourceType.DATABRICKS
    assert out.display_name == "dbx-prod"
    assert out.resource_group == "rg-prod"
    assert out.subscription_id == "sub-x"
    assert out.location == "westeurope"
    assert out.extras["workspace_url"].startswith("adb-")
    assert out.extras["sku"] == "premium"


def test_workspace_to_descriptor_handles_missing_optionals():
    ws = types.SimpleNamespace(
        id="", name="dbx-x", location=None,
        properties=types.SimpleNamespace(), sku=None,
    )
    out = _workspace_to_descriptor(ws, subscription_id="sub-x")
    assert out.display_name == "dbx-x"
    assert out.resource_group == ""
    assert out.location is None
    # Phase 4.7: every Azure-discovered descriptor carries platform=azure.
    assert out.extras == {"platform": "azure"}


def test_databricks_aad_resource_constant():
    # Pin the AAD app id so accidental edits surface in PR review.
    assert DATABRICKS_AAD_RESOURCE == "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
