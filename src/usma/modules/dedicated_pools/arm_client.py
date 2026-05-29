"""Azure Resource Manager client for Synapse dedicated SQL pools."""
from __future__ import annotations

import logging
from typing import Iterator

from azure.mgmt.synapse import SynapseManagementClient

from ...auth import get_credential
from ...config import AzureConfig
from .models import PoolInventory

log = logging.getLogger(__name__)


class SynapseArmClient:
    """Thin wrapper around `SynapseManagementClient` scoped to a single workspace."""

    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        self._client = SynapseManagementClient(
            credential=get_credential(azure),
            subscription_id=azure.subscription_id,
        )

    def list_dedicated_pools(self) -> Iterator[PoolInventory]:
        """Yield inventory entries for each dedicated SQL pool in the workspace."""
        rg = self._azure.resource_group
        ws = self._azure.workspace_name
        log.info("Listing dedicated SQL pools in %s/%s ...", rg, ws)

        pools = self._client.sql_pools.list_by_workspace(resource_group_name=rg, workspace_name=ws)
        for pool in pools:
            if self._azure.dedicated_pool and pool.name != self._azure.dedicated_pool:
                continue
            sku = getattr(pool, "sku", None)
            yield PoolInventory(
                name=pool.name,
                location=getattr(pool, "location", "") or "",
                sku_name=getattr(sku, "name", None),
                sku_capacity=getattr(sku, "capacity", None),
                status=getattr(pool, "status", None),
                create_date=getattr(pool, "creation_date", None),
                storage_account_type=getattr(pool, "storage_account_type", None),
                collation=getattr(pool, "collation", None),
                max_size_bytes=getattr(pool, "max_size_bytes", None),
                tags=getattr(pool, "tags", None) or {},
            )

    def workspace_sql_endpoint(self) -> str:
        """Return the dedicated-SQL endpoint FQDN for the workspace."""
        return f"{self._azure.workspace_name}.sql.azuresynapse.net"
