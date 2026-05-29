"""Pydantic models for the storage module.

Captures:

* ADLS / Storage account inventory + capacity metrics (Azure Monitor).
* Dedicated SQL pool actual storage usage in MB / GB (sourced from
  `sys.dm_pdw_nodes_db_partition_stats`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StorageAccountInventory(BaseModel):
    """Static inventory captured from `Microsoft.Storage/storageAccounts`."""

    name: str
    resource_id: str
    location: str | None = None
    sku: str | None = None
    kind: str | None = None
    access_tier: str | None = None
    is_hns_enabled: bool | None = None       # ADLS Gen2 indicator
    primary_endpoint_dfs: str | None = None  # `https://<acct>.dfs.core.windows.net/`
    primary_endpoint_blob: str | None = None
    is_workspace_default: bool = False       # Synapse workspace's default storage
    default_filesystem: str | None = None    # workspace default container
    tags: dict[str, str] = Field(default_factory=dict)


class StorageCapacity(BaseModel):
    """Capacity snapshot for a single storage account, sourced from Azure Monitor."""

    account_name: str
    captured_at: datetime
    used_capacity_bytes: int | None = None
    blob_capacity_bytes: int | None = None
    blob_count: int | None = None
    container_count: int | None = None
    file_capacity_bytes: int | None = None
    file_count: int | None = None
    table_capacity_bytes: int | None = None
    queue_capacity_bytes: int | None = None

    # Convenience derived fields (population is the analyzer's responsibility).
    used_capacity_mb: float | None = None
    used_capacity_gb: float | None = None
    blob_capacity_mb: float | None = None
    blob_capacity_gb: float | None = None


class DedicatedPoolStorage(BaseModel):
    """Actual storage occupied by a dedicated SQL pool (sum across all tables)."""

    pool_name: str
    captured_at: datetime
    table_count: int = 0
    row_count: int = 0
    reserved_space_mb: float = 0.0
    data_space_mb: float = 0.0
    index_space_mb: float = 0.0
    unused_space_mb: float = 0.0
    reserved_space_gb: float = 0.0
    data_space_gb: float = 0.0
    index_space_gb: float = 0.0
    # From `sys.databases.max_size` / pool inventory; helps surface headroom.
    max_size_bytes: int | None = None
    max_size_gb: float | None = None
    used_pct_of_max: float | None = None
    notes: list[str] = Field(default_factory=list)


class StorageAnalysis(BaseModel):
    """Top-level storage analysis result."""

    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    accounts: list[StorageAccountInventory] = Field(default_factory=list)
    capacities: list[StorageCapacity] = Field(default_factory=list)
    dedicated_pool_storage: list[DedicatedPoolStorage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
