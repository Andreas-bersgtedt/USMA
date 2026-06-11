/**
 * Canonical SourceType identifiers shared across the SPA.
 *
 * Mirrors the backend `SourceType` StrEnum in
 * `src/usma/sources/__init__.py`. The string values
 * are the stable wire format used by `RunMeta.scopes[].type`,
 * `Recommendation.source_type`, and the `?scope=<type>__<slug>` query
 * parameter on `/api/runs/{id}/modules/{module}`.
 *
 * History: this used to be re-exported from `SourcePicker.tsx`; that
 * component was a Phase-0 stub that never got mounted (Configuration.tsx
 * grew its own per-source radio + `scopeNoun()` helpers instead — see
 * commits 7337c06 / 3a22c3b). Type lifted here in 2026-05-20 so the
 * unused component could be removed.
 */
export type SourceTypeId =
  | "synapse_workspace"
  | "adf"
  | "databricks"
  | "bigquery"
  | "sap_bw"
  | "sql_server"
  | "snowflake"
  | "synapse_dedicated_sql";
