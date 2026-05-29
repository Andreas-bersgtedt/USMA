"""Fabric-compatibility mapping for Databricks workflow tasks.

Phase 4 Slice 4-B keeps this intentionally small — a coarse, deterministic
mapping from Databricks task types to a Fabric support label + human
note. Slice 4-C will widen this into the full ``fabric_mapping`` rule
surface (effort points, target Fabric artifact, doc URL).
"""
from __future__ import annotations

from typing import Literal


TaskSupport = Literal["supported", "partial", "unsupported", "unknown"]


# Order matters only for readability; the dict is consulted by key.
_TASK_SUPPORT: dict[str, tuple[TaskSupport, str]] = {
    "notebook_task": (
        "supported",
        "Notebook task — migrate to a Fabric notebook job.",
    ),
    "spark_python_task": (
        "supported",
        "Spark Python task — port to a Fabric notebook or Spark job definition.",
    ),
    "spark_jar_task": (
        "partial",
        "JAR task — Fabric supports Spark JARs via Spark job definitions; verify Scala version.",
    ),
    "spark_submit_task": (
        "partial",
        "spark-submit task — Fabric Spark job definition covers most cases; non-standard configs need review.",
    ),
    "sql_task": (
        "supported",
        "SQL task — map to a Fabric Warehouse stored procedure or a Lakehouse SQL endpoint script.",
    ),
    "pipeline_task": (
        "partial",
        "DLT pipeline task — Fabric data pipelines + dataflow gen2 cover most patterns; DLT-specific expectations need redesign.",
    ),
    "dbt_task": (
        "partial",
        "dbt task — supported in Fabric via dbt-fabric adapter; review profiles.yml + warehouse target.",
    ),
    "run_job_task": (
        "supported",
        "Run-job task — map to a Fabric data pipeline 'Invoke pipeline' activity.",
    ),
    "condition_task": (
        "supported",
        "Condition task — map to Fabric data pipeline If Condition activity.",
    ),
    "for_each_task": (
        "supported",
        "For-each task — map to Fabric data pipeline ForEach activity.",
    ),
}


def classify_task(task_type: str | None) -> tuple[TaskSupport, str]:
    """Return ``(support_label, note)`` for a Databricks task type.

    Unknown types map to ``("unknown", "...")`` so the analyzer always
    has a non-empty note to surface in the report.
    """
    if not task_type:
        return ("unknown", "Unrecognised task type — manual review required.")
    return _TASK_SUPPORT.get(
        task_type,
        ("unknown", f"Unrecognised task type '{task_type}' — manual review required."),
    )


# ---------------------------------------------------------------------------
# SQL warehouse → Fabric Warehouse mapping (deterministic).
# ---------------------------------------------------------------------------

# Databricks SQL warehouse type → (support, Fabric target, note).
# All three sub-types map to Fabric Warehouse; ``CLASSIC`` is flagged
# ``partial`` because BI caching + result-set behaviour can differ.
_WAREHOUSE_TARGETS: dict[str, tuple[TaskSupport, str, str]] = {
    "SERVERLESS": (
        "supported",
        "Fabric Warehouse",
        "Serverless SQL warehouse — Fabric Warehouse is serverless by design; mapping is direct.",
    ),
    "PRO": (
        "supported",
        "Fabric Warehouse",
        "Pro SQL warehouse — Fabric Warehouse covers the equivalent T-SQL surface.",
    ),
    "CLASSIC": (
        "partial",
        "Fabric Warehouse",
        "Classic SQL warehouse — Fabric Warehouse is the target; review long-running BI workloads for caching and result-cache differences.",
    ),
}


# Standard Fabric capacity SKUs (F-units). One F-unit ≈ one CU.
FABRIC_SKUS: tuple[int, ...] = (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048)

# Default headroom multiplier applied to the lookback-window average CU
# usage before picking an F-SKU. REST ``query_history`` exposes only
# totals (not minute-level peaks); the multiplier covers the gap between
# the smoothed average and realistic BI burst concurrency. Override per
# call when you have better evidence (e.g. minute-grain telemetry).
DEFAULT_PEAK_TO_AVG_HEADROOM: float = 4.0


def classify_warehouse(
    warehouse_type: str | None,
) -> tuple[TaskSupport, str, str]:
    """Return ``(support_label, fabric_target, note)`` for a SQL warehouse.

    Unknown types map to ``("unknown", "Fabric Warehouse", "...")`` so
    the caller always has a non-empty mapping payload to surface.
    """
    key = (warehouse_type or "").upper()
    if key in _WAREHOUSE_TARGETS:
        return _WAREHOUSE_TARGETS[key]
    return (
        "unknown",
        "Fabric Warehouse",
        (
            f"Unrecognised warehouse type '{warehouse_type}' — defaulting to "
            "Fabric Warehouse, but verify the source SKU before sizing."
        ),
    )


def recommend_fabric_sku(
    avg_concurrent_cus: float | None,
    *,
    headroom: float = DEFAULT_PEAK_TO_AVG_HEADROOM,
) -> str | None:
    """Pick the smallest standard F-SKU that covers ``avg_concurrent_cus * headroom``.

    Returns ``None`` when ``avg_concurrent_cus`` is missing or non-positive
    (no usage signal — sizing withheld). Returns ``"F2048+"`` when the
    workload exceeds the largest standard SKU.
    """
    if avg_concurrent_cus is None or avg_concurrent_cus <= 0:
        return None
    if headroom <= 0:
        headroom = DEFAULT_PEAK_TO_AVG_HEADROOM
    sized = float(avg_concurrent_cus) * float(headroom)
    for sku in FABRIC_SKUS:
        if float(sku) >= sized:
            return f"F{sku}"
    return f"F{FABRIC_SKUS[-1]}+"


__all__ = [
    "classify_task",
    "classify_warehouse",
    "recommend_fabric_sku",
    "TaskSupport",
    "FABRIC_SKUS",
    "DEFAULT_PEAK_TO_AVG_HEADROOM",
]
