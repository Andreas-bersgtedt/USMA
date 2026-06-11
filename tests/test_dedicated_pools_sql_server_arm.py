"""Tests for SqlServerArmClient (Slice A — standalone DWU)."""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from usma.config import AzureConfig
from usma.modules.dedicated_pools.models import PoolInventory


def _fake_db(
    *,
    name: str,
    sku_tier: str | None = None,
    sku_name: str | None = None,
    sku_capacity: int | None = None,
    status: str = "Online",
) -> Any:
    db = types.SimpleNamespace()
    db.name = name
    db.status = status
    db.location = "eastus"
    db.collation = "SQL_Latin1_General_CP1_CI_AS"
    db.creation_date = None
    db.max_size_bytes = 1_000_000
    db.tags = {}
    db.edition = None
    if sku_tier or sku_name or sku_capacity is not None:
        db.sku = types.SimpleNamespace(tier=sku_tier, name=sku_name, capacity=sku_capacity)
    else:
        db.sku = None
    return db


@pytest.fixture
def fake_sql_mgmt(monkeypatch: pytest.MonkeyPatch):
    """Stub ``azure.mgmt.sql.SqlManagementClient`` and the AAD credential
    so importing :class:`SqlServerArmClient` doesn't try a real network call.
    """
    mgmt_class = MagicMock(name="SqlManagementClient")
    mgmt_instance = MagicMock(name="mgmt_instance")
    mgmt_class.return_value = mgmt_instance

    azure_mgmt_sql = types.ModuleType("azure.mgmt.sql")
    azure_mgmt_sql.SqlManagementClient = mgmt_class  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "azure.mgmt.sql", azure_mgmt_sql)

    # Also stub the credential factory so ``SqlServerArmClient.__init__``
    # doesn't try to mint a real ClientSecretCredential.
    from usma.modules.dedicated_pools import sql_server_arm_client as mod
    monkeypatch.setattr(mod, "get_credential", lambda _azure: MagicMock(name="cred"))

    # Re-import the module so it picks up the stubbed SqlManagementClient.
    import importlib
    importlib.reload(mod)
    monkeypatch.setattr(mod, "get_credential", lambda _azure: MagicMock(name="cred"))
    return mgmt_class, mgmt_instance, mod


def _cfg(server: str = "srv-a", dedicated_pool: str | None = None) -> AzureConfig:
    return AzureConfig(
        tenant_id="t", client_id="c", client_secret="s",
        subscription_id="sub-x",
        resource_group="rg-1",
        workspace_name=server,            # repurposed as SQL server name
        dedicated_pool=dedicated_pool,
    )


def test_endpoint_uses_database_windows_net(fake_sql_mgmt):
    _, _, mod = fake_sql_mgmt
    client = mod.SqlServerArmClient(_cfg())
    assert client.sql_endpoint() == "srv-a.database.windows.net"


def test_list_dedicated_pools_filters_to_dwu(fake_sql_mgmt):
    _, mgmt_instance, mod = fake_sql_mgmt
    mgmt_instance.databases.list_by_server.return_value = iter([
        _fake_db(name="master"),
        _fake_db(name="dwh1", sku_tier="DataWarehouse", sku_name="DW1000c", sku_capacity=1000),
        _fake_db(name="oltp1", sku_tier="GeneralPurpose"),
        _fake_db(name="dwh2", sku_tier="DataWarehouse", sku_name="DW500c", sku_capacity=500),
        _fake_db(name="hyper", sku_tier="Hyperscale"),
    ])

    client = mod.SqlServerArmClient(_cfg())
    pools = list(client.list_dedicated_pools())
    assert [p.name for p in pools] == ["dwh1", "dwh2"]
    assert all(isinstance(p, PoolInventory) for p in pools)
    assert pools[0].sku_name == "DW1000c"
    assert pools[0].sku_capacity == 1000
    assert pools[0].status == "Online"


def test_list_dedicated_pools_respects_pool_name_filter(fake_sql_mgmt):
    _, mgmt_instance, mod = fake_sql_mgmt
    mgmt_instance.databases.list_by_server.return_value = iter([
        _fake_db(name="dwh1", sku_tier="DataWarehouse"),
        _fake_db(name="dwh2", sku_tier="DataWarehouse"),
    ])
    client = mod.SqlServerArmClient(_cfg(dedicated_pool="dwh2"))
    names = [p.name for p in client.list_dedicated_pools()]
    assert names == ["dwh2"]


def test_list_dedicated_pools_passes_rg_and_server(fake_sql_mgmt):
    _, mgmt_instance, mod = fake_sql_mgmt
    mgmt_instance.databases.list_by_server.return_value = iter([])

    client = mod.SqlServerArmClient(_cfg(server="srv-z"))
    list(client.list_dedicated_pools())
    mgmt_instance.databases.list_by_server.assert_called_once_with(
        resource_group_name="rg-1", server_name="srv-z",
    )
