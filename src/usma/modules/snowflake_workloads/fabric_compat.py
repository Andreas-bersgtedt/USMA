"""Fabric-compatibility mapping for Snowflake objects.

Phase 7 Slice 7-C keeps this intentionally coarse — a deterministic
lookup that gives the SPA + Markdown report a non-empty support label
+ note for every Snowflake object kind. Slice 7-D widens this into the
full ``fabric_mapping`` rule surface (effort points, target Fabric
artifact, doc URL) via ``rules_for_snowflake_workloads``.

Reference for the per-kind verdicts:
- Tables / views: Fabric Warehouse + Lakehouse Delta cover the common case.
- Materialized views: Fabric Warehouse MVs differ on refresh semantics.
- External / Iceberg tables: map to Lakehouse shortcuts or OneLake external tables.
- Dynamic tables: no direct Fabric equivalent — partial via materialized lake views.
- Snowpark Python: partial (Fabric Spark Python supported; container UDFs unsupported).
- Snowpark JavaScript / Java / Scala: unsupported.
- Streams: partial via CDC patterns in Fabric Eventstreams / Spark structured streaming.
- Tasks: partial via Data Factory pipelines + scheduled triggers.
- Pipes (Snowpipe): partial via Fabric Eventstream + Lakehouse landing.
- Stages: partial via Lakehouse shortcuts to ADLS / S3 / GCS.
"""
from __future__ import annotations

from typing import Literal


ObjectSupport = Literal["supported", "partial", "unsupported", "unknown"]


_TABLE_SUPPORT: dict[str, tuple[ObjectSupport, str]] = {
    "TABLE": (
        "supported",
        "Standard table — migrate to a Fabric Warehouse or Lakehouse Delta table.",
    ),
    "VIEW": (
        "partial",
        "View — Fabric Warehouse supports SQL views; review for Snowflake-specific functions (FLATTEN, semi-structured selectors).",
    ),
    "MATERIALIZED_VIEW": (
        "partial",
        "Materialized view — Fabric Warehouse MVs differ in refresh semantics; review or rebuild as a scheduled CTAS.",
    ),
    "EXTERNAL_TABLE": (
        "partial",
        "External table — map to a Fabric Lakehouse shortcut or OneLake external table.",
    ),
    "DYNAMIC_TABLE": (
        "partial",
        "Dynamic table — no direct Fabric equivalent; emulate via a Lakehouse materialized lake view + scheduled refresh.",
    ),
    "ICEBERG_TABLE": (
        "partial",
        "Iceberg table — Fabric Lakehouse supports Iceberg via OneLake shortcuts; verify catalog binding.",
    ),
    "TEMPORARY": (
        "partial",
        "Temporary table — rebuild as a Lakehouse staging table or in-pipeline temp dataset.",
    ),
    "TRANSIENT": (
        "partial",
        "Transient table — Fabric has no transient concept; rebuild as a regular table with shorter retention policy.",
    ),
}


_ROUTINE_BY_KIND_AND_LANG: dict[tuple[str, str], tuple[ObjectSupport, str]] = {
    ("FUNCTION", "SQL"): (
        "partial",
        "SQL UDF — Fabric Warehouse supports T-SQL scalar/inline TVFs; rewrite Snowflake SQL UDF body.",
    ),
    ("FUNCTION", "JAVASCRIPT"): (
        "unsupported",
        "JavaScript UDF — no Fabric equivalent; reimplement in Spark Scala/Python on a Lakehouse.",
    ),
    ("FUNCTION", "PYTHON"): (
        "partial",
        "Snowpark Python UDF — Fabric Spark supports Python UDFs; container-based execution model differs.",
    ),
    ("FUNCTION", "JAVA"): (
        "unsupported",
        "Java UDF — no T-SQL equivalent; reimplement as a Spark JAR or Python UDF on a Lakehouse.",
    ),
    ("FUNCTION", "SCALA"): (
        "partial",
        "Scala UDF — Fabric Spark Scala supports UDFs; review packaging.",
    ),
    ("PROCEDURE", "SQL"): (
        "partial",
        "SQL stored procedure — Fabric Warehouse supports T-SQL procedures; rewrite Snowflake scripting.",
    ),
    ("PROCEDURE", "JAVASCRIPT"): (
        "unsupported",
        "JavaScript procedure — no Fabric equivalent; reimplement in T-SQL or Spark notebook.",
    ),
    ("PROCEDURE", "PYTHON"): (
        "partial",
        "Snowpark Python procedure — reimplement as a Fabric notebook (Spark Python) or Data Factory activity.",
    ),
    ("PROCEDURE", "JAVA"): (
        "unsupported",
        "Java procedure — no T-SQL equivalent; rebuild as Spark JAR or Python notebook on a Lakehouse.",
    ),
    ("PROCEDURE", "SCALA"): (
        "partial",
        "Scala procedure — reimplement as a Fabric Spark Scala notebook.",
    ),
}


_OBJECT_KIND_SUPPORT: dict[str, tuple[ObjectSupport, str]] = {
    "STAGE": (
        "partial",
        "Stage — map external stages to Fabric Lakehouse shortcuts (ADLS / S3 / GCS); rebuild internal stages as Lakehouse files areas.",
    ),
    "STREAM": (
        "partial",
        "Stream (CDC) — emulate via Fabric Eventstream or Spark structured streaming over a Lakehouse table.",
    ),
    "TASK": (
        "partial",
        "Task — replace with Data Factory pipeline + scheduled trigger; SERIAL_DAG semantics map to pipeline dependencies.",
    ),
    "PIPE": (
        "partial",
        "Snowpipe — replace with Fabric Eventstream + Lakehouse landing or Data Factory copy on event.",
    ),
}


def classify_table(table_kind: str | None) -> tuple[ObjectSupport, str]:
    """Return ``(support_label, note)`` for a Snowflake table kind."""
    if not table_kind:
        return ("unknown", "Unrecognised table kind — manual review required.")
    return _TABLE_SUPPORT.get(
        table_kind.upper(),
        ("unknown", f"Unrecognised table kind '{table_kind}' — manual review required."),
    )


def classify_routine(
    routine_kind: str | None,
    language: str | None,
) -> tuple[ObjectSupport, str]:
    """Return ``(support_label, note)`` for a Snowflake routine."""
    if not routine_kind:
        return ("unknown", "Unrecognised routine kind — manual review required.")
    rk = routine_kind.upper()
    lg = (language or "UNKNOWN").upper()
    key = (rk, lg)
    if key in _ROUTINE_BY_KIND_AND_LANG:
        return _ROUTINE_BY_KIND_AND_LANG[key]
    # Fall back to kind-only verdict.
    if rk == "FUNCTION":
        return (
            "partial",
            f"User-defined function (language={lg}) — review Fabric equivalent.",
        )
    if rk == "PROCEDURE":
        return (
            "partial",
            f"Stored procedure (language={lg}) — review Fabric equivalent.",
        )
    return ("unknown", f"Unrecognised routine kind '{routine_kind}' — manual review required.")


def classify_object(kind: str | None) -> tuple[ObjectSupport, str]:
    """Return ``(support_label, note)`` for a non-table object (stage / stream / task / pipe)."""
    if not kind:
        return ("unknown", "Unrecognised object kind — manual review required.")
    return _OBJECT_KIND_SUPPORT.get(
        kind.upper(),
        ("unknown", f"Unrecognised object kind '{kind}' — manual review required."),
    )


__all__ = [
    "classify_table",
    "classify_routine",
    "classify_object",
    "ObjectSupport",
]
