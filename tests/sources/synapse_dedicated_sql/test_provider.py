"""Tests for SynapseDedicatedSqlProvider (Slice A — standalone DWU)."""
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
from usma.sources.synapse_dedicated_sql.provider import (
    SqlServerClientBundle,
    SynapseDedicatedSqlProvider,
    _is_dwu_database,
    _server_to_descriptor,
)


CREDS = Credentials(tenant_id="t", client_id="c", client_secret="s")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _fake_server(
    *,
    name: str,
    rg: str,
    location: str = "eastus",
    fqdn: str | None = None,
) -> Any:
    s = types.SimpleNamespace()
    s.id = (
        f"/subscriptions/00000000-0000-0000-0000-000000000000"
        f"/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{name}"
    )
    s.name = name
    s.location = location
    s.fully_qualified_domain_name = fqdn or f"{name}.database.windows.net"
    return s


def _fake_db(
    *,
    name: str,
    sku_tier: str | None = None,
    sku_name: str | None = None,
    sku_capacity: int | None = None,
    edition: str | None = None,
    status: str = "Online",
) -> Any:
    db = types.SimpleNamespace()
    db.name = name
    db.status = status
    db.edition = edition
    db.location = "eastus"
    db.collation = "SQL_Latin1_General_CP1_CI_AS"
    db.creation_date = None
    db.max_size_bytes = None
    db.tags = {}
    if sku_tier or sku_name or sku_capacity is not None:
        db.sku = types.SimpleNamespace(tier=sku_tier, name=sku_name, capacity=sku_capacity)
    else:
        db.sku = None
    return db


@pytest.fixture
def fake_azure_sdks(monkeypatch: pytest.MonkeyPatch):
    cred_mock = MagicMock(name="ClientSecretCredential")
    cred_instance = MagicMock(name="cred_instance")
    cred_instance.get_token = MagicMock(
        return_value=types.SimpleNamespace(token="fake-token", expires_on=0),
    )
    cred_mock.return_value = cred_instance

    mgmt_mock = MagicMock(name="SqlManagementClient")
    mgmt_instance = MagicMock(name="mgmt_instance")
    mgmt_mock.return_value = mgmt_instance

    azure_identity = types.ModuleType("azure.identity")
    azure_identity.ClientSecretCredential = cred_mock  # type: ignore[attr-defined]
    azure_mgmt_sql = types.ModuleType("azure.mgmt.sql")
    azure_mgmt_sql.SqlManagementClient = mgmt_mock  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setitem(sys.modules, "azure.mgmt.sql", azure_mgmt_sql)

    return cred_mock, mgmt_mock, mgmt_instance


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_discover_requires_subscription_id():
    provider = SynapseDedicatedSqlProvider()
    with pytest.raises(ValueError, match="subscription_id is required"):
        provider.discover(CREDS)


def test_discover_returns_descriptors(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.servers.list.return_value = iter([
        _fake_server(name="srv-a", rg="rg-1"),
        _fake_server(name="srv-b", rg="rg-2", fqdn="srv-b.database.windows.net"),
    ])

    provider = SynapseDedicatedSqlProvider()
    out = provider.discover(CREDS, subscription_id="sub-x")

    assert len(out) == 2
    assert all(isinstance(d, SourceDescriptor) for d in out)
    assert {d.display_name for d in out} == {"srv-a", "srv-b"}
    assert all(d.type == SourceType.SYNAPSE_DEDICATED_SQL for d in out)
    assert all(d.subscription_id == "sub-x" for d in out)
    a = next(d for d in out if d.display_name == "srv-a")
    assert a.resource_group == "rg-1"
    assert a.extras["sql_server_fqdn"] == "srv-a.database.windows.net"


# ---------------------------------------------------------------------------
# make_clients()
# ---------------------------------------------------------------------------


def test_make_clients_returns_bundle(fake_azure_sdks):
    provider = SynapseDedicatedSqlProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id="/subscriptions/sub-x/resourceGroups/rg-1/providers/Microsoft.Sql/servers/srv-a",
        display_name="srv-a",
        subscription_id="sub-x",
        resource_group="rg-1",
        location="eastus",
        extras={"sql_server_fqdn": "srv-a.database.windows.net"},
    )
    bundle = provider.make_clients(descriptor, CREDS)
    assert isinstance(bundle, SqlServerClientBundle)
    assert bundle.subscription_id == "sub-x"
    assert bundle.resource_group == "rg-1"
    assert bundle.server_name == "srv-a"
    assert bundle.server_fqdn == "srv-a.database.windows.net"
    assert bundle.location == "eastus"
    assert bundle.credential is not None


def test_make_clients_validates_descriptor():
    provider = SynapseDedicatedSqlProvider()
    bad = SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id="x", display_name="x", subscription_id=None, resource_group="rg",
    )
    with pytest.raises(ValueError, match="subscription_id"):
        provider.make_clients(bad, CREDS)
    bad2 = SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id="x", display_name="x", subscription_id="s", resource_group=None,
    )
    with pytest.raises(ValueError, match="resource_group"):
        provider.make_clients(bad2, CREDS)


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_pass_with_dwu_databases(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.servers.get.return_value = _fake_server(name="srv-a", rg="rg-1")
    mgmt_instance.databases.list_by_server.return_value = iter([
        _fake_db(name="master"),  # always excluded
        _fake_db(name="dwh1", sku_tier="DataWarehouse", sku_name="DW1000c", sku_capacity=1000),
        _fake_db(name="oltp", sku_tier="GeneralPurpose"),
    ])
    provider = SynapseDedicatedSqlProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id="/subscriptions/sub-x/resourceGroups/rg-1/providers/Microsoft.Sql/servers/srv-a",
        display_name="srv-a",
        subscription_id="sub-x",
        resource_group="rg-1",
    )
    checks = provider.validate(descriptor, CREDS)
    names = {c.name: c for c in checks}
    assert names["SQL server (ARM Reader)"].ok
    assert names["Dedicated SQL pools (formerly SQL DW)"].ok
    assert "1 DWU" in names["Dedicated SQL pools (formerly SQL DW)"].detail
    assert names["AAD token (SQL)"].ok


def test_validate_warns_when_no_dwu_databases(fake_azure_sdks):
    _, _, mgmt_instance = fake_azure_sdks
    mgmt_instance.servers.get.return_value = _fake_server(name="srv-a", rg="rg-1")
    mgmt_instance.databases.list_by_server.return_value = iter([
        _fake_db(name="master"),
        _fake_db(name="oltp", sku_tier="GeneralPurpose"),
    ])
    provider = SynapseDedicatedSqlProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id="x", display_name="srv-a",
        subscription_id="sub-x", resource_group="rg-1",
    )
    checks = provider.validate(descriptor, CREDS)
    dwu = next(c for c in checks if c.name == "Dedicated SQL pools (formerly SQL DW)")
    assert not dwu.ok
    assert "No databases" in dwu.detail


# ---------------------------------------------------------------------------
# _is_dwu_database() — same predicate as the ARM-client copy
# ---------------------------------------------------------------------------


def test_is_dwu_database_matches_on_sku_tier():
    assert _is_dwu_database(_fake_db(name="dwh", sku_tier="DataWarehouse"))
    assert _is_dwu_database(_fake_db(name="dwh", sku_tier="datawarehouse"))


def test_is_dwu_database_matches_on_edition_legacy():
    assert _is_dwu_database(_fake_db(name="dwh", edition="DataWarehouse"))


def test_is_dwu_database_rejects_master():
    assert not _is_dwu_database(_fake_db(name="master", sku_tier="DataWarehouse"))


def test_is_dwu_database_rejects_oltp():
    assert not _is_dwu_database(_fake_db(name="oltp", sku_tier="GeneralPurpose"))
    assert not _is_dwu_database(_fake_db(name="hyper", sku_tier="Hyperscale"))


def test_is_dwu_database_handles_missing_sku():
    db = _fake_db(name="dwh", edition="DataWarehouse")
    db.sku = None
    assert _is_dwu_database(db)


# ---------------------------------------------------------------------------
# _server_to_descriptor()
# ---------------------------------------------------------------------------


def test_server_to_descriptor_parses_rg_from_arm_id():
    server = _fake_server(name="srv-a", rg="rg-prod", location="westeu")
    d = _server_to_descriptor(server, "sub-y")
    assert d.type == SourceType.SYNAPSE_DEDICATED_SQL
    assert d.display_name == "srv-a"
    assert d.subscription_id == "sub-y"
    assert d.resource_group == "rg-prod"
    assert d.location == "westeu"
    assert d.extras["sql_server_fqdn"] == "srv-a.database.windows.net"
