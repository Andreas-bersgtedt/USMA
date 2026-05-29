"""Knowledge of which Synapse pipeline pieces are not (yet) supported in Fabric Data Factory.

These lists are heuristics maintained by the SMA project — they should be reviewed
periodically against the Microsoft docs. Each entry maps to a recommendation severity
in `fabric_mapping.rules`.

In addition to the bare type-tier classification (`supported` / `partial` / `unsupported`),
:func:`analyze_activity` walks the activity's ``type_properties`` and surfaces concrete
caveats (auth method, IR binding, staged copy, parallelism, etc.) so the user knows
*what specifically is partial* rather than just *that it is partial*.

Sources (subject to change):
- https://learn.microsoft.com/fabric/data-factory/activity-overview
- https://learn.microsoft.com/fabric/data-factory/connector-overview
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Activity types that have no equivalent in Fabric Data Factory pipelines today.
UNSUPPORTED_ACTIVITY_TYPES: frozenset[str] = frozenset({
    # Mapping data flows are Synapse-only; in Fabric, use Dataflow Gen2.
    "ExecuteDataFlow",
    # Power Query mashup activity is not in Fabric pipelines.
    "ExecuteWranglingDataflow",
    # SSIS / IR-bound activities have no Fabric equivalent.
    "ExecuteSSISPackage",
    # Custom .NET on Batch.
    "Custom",
    # HDInsight activities (Hive, Pig, MapReduce, Streaming, Spark on HDI).
    "HDInsightHive",
    "HDInsightPig",
    "HDInsightMapReduce",
    "HDInsightStreaming",
    "HDInsightSpark",
    # Azure ML v1 batch execution and update resource (use AML v2 from notebooks/pipelines).
    "AzureMLBatchExecution",
    "AzureMLUpdateResource",
    # ML Studio classic.
    "AzureMLExecutePipeline",
    # Data Lake Analytics is retired but still appears in legacy pipelines.
    "DataLakeAnalyticsU-SQL",
})


# ---------------------------------------------------------------------------
# Per-activity compatibility knowledge base.
#
# Each entry describes:
#   tier              — supported | partial | unsupported
#   reasons           — generic, type-level reasons the activity is partial / unsupported
#   fabric_equivalent — the matching activity in Fabric Data Factory (if any)
#   migration_action  — short, action-oriented hint for the migration team
#   doc_url           — anchor for "go read more"
# ---------------------------------------------------------------------------

_FABRIC_DOC_BASE = "https://learn.microsoft.com/fabric/data-factory"

_ACTIVITY_DETAILS: dict[str, dict[str, Any]] = {
    # --- partial: present in Fabric but with reduced or different config -----
    "Copy": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric, but the source/sink connector catalog is smaller than Synapse — verify each linked service.",
            "Self-hosted IR copies map to the Fabric on-premises data gateway (different auth model).",
            "Staged copy requires a Fabric-supported Azure Storage linked service for the staging area.",
        ],
        "fabric_equivalent": "Copy data activity",
        "migration_action": "Recreate source/sink linked services in Fabric; re-test column mappings and staging config.",
        "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview",
    },
    "WebActivity": {
        "tier": "partial",
        "reasons": [
            "Supported in Fabric, but only a subset of authentication types is available — `ClientCertificate` and `MSI` against on-prem endpoints are not supported.",
            "Body / URL expressions using Synapse system variables may need to be rewritten.",
        ],
        "fabric_equivalent": "Web activity",
        "migration_action": "Re-pick auth type in Fabric (Anonymous / SystemAssignedManagedIdentity / ServicePrincipal) and rotate any embedded secrets into a Fabric Key Vault linked service.",
        "doc_url": f"{_FABRIC_DOC_BASE}/web-activity",
    },
    "WebHook": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric, but the callback-URL convention and timeout semantics differ slightly — long-running flows must be retested.",
        ],
        "fabric_equivalent": "Webhook activity",
        "migration_action": "Re-test callback timeouts and the `reportStatusOnCallBack` flag end-to-end after migration.",
        "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview",
    },
    "Validation": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric, but the underlying dataset must point at a Fabric-supported store (file / blob / lakehouse).",
        ],
        "fabric_equivalent": "Validation activity",
        "migration_action": "Verify the dataset's linked service is Fabric-supported.",
        "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview",
    },
    "Lookup": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric, but limited to Fabric-supported sources.",
            "When `firstRowOnly=false`, downstream expressions (`@activity('x').output.value`) must be rewritten because Fabric returns the array under a slightly different shape.",
        ],
        "fabric_equivalent": "Lookup activity",
        "migration_action": "Re-test downstream expressions when `firstRowOnly` is false.",
        "doc_url": f"{_FABRIC_DOC_BASE}/lookup-activity",
    },
    "GetMetadata": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric, but only a subset of `fieldList` values is exposed and the supported dataset types are narrower.",
        ],
        "fabric_equivalent": "Get Metadata activity",
        "migration_action": "Verify each requested field (`itemName`, `lastModified`, `childItems`, …) is supported on the target Fabric connector.",
        "doc_url": f"{_FABRIC_DOC_BASE}/get-metadata-activity",
    },
    "Filter": {
        "tier": "partial",
        "reasons": [
            "Supported, but the expression language differs slightly (Fabric pipelines use the same `@` syntax with a smaller built-in function set).",
        ],
        "fabric_equivalent": "Filter activity",
        "migration_action": "Re-validate `condition` expressions against Fabric's expression catalog.",
        "doc_url": f"{_FABRIC_DOC_BASE}/expression-language",
    },
    "ForEach": {
        "tier": "partial",
        "reasons": [
            "Supported, with the same `isSequential` / `batchCount` shape — but the `batchCount` ceiling and per-iteration timeout defaults differ.",
        ],
        "fabric_equivalent": "ForEach activity",
        "migration_action": "Re-tune `batchCount` (Fabric default 20, max 50) and re-test timeout-sensitive loops.",
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "Until": {
        "tier": "partial",
        "reasons": [
            "Supported, but the inner activity catalog is whatever Fabric supports — gaps inside the loop body may need rework.",
        ],
        "fabric_equivalent": "Until activity",
        "migration_action": "Re-validate body activities against this same compatibility report.",
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "IfCondition": {
        "tier": "partial",
        "reasons": [
            "Supported. As with `Until`, the inner activity catalog determines true partiality.",
        ],
        "fabric_equivalent": "IfCondition activity",
        "migration_action": "Re-validate inner activities (true/false branches).",
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "Switch": {
        "tier": "partial",
        "reasons": [
            "Supported. As with `Until`, the inner activity catalog determines true partiality.",
        ],
        "fabric_equivalent": "Switch activity",
        "migration_action": "Re-validate inner activities for each case (and `defaultActivities`).",
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "SetVariable": {
        "tier": "partial",
        "reasons": [
            "Supported, but variables in Fabric are scoped per pipeline run with subtly different lifecycle around `Concurrency` / `pipeline()` references.",
        ],
        "fabric_equivalent": "Set variable activity",
        "migration_action": "Re-run the parent pipeline end-to-end after migration to confirm variable propagation.",
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "AppendVariable": {
        "tier": "partial",
        "reasons": [
            "Supported. Same lifecycle caveat as `SetVariable`.",
        ],
        "fabric_equivalent": "Append variable activity",
        "migration_action": "Re-run end-to-end after migration to confirm variable propagation.",
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "Wait": {
        "tier": "supported",
        "reasons": [],
        "fabric_equivalent": "Wait activity",
        "migration_action": None,
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "Fail": {
        "tier": "supported",
        "reasons": [],
        "fabric_equivalent": "Fail activity",
        "migration_action": None,
        "doc_url": f"{_FABRIC_DOC_BASE}/control-flow-activities",
    },
    "ExecutePipeline": {
        "tier": "partial",
        "reasons": [
            "Supported, but only references **another Fabric pipeline in the same workspace** — cross-workspace `ExecutePipeline` is not supported.",
        ],
        "fabric_equivalent": "Invoke pipeline activity",
        "migration_action": "Verify the called pipeline lives in the same Fabric workspace; otherwise refactor into a triggering pattern.",
        "doc_url": f"{_FABRIC_DOC_BASE}/invoke-pipeline-activity",
    },
    "SynapseNotebook": {
        "tier": "partial",
        "reasons": [
            "No direct mapping — Synapse notebooks must be re-imported into the Fabric workspace as Fabric notebooks.",
            "Parameter passing is supported, but `sparkPool` / `executorSize` are replaced by Fabric capacity defaults.",
        ],
        "fabric_equivalent": "Notebook activity (Fabric)",
        "migration_action": "Import notebooks into Fabric; rebind activity to the new notebook artifact id.",
        "doc_url": f"{_FABRIC_DOC_BASE}/notebook-activity",
    },
    "SqlServerStoredProcedure": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric as the **Stored procedure activity**, but only against Fabric-supported SQL targets (Azure SQL DB, Fabric Data Warehouse, on-prem SQL via gateway).",
        ],
        "fabric_equivalent": "Stored procedure activity",
        "migration_action": "Re-bind the activity's linked service to a Fabric-compatible SQL endpoint.",
        "doc_url": f"{_FABRIC_DOC_BASE}/stored-procedure-activity",
    },
    "SparkJob": {
        "tier": "partial",
        "reasons": [
            "Maps to the Spark Job Definition activity in Fabric, but `targetSparkConfiguration`, executor sizing and library references must be re-defined against Fabric capacities.",
        ],
        "fabric_equivalent": "Spark Job Definition activity",
        "migration_action": "Re-create the Spark job definition artifact in Fabric and rebind.",
        "doc_url": f"{_FABRIC_DOC_BASE}/spark-job-definition-activity",
    },
    "Script": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric, but only against Fabric-supported SQL connectors. Multi-statement scripts behave the same.",
        ],
        "fabric_equivalent": "Script activity",
        "migration_action": "Re-bind the script activity's linked service to a Fabric-compatible SQL endpoint.",
        "doc_url": f"{_FABRIC_DOC_BASE}/script-activity",
    },
    "Delete": {
        "tier": "partial",
        "reasons": [
            "Available in Fabric, but the dataset must point at a Fabric-supported store (Blob, ADLS Gen2, Fabric Lakehouse files).",
        ],
        "fabric_equivalent": "Delete activity",
        "migration_action": "Verify the target dataset's linked service is supported in Fabric.",
        "doc_url": f"{_FABRIC_DOC_BASE}/delete-activity",
    },

    # --- unsupported (mirrored here so analyze_activity can return reasons + action) -
    "ExecuteDataFlow": {
        "tier": "unsupported",
        "reasons": ["Mapping data flows are Synapse-only; Fabric uses Dataflow Gen2 (Power Query M)."],
        "fabric_equivalent": "Dataflow Gen2 (different language / runtime)",
        "migration_action": "Rebuild data flow logic as a Dataflow Gen2 or as a Spark notebook.",
        "doc_url": f"{_FABRIC_DOC_BASE}/dataflows-gen2-overview",
    },
    "ExecuteWranglingDataflow": {
        "tier": "unsupported",
        "reasons": ["Power Query mashup activity is not present in Fabric pipelines."],
        "fabric_equivalent": "Dataflow Gen2",
        "migration_action": "Recreate the mashup as a Dataflow Gen2.",
        "doc_url": f"{_FABRIC_DOC_BASE}/dataflows-gen2-overview",
    },
    "ExecuteSSISPackage": {
        "tier": "unsupported",
        "reasons": ["No Azure-SSIS IR equivalent in Fabric."],
        "fabric_equivalent": None,
        "migration_action": "Re-engineer the package as a Fabric pipeline + Dataflow Gen2 / notebook combination.",
        "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview",
    },
    "Custom": {
        "tier": "unsupported",
        "reasons": ["Custom .NET activities backed by Azure Batch have no equivalent in Fabric."],
        "fabric_equivalent": None,
        "migration_action": "Port the .NET logic to a Fabric notebook (Python / Scala) or call it via a Web activity into an external host.",
        "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview",
    },
    "HDInsightHive": {"tier": "unsupported", "reasons": ["HDInsight activities have no Fabric equivalent."], "fabric_equivalent": None, "migration_action": "Port to Spark SQL in a Fabric notebook.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
    "HDInsightPig": {"tier": "unsupported", "reasons": ["HDInsight activities have no Fabric equivalent."], "fabric_equivalent": None, "migration_action": "Rewrite Pig logic in a Spark notebook.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
    "HDInsightMapReduce": {"tier": "unsupported", "reasons": ["HDInsight activities have no Fabric equivalent."], "fabric_equivalent": None, "migration_action": "Rewrite as a Spark job in a Fabric notebook.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
    "HDInsightStreaming": {"tier": "unsupported", "reasons": ["HDInsight activities have no Fabric equivalent."], "fabric_equivalent": None, "migration_action": "Rewrite as a Fabric Spark notebook or Eventstream.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
    "HDInsightSpark": {"tier": "unsupported", "reasons": ["HDInsight activities have no Fabric equivalent."], "fabric_equivalent": "Spark Job Definition activity", "migration_action": "Re-target to a Fabric Spark Job Definition.", "doc_url": f"{_FABRIC_DOC_BASE}/spark-job-definition-activity"},
    "AzureMLBatchExecution": {"tier": "unsupported", "reasons": ["Azure ML v1 (Studio Classic) is retired."], "fabric_equivalent": None, "migration_action": "Rebuild on Azure ML v2 and call from a Fabric Web/REST activity.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
    "AzureMLUpdateResource": {"tier": "unsupported", "reasons": ["Azure ML v1 (Studio Classic) is retired."], "fabric_equivalent": None, "migration_action": "Re-engineer using Azure ML v2 REST endpoints.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
    "AzureMLExecutePipeline": {"tier": "unsupported", "reasons": ["Azure ML v1 (Studio Classic) is retired."], "fabric_equivalent": None, "migration_action": "Re-engineer using Azure ML v2 REST endpoints.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
    "DataLakeAnalyticsU-SQL": {"tier": "unsupported", "reasons": ["Azure Data Lake Analytics is retired."], "fabric_equivalent": None, "migration_action": "Rewrite U-SQL as Spark SQL in a Fabric notebook.", "doc_url": f"{_FABRIC_DOC_BASE}/activity-overview"},
}


# Backwards-compatibility shim — the old frozenset, derived from _ACTIVITY_DETAILS.
PARTIALLY_SUPPORTED_ACTIVITY_TYPES: frozenset[str] = frozenset(
    t for t, d in _ACTIVITY_DETAILS.items() if d["tier"] == "partial"
)


# Linked-service / connector type names that don't have a first-class Fabric connector yet.
# Synapse exposes 100+ connectors; Fabric is catching up but lags on some.
UNSUPPORTED_LINKED_SERVICE_TYPES: frozenset[str] = frozenset({
    "AzureMLService",          # ML v1
    "AzureMLLinkedService",
    "AzureBatch",              # Custom activity backing
    "HDInsight",
    "HDInsightOnDemand",
    "AzureDataLakeAnalytics",
    "Cassandra",
    "Couchbase",
    "Drill",
    "Greenplum",
    "HBase",
    "Hive",
    "Impala",
    "Informix",
    "MariaDB",
    "Phoenix",
    "Presto",
    "Spark",                   # external Spark connector
    "Sybase",
    "Vertica",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class CompatDetail:
    """Structured outcome of analysing a single activity."""

    tier: str  # supported | partial | unsupported | unknown
    reasons: list[str] = field(default_factory=list)         # generic, type-level
    caveats: list[str] = field(default_factory=list)         # instance-specific, from type_properties
    fabric_equivalent: str | None = None
    migration_action: str | None = None
    doc_url: str | None = None


def classify_activity(activity_type: str | None) -> str:
    """Return one of: 'supported', 'partial', 'unsupported', 'unknown'.

    Kept for backward compatibility — :func:`analyze_activity` is richer.
    """
    if not activity_type:
        return "unknown"
    if activity_type in UNSUPPORTED_ACTIVITY_TYPES:
        return "unsupported"
    if activity_type in PARTIALLY_SUPPORTED_ACTIVITY_TYPES:
        return "partial"
    return "supported"


def linked_service_supported(ls_type: str | None) -> bool:
    if not ls_type:
        return True
    return ls_type not in UNSUPPORTED_LINKED_SERVICE_TYPES


def analyze_activity(
    activity_type: str | None,
    type_properties: dict[str, Any] | None = None,
) -> CompatDetail:
    """Return a structured compatibility analysis for one activity.

    Parameters
    ----------
    activity_type:
        The activity ``type`` string, e.g. ``Copy``, ``WebActivity``.
    type_properties:
        Best-effort dictionary view of the activity's ``type_properties``.
        ``None`` or ``{}`` is fine — the analyser falls back to the generic
        type-level reasons in that case.

    Returns
    -------
    CompatDetail
        Carries the tier, generic reasons, instance-specific caveats, the
        suggested Fabric equivalent, a migration action and a doc URL.
    """
    if not activity_type:
        return CompatDetail(tier="unknown")

    detail = _ACTIVITY_DETAILS.get(activity_type)
    if detail is None:
        return CompatDetail(tier="supported")

    out = CompatDetail(
        tier=detail["tier"],
        reasons=list(detail.get("reasons") or []),
        fabric_equivalent=detail.get("fabric_equivalent"),
        migration_action=detail.get("migration_action"),
        doc_url=detail.get("doc_url"),
    )

    props = type_properties or {}
    out.caveats = _instance_caveats(activity_type, props)

    # If we found instance-specific caveats and the activity was previously
    # ranked 'supported', escalate to 'partial' so reports surface them.
    if out.caveats and out.tier == "supported":
        out.tier = "partial"

    return out


# ---------------------------------------------------------------------------
# Instance-level caveat detection
# ---------------------------------------------------------------------------

def _instance_caveats(activity_type: str, props: dict[str, Any]) -> list[str]:
    """Return concrete caveats based on the activity's ``type_properties``."""
    fn = _CAVEAT_DISPATCH.get(activity_type)
    if not fn:
        return []
    try:
        return [c for c in fn(props) if c]
    except Exception:  # noqa: BLE001 — caveat extraction must never break the run
        return []


def _g(d: dict[str, Any], *path: str) -> Any:
    """Dotted-path getter that tolerates missing keys / non-dict intermediates."""
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _caveats_copy(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if p.get("enableStaging"):
        out.append("Staged copy is enabled — the staging linked service must be a Fabric-supported Azure Storage account.")
    parallel = p.get("parallelCopies")
    if isinstance(parallel, int) and parallel > 32:
        out.append(f"`parallelCopies={parallel}` is above Fabric's recommended ceiling (32); re-tune after migration.")
    diu = p.get("dataIntegrationUnits")
    if isinstance(diu, int) and diu > 256:
        out.append(f"`dataIntegrationUnits={diu}` exceeds typical Fabric capacity allocations; verify capacity sizing.")
    if p.get("enableSkipIncompatibleRow"):
        out.append("`enableSkipIncompatibleRow=true` — Fabric supports it but the redirect-error log must point at a Fabric-supported store.")
    src_type = _g(p, "source", "type")
    if isinstance(src_type, str) and src_type.endswith("Source") and "Hdfs" in src_type:
        out.append(f"Source type `{src_type}` requires the Fabric on-premises data gateway.")
    sink_type = _g(p, "sink", "type")
    if isinstance(sink_type, str) and "Polybase" in sink_type:
        out.append("Polybase sink (`PolybaseSettings`) is dedicated-pool-only — switch to `CopyCommand` (Fabric Warehouse) or bulk insert.")
    if isinstance(_g(p, "sink", "polybaseSettings"), dict):
        out.append("Sink uses Polybase settings — Polybase is dedicated-pool-only; switch to `CopyCommand` (Fabric Warehouse) or bulk insert.")
    return out


def _caveats_web(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    auth_type = _g(p, "authentication", "type")
    if isinstance(auth_type, str):
        if auth_type == "ClientCertificate":
            out.append("Authentication type `ClientCertificate` is not supported in Fabric; re-issue as Service Principal or Managed Identity.")
        elif auth_type == "ServicePrincipal":
            out.append("Service principal auth is supported but the secret must be re-created in a Fabric Key Vault linked service.")
        elif auth_type == "MSI":
            out.append("MSI auth maps to `SystemAssignedManagedIdentity` in Fabric; grant the Fabric workspace identity the same permissions.")
    method = p.get("method")
    if isinstance(method, str) and method.upper() not in ("GET", "POST", "PUT", "DELETE"):
        out.append(f"HTTP method `{method}` may not be available in Fabric's Web activity.")
    if p.get("disableCertValidation"):
        out.append("`disableCertValidation=true` — verify Fabric exposes the same flag (it does today, but Microsoft has flagged it for deprecation).")
    return out


def _caveats_lookup(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if p.get("firstRowOnly") is False:
        out.append("`firstRowOnly=false` — downstream `@activity('x').output.value` references must be re-validated under Fabric's slightly different output shape.")
    return out


def _caveats_get_metadata(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    fields = p.get("fieldList") or []
    if isinstance(fields, list):
        rare = [f for f in fields if isinstance(f, str) and f in {"structure", "columnCount"}]
        if rare:
            out.append(f"`fieldList` includes {sorted(rare)} — these fields are only exposed for a subset of Fabric connectors.")
    return out


def _caveats_foreach(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    bc = p.get("batchCount")
    if isinstance(bc, int) and bc > 50:
        out.append(f"`batchCount={bc}` exceeds Fabric's max of 50; will be capped on import.")
    if p.get("isSequential") is False and isinstance(bc, int) and bc > 20:
        out.append(f"Parallel ForEach with `batchCount={bc}` — Fabric default is 20; verify capacity headroom.")
    return out


def _caveats_execute_pipeline(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if p.get("waitOnCompletion") is False:
        out.append("`waitOnCompletion=false` — Fabric's `Invoke pipeline` activity also supports fire-and-forget but the run-id propagation differs; re-test.")
    return out


def _caveats_synapse_notebook(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if _g(p, "sparkPool", "referenceName"):
        out.append(f"Bound to Synapse Spark pool `{_g(p, 'sparkPool', 'referenceName')}` — no equivalent in Fabric; capacity is used implicitly.")
    if p.get("executorSize"):
        out.append(f"`executorSize={p.get('executorSize')}` is not portable; Fabric uses capacity-level sizing.")
    if p.get("conf"):
        out.append("Custom Spark `conf` overrides — re-validate against the Fabric Spark runtime version.")
    return out


def _caveats_sql_stored_proc(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if p.get("storedProcedureParameters"):
        out.append("Parameter expressions must be re-tested against Fabric's expression catalog.")
    return out


def _caveats_spark_job(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if _g(p, "targetBigDataPool", "referenceName"):
        out.append(f"Bound to Synapse Big Data pool `{_g(p, 'targetBigDataPool', 'referenceName')}` — must be replaced by a Fabric Spark Job Definition target.")
    if p.get("targetSparkConfiguration"):
        out.append("Custom `targetSparkConfiguration` — re-create as a Fabric Spark environment.")
    return out


def _caveats_script(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    scripts = p.get("scripts") or []
    if isinstance(scripts, list) and len(scripts) > 1:
        out.append(f"Multi-statement Script ({len(scripts)} blocks) — re-test transactional semantics in Fabric.")
    return out


def _caveats_delete(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if p.get("enableLogging"):
        out.append("`enableLogging=true` — the log linked service must be a Fabric-supported Azure Storage account.")
    if p.get("recursive"):
        out.append("`recursive=true` against an unsupported source store will fail in Fabric — verify the dataset.")
    return out


_CAVEAT_DISPATCH: dict[str, Any] = {
    "Copy": _caveats_copy,
    "WebActivity": _caveats_web,
    "WebHook": _caveats_web,
    "Lookup": _caveats_lookup,
    "GetMetadata": _caveats_get_metadata,
    "ForEach": _caveats_foreach,
    "ExecutePipeline": _caveats_execute_pipeline,
    "SynapseNotebook": _caveats_synapse_notebook,
    "SqlServerStoredProcedure": _caveats_sql_stored_proc,
    "SparkJob": _caveats_spark_job,
    "Script": _caveats_script,
    "Delete": _caveats_delete,
}

