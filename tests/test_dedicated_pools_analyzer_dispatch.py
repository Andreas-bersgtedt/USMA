"""Tests for :class:`DedicatedPoolsAnalyzer` ARM-client auto-selection.

Verifies that the analyzer picks :class:`SynapseArmClient` for legacy
Synapse-workspace scopes and :class:`SqlServerArmClient` for the new
standalone ``SYNAPSE_DEDICATED_SQL`` scopes — without touching any
network. See ADR-0009.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.sources import SourceDescriptor, SourceType


def _make_cfg(scope_type: SourceType) -> AppConfig:
    azure = AzureConfig(
        tenant_id="t", client_id="c", client_secret="s",
        subscription_id="sub-x",
        resource_group="rg-1",
        workspace_name="thing",
    )
    if scope_type is SourceType.SYNAPSE_DEDICATED_SQL:
        scope = SourceDescriptor(
            type=SourceType.SYNAPSE_DEDICATED_SQL,
            id="/subscriptions/sub-x/resourceGroups/rg-1/providers/Microsoft.Sql/servers/thing",
            display_name="thing",
            subscription_id="sub-x",
            resource_group="rg-1",
        )
    else:
        scope = SourceDescriptor(
            type=SourceType.SYNAPSE_WORKSPACE,
            id="/subscriptions/sub-x/resourceGroups/rg-1/providers/Microsoft.Synapse/workspaces/thing",
            display_name="thing",
            subscription_id="sub-x",
            resource_group="rg-1",
        )
    return AppConfig(azure=azure, sql=SqlConfig(), output_dir=Path("."), scopes=(scope,))


def test_select_arm_client_uses_synapse_for_workspace_scope():
    from usma.modules.dedicated_pools import analyzer as mod

    cfg = _make_cfg(SourceType.SYNAPSE_WORKSPACE)
    with patch.object(mod, "SynapseArmClient") as syn_cls, \
         patch.object(mod, "SqlServerArmClient") as sql_cls:
        syn_cls.return_value = MagicMock(name="syn")
        sql_cls.return_value = MagicMock(name="sql")
        client = mod._select_arm_client(cfg)
        syn_cls.assert_called_once_with(cfg.azure)
        sql_cls.assert_not_called()
        assert client is syn_cls.return_value


def test_select_arm_client_uses_sql_server_for_dedicated_sql_scope():
    from usma.modules.dedicated_pools import analyzer as mod

    cfg = _make_cfg(SourceType.SYNAPSE_DEDICATED_SQL)
    with patch.object(mod, "SynapseArmClient") as syn_cls, \
         patch.object(mod, "SqlServerArmClient") as sql_cls:
        syn_cls.return_value = MagicMock(name="syn")
        sql_cls.return_value = MagicMock(name="sql")
        client = mod._select_arm_client(cfg)
        sql_cls.assert_called_once_with(cfg.azure)
        syn_cls.assert_not_called()
        assert client is sql_cls.return_value


def test_select_arm_client_defaults_to_synapse_when_no_scope():
    """Backwards compat: callers that never set ``scopes`` still get the
    legacy Synapse-workspace ARM client."""
    from usma.modules.dedicated_pools import analyzer as mod

    azure = AzureConfig(
        tenant_id="t", client_id="c", client_secret="s",
        subscription_id="sub-x", resource_group="rg-1", workspace_name="ws",
    )
    cfg = AppConfig(azure=azure, sql=SqlConfig(), output_dir=Path("."))
    with patch.object(mod, "SynapseArmClient") as syn_cls, \
         patch.object(mod, "SqlServerArmClient") as sql_cls:
        syn_cls.return_value = MagicMock(name="syn")
        sql_cls.return_value = MagicMock(name="sql")
        client = mod._select_arm_client(cfg)
        assert client is syn_cls.return_value
        sql_cls.assert_not_called()


def test_analyzer_uses_injected_arm_client():
    """Explicit ``arm_client`` kwarg overrides auto-selection."""
    from usma.modules.dedicated_pools.analyzer import DedicatedPoolsAnalyzer

    cfg = _make_cfg(SourceType.SYNAPSE_DEDICATED_SQL)
    fake_arm = MagicMock(name="injected_arm")
    fake_arm.list_dedicated_pools.return_value = iter([])
    fake_arm.sql_endpoint.return_value = "fake.example.net"

    analyzer = DedicatedPoolsAnalyzer(cfg, arm_client=fake_arm)
    result = analyzer.run()

    fake_arm.list_dedicated_pools.assert_called_once_with()
    fake_arm.sql_endpoint.assert_called_once_with()
    assert result.pools == []


def test_config_auto_populates_dedicated_sql_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """``SMA_SOURCE_TYPE=synapse_dedicated_sql`` yields a single scope of
    that type with ARM id under ``Microsoft.Sql/servers``."""
    env = tmp_path / ".env"
    env.write_text(
        "SMA_SOURCE_TYPE=synapse_dedicated_sql\n"
        "AZURE_TENANT_ID=t\n"
        "AZURE_CLIENT_ID=c\n"
        "AZURE_CLIENT_SECRET=s\n"
        "AZURE_SUBSCRIPTION_ID=sub-x\n"
        "SYNAPSE_RESOURCE_GROUP=rg-1\n"
        "SYNAPSE_WORKSPACE_NAME=devlebdatalakesql\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # Clean ambient env so load_config doesn't pick up the dev machine's values.
    for k in (
        "SMA_SOURCE_TYPE", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP", "SYNAPSE_WORKSPACE_NAME",
        "SYNAPSE_DEDICATED_POOL", "SMA_OUTPUT_DIR",
    ):
        monkeypatch.delenv(k, raising=False)

    from usma.config import load_config

    cfg = load_config(env)
    assert len(cfg.scopes) == 1
    scope = cfg.scopes[0]
    assert scope.type is SourceType.SYNAPSE_DEDICATED_SQL
    assert scope.display_name == "devlebdatalakesql"
    assert scope.resource_group == "rg-1"
    assert "Microsoft.Sql/servers/devlebdatalakesql" in scope.id
    assert scope.extras["sql_server_fqdn"] == "devlebdatalakesql.database.windows.net"
