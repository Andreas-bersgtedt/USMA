"""ARM client for Synapse Apache Spark (big data) pools."""
from __future__ import annotations

import logging
from typing import Iterator

from azure.mgmt.synapse import SynapseManagementClient

from ...auth import get_credential
from ...config import AzureConfig
from .models import SparkPool

log = logging.getLogger(__name__)


class SparkArmClient:
    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        self._client = SynapseManagementClient(get_credential(azure), azure.subscription_id)

    def list_pools(self) -> Iterator[SparkPool]:
        rg = self._azure.resource_group
        ws = self._azure.workspace_name
        log.info("Listing Spark pools in %s/%s ...", rg, ws)

        for pool in self._client.big_data_pools.list_by_workspace(rg, ws):
            auto_scale = getattr(pool, "auto_scale", None)
            auto_pause = getattr(pool, "auto_pause", None)
            dyn_alloc = getattr(pool, "dynamic_executor_allocation", None)
            yield SparkPool(
                name=pool.name,
                location=getattr(pool, "location", None),
                spark_version=getattr(pool, "spark_version", None),
                node_size=getattr(pool, "node_size", None),
                node_size_family=getattr(pool, "node_size_family", None),
                node_count=getattr(pool, "node_count", None),
                auto_scale_enabled=bool(getattr(auto_scale, "enabled", False)) if auto_scale else False,
                min_node_count=getattr(auto_scale, "min_node_count", None) if auto_scale else None,
                max_node_count=getattr(auto_scale, "max_node_count", None) if auto_scale else None,
                auto_pause_enabled=bool(getattr(auto_pause, "enabled", False)) if auto_pause else False,
                auto_pause_delay_minutes=getattr(auto_pause, "delay_in_minutes", None) if auto_pause else None,
                isolated_compute_enabled=bool(getattr(pool, "isolated_compute", None)
                                              and getattr(pool.isolated_compute, "enabled", False)),
                session_level_packages_enabled=bool(getattr(pool, "session_level_packages_enabled", False)),
                dynamic_executor_allocation_enabled=bool(getattr(dyn_alloc, "enabled", False)) if dyn_alloc else False,
                provisioning_state=getattr(pool, "provisioning_state", None),
                creation_date=getattr(pool, "creation_date", None),
                tags=getattr(pool, "tags", None) or {},
            )
