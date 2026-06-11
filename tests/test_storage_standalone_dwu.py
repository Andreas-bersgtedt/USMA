"""Slice G — standalone Dedicated SQL pool (formerly SQL DW) storage support.

Covers:
- ``StorageAnalyzer`` skips workspace ADLS / blob inventory when the
  primary scope is ``SYNAPSE_DEDICATED_SQL`` (no parent workspace =
  no accounts to list) and runs only the per-pool DMV path so the
  Dashboard Storage section gets populated.
- The pool ARM client used for DMV discovery comes from the unified
  Protocol (``_select_arm_client(cfg)``) — ``SqlServerArmClient`` for
  standalone scopes, ``SynapseArmClient`` otherwise.
- ``MODULE_SPECS["storage"].supports`` now includes
  ``SYNAPSE_DEDICATED_SQL`` so the SPA + planner expose the module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from unittest.mock import patch

from usma.config import AppConfig, AzureConfig, SourceDescriptor, SqlConfig
from usma.modules.dedicated_pools.models import PoolInventory
from usma.modules.spec import MODULE_SPECS
from usma.modules.storage.analyzer import StorageAnalyzer
from usma.sources import SourceType


def _standalone_cfg() -> AppConfig:
    azure = AzureConfig(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        subscription_id="sub",
        resource_group="rg",
        workspace_name="srv",  # repurposed as SQL server name for standalone
        dedicated_pool="testdedicatedpool",
    )
    scope = SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id=(
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.Sql/servers/srv/databases/testdedicatedpool"
        ),
        display_name="testdedicatedpool",
        subscription_id="sub",
        resource_group="rg",
    )
    return AppConfig(
        azure=azure, sql=SqlConfig(), output_dir=Path("./output"), scopes=[scope],
    )


def _workspace_cfg() -> AppConfig:
    azure = AzureConfig(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        subscription_id="sub",
        resource_group="rg",
        workspace_name="ws",
    )
    return AppConfig(azure=azure, sql=SqlConfig(), output_dir=Path("./output"))


class _FakeStandalonePoolArm:
    """Stands in for ``SqlServerArmClient`` — yields one DWU pool."""

    def __init__(self) -> None:
        self.list_called = False
        self.endpoint_called = False

    def list_dedicated_pools(self) -> Iterable[PoolInventory]:
        self.list_called = True
        yield PoolInventory(
            name="testdedicatedpool",
            location="northeurope",
            sku_name="DataWarehouse",
            sku_capacity=9000,
            status="Online",
            create_date=None,
            storage_account_type=None,
            collation="SQL_Latin1_General_CP1_CI_AS",
            max_size_bytes=240 * 1024 ** 4,
            tags={},
        )

    def sql_endpoint(self) -> str:
        self.endpoint_called = True
        return "srv.database.windows.net"


def test_analyzer_skips_workspace_inventory_for_standalone_scope() -> None:
    """Standalone scope must not construct or invoke the workspace
    ``StorageArmClient``. The per-pool DMV path must still run via the
    Protocol-selected pool ARM client.
    """
    fake_pool_arm = _FakeStandalonePoolArm()

    with patch(
        "usma.modules.storage.analyzer.StorageArmClient"
    ) as MockStorageArm, patch(
        "usma.modules.storage.analyzer.DedicatedPoolSqlClient"
    ) as MockSqlClient:
        # If the DMV path opens a session, return a synthetic size row.
        MockSqlClient.return_value.fetch_all.return_value = [{
            "reserved_space_mb": 1024.0,
            "data_space_mb": 800.0,
            "index_space_mb": 100.0,
            "unused_space_mb": 124.0,
            "row_count": 1000000,
        }]
        analyzer = StorageAnalyzer(_standalone_cfg(), pool_arm_client=fake_pool_arm)

        # Workspace ARM client must not be constructed at all.
        MockStorageArm.assert_not_called()
        assert analyzer._arm is None  # type: ignore[attr-defined]

        result = analyzer.run()

        # The pool ARM client was used to discover + endpoint the DMV.
        assert fake_pool_arm.list_called is True
        assert fake_pool_arm.endpoint_called is True
        # Per-pool storage row produced.
        assert len(result.dedicated_pool_storage) == 1
        entry = result.dedicated_pool_storage[0]
        assert entry.pool_name == "testdedicatedpool"
        assert entry.max_size_bytes == 240 * 1024 ** 4
        # Workspace-only artefacts must remain empty.
        assert result.accounts == []
        assert result.capacities == []


def test_analyzer_workspace_path_still_constructs_storage_arm() -> None:
    """Workspace scope must continue to build the StorageArmClient.

    We patch ``_select_arm_client`` so the analyzer doesn't try to talk
    to real Azure; we only assert the workspace branch did wire up
    ``StorageArmClient`` and the pool-ARM Protocol.
    """
    with patch(
        "usma.modules.storage.analyzer.StorageArmClient"
    ) as MockStorageArm, patch(
        "usma.modules.storage.analyzer._select_arm_client"
    ) as MockSelect:
        MockSelect.return_value = _FakeStandalonePoolArm()
        analyzer = StorageAnalyzer(_workspace_cfg())
        assert analyzer._arm is not None  # type: ignore[attr-defined]
        MockStorageArm.assert_called_once()
        MockSelect.assert_called_once()


def test_module_spec_storage_supports_synapse_dedicated_sql() -> None:
    spec = MODULE_SPECS["storage"]
    assert spec.supports_source(SourceType.SYNAPSE_DEDICATED_SQL)
    # Workspace must still be supported.
    assert spec.supports_source(SourceType.SYNAPSE_WORKSPACE)
