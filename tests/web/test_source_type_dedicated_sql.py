"""Tests for the synapse_dedicated_sql web plumbing (Slice B).

Mirrors :mod:`tests.test_source_type_plumbing` for the new standalone
Dedicated SQL pool source type. Covers:

1. ``read_config`` / ``write_config`` round-trip ``SMA_SOURCE_TYPE=
   synapse_dedicated_sql``.
2. ``load_config`` materialises a ``SourceType.SYNAPSE_DEDICATED_SQL``
   scope with a ``Microsoft.Sql/servers/...`` ARM id.
3. ``discover_sql_servers`` delegates to
   :class:`SynapseDedicatedSqlProvider.discover` and returns the
   expected :class:`WorkspaceSummary` list.
4. ``validate_config_live`` takes the new branch (no Synapse-workspace
   ARM probe is attempted).
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest

from usma.config import load_config
from usma.sources import SourceType
from usma.web.config_io import (
    discover_sql_servers,
    read_config,
    validate_config_live,
    write_config,
)
from usma.web.schemas import AppConfigUpdate, AzureConfigUpdate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_env(env_file: Path, *, source_type: str | None = None) -> None:
    lines = [
        "AZURE_TENANT_ID=11111111-1111-1111-1111-111111111111",
        "AZURE_CLIENT_ID=22222222-2222-2222-2222-222222222222",
        "AZURE_CLIENT_SECRET=secret",
        "AZURE_SUBSCRIPTION_ID=33333333-3333-3333-3333-333333333333",
        "SYNAPSE_RESOURCE_GROUP=rg-data",
        "SYNAPSE_WORKSPACE_NAME=examplesqlserver",
    ]
    if source_type is not None:
        lines.append(f"SMA_SOURCE_TYPE={source_type}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP",
        "SYNAPSE_WORKSPACE_NAME", "SYNAPSE_DEDICATED_POOL",
        "SMA_SOURCE_TYPE", "SMA_OUTPUT_DIR",
    ):
        monkeypatch.delenv(k, raising=False)


def _fake_server(*, name: str, rg: str, location: str = "eastus") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=(
            f"/subscriptions/00000000-0000-0000-0000-000000000000"
            f"/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{name}"
        ),
        name=name,
        location=location,
        fully_qualified_domain_name=f"{name}.database.windows.net",
    )


def _fake_db(*, name: str, sku_tier: str | None = None) -> types.SimpleNamespace:
    db = types.SimpleNamespace(
        name=name,
        status="Online",
        edition=None,
        location="eastus",
        collation=None,
        creation_date=None,
        max_size_bytes=None,
        tags={},
    )
    if sku_tier:
        db.sku = types.SimpleNamespace(tier=sku_tier, name="DW1000c", capacity=1000)
    else:
        db.sku = None
    return db


@pytest.fixture
def fake_sql_sdks(monkeypatch: pytest.MonkeyPatch):
    """Stub azure.identity + azure.mgmt.sql so the provider can be
    exercised without any network calls or real credentials."""
    cred_instance = MagicMock(name="cred_instance")
    cred_instance.get_token = MagicMock(
        return_value=types.SimpleNamespace(token="fake-token", expires_on=0),
    )
    cred_cls = MagicMock(name="ClientSecretCredential", return_value=cred_instance)

    mgmt_instance = MagicMock(name="mgmt_instance")
    mgmt_cls = MagicMock(name="SqlManagementClient", return_value=mgmt_instance)

    azure_identity = types.ModuleType("azure.identity")
    azure_identity.ClientSecretCredential = cred_cls  # type: ignore[attr-defined]
    azure_mgmt_sql = types.ModuleType("azure.mgmt.sql")
    azure_mgmt_sql.SqlManagementClient = mgmt_cls  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setitem(sys.modules, "azure.mgmt.sql", azure_mgmt_sql)
    return cred_cls, mgmt_cls, mgmt_instance


# ---------------------------------------------------------------------------
# read / write round-trip
# ---------------------------------------------------------------------------


def test_read_config_returns_synapse_dedicated_sql(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env, source_type="synapse_dedicated_sql")
    cfg = read_config(env)
    assert cfg.azure.source_type == "synapse_dedicated_sql"
    assert cfg.azure.workspace_name == "examplesqlserver"


def test_write_config_persists_synapse_dedicated_sql(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env)
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(source_type="synapse_dedicated_sql"),
    )
    write_config(env, update)
    contents = env.read_text(encoding="utf-8")
    assert "SMA_SOURCE_TYPE=synapse_dedicated_sql" in contents
    assert read_config(env).azure.source_type == "synapse_dedicated_sql"


# ---------------------------------------------------------------------------
# load_config builds the right scope
# ---------------------------------------------------------------------------


def test_load_config_builds_synapse_dedicated_sql_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = tmp_path / ".env"
    _seed_env(env, source_type="synapse_dedicated_sql")
    _clear_env(monkeypatch)
    cfg = load_config(env)
    primary = cfg.primary_scope()
    assert primary is not None
    assert primary.type is SourceType.SYNAPSE_DEDICATED_SQL
    assert (
        "Microsoft.Sql/servers/examplesqlserver" in primary.id
    ), primary.id
    assert primary.resource_group == "rg-data"
    assert (
        primary.extras.get("sql_server_fqdn")
        == "examplesqlserver.database.windows.net"
    )


# ---------------------------------------------------------------------------
# discover_sql_servers
# ---------------------------------------------------------------------------


def test_discover_sql_servers_gates_on_required_env(tmp_path: Path) -> None:
    """Missing SP creds → single failing check, no servers returned."""
    env = tmp_path / ".env"
    env.write_text("SMA_SOURCE_TYPE=synapse_dedicated_sql\n", encoding="utf-8")
    checks, servers = discover_sql_servers(env)
    assert servers == []
    assert any(not c.ok and "missing required fields" in (c.detail or "") for c in checks)


def test_discover_sql_servers_returns_workspace_summaries(
    tmp_path: Path, fake_sql_sdks,
) -> None:
    _, _, mgmt_instance = fake_sql_sdks
    mgmt_instance.servers.list.return_value = iter([
        _fake_server(name="srv-a", rg="rg-1"),
        _fake_server(name="srv-b", rg="rg-2", location="westeu"),
    ])
    env = tmp_path / ".env"
    _seed_env(env, source_type="synapse_dedicated_sql")

    checks, servers = discover_sql_servers(env)

    assert {s.name for s in servers} == {"srv-a", "srv-b"}
    assert all(s.sql_endpoint and s.sql_endpoint.endswith(".database.windows.net") for s in servers)
    visible = next(c for c in checks if "SQL servers visible" in c.name)
    assert visible.ok
    assert "2 server(s)" in (visible.detail or "")


# ---------------------------------------------------------------------------
# validate_config_live takes the new branch
# ---------------------------------------------------------------------------


def test_validate_config_live_uses_provider_for_synapse_dedicated_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_sql_sdks,
) -> None:
    """The synapse_dedicated_sql branch must run the provider's checks
    and skip the Synapse-workspace ARM probe (no SynapseManagementClient
    call should happen)."""
    _, _, mgmt_instance = fake_sql_sdks
    mgmt_instance.servers.get.return_value = _fake_server(name="examplesqlserver", rg="rg-data")
    mgmt_instance.databases.list_by_server.return_value = iter([
        _fake_db(name="master"),
        _fake_db(name="dwh1", sku_tier="DataWarehouse"),
    ])
    mgmt_instance.servers.list.return_value = iter([
        _fake_server(name="examplesqlserver", rg="rg-data"),
    ])

    # Booby-trap the Synapse SDK so the test fails loudly if the legacy
    # workspace-probe branch ever fires for this source type.
    bad_synapse = types.ModuleType("azure.mgmt.synapse")
    bad_synapse.SynapseManagementClient = MagicMock(  # type: ignore[attr-defined]
        side_effect=AssertionError("Synapse workspace ARM probe must NOT run for synapse_dedicated_sql"),
    )
    monkeypatch.setitem(sys.modules, "azure.mgmt.synapse", bad_synapse)

    env = tmp_path / ".env"
    _seed_env(env, source_type="synapse_dedicated_sql")
    checks, servers = validate_config_live(env)

    names = {c.name for c in checks}
    assert "SQL server (ARM Reader)" in names
    assert "Dedicated SQL pools (formerly SQL DW)" in names
    assert "AAD token (SQL)" in names
    assert "Synapse workspace (ARM Reader)" not in names
    # The discovery hop appended at the end re-uses the provider so we get
    # the visible-servers row too.
    assert any(s.name == "examplesqlserver" for s in servers)


def test_read_config_falls_back_for_unknown_source_type_keeps_default(tmp_path: Path) -> None:
    """Regression: the widened source-type allow-list still rejects junk."""
    env = tmp_path / ".env"
    _seed_env(env, source_type="not_real")
    cfg = read_config(env)
    assert cfg.azure.source_type == "synapse_workspace"
