"""Fabric-compatibility mapping for BigQuery tables / routines / jobs.

Phase 5 Slice 5-B keeps this intentionally coarse — a deterministic
lookup that gives the SPA + Markdown report a non-empty support label
+ note for every BigQuery artifact kind. Slice 5-C widens this into
the full ``fabric_mapping`` rule surface (effort points, target Fabric
artifact, doc URL) via ``rules_for_bigquery_workloads``.
"""
from __future__ import annotations

from typing import Literal


TableSupport = Literal["supported", "partial", "unsupported", "unknown"]


_TABLE_SUPPORT: dict[str, tuple[TableSupport, str]] = {
    "TABLE": (
        "supported",
        "Standard table — migrate to a Fabric Warehouse or Lakehouse Delta table.",
    ),
    "VIEW": (
        "partial",
        "View — Fabric Warehouse supports SQL views; review for BigQuery-specific functions.",
    ),
    "MATERIALIZED_VIEW": (
        "partial",
        "Materialized view — Fabric Warehouse materialized views differ in refresh semantics; review.",
    ),
    "EXTERNAL": (
        "partial",
        "External table — map to a Fabric Lakehouse shortcut or OneLake external table.",
    ),
    "SNAPSHOT": (
        "unsupported",
        "Table snapshot — no Fabric equivalent; convert to a Delta time-travel query or scheduled copy.",
    ),
    "CLONE": (
        "unsupported",
        "Table clone — no zero-copy clone in Fabric; replace with a CTAS or shallow copy.",
    ),
}


_ROUTINE_SUPPORT: dict[str, tuple[TableSupport, str]] = {
    "SCALAR_FUNCTION": (
        "partial",
        "Scalar UDF — Fabric Warehouse supports T-SQL scalar UDFs; rewrite BigQuery SQL UDF body.",
    ),
    "PROCEDURE": (
        "partial",
        "Stored procedure — Fabric Warehouse supports T-SQL procedures; rewrite BigQuery scripting.",
    ),
    "TABLE_VALUED_FUNCTION": (
        "partial",
        "Table-valued function — Fabric Warehouse supports inline TVFs; rewrite body.",
    ),
    "AGGREGATE_FUNCTION": (
        "unsupported",
        "User-defined aggregate — no direct Fabric equivalent; consider Spark UDAF in a Lakehouse.",
    ),
}


# Job types group naturally into "data-plane query" (QUERY) vs
# "data-movement" (LOAD/EXTRACT/COPY). Slice 5-C will refine.
_JOB_TYPE_BUCKET: dict[str, str] = {
    "QUERY": "query",
    "LOAD": "ingest",
    "EXTRACT": "egress",
    "COPY": "copy",
}


def classify_table(table_type: str | None) -> tuple[TableSupport, str]:
    """Return ``(support_label, note)`` for a BigQuery table kind."""
    if not table_type:
        return ("unknown", "Unrecognised table type — manual review required.")
    return _TABLE_SUPPORT.get(
        table_type,
        ("unknown", f"Unrecognised table type '{table_type}' — manual review required."),
    )


def classify_routine(routine_type: str | None) -> tuple[TableSupport, str]:
    """Return ``(support_label, note)`` for a BigQuery routine kind."""
    if not routine_type:
        return ("unknown", "Unrecognised routine type — manual review required.")
    return _ROUTINE_SUPPORT.get(
        routine_type,
        ("unknown", f"Unrecognised routine type '{routine_type}' — manual review required."),
    )


def job_type_bucket(job_type: str | None) -> str:
    """Coarse migration bucket for a BigQuery job type."""
    if not job_type:
        return "unknown"
    return _JOB_TYPE_BUCKET.get(job_type.upper(), "unknown")


__all__ = ["classify_table", "classify_routine", "job_type_bucket", "TableSupport"]
