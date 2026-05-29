/**
 * TypeScript types mirroring the Pydantic v2 models under
 * src/usma/modules/<module>/models.py. We intentionally
 * declare these by hand (not auto-generated) so the front end stays decoupled
 * from any one analyzer release; missing fields are tolerated via optionals.
 *
 * When the analyzer adds a field, add it here and the table renderers will
 * pick it up. When the analyzer renames a field, treat that as a breaking
 * change and bump the SPA's expected schema version.
 */

export type Severity = "blocker" | "warning" | "info";
export type Effort = "high" | "medium" | "low";
export type Impact = "high" | "medium" | "low" | "unknown";
export type Compatibility = "compatible" | "needs_review" | "incompatible";

// ---------------------------------------------------------------------------
// fabric_mapping.json
// ---------------------------------------------------------------------------

export interface Recommendation {
  id: string;
  area: string;
  title: string;
  severity: Severity;
  effort: Effort;
  target?: string | null;
  detail: string;
  fabric_action?: string | null;
  // v2.11 � business-impact axis. Optional so older artefacts deserialise.
  impact?: Impact;
  impact_detail?: string | null;
  // v3 � source platform this recommendation was produced from
  // ("synapse_workspace", "adf", �). Stamped by the analyzer.
  source_type?: string | null;
}

export interface ReadinessSummary {
  score: number;
  bucket: "ready" | "ready-with-effort" | "blocked";
  counts: Partial<Record<Severity, number>>;
  top_blockers: Recommendation[];
  tsql_compatibility_pct?: number | null;
  tsql_objects_total?: number;
  tsql_objects_incompatible?: number;
  tsql_objects_needs_review?: number;
}

export interface CapacityProjection {
  peak_dwu: number;
  peak_dwu_with_headroom: number;
  estimated_cu: number;
  recommended_sku: string;
  headroom_pct: number;
  notes: string[];
  // v2.6.2 � per-component CU contribution (post-headroom). Older runs
  // serialised before this release will be missing these fields.
  dwu_cu_contribution?: number;
  spark_cu_contribution?: number;
  pipelines_cu_contribution?: number;
  // Serverless SQL contribution (heuristic 0.02 CU per 60 GB � duration).
  serverless_cu_contribution?: number;
  // Raw peak-day CU-hours for serverless SQL (pre-24h smoothing).
  serverless_peak_day_cu_hours?: number;
}

export interface RunbookStep {
  phase: string;
  order: number;
  title: string;
  detail: string;
  severity: string;
  effort: string;
  target?: string | null;
  rollback?: string | null;
  source_recommendation_id?: string | null;
  // v2.10 � effort estimator output. Older artefacts will be missing
  // these fields (the SPA falls back to the qualitative ``effort`` label).
  effort_hours_p50?: number | null;
  effort_hours_p90?: number | null;
  effort_breakdown?: {
    area?: string | null;
    components?: string[];
    area_total_hours?: number;
    phase_share_hours?: number;
    fallback_used?: boolean;
    capped?: boolean;
  } | null;
  // v3 � source platform inherited from the originating recommendation.
  source_type?: string | null;
}

export interface PhaseEffortSummary {
  phase: string;
  p50_hours: number;
  p90_hours: number;
  step_count: number;
  p50_days?: number | null;
  p90_days?: number | null;
  // v2.11 � critical-path projection within a phase.
  parallel_p50_hours?: number | null;
  parallel_p90_hours?: number | null;
  parallel_p50_days?: number | null;
  parallel_p90_days?: number | null;
  max_step_p50_hours?: number | null;
  max_step_p90_hours?: number | null;
}

export interface EffortSummary {
  total_p50_hours: number;
  total_p90_hours: number;
  total_p50_days?: number | null;
  total_p90_days?: number | null;
  per_phase: PhaseEffortSummary[];
  card_source: string;
  card_version: number;
  // v2.11 � project-level critical-path projection.
  parallel_p50_hours?: number | null;
  parallel_p90_hours?: number | null;
  parallel_p50_days?: number | null;
  parallel_p90_days?: number | null;
  parallel_workers?: number | null;
}

export interface ModuleSummary {
  module: string;
  source_file: string;
  counts: Record<string, number>;
  notes?: string[];
}

export interface FabricMappingReport {
  workspace_name?: string | null;
  generated_at: string;
  inputs: ModuleSummary[];
  recommendations: Recommendation[];
  readiness?: ReadinessSummary | null;
  runbook: RunbookStep[];
  capacity_projection?: CapacityProjection | null;
  effort_summary?: EffortSummary | null;
}

// ---------------------------------------------------------------------------
// dedicated_pools.json
// ---------------------------------------------------------------------------

export interface CodeObjectParameter {
  schema_name: string;
  object_name: string;
  object_type: string;
  parameter_name: string;
  data_type?: string | null;
  max_length?: number | null;
  is_output: boolean;
  has_default: boolean;
  ordinal: number;
}

export interface CodeObject {
  schema_name: string;
  object_name: string;
  object_type: string;
  definition?: string | null;
  code_object_id?: string | null;
  create_date?: string | null;
  modify_date?: string | null;
  line_count?: number | null;
  definition_length?: number | null;
  parameter_count?: number;
  parameters?: CodeObjectParameter[];
  uses_ansi_nulls?: boolean | null;
  uses_quoted_identifier?: boolean | null;
  compatibility?: Compatibility;
  gap_severities?: string[];
  gap_count?: number;
}

export interface CodeObjectSummary {
  total: number;
  by_type: Record<string, number>;
  by_compatibility: Partial<Record<Compatibility, number>>;
  compatibility_pct?: number | null;
  incompatible_object_ids: string[];
  needs_review_object_ids: string[];
}

export interface TsqlSurfaceGap {
  code_object_id: string;
  schema_name: string;
  object_name: string;
  object_type: string;
  rule_id: string;
  label: string;
  severity: string;
  matches: number;
  fabric_action?: string | null;
}

export interface PoolInventory {
  name: string;
  status?: string;
  location?: string;
  collation?: string;
}

export interface PoolAnalysis {
  inventory: PoolInventory;
  tables?: unknown[];
  schemas?: unknown[];
  code_objects?: CodeObject[];
  tsql_surface_gaps?: TsqlSurfaceGap[];
  code_object_summary?: CodeObjectSummary | null;
  top_queries?: DedicatedTopQuery[];
  top_consumed_objects?: DedicatedTopConsumedObject[];
  workload_capture_stats?: WorkloadCaptureStats | null;
  errors?: string[];
}

export interface DedicatedTopQuery {
  request_id?: string | null;
  session_id?: string | null;
  status?: string | null;
  submit_time?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  total_elapsed_ms?: number | null;
  resource_class?: string | null;
  importance?: string | null;
  query_label?: string | null;
  error_id?: string | null;
  login_name?: string | null;
  command_text?: string | null;
}

/**
 * A table or view that appears frequently in recent workload SQL on a
 * dedicated SQL pool. Derived by parsing the submitted command text
 * from `sys.dm_pdw_exec_requests` with sqlglot and resolving each
 * referenced object against `INFORMATION_SCHEMA`. Aggregated across a
 * rolling on-disk workload cache (default 30-day window).
 *
 * `match_kind` indicates how the reference resolved:
 *   - `qualified`            � schema.name match in the catalog
 *   - `unqualified-resolved` � 1-part name with exactly one owner
 *   - `ambiguous`            � 1-part name found in >1 schema (treat
 *                              counts as a suspect cluster, not a fact)
 */
export interface DedicatedTopConsumedObject {
  object_name: string;
  object_type: string;
  usage_count: number;
  elapsed_time_ms?: number;
  match_kind?: "qualified" | "unqualified-resolved" | "ambiguous";
}

/** Coverage / health stats for the top-consumed-tables ranking. */
export interface WorkloadCaptureStats {
  dmv_rows: number;
  parsed_ok: number;
  parsed_failed: number;
  parsed_empty: number;
  cache_requests_total: number;
  cache_window_days: number;
  oldest_cache_entry?: string | null;
  newest_cache_entry?: string | null;
}

export interface DedicatedPoolsReport {
  workspace_name?: string | null;
  generated_at: string;
  pools: PoolAnalysis[];
}

// ---------------------------------------------------------------------------
// run_delta.json (incremental / delta runs)
// ---------------------------------------------------------------------------

export interface DeltaArtifact {
  module: string;
  path: string;
  status: "added" | "removed" | "changed" | "unchanged";
  size_bytes?: number;
  sha256?: string;
  record_count?: number | null;
  record_count_delta?: number | null;
}

export interface RunDelta {
  previous_run?: string | null;
  current_run: string;
  generated_at: string;
  artifacts: DeltaArtifact[];
  notes?: string[];
}

// ---------------------------------------------------------------------------
// storage.json
// ---------------------------------------------------------------------------

export interface StorageAccountInventory {
  name: string;
  resource_id: string;
  location?: string | null;
  sku?: string | null;
  kind?: string | null;
  access_tier?: string | null;
  is_hns_enabled?: boolean | null;
  is_workspace_default?: boolean;
  default_filesystem?: string | null;
}

export interface StorageCapacity {
  account_name: string;
  captured_at: string;
  used_capacity_bytes?: number | null;
  used_capacity_gb?: number | null;
  blob_capacity_gb?: number | null;
  blob_count?: number | null;
  container_count?: number | null;
}

export interface DedicatedPoolStorage {
  pool_name: string;
  captured_at: string;
  table_count: number;
  row_count: number;
  reserved_space_mb: number;
  data_space_mb: number;
  index_space_mb: number;
  unused_space_mb: number;
  reserved_space_gb: number;
  data_space_gb: number;
  index_space_gb: number;
  max_size_gb?: number | null;
  used_pct_of_max?: number | null;
}

export interface StorageReport {
  workspace_name?: string | null;
  generated_at: string;
  accounts: StorageAccountInventory[];
  capacities: StorageCapacity[];
  dedicated_pool_storage: DedicatedPoolStorage[];
  errors?: string[];
}

// ---------------------------------------------------------------------------
// pipelines.json
// ---------------------------------------------------------------------------

export interface PipelineRunWindowStats {
  window_days: number;
  run_count: number;
  succeeded: number;
  failed: number;
  other: number;
  success_rate?: number | null;
  avg_duration_ms?: number | null;
  p95_duration_ms?: number | null;
  avg_data_moved_mb_per_run?: number | null;
  total_data_moved_mb?: number | null;
  // Azure-IR Data Integration Units consumed in the window (sum of
  // billableDuration[].duration entries with unit "DIUHours").
  avg_diu_hours_per_run?: number | null;
  total_diu_hours?: number | null;
  // Heuristic Fabric CU-hours equivalent (= total_diu_hours * 1.5).
  est_cu_hours_from_diu?: number | null;
  // Mapping Data Flow Spark cluster compute (vCore-hours from Azure-IR
  // billing entries with unit coreHour / vCoreHour). Projected to Fabric
  // Spark CU at 1 vCore-second = 0.5 CU-second (= total * 0.5).
  avg_vcore_hours_per_run?: number | null;
  total_vcore_hours?: number | null;
  est_cu_hours_from_vcore?: number | null;
  // Data Orchestration meter (Microsoft-published 0.0056 CU-hr per non-copy
  // activity run). Estimated as static non-copy activity count � pipeline runs.
  est_non_copy_activity_runs?: number;
  est_cu_hours_from_orchestration?: number;
}

export interface PipelineRunStats {
  pipeline: string;
  has_data_movement: boolean;
  last_run_at?: string | null;
  last_run_status?: string | null;
  windows: PipelineRunWindowStats[];
}

export interface PipelineRunHistory {
  window_start: string;
  window_end: string;
  fetched_run_count: number;
  fetched_activity_run_count: number;
  truncated: boolean;
  by_pipeline: PipelineRunStats[];
  /**
   * Per-UTC-day rollup of run outcomes across all pipelines in the
   * window. Keys are ISO date strings (YYYY-MM-DD); values count
   * succeeded / failed / other (in-progress, cancelled, queued).
   */
  daily_status?: Record<string, { succeeded: number; failed: number; other: number }>;
  /**
   * Per-UTC-hour rollup of run outcomes covering the trailing 24 hours
   * of the window. Keys are ISO hour strings (YYYY-MM-DDTHH:00:00Z);
   * values count succeeded / failed / other. Powers the 24h companion
   * chart on the dashboard.
   */
  hourly_status?: Record<string, { succeeded: number; failed: number; other: number }>;
}

export interface PipelinesReport {
  workspace_name?: string | null;
  generated_at: string;
  pipelines: Array<{ name: string; activity_count?: number }>;
  run_history?: PipelineRunHistory | null;
  errors?: string[];
}

// ---------------------------------------------------------------------------
// spark_pools.json
// ---------------------------------------------------------------------------

export interface SparkRunRecord {
  livy_id: number;
  kind: "scheduled" | "interactive";
  pool: string;
  name?: string | null;
  app_id?: string | null;
  submitter_id?: string | null;
  submitter_name?: string | null;
  artifact_id?: string | null;
  state?: string | null;
  result?: string | null;
  outcome: "succeeded" | "failed" | "in_progress";
  submitted_at?: string | null;
  ended_at?: string | null;
  duration_seconds?: number | null;
  driver_cores?: number | null;
  executor_cores?: number | null;
  num_executors?: number | null;
  total_vcores?: number | null;
  vcore_seconds?: number | null;
  vcore_hours?: number | null;
  // Fabric CU-hours = vcore_hours * 0.5 (1 CU = 2 Spark vCores).
  est_cu_hours_fabric_spark?: number | null;
}

export interface SparkRunWindowStats {
  window_days: number;
  run_count: number;
  succeeded: number;
  failed: number;
  in_progress: number;
  total_duration_hours: number;
  total_vcore_hours: number;
  est_cu_hours_fabric_spark: number;
  avg_vcore_hours_per_run?: number | null;
}

export interface SparkPoolRunStats {
  pool: string;
  kind: "scheduled" | "interactive";
  windows: SparkRunWindowStats[];
}

export interface SparkPoolsReport {
  workspace_name?: string | null;
  subscription_id?: string | null;
  resource_group?: string | null;
  generated_at: string;
  pools?: Array<{
    name: string;
    spark_version?: string | null;
    node_size?: string | null;
    node_count?: number | null;
    auto_scale_enabled?: boolean;
  }>;
  notebooks?: Array<{ name: string }>;
  spark_job_definitions?: Array<{ name: string }>;
  spark_runs?: SparkRunRecord[];
  run_stats?: SparkPoolRunStats[];
  errors?: string[];
}

// ---------------------------------------------------------------------------
// databricks_workflows.json (Slice 4-F)
// ---------------------------------------------------------------------------

export interface DatabricksWorkflowRun {
  job_id: number;
  job_name: string;
  run_id: number;
  run_name?: string | null;
  run_type?: string | null;
  trigger?: string | null;
  state?: string | null;
  result?: string | null;
  outcome?: "success" | "failed" | "cancelled" | "in_progress" | "unknown";
  start_time?: string | null;       // ISO-8601 UTC
  end_time?: string | null;
  duration_seconds?: number | null;
  cluster_kind?: "job_cluster" | "existing_cluster" | "serverless" | "unknown";
  cluster_id?: string | null;
  node_type_id?: string | null;
  driver_node_type_id?: string | null;
  num_workers?: number | null;
  worker_count_source?: "static" | "autoscale_min" | "autoscale_avg" | "autoscale_max" | "unknown";
  driver_vcores?: number | null;
  worker_vcores?: number | null;
  total_vcores?: number | null;
  vcore_seconds?: number | null;
  vcore_hours?: number | null;
  est_cu_hours_fabric_spark?: number | null;
  run_page_url?: string | null;
}

export interface DatabricksWorkflowRunWindowStats {
  window_days: number;
  run_count: number;
  completed_count: number;
  succeeded_count: number;
  failed_count: number;
  success_rate?: number | null;          // 0..1
  avg_duration_seconds?: number | null;
  total_vcore_hours: number;
  avg_vcore_hours_per_run?: number | null;
  // Fabric CU-hours = vcore_hours * 0.5 (1 CU = 2 Spark vCores).
  est_cu_hours_fabric_spark: number;
}

export interface DatabricksWorkflowRunStats {
  job_id: number;
  job_name: string;
  total_runs_observed: number;
  windows: DatabricksWorkflowRunWindowStats[];
}

export interface DatabricksInteractiveClusterUsage {
  cluster_id: string;
  cluster_name?: string | null;
  node_type_id?: string | null;
  driver_node_type_id?: string | null;
  num_workers?: number | null;
  worker_count_source?: "static" | "autoscale_min" | "autoscale_avg" | "autoscale_max" | "unknown";
  driver_vcores?: number | null;
  worker_vcores?: number | null;
  total_vcores?: number | null;
  windows: DatabricksWorkflowRunWindowStats[];
}

export interface DatabricksWorkflowsReport {
  workspace_name?: string | null;
  workspace_url?: string | null;
  workspace_id?: string | null;
  subscription_id?: string | null;
  resource_group?: string | null;
  generated_at: string;
  workflows?: Array<{
    job_id: number;
    name: string;
    task_count?: number;
    job_cluster_count?: number;
    uses_serverless?: boolean;
  }>;
  tasks?: unknown[];
  job_clusters?: unknown[];
  interactive_clusters?: unknown[];
  workflow_runs?: DatabricksWorkflowRun[];
  workflow_run_stats?: DatabricksWorkflowRunStats[];
  interactive_cluster_usage?: DatabricksInteractiveClusterUsage[];
  cluster_sizing_caveats?: string[];
  errors?: string[];
  workflow_count?: number;
  task_count?: number;
  unsupported_task_count?: number;
  partial_task_count?: number;
  sql_warehouses?: DatabricksSqlWarehouse[];
  sql_warehouse_queries?: unknown[];
  sql_warehouse_stats?: DatabricksSqlWarehouseStats[];
  sql_warehouse_daily_usage?: DatabricksSqlWarehouseDailyUsage[];
  sql_warehouse_fabric_mappings?: DatabricksSqlWarehouseFabricMapping[];
  sql_warehouse_count?: number;
  sql_warehouse_query_count?: number;
}

export interface DatabricksSqlWarehouse {
  warehouse_id: string;
  name: string;
  warehouse_type?: string | null;
  cluster_size?: string | null;
  state?: string | null;
  auto_stop_mins?: number | null;
  enable_serverless_compute?: boolean | null;
  enable_photon?: boolean | null;
  min_num_clusters?: number | null;
  max_num_clusters?: number | null;
  num_clusters?: number | null;
  creator_name?: string | null;
}

export interface DatabricksSqlWarehouseStats {
  warehouse_id: string;
  warehouse_name?: string | null;
  lookback_days: number;
  query_count: number;
  succeeded_count?: number;
  failed_count?: number;
  canceled_count?: number;
  success_rate?: number | null;
  total_duration_seconds?: number | null;
  avg_duration_seconds?: number | null;
  p50_duration_seconds?: number | null;
  p95_duration_seconds?: number | null;
  total_rows_produced?: number | null;
  total_bytes_read?: number | null;
  unique_users?: number;
  queries_from_jobs?: number;
  total_cpu_seconds?: number | null;
  queries_with_cpu_metric?: number;
}

export interface DatabricksSqlWarehouseDailyUsage {
  warehouse_id: string;
  warehouse_name?: string | null;
  usage_date: string;
  query_count?: number | null;
  total_task_seconds?: number | null;
  dbu_hours?: number | null;
  sku_name?: string | null;
}

export interface DatabricksSqlWarehouseFabricMapping {
  warehouse_id: string;
  warehouse_name?: string | null;
  source_warehouse_type?: string | null;
  target_fabric_artifact: string;
  recommended_sku?: string | null;
  support: "supported" | "partial" | "unsupported" | "unknown";
  confidence: "high" | "medium" | "low";
  lookback_days?: number | null;
  total_cpu_seconds?: number | null;
  queries_with_cpu_metric?: number;
  total_task_seconds?: number | null;
  total_dbu_hours?: number | null;
  avg_concurrent_cus?: number | null;
  peak_to_avg_headroom?: number;
  evidence_source: "rest_metrics" | "system_tables" | "both" | "none";
  notes?: string[];
}

// ---------------------------------------------------------------------------
// serverless_pools.json
// ---------------------------------------------------------------------------

export interface ServerlessDailyUsage {
  day: string;                  // "YYYY-MM-DD" (UTC)
  request_count: number;
  data_processed_mb: number;
  // Sum of per-query execution time for the day (seconds). Optional for
  // back-compat with pre-v2.7 artefacts.
  duration_seconds?: number;
  // SUM(data_processed_mb * duration_seconds) for the day � used by the
  // Fabric CU heuristic. Optional for back-compat.
  mb_seconds?: number;
}

export interface ServerlessHourlyUsage {
  hour: string;                 // ISO UTC hour, e.g. "2026-05-18T14:00:00Z"
  request_count: number;
  data_processed_mb: number;
  duration_seconds?: number;
  mb_seconds?: number;
}

export interface ServerlessCostEstimate {
  window_days: number;
  total_data_processed_tb: number;
  list_price_usd_per_tb: number;
  estimated_cost_usd: number;
  notes?: string | null;
}

export interface ServerlessTopQuery {
  request_id?: string | null;
  login_name?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  duration_seconds?: number | null;
  status?: string | null;
  error_code?: string | null;
  data_processed_mb?: number | null;
  command_text?: string | null;
}

export interface ServerlessReport {
  workspace_name?: string | null;
  endpoint_fqdn?: string | null;
  generated_at: string;
  databases?: Array<{ name: string }>;
  external_tables?: Array<unknown>;
  daily_usage?: ServerlessDailyUsage[];
  hourly_usage?: ServerlessHourlyUsage[];
  top_queries?: ServerlessTopQuery[];
  cost_estimate?: ServerlessCostEstimate | null;
  errors?: string[];
}

// ---------------------------------------------------------------------------
// cost.json
// ---------------------------------------------------------------------------

export interface MonthlyCostRow {
  month: string;
  resource_kind: string;
  resource_name?: string | null;
  sku?: string | null;
  cost: number;
  currency: string;
  usage_quantity: number;
  usage_unit?: string | null;
}

export interface FabricCostComparison {
  synapse_avg_monthly_cost: number;
  fabric_capacity_sku?: string | null;
  fabric_estimated_monthly_cost?: number | null;
  delta_abs?: number | null;
  delta_pct?: number | null;
  notes?: string | null;
}

export interface CostFinding {
  rule_id: string;
  severity: string;
  title: string;
  detail?: string | null;
  resource?: string | null;
}

export interface CostReport {
  workspace_name: string;
  subscription_id: string;
  resource_group: string;
  generated_at: string;
  window_start: string;
  window_end: string;
  rows: MonthlyCostRow[];
  monthly_totals: Record<string, number>;
  by_resource_kind: Record<string, number>;
  by_resource_name: Record<string, number>;
  fabric_comparison?: FabricCostComparison | null;
  findings: CostFinding[];
  errors?: string[];
  collection_status: string;
}

// ---------------------------------------------------------------------------
// governance.json
// ---------------------------------------------------------------------------

export interface RoleAssignment {
  scope: string;
  scope_kind: string;
  role_name: string;
  role_definition_id: string;
  principal_id: string;
  principal_type?: string | null;
  principal_display_name?: string | null;
  assignment_id: string;
  plane?: string;
}

export interface ManagedPrivateEndpoint {
  name: string;
  target_resource_id?: string | null;
  target_resource_type?: string | null;
  group_id?: string | null;
  provisioning_state?: string | null;
  connection_state?: string | null;
  fqdns?: string[];
}

export interface CustomerManagedKey {
  resource_id: string;
  resource_kind: string;
  enabled: boolean;
  key_vault_uri?: string | null;
  key_name?: string | null;
  key_version?: string | null;
  user_assigned_identity_id?: string | null;
  notes?: string | null;
}

export interface GovernanceFinding {
  rule_id: string;
  severity: string;
  resource_id?: string | null;
  title: string;
  detail?: string | null;
}

export interface GovernanceReport {
  workspace_name: string;
  subscription_id: string;
  resource_group: string;
  generated_at: string;
  role_assignments: RoleAssignment[];
  managed_private_endpoints: ManagedPrivateEndpoint[];
  customer_managed_keys: CustomerManagedKey[];
  purview_account?: string | null;
  purview_lineage?: unknown[];
  findings: GovernanceFinding[];
  errors?: string[];
}

// ---------------------------------------------------------------------------
// security.json
// ---------------------------------------------------------------------------

export interface FirewallRule {
  resource_id: string;
  resource_kind: string;
  name: string;
  start_ip?: string | null;
  end_ip?: string | null;
  is_allow_all?: boolean;
  is_allow_azure_services?: boolean;
}

export interface WorkspaceSecuritySettings {
  workspace_name: string;
  aad_only_authentication?: boolean | null;
  public_network_access?: string | null;
  minimum_tls_version?: string | null;
  encryption_at_rest?: string | null;
  managed_vnet?: boolean | null;
  notes?: string | null;
}

export interface CredentialEntry {
  container: string;
  container_name: string;
  credential_kind: string;
  secret_reference?: string | null;
  has_inline_secret?: boolean;
  notes?: string | null;
}

export interface PoolTdeStatus {
  pool_name: string;
  resource_id: string;
  status: string;
}

export interface SecurityFinding {
  rule_id: string;
  severity: string;
  resource_id?: string | null;
  title: string;
  detail?: string | null;
}

export interface SecurityReport {
  workspace_name: string;
  subscription_id: string;
  resource_group: string;
  generated_at: string;
  workspace_settings?: WorkspaceSecuritySettings | null;
  firewall_rules: FirewallRule[];
  credentials: CredentialEntry[];
  pool_tde_status: PoolTdeStatus[];
  aad_admins: string[];
  findings: SecurityFinding[];
  errors?: string[];
}

// ---------------------------------------------------------------------------
// monitoring.json
// ---------------------------------------------------------------------------

/**
 * A single Azure Monitor metric series for one resource (typically a
 * dedicated SQL pool). `points` is an ordered list of
 * `[ISO-8601 timestamp, value|null]` pairs sampled at `interval`.
 */
export interface MonitoringMetricSeries {
  resource_id: string;
  resource_kind: string; // "dedicated_pool"
  resource_name: string; // pool name
  metric_name: string;   // e.g. "DWUUsed", "DWUUsedPercent", "DWULimit"
  unit?: string | null;
  aggregation: string;   // "Average" | "Total" | "Maximum" | "Minimum"
  interval: string;      // ISO-8601 duration, e.g. "PT1H"
  points: Array<[string, number | null]>;
  min_value?: number | null;
  max_value?: number | null;
  avg_value?: number | null;
  p95_value?: number | null;
}

export interface MonitoringDwuDayStat {
  pool_name: string;
  day: string; // ISO-8601 date
  active_hours: number;
  active_dwu_hours: number;
  peak_dwu: number;
  peak_pct: number;
}

export interface MonitoringReport {
  workspace_name?: string | null;
  subscription_id?: string | null;
  resource_group?: string | null;
  generated_at: string;
  window_start: string;
  window_end: string;
  interval: string;
  series: MonitoringMetricSeries[];
  dwu_days: MonitoringDwuDayStat[];
  errors?: string[];
}

// ---------------------------------------------------------------------------
// Estate Overview (control-plane: GET /api/estate)
// ---------------------------------------------------------------------------

export interface EstateHistoryPoint {
  run_id: string;
  finished_at: string;
  status: 'queued' | 'running' | 'ok' | 'failed' | 'cancelled';
  readiness_score: number | null;
  blocker_count: number;
  warning_count: number;
  actual_monthly_cost: number | null;
}

export interface EstateWorkspace {
  key: string;
  tenant_id: string | null;
  subscription_id: string | null;
  resource_group: string | null;
  workspace_name: string;
  run_count: number;
  latest_run_id: string;
  latest_status: 'queued' | 'running' | 'ok' | 'failed' | 'cancelled';
  latest_finished_at: string;
  modules_run: string[];
  // v3 � source platform inherited from the latest run's first scope.
  source_type?: string | null;
  // 5.1.2 � hyperscaler bucket derived from source_type + (for
  // Databricks) the scope's extras["platform"]. "azure" | "aws" |
  // "gcp" | "on_prem"; null for legacy runs.
  cloud?: string | null;
  readiness_score: number | null;
  readiness_bucket: string | null;
  blocker_count: number;
  warning_count: number;
  info_count: number;
  tsql_compatibility_pct: number | null;
  projected_fabric_cu: number | null;
  recommended_fabric_sku: string | null;
  actual_monthly_cost: number | null;
  actual_currency: string | null;
  fabric_estimated_monthly_cost: number | null;
  fabric_estimated_monthly_cost_1y_ri?: number | null;
  fabric_estimated_monthly_cost_3y_ri?: number | null;
  fabric_pricing_source?: string | null;
  fabric_cost_delta_abs: number | null;
  fabric_cost_delta_pct: number | null;
  effort_hours_p50: number | null;
  effort_hours_p90: number | null;
  effort_days_p50: number | null;
  effort_days_p90: number | null;
  history: EstateHistoryPoint[];
}

export interface EstateTotals {
  workspaces: number;
  runs: number;
  tenants: number;
  subscriptions: number;
  ready: number;
  ready_with_effort: number;
  blocked: number;
  blockers_total: number;
  tsql_compatibility_pct_avg: number | null;
  projected_fabric_cu_total: number | null;
  actual_monthly_cost_total: number | null;
  fabric_estimated_monthly_cost_total: number | null;
  fabric_estimated_monthly_cost_1y_ri_total?: number | null;
  fabric_estimated_monthly_cost_3y_ri_total?: number | null;
  effort_hours_p50_total: number | null;
  effort_hours_p90_total: number | null;
  effort_days_p50_total: number | null;
  effort_days_p90_total: number | null;
}

export interface EstateTopBlocker {
  area: string;
  title: string;
  fabric_action: string | null;
  effort: string;
  workspaces: number;
  occurrences: number;
  example_run_id: string | null;
}

export interface EstateReport {
  generated_at: string;
  totals: EstateTotals;
  workspaces: EstateWorkspace[];
  top_blockers: EstateTopBlocker[];
}

// ---------------------------------------------------------------------------
// bigquery_workloads.json (BigQuery source)
// ---------------------------------------------------------------------------

export type BigQueryTableSupport =
  | "supported"
  | "partial"
  | "unsupported"
  | "unknown";

export interface BigQueryDataset {
  dataset_id: string;
  location?: string | null;
  description?: string | null;
  labels?: Record<string, string>;
  default_table_expiration_ms?: number | null;
  default_partition_expiration_ms?: number | null;
  created?: string | null;
  modified?: string | null;
}

export interface BigQueryTable {
  full_table_id: string;
  project_id: string;
  dataset_id: string;
  table_id: string;
  kind?: string | null;
  description?: string | null;
  num_rows?: number | null;
  num_bytes?: number | null;
  created?: string | null;
  modified?: string | null;
  support?: BigQueryTableSupport;
  notes?: string[];
}

export interface BigQueryRoutine {
  full_routine_id: string;
  project_id: string;
  dataset_id: string;
  routine_id: string;
  kind?: string | null;
  language?: string | null;
  definition?: string | null;
  created?: string | null;
  modified?: string | null;
}

export interface BigQueryScheduledQuery {
  name: string;
  display_name?: string | null;
  state?: string | null;
  schedule?: string | null;
  destination_dataset_id?: string | null;
  query?: string | null;
  next_run_time?: string | null;
  user_id?: string | null;
}

export interface BigQueryJob {
  job_id: string;
  project_id: string;
  location?: string | null;
  user_email?: string | null;
  job_type?: string | null;
  statement_type?: string | null;
  outcome?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  duration_seconds?: number | null;
  total_slot_ms?: number | null;
  total_slot_hours?: number | null;
  total_billed_bytes?: number | null;
  reservation_id?: string | null;
  priority?: string | null;
  parent_job_id?: string | null;
  edition?: string | null;
  labels?: Record<string, string>;
  query_text?: string | null;
  referenced_tables?: string[];
}

export interface BigQueryTableUsage {
  full_table_id: string;
  project_id: string;
  dataset_id: string;
  table_id: string;
  usage_count: number;
  total_elapsed_seconds: number;
  total_slot_ms: number;
  total_slot_hours: number;
  total_billed_bytes: number;
  est_cu_hours_fabric_spark: number;
  in_catalog: boolean;
  table_support?: BigQueryTableSupport;
}

export interface BigQueryDailyJobStats {
  date: string; // YYYY-MM-DD
  job_count: number;
  succeeded_count: number;
  failed_count: number;
  cancelled_count: number;
  other_count: number;
  total_slot_ms: number;
  total_slot_hours: number;
  total_billed_bytes: number;
  avg_duration_seconds?: number | null;
  success_rate?: number | null;
  est_cu_hours_fabric_spark: number;
}

export interface BigQueryJobBreakdown {
  dimension: string;
  key: string;
  job_count: number;
  succeeded_count: number;
  failed_count: number;
  total_slot_ms: number;
  total_slot_hours: number;
  total_billed_bytes: number;
  avg_duration_seconds?: number | null;
  est_cu_hours_fabric_spark: number;
}

export interface BigQueryQueryFeatureCount {
  feature: string;
  job_count: number;
}

export interface BigQueryJobWindowStats {
  window_days: number;
  job_count: number;
  completed_count: number;
  succeeded_count: number;
  failed_count: number;
  success_rate?: number | null;
  avg_duration_seconds?: number | null;
  total_slot_hours: number;
  total_billed_bytes: number;
  avg_slot_hours_per_job?: number | null;
  est_cu_hours_fabric_spark: number;
}

export interface BigQueryWorkloadsReport {
  project_id: string;
  project_number?: string | null;
  location?: string | null;
  generated_at: string;
  datasets: BigQueryDataset[];
  tables: BigQueryTable[];
  routines: BigQueryRoutine[];
  scheduled_queries: BigQueryScheduledQuery[];
  jobs: BigQueryJob[];
  job_window_stats: BigQueryJobWindowStats[];
  table_usage: BigQueryTableUsage[];
  daily_stats: BigQueryDailyJobStats[];
  breakdowns: BigQueryJobBreakdown[];
  query_features?: BigQueryQueryFeatureCount[];
  caveats?: string[];
  errors?: string[];
  dataset_count?: number;
  table_count?: number;
  view_count?: number;
  materialized_view_count?: number;
  external_table_count?: number;
  routine_count?: number;
  scheduled_query_count?: number;
  job_count?: number;
  unsupported_table_count?: number;
  partial_table_count?: number;
}

// ---------------------------------------------------------------------------
// Snowflake workloads — mirrors src/usma/modules/snowflake_workloads/models.py
// ---------------------------------------------------------------------------

export type SnowflakeObjectSupport = "supported" | "partial" | "unsupported" | "unknown";

export interface SnowflakeWarehouse {
  name: string;
  size?: string | null;
  type?: string | null;
  auto_suspend_seconds?: number | null;
  auto_resume?: boolean | null;
  min_cluster_count?: number | null;
  max_cluster_count?: number | null;
  scaling_policy?: string | null;
  state?: string | null;
  credits_per_hour?: number | null;
  est_vcore_hours_per_hour?: number | null;
  est_cu_hours_fabric_warehouse_per_hour?: number | null;
}

export interface SnowflakeDatabase {
  name: string;
  kind?: string | null;
  owner?: string | null;
  comment?: string | null;
}

export interface SnowflakeTable {
  database_name: string;
  schema_name: string;
  name: string;
  full_name: string;
  kind?: string | null;
  row_count?: number | null;
  bytes?: number | null;
  is_transient?: boolean;
  is_iceberg?: boolean;
  is_dynamic?: boolean;
  support: SnowflakeObjectSupport;
  notes?: string[];
}

export interface SnowflakeRoutine {
  database_name: string;
  schema_name: string;
  name: string;
  full_name: string;
  routine_kind?: string | null;
  language?: string | null;
  argument_count?: number;
  is_secure?: boolean;
  support: SnowflakeObjectSupport;
  notes?: string[];
}

export interface SnowflakeJob {
  query_id: string;
  warehouse_name?: string | null;
  warehouse_size?: string | null;
  user_name?: string | null;
  query_type?: string | null;
  execution_status?: string | null;
  outcome?: string | null;
  database_name?: string | null;
  schema_name?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  duration_seconds?: number | null;
  execution_ms?: number | null;
  queued_ms?: number | null;
  bytes_scanned?: number | null;
  rows_produced?: number | null;
  est_credits?: number | null;
  est_cu_hours_fabric_warehouse?: number | null;
}

export interface SnowflakeJobWindowStats {
  window_days: number;
  job_count: number;
  succeeded_count: number;
  failed_count: number;
  success_rate?: number | null;
  avg_duration_seconds?: number | null;
  total_bytes_scanned: number;
  est_credits: number;
  est_cu_hours_fabric_warehouse: number;
}

export interface SnowflakeWarehouseWindowStats {
  warehouse_name: string;
  window_days: number;
  total_credits: number;
  est_cu_hours_fabric_warehouse: number;
}

export interface SnowflakeDailyJobStats {
  date: string;
  job_count: number;
  succeeded_count: number;
  failed_count: number;
  other_count: number;
  total_bytes_scanned: number;
  total_execution_ms: number;
  total_queued_ms: number;
  avg_duration_seconds?: number | null;
  p95_duration_seconds?: number | null;
  success_rate?: number | null;
  est_credits: number;
  est_cu_hours_fabric_warehouse: number;
}

export interface SnowflakeJobBreakdown {
  dimension: string;
  key: string;
  job_count: number;
  succeeded_count: number;
  failed_count: number;
  total_bytes_scanned: number;
  total_execution_ms: number;
  avg_duration_seconds?: number | null;
  est_credits: number;
  est_cu_hours_fabric_warehouse: number;
}

export interface SnowflakeTableUsage {
  full_name: string;
  database_name: string;
  schema_name: string;
  object_name: string;
  object_domain: string;
  usage_count: number;
  total_bytes_scanned: number;
  total_rows_produced: number;
  total_execution_ms: number;
  last_seen?: string | null;
  in_catalog: boolean;
  table_support: SnowflakeObjectSupport;
  source: "access_history" | "query_history_fallback" | string;
}

export interface SnowflakeCodeObjectSummary {
  total: number;
  by_kind: Record<string, number>;
  by_support: Record<string, number>;
  by_language: Record<string, number>;
  compatibility_pct?: number | null;
  unsupported_object_names: string[];
  partial_object_names: string[];
  unknown_object_names: string[];
}

export interface SnowflakeWorkloadsReport {
  account: string;
  platform?: string | null;
  region?: string | null;
  edition?: string | null;
  generated_at: string;
  warehouses: SnowflakeWarehouse[];
  databases?: SnowflakeDatabase[];
  tables?: SnowflakeTable[];
  routines?: SnowflakeRoutine[];
  jobs?: SnowflakeJob[];
  job_window_stats?: SnowflakeJobWindowStats[];
  warehouse_window_stats?: SnowflakeWarehouseWindowStats[];
  daily_stats?: SnowflakeDailyJobStats[];
  breakdowns?: SnowflakeJobBreakdown[];
  table_usage?: SnowflakeTableUsage[];
  code_object_summary?: SnowflakeCodeObjectSummary | null;
  caveats?: string[];
  errors?: string[];
  warehouse_count?: number;
  database_count?: number;
  schema_count?: number;
  table_count?: number;
  external_table_count?: number;
  view_count?: number;
  materialized_view_count?: number;
  routine_count?: number;
  stage_count?: number;
  stream_count?: number;
  task_count?: number;
  pipe_count?: number;
  job_count?: number;
  unsupported_object_count?: number;
  partial_object_count?: number;
}

