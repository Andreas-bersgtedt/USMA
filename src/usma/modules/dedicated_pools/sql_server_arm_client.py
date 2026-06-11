"""ARM client for **standalone** Dedicated SQL pools (formerly SQL DW).

The standalone resource is ``Microsoft.Sql/servers/<server>/databases/<db>``
with ``sku.tier == 'DataWarehouse'``. There is no parent Synapse
workspace, so the existing
:class:`~usma.modules.dedicated_pools.arm_client.SynapseArmClient`
(which calls ``Microsoft.Synapse/workspaces/<ws>/sqlPools``) cannot
enumerate it.

This client implements the same minimal contract — ``list_dedicated_pools()``
+ ``sql_endpoint()`` — so :class:`DedicatedPoolsAnalyzer` can use either
implementation interchangeably. See ADR-0009.
"""
from __future__ import annotations

import logging
from typing import Iterator

from azure.mgmt.sql import SqlManagementClient

from ...auth import get_credential
from ...config import AzureConfig
from .models import PoolInventory

log = logging.getLogger(__name__)


class SqlServerArmClient:
    """Thin wrapper around :class:`SqlManagementClient` scoped to one
    ``Microsoft.Sql/servers/<server>`` resource.
    """

    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        self._client = SqlManagementClient(
            credential=get_credential(azure),
            subscription_id=azure.subscription_id,
        )

    def list_dedicated_pools(self) -> Iterator[PoolInventory]:
        """Yield inventory entries for each DWU database on the server.

        Regular (non-DWU) Azure SQL databases and the system ``master``
        database are skipped. When ``azure.dedicated_pool`` is set the
        list is further filtered to a single database name.
        """
        rg = self._azure.resource_group
        server = self._azure.workspace_name  # repurposed as SQL server name
        log.info(
            "Listing Dedicated SQL pools (formerly SQL DW) on %s/%s ...",
            rg, server,
        )

        dbs = self._client.databases.list_by_server(
            resource_group_name=rg,
            server_name=server,
        )
        for db in dbs:
            if not _is_dwu_database(db):
                continue
            name = getattr(db, "name", None) or ""
            if self._azure.dedicated_pool and name != self._azure.dedicated_pool:
                continue
            sku = getattr(db, "sku", None)
            yield PoolInventory(
                name=name,
                location=getattr(db, "location", "") or "",
                sku_name=getattr(sku, "name", None),
                # On standalone DWU the capacity comes from sku.capacity
                # (e.g. 1000 for DW1000c). Older API versions expose the
                # numeric DWU as ``current_service_objective_name``
                # (e.g. "DW1000c"); we keep capacity as an int when
                # available, otherwise leave it as None — the analyzer
                # downstream only uses it for display.
                sku_capacity=getattr(sku, "capacity", None),
                status=getattr(db, "status", None),
                create_date=getattr(db, "creation_date", None),
                # ``storage_account_type`` is a Synapse-pool-only field
                # (GRS/LRS) — not exposed on standalone SQL databases.
                storage_account_type=None,
                collation=getattr(db, "collation", None),
                max_size_bytes=getattr(db, "max_size_bytes", None),
                tags=getattr(db, "tags", None) or {},
            )

    def sql_endpoint(self) -> str:
        """Return the SQL endpoint FQDN for the server."""
        return f"{self._azure.workspace_name}.database.windows.net"


def _is_dwu_database(db: object) -> bool:
    """Return True when ``db`` is a Dedicated SQL pool (formerly SQL DW).

    Duplicated from
    :func:`usma.sources.synapse_dedicated_sql.provider._is_dwu_database`
    to avoid a sources→modules import (the analyzer must not depend on
    the source-provider package). The two implementations stay in lock-
    step via the shared unit test.
    """
    if getattr(db, "name", "") == "master":
        return False
    sku = getattr(db, "sku", None)
    tier = (getattr(sku, "tier", "") or "").strip().lower()
    if tier == "datawarehouse":
        return True
    edition = (getattr(db, "edition", "") or "").strip().lower()
    return edition == "datawarehouse"


__all__ = ["SqlServerArmClient"]
