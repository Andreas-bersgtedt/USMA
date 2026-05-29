"""Synapse Spark runtime → Fabric Spark runtime compatibility table.

The mapping below reflects the supported runtimes at time of writing. Treat the
result as advisory — Fabric Spark runtime versions evolve frequently.

Sources:
* https://learn.microsoft.com/azure/synapse-analytics/spark/apache-spark-version-support
* https://learn.microsoft.com/fabric/data-engineering/runtime-1-3
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["matches", "upgrade", "deprecated", "unknown"]


@dataclass(frozen=True)
class RuntimeMapping:
    synapse_version: str   # e.g. "3.4"
    fabric_runtime: str    # e.g. "1.3"
    fabric_spark: str      # e.g. "3.5"
    status: Status
    note: str


# Keyed by Synapse Spark major.minor (matches what `SparkPool.spark_version` reports).
_TABLE: dict[str, RuntimeMapping] = {
    "2.4": RuntimeMapping(
        synapse_version="2.4", fabric_runtime="1.1 (legacy)", fabric_spark="3.3",
        status="deprecated",
        note="Spark 2.4 is end-of-life in both Synapse and Fabric. Test for Python 2/3 and Scala 2.11/2.12 differences.",
    ),
    "3.1": RuntimeMapping(
        synapse_version="3.1", fabric_runtime="1.1 / 1.2", fabric_spark="3.3 / 3.4",
        status="upgrade",
        note="Validate Delta Lake version, AQE behavior, and pandas API changes.",
    ),
    "3.2": RuntimeMapping(
        synapse_version="3.2", fabric_runtime="1.2", fabric_spark="3.4",
        status="upgrade",
        note="Minor version bump; review Delta Lake 2.4 → 3.x notes if you pinned the version.",
    ),
    "3.3": RuntimeMapping(
        synapse_version="3.3", fabric_runtime="1.2", fabric_spark="3.4",
        status="upgrade",
        note="Closest Fabric runtime; expect minor behavior changes around AQE and Delta.",
    ),
    "3.4": RuntimeMapping(
        synapse_version="3.4", fabric_runtime="1.3", fabric_spark="3.5",
        status="matches",
        note="Closest equivalent Fabric runtime.",
    ),
}


def map_runtime(synapse_version: str | None) -> RuntimeMapping:
    if not synapse_version:
        return RuntimeMapping(synapse_version="?", fabric_runtime="?", fabric_spark="?",
                              status="unknown", note="Spark version not reported by ARM.")
    # Normalize: keep "X.Y" prefix; ignore patch.
    parts = synapse_version.strip().split(".")
    key = ".".join(parts[:2]) if len(parts) >= 2 else synapse_version
    return _TABLE.get(key, RuntimeMapping(
        synapse_version=synapse_version, fabric_runtime="?", fabric_spark="?",
        status="unknown",
        note=f"No mapping for Spark {synapse_version}; check the Fabric runtime release notes.",
    ))
