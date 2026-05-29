/**
 * Display-name helpers.
 *
 * The analyzer uses snake_case identifiers internally (module names,
 * runbook phases, recommendation areas, severity / effort enums). They
 * leak through to the UI in many places and read like system identifiers.
 * These helpers translate them to title-cased, human-friendly labels
 * while leaving the raw value reachable as a `title=` tooltip when
 * callers want it.
 */

const MODULE_LABELS: Record<string, string> = {
  dedicated_pools: "Dedicated SQL pools",
  serverless_pools: "Serverless SQL pools",
  spark_pools: "Spark pools",
  pipelines: "Pipelines",
  databricks_workflows: "Databricks workflows",
  bigquery_workloads: "BigQuery workloads",
  snowflake_workloads: "Snowflake workloads",
  monitoring: "Monitoring",
  storage: "Storage",
  fabric_mapping: "Fabric mapping",
  governance: "Governance",
  security: "Security",
  cost: "Cost",
  fabric_validation: "Fabric validation",
};

const PHASE_LABELS: Record<string, string> = {
  foundation: "Foundation",
  data_plane_prep: "Data plane preparation",
  ingest_shortcuts: "Ingest & shortcuts",
  compute_migration: "Compute migration",
  orchestration_migration: "Orchestration migration",
  verification: "Verification",
};

const SEVERITY_LABELS: Record<string, string> = {
  blocker: "Blocker",
  warning: "Warning",
  info: "Info",
  high: "High",
  medium: "Medium",
  low: "Low",
  incompatible: "Incompatible",
  needs_review: "Needs review",
  compatible: "Compatible",
};

const EFFORT_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
};

const IMPACT_LABELS: Record<string, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  unknown: "Unknown",
};

const STATE_LABELS: Record<string, string> = {
  ok: "OK",
  failed: "Failed",
  running: "Running",
  cancelled: "Cancelled",
  partial: "Partial",
  queued: "Queued",
};

/**
 * Source-platform display names. Keep in sync with the
 * ``SourceType`` enum in ``src/usma/sources/__init__.py``.
 * Used by Dashboard / Recommendations / Runbook / EstateOverview to phrase
 * scope labels and recommendation chips correctly per source.
 */
const SOURCE_TYPE_LABELS: Record<string, string> = {
  synapse_workspace: "Synapse workspace",
  adf: "Azure Data Factory",
  databricks: "Databricks workspace",
  sap_bw: "SAP BW system",
};

/**
 * Per-source noun for the scope itself ("workspace", "factory", "system").
 * Used when the UI needs a one-word noun rather than the full platform name
 * (e.g. "Re-run the monitoring analyzer for this workspace").
 */
const SOURCE_NOUN_LABELS: Record<string, string> = {
  synapse_workspace: "workspace",
  adf: "factory",
  databricks: "workspace",
  sap_bw: "system",
};

/** Title-case a snake_case / kebab-case identifier as a fallback. */
function titleCase(raw: string): string {
  if (!raw) return raw;
  return raw
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b([a-z])/g, (_, c: string) => c.toUpperCase());
}

export function moduleLabel(raw: string): string {
  return MODULE_LABELS[raw] ?? titleCase(raw);
}

export function phaseLabel(raw: string): string {
  return PHASE_LABELS[raw] ?? titleCase(raw);
}

export function severityLabel(raw: string): string {
  return SEVERITY_LABELS[raw] ?? titleCase(raw);
}

export function effortLabel(raw: string): string {
  return EFFORT_LABELS[raw] ?? titleCase(raw);
}

export function impactLabel(raw: string | null | undefined): string {
  if (!raw) return "Unknown";
  return IMPACT_LABELS[raw] ?? titleCase(raw);
}

export function stateLabel(raw: string): string {
  return STATE_LABELS[raw] ?? titleCase(raw);
}

/**
 * Recommendation/finding "area" identifiers are dotted snake_case like
 * `dedicated_pools.tsql_surface`. Render the module half via
 * `moduleLabel` so callers get "Dedicated SQL pools \u2192 T-SQL surface".
 */
export function areaLabel(raw: string): string {
  if (!raw) return raw;
  const [head, ...rest] = raw.split(".");
  const tail = rest.join(".");
  const left = moduleLabel(head);
  if (!tail) return left;
  return `${left} \u2192 ${titleCase(tail)}`;
}

/**
 * Human-friendly source-platform name. Returns "Scope" for ``null`` /
 * unknown ids so the UI can use it as a generic fallback.
 */
export function sourceLabel(raw: string | null | undefined): string {
  if (!raw) return "Scope";
  return SOURCE_TYPE_LABELS[raw] ?? titleCase(raw);
}

/**
 * Per-source noun for the scope ("workspace" / "factory" / "system").
 * Returns "scope" for unknown ids.
 */
export function sourceNoun(raw: string | null | undefined): string {
  if (!raw) return "scope";
  return SOURCE_NOUN_LABELS[raw] ?? "scope";
}
