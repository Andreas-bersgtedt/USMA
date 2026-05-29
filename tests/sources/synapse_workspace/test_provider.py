"""Tests for SynapseWorkspaceProvider (Phase 1)."""
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
from usma.sources.synapse_workspace.provider import (
    SynapseClientBundle,
    SynapseWorkspaceProvider,
    _workspace_to_descriptor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


CREDS = Credentials(tenant_id="t", client_id="c", client_secret="s")


def _fake_ws(
    *,
    name: str,
    rg: str,
    location: str = "eastus",
    sql: str | None = None,
    sql_on_demand: str | None = None,
) -> Any:
    """Build a fake azure-mgmt-synapse Workspace-like object."""
    ws = types.SimpleNamespace()
    ws.id = (
        f"/subscriptions/00000000-0000-0000-0000-000000000000"
        f"/resourceGroups/{rg}/providers/Microsoft.Synapse/workspaces/{name}"
    )
    ws.name = name
    ws.location = location
    ws.connectivity_endpoints = {}
    if sql:
        ws.connectivity_endpoints["sql"] = sql
    if sql_on_demand:
        ws.connectivity_endpoints["sqlOnDemand"] = sql_on_demand
    return ws


@pytest.fixture
def fake_azure_sdks(monkeypatch: pytest.MonkeyPatch):
    """Install fake ``azure.identity`` + ``azure.mgmt.synapse`` modules.

    Returns ``(cred_class_mock, mgmt_class_mock)`` so the test can
    inspect what was constructed and stub return values.
    """
    cred_mock = MagicMock(name="ClientSecretCredential")
    cred_instance = MagicMock(name="cred_instance")
    cred_instance.get_token = MagicMock(
        return_value=types.SimpleNamespace(token="fake-token", expires_on=0),
    )
    cred_mock.return_value = cred_instance

    mgmt_mock = MagicMock(name="SynapseManagementClient")
    mgmt_instance = MagicMock(name="mgmt_instance")
    mgmt_mock.return_value = mgmt_instance

    azure_identity = types.ModuleType("azure.identity")
    azure_identity.ClientSecretCredential = cred_mock  # type: ignore[attr-defined]
    azure_mgmt_synapse = types.ModuleType("azure.mgmt.synapse")
    azure_mgmt_synapse.SynapseManagementClient = mgmt_mock  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setitem(sys.modules, "azure.mgmt.synapse", azure_mgmt_synapse)

    return cred_mock, mgmt_mock, mgmt_instance


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_discover_requires_subscription_id():
    provider = SynapseWorkspaceProvider()
    with pytest.raises(ValueError, match="subscription_id is required"):
        provider.discover(CREDS)


def test_discover_returns_descriptors(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.workspaces.list.return_value = iter([
        _fake_ws(name="ws-a", rg="rg-1", sql="ws-a.sql.azuresynapse.net"),
        _fake_ws(name="ws-b", rg="rg-2"),
    ])

    provider = SynapseWorkspaceProvider()
    out = provider.discover(CREDS, subscription_id="sub-x")

    assert len(out) == 2
    assert all(isinstance(d, SourceDescriptor) for d in out)
    assert {d.display_name for d in out} == {"ws-a", "ws-b"}
    assert all(d.type == SourceType.SYNAPSE_WORKSPACE for d in out)
    assert all(d.subscription_id == "sub-x" for d in out)
    a = next(d for d in out if d.display_name == "ws-a")
    assert a.resource_group == "rg-1"
    assert a.extras.get("sql_endpoint") == "ws-a.sql.azuresynapse.net"


# ---------------------------------------------------------------------------
# make_clients()
# ---------------------------------------------------------------------------


def test_make_clients_returns_bundle(fake_azure_sdks):
    provider = SynapseWorkspaceProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="/subscriptions/sub-x/resourceGroups/rg-1/.../workspaces/ws-a",
        display_name="ws-a",
        subscription_id="sub-x",
        resource_group="rg-1",
        location="eastus",
    )
    bundle = provider.make_clients(descriptor, CREDS)
    assert isinstance(bundle, SynapseClientBundle)
    assert bundle.subscription_id == "sub-x"
    assert bundle.resource_group == "rg-1"
    assert bundle.workspace_name == "ws-a"
    assert bundle.location == "eastus"
    assert bundle.credential is not None


def test_make_clients_validates_descriptor():
    provider = SynapseWorkspaceProvider()
    bad = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="x", display_name="x", subscription_id=None, resource_group="rg",
    )
    with pytest.raises(ValueError, match="subscription_id"):
        provider.make_clients(bad, CREDS)
    bad2 = SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id="x", display_name="x", subscription_id="s", resource_group=None,
    )
    with pytest.raises(ValueError, match="resource_group"):
        provider.make_clients(bad2, CREDS)


# ---------------------------------------------------------------------------
# _workspace_to_descriptor()
# ---------------------------------------------------------------------------


def test_workspace_to_descriptor_parses_rg_from_arm_id():
    ws = _fake_ws(name="ws-c", rg="rg-3")
    d = _workspace_to_descriptor(ws, "sub-y")
    assert d.resource_group == "rg-3"
    assert d.display_name == "ws-c"
    assert d.subscription_id == "sub-y"
    assert d.location == "eastus"


def test_workspace_to_descriptor_handles_missing_endpoints():
    ws = _fake_ws(name="ws-d", rg="rg-4")
    ws.connectivity_endpoints = None
    d = _workspace_to_descriptor(ws, "sub-z")
    assert d.extras == {}
