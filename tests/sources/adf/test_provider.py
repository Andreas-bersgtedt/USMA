"""Tests for AdfProvider (Phase 2)."""
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
from usma.sources.adf.provider import (
    AdfClientBundle,
    AdfProvider,
    _factory_to_descriptor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


CREDS = Credentials(tenant_id="t", client_id="c", client_secret="s")


def _fake_factory(
    *,
    name: str,
    rg: str,
    location: str = "westeurope",
    public_network_access: str | None = "Enabled",
    version: str | None = "2018-06-01",
) -> Any:
    """Build a fake azure-mgmt-datafactory Factory-like object."""
    factory = types.SimpleNamespace()
    factory.id = (
        f"/subscriptions/00000000-0000-0000-0000-000000000000"
        f"/resourceGroups/{rg}/providers/Microsoft.DataFactory/factories/{name}"
    )
    factory.name = name
    factory.location = location
    factory.public_network_access = public_network_access
    factory.version = version
    return factory


@pytest.fixture
def fake_azure_sdks(monkeypatch: pytest.MonkeyPatch):
    """Install fake ``azure.identity`` + ``azure.mgmt.datafactory`` modules."""
    cred_mock = MagicMock(name="ClientSecretCredential")
    cred_instance = MagicMock(name="cred_instance")
    cred_instance.get_token = MagicMock(
        return_value=types.SimpleNamespace(token="fake-token", expires_on=0),
    )
    cred_mock.return_value = cred_instance

    mgmt_mock = MagicMock(name="DataFactoryManagementClient")
    mgmt_instance = MagicMock(name="mgmt_instance")
    mgmt_mock.return_value = mgmt_instance

    azure_identity = types.ModuleType("azure.identity")
    azure_identity.ClientSecretCredential = cred_mock  # type: ignore[attr-defined]
    azure_mgmt_df = types.ModuleType("azure.mgmt.datafactory")
    azure_mgmt_df.DataFactoryManagementClient = mgmt_mock  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setitem(sys.modules, "azure.mgmt.datafactory", azure_mgmt_df)

    return cred_mock, mgmt_mock, mgmt_instance


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_discover_requires_subscription_id():
    provider = AdfProvider()
    with pytest.raises(ValueError, match="subscription_id is required"):
        provider.discover(CREDS)


def test_discover_returns_descriptors(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.factories.list.return_value = iter([
        _fake_factory(name="adf-prod", rg="rg-1"),
        _fake_factory(
            name="adf-dev", rg="rg-2", public_network_access="Disabled",
        ),
    ])

    provider = AdfProvider()
    out = provider.discover(CREDS, subscription_id="sub-x")

    assert len(out) == 2
    assert all(isinstance(d, SourceDescriptor) for d in out)
    assert {d.display_name for d in out} == {"adf-prod", "adf-dev"}
    assert all(d.type == SourceType.ADF for d in out)
    assert all(d.subscription_id == "sub-x" for d in out)
    prod = next(d for d in out if d.display_name == "adf-prod")
    assert prod.resource_group == "rg-1"
    assert prod.extras.get("public_network_access") == "Enabled"
    assert prod.extras.get("version") == "2018-06-01"
    dev = next(d for d in out if d.display_name == "adf-dev")
    assert dev.extras.get("public_network_access") == "Disabled"


# ---------------------------------------------------------------------------
# make_clients()
# ---------------------------------------------------------------------------


def test_make_clients_returns_bundle(fake_azure_sdks):
    provider = AdfProvider()
    descriptor = SourceDescriptor(
        type=SourceType.ADF,
        id="/subscriptions/sub-x/resourceGroups/rg-1/.../factories/adf-prod",
        display_name="adf-prod",
        subscription_id="sub-x",
        resource_group="rg-1",
        location="westeurope",
    )
    bundle = provider.make_clients(descriptor, CREDS)
    assert isinstance(bundle, AdfClientBundle)
    assert bundle.subscription_id == "sub-x"
    assert bundle.resource_group == "rg-1"
    assert bundle.factory_name == "adf-prod"
    assert bundle.location == "westeurope"
    assert bundle.credential is not None


def test_make_clients_validates_descriptor():
    provider = AdfProvider()
    bad = SourceDescriptor(
        type=SourceType.ADF,
        id="x", display_name="x", subscription_id=None, resource_group="rg",
    )
    with pytest.raises(ValueError, match="subscription_id"):
        provider.make_clients(bad, CREDS)
    bad2 = SourceDescriptor(
        type=SourceType.ADF,
        id="x", display_name="x", subscription_id="s", resource_group=None,
    )
    with pytest.raises(ValueError, match="resource_group"):
        provider.make_clients(bad2, CREDS)


# ---------------------------------------------------------------------------
# _factory_to_descriptor()
# ---------------------------------------------------------------------------


def test_factory_to_descriptor_parses_rg_from_arm_id():
    factory = _fake_factory(name="adf-prod", rg="rg-prod")
    out = _factory_to_descriptor(factory, subscription_id="sub-x")
    assert out.type == SourceType.ADF
    assert out.display_name == "adf-prod"
    assert out.resource_group == "rg-prod"
    assert out.subscription_id == "sub-x"
    assert out.location == "westeurope"
    assert out.extras["public_network_access"] == "Enabled"


def test_factory_to_descriptor_handles_missing_optionals():
    factory = types.SimpleNamespace(
        id="", name="adf-x", location=None,
        public_network_access=None, version=None,
    )
    out = _factory_to_descriptor(factory, subscription_id="sub-x")
    assert out.display_name == "adf-x"
    assert out.resource_group == ""
    assert out.location is None
    assert out.extras == {}
