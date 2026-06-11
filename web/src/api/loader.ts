/**
 * Data loader.
 *
 * Two modes:
 *
 *   1. Static / "deliverable" mode (default). The SPA is served alongside
 *      JSON files (e.g. `output/webui/index.html` next to
 *      `output/fabric_mapping.json`). Loaders fetch `./fabric_mapping.json`
 *      relative to the bundle.
 *
 *   2. Control-plane mode. The SPA is served by `sma serve --with-api` and
 *      the FastAPI backend is reachable at `/api`. Loaders detect this at
 *      startup (`GET /api/healthz`) and switch to
 *      `/api/runs/<id>/modules/<module>` against the currently selected run.
 *
 * The selected run id lives in the URL hash (`#run=<id>`) so it survives
 * deep-links and refreshes.
 */
import type {
  BigQueryWorkloadsReport,
  CostReport,
  DedicatedPoolsReport,
  EstateReport,
  EstateWorkspace,
  FabricMappingReport,
  DatabricksWorkflowsReport,
  GovernanceReport,
  MonitoringReport,
  PipelinesReport,
  RunDelta,
  SecurityReport,
  ServerlessReport,
  SnowflakeWorkloadsReport,
  SparkPoolsReport,
  StorageReport,
} from "../types";

export type ApiMode = "static" | "control-plane";
export type Health = { status: string; version: string };

let _modeProbe: Promise<ApiMode> | null = null;

export async function detectMode(): Promise<ApiMode> {
  if (_modeProbe) return _modeProbe;
  _modeProbe = (async () => {
    try {
      const r = await fetch("/api/healthz", { cache: "no-store" });
      if (r.ok) {
        const body = (await r.json()) as Health;
        if (body.status === "ok") return "control-plane" as const;
      }
    } catch {
      /* fall through */
    }
    return "static" as const;
  })();
  return _modeProbe;
}

const BASE = (import.meta.env.VITE_SMA_DATA_BASE as string | undefined) ?? "./";

function staticUrl(name: string): string {
  if (BASE.startsWith("/")) return `${BASE.replace(/\/$/, "")}/${name}`;
  return new URL(name, new URL(BASE, window.location.href)).toString();
}

export function getRunIdFromHash(): string | null {
  const hash = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(hash);
  const fromHash = params.get("run");
  if (fromHash) return fromHash;
  // Fallback: React Router's <NavLink to="/code-objects"> drops the hash
  // when the user navigates between tabs, so persist the selected run in
  // sessionStorage so the data pages can still resolve it.
  try {
    return window.sessionStorage.getItem("sma:runId");
  } catch {
    return null;
  }
}

export function setRunIdInHash(runId: string | null): void {
  const hash = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(hash);
  if (runId) params.set("run", runId);
  else params.delete("run");
  const next = params.toString();
  window.location.hash = next ? `#${next}` : "";
  try {
    if (runId) window.sessionStorage.setItem("sma:runId", runId);
    else window.sessionStorage.removeItem("sma:runId");
  } catch {
    /* sessionStorage unavailable — hash-only is fine */
  }
}

const _cache = new Map<string, Promise<unknown | null>>();

function cacheKey(mode: ApiMode, runId: string | null, name: string): string {
  return `${mode}|${runId ?? ""}|${name}`;
}

async function fetchJson<T>(name: string): Promise<T | null> {
  const mode = await detectMode();
  const runId = mode === "control-plane" ? getRunIdFromHash() : null;
  // In control-plane mode we *only* fetch via the run-id-aware endpoint.
  // If no run is selected yet, return null so the page renders an empty
  // state instead of trying ./fabric_mapping.json on the server (which
  // would 404 because the control-plane server only serves /api/* and
  // the SPA bundle).
  if (mode === "control-plane" && !runId) {
    return null;
  }
  const key = cacheKey(mode, runId, name);
  const cached = _cache.get(key);
  if (cached) return cached as Promise<T | null>;
  const promise = (async () => {
    const url =
      mode === "control-plane" && runId
        ? `/api/runs/${encodeURIComponent(runId)}/modules/${name.replace(/\.json$/, "")}`
        : staticUrl(name);
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) {
        console.warn(`[sma] ${name} not available (HTTP ${r.status} at ${url})`);
        return null;
      }
      return (await r.json()) as T;
    } catch (err) {
      console.warn(`[sma] ${name} fetch failed at ${url}:`, err);
      return null;
    }
  })();
  _cache.set(key, promise);
  return promise;
}

export function clearLoaderCache(): void {
  _cache.clear();
  _scopesCache.clear();
}

export const loadFabricMapping = () =>
  fetchJson<FabricMappingReport>("fabric_mapping.json");

export const loadDedicatedPools = () =>
  fetchJson<DedicatedPoolsReport>("dedicated_pools.json");

export const loadRunDelta = () => fetchJson<RunDelta>("run_delta.json");

export const loadStorage = () => fetchJson<StorageReport>("storage.json");

export const loadPipelines = () => fetchJson<PipelinesReport>("pipelines.json");

export const loadSparkPools = () => fetchJson<SparkPoolsReport>("spark_pools.json");

export const loadServerless = () => fetchJson<ServerlessReport>("serverless_pools.json");

export const loadDatabricksWorkflows = () =>
  fetchJson<DatabricksWorkflowsReport>("databricks_workflows.json");

export const loadBigqueryWorkloads = () =>
  fetchJson<BigQueryWorkloadsReport>("bigquery_workloads.json");

export const loadSnowflakeWorkloads = () =>
  fetchJson<SnowflakeWorkloadsReport>("snowflake_workloads.json");

export const loadMonitoring = () => fetchJson<MonitoringReport>("monitoring.json");

export const loadCost = () => fetchJson<CostReport>("cost.json");

export const loadGovernance = () => fetchJson<GovernanceReport>("governance.json");

export const loadSecurity = () => fetchJson<SecurityReport>("security.json");

export const loadModule = <T,>(filename: string) => fetchJson<T>(filename);

// ---------------------------------------------------------------------------
// Phase 2.7-C — scope-aware artefact loading
// ---------------------------------------------------------------------------

/**
 * Backend `/api/runs/{id}/scopes` entry. Mirrors
 * `FilesystemRunRepo.list_scopes` in `web/storage.py`.
 */
export type RunScope = {
  dir: string;
  source_type: ScopeRef["source_type"];
  slug: string;
  modules: string[];
};

export type ScopedPayload<T> = {
  /** ``null`` for legacy flat (single-scope) runs and for static mode. */
  scope: RunScope | null;
  payload: T | null;
};

const _scopesCache = new Map<string, Promise<RunScope[]>>();

/**
 * List per-scope sub-directories for a run. Returns ``[]`` for legacy
 * flat single-scope runs (backend convention). Cached per (mode, run).
 */
export async function apiListScopes(runId: string): Promise<RunScope[]> {
  const key = `scopes|${runId}`;
  const cached = _scopesCache.get(key);
  if (cached) return cached;
  const promise = (async () => {
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(runId)}/scopes`, {
        cache: "no-store",
      });
      if (!r.ok) {
        console.warn(`[sma] /scopes returned HTTP ${r.status} for ${runId}`);
        return [] as RunScope[];
      }
      const body = (await r.json()) as { run_id: string; scopes: RunScope[] };
      return body.scopes ?? [];
    } catch (err) {
      console.warn(`[sma] /scopes fetch failed for ${runId}:`, err);
      return [] as RunScope[];
    }
  })();
  _scopesCache.set(key, promise);
  return promise;
}

async function fetchJsonForScope<T>(
  runId: string,
  scopeDir: string,
  module: string,
): Promise<T | null> {
  const url =
    `/api/runs/${encodeURIComponent(runId)}/modules/${module}` +
    `?scope=${encodeURIComponent(scopeDir)}`;
  const key = cacheKey("control-plane", `${runId}::${scopeDir}`, `${module}.json`);
  const cached = _cache.get(key);
  if (cached) return cached as Promise<T | null>;
  const promise = (async () => {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) {
        console.warn(`[sma] ${module} scope=${scopeDir} HTTP ${r.status}`);
        return null;
      }
      return (await r.json()) as T;
    } catch (err) {
      console.warn(`[sma] ${module} scope=${scopeDir} fetch failed:`, err);
      return null;
    }
  })();
  _cache.set(key, promise);
  return promise;
}

/**
 * Load a module artefact for every scope of the current run.
 *
 * - **Static mode**: returns ``[{scope: null, payload}]`` where payload is
 *   the un-scoped JSON next to the bundle (legacy behaviour).
 * - **Control-plane, single-scope run** (or `/scopes` returned []): same
 *   shape — ``[{scope: null, payload}]`` from the flat artefact route.
 * - **Control-plane, multi-scope run**: one entry per scope, each carrying
 *   its `RunScope` descriptor and the per-scope payload (null if the
 *   module did not produce output for that scope).
 *
 * Pages opt in by calling this instead of the single-payload loaders.
 */
export async function loadModulePerScope<T>(
  module: string,
): Promise<ScopedPayload<T>[]> {
  const mode = await detectMode();
  const runId = mode === "control-plane" ? getRunIdFromHash() : null;
  if (mode !== "control-plane" || !runId) {
    const payload = await fetchJson<T>(`${module}.json`);
    return [{ scope: null, payload }];
  }
  const scopes = await apiListScopes(runId);
  if (scopes.length === 0) {
    const payload = await fetchJson<T>(`${module}.json`);
    return [{ scope: null, payload }];
  }
  const results = await Promise.all(
    scopes.map(async (scope) => ({
      scope,
      payload: scope.modules.includes(module)
        ? await fetchJsonForScope<T>(runId, scope.dir, module)
        : null,
    })),
  );
  return results;
}

/** Clear the cached `/scopes` response. Pairs with ``clearLoaderCache``. */
export function clearScopesCache(): void {
  _scopesCache.clear();
}

// ---------------------------------------------------------------------------
// Module availability probe (powers dynamic nav tabs)
// ---------------------------------------------------------------------------

/**
 * Module slugs that map 1:1 to a JSON artefact in the run output. Used to
 * decide which top-bar tabs to show: a tab whose required module is not
 * present in the current run (static mode) or whose state isn't ok/carried
 * (control-plane mode) is hidden.
 */
const PROBE_MODULES: ReadonlyArray<string> = [
  "fabric_mapping",
  "dedicated_pools",
  "run_delta",
  "cost",
  "governance",
  "security",
  "storage",
  "pipelines",
  "spark_pools",
  "serverless_pools",
  "monitoring",
  "bigquery_workloads",
  "snowflake_workloads",
  "databricks_workflows",
];

let _availabilityProbe: Promise<Set<string>> | null = null;

/**
 * Returns the set of module slugs that have produced data for the current
 * run. Result is cached for the lifetime of the page (the RunPicker
 * triggers a full reload when the user selects a different run, so the
 * cache lifetime matches the selected run).
 */
export async function detectAvailableModules(): Promise<Set<string>> {
  if (_availabilityProbe) return _availabilityProbe;
  _availabilityProbe = (async () => {
    const mode = await detectMode();
    const runId = mode === "control-plane" ? getRunIdFromHash() : null;
    if (mode === "control-plane") {
      if (!runId) return new Set<string>();
      try {
        const meta = await apiGetRun(runId);
        const available = new Set<string>();
        for (const m of meta.modules ?? []) {
          // Treat any state that produced output as "available". The
          // backend reports "ok" for fresh runs, "carried" for artefacts
          // inherited from a previous run, and may also report cached
          // variants. Anything else (queued/running/failed/skipped) means
          // there is no artefact for the user to drill into yet.
          const state = (m.state ?? "").toLowerCase();
          if (state === "ok" || state === "carried" || state.startsWith("ok-")) {
            available.add(m.name);
          }
        }
        return available;
      } catch {
        return new Set<string>();
      }
    }
    // Static mode: probe each known JSON in parallel. Loader cache means
    // pages re-using these loaders won't refetch.
    const probes: Array<[string, () => Promise<unknown | null>]> = [
      ["fabric_mapping", loadFabricMapping],
      ["dedicated_pools", loadDedicatedPools],
      ["run_delta", loadRunDelta],
      ["cost", loadCost],
      ["governance", loadGovernance],
      ["security", loadSecurity],
      ["storage", loadStorage],
      ["pipelines", loadPipelines],
      ["spark_pools", loadSparkPools],
      ["serverless_pools", loadServerless],
      ["monitoring", loadMonitoring],
      ["bigquery_workloads", loadBigqueryWorkloads],
      ["snowflake_workloads", loadSnowflakeWorkloads],
      ["databricks_workflows", loadDatabricksWorkflows],
    ];
    const results = await Promise.all(
      probes.map(async ([name, fn]) => {
        try {
          return (await fn()) ? name : null;
        } catch {
          return null;
        }
      }),
    );
    return new Set(results.filter((x): x is string => x !== null));
  })();
  return _availabilityProbe;
}

// Re-export so unit tests / dev tooling can poke at the probe list.
export const _PROBE_MODULES_FOR_TESTS = PROBE_MODULES;

// ---------------------------------------------------------------------------
// Control-plane API helpers
// ---------------------------------------------------------------------------

const API_HEADERS: HeadersInit = { "Content-Type": "application/json", "X-SMA-API": "1" };

/**
 * Phase 1.5 — wire representation of a `SourceDescriptor`.
 *
 * Mirrors the backend `usma.web.schemas.ScopeRef`
 * so the SPA can render scope counts + source-type chips on runs
 * history, the estate overview, and the run starter without having to
 * know the analyzer's internal source-provider machinery.
 */
export type ScopeRef = {
  source_type: "synapse_workspace" | "synapse_dedicated_sql" | "adf" | "databricks" | "bigquery" | "snowflake" | "sap_bw";
  id: string;
  display_name: string;
  subscription_id?: string | null;
  resource_group?: string | null;
  extras?: Record<string, unknown>;
};

export type RunMeta = {
  id: string;
  label: string | null;
  status: "queued" | "running" | "ok" | "failed" | "cancelled";
  started_at: string;
  finished_at: string | null;
  config_hash: string;
  modules: Array<{
    name: string;
    // "carried" indicates the artefact was inherited from a prior run
    // for the same workspace identity rather than produced in this run.
    state: string;
    started_at?: string | null;
    finished_at?: string | null;
    duration_ms?: number | null;
    error?: string | null;
    progress?: {
      current: number;
      total: number;
      label?: string | null;
      message?: string | null;
    } | null;
    carried_from_run_id?: string | null;
    carried_from_started_at?: string | null;
  }>;
  readiness_score: number | null;
  errors_count: number;
  tenant_id?: string | null;
  subscription_id?: string | null;
  resource_group?: string | null;
  workspace_name?: string | null;
  /** Phase 1.5 — source scopes targeted by this run. Legacy single-scope
   * runs leave this empty (workspace_name above is the single scope). */
  scopes?: ScopeRef[];
  /** Map module name -> source run id for carried-forward artefacts. */
  carried_from?: Record<string, string>;
};

export type SourceType = "synapse_workspace" | "adf" | "databricks" | "bigquery" | "snowflake" | "synapse_dedicated_sql";

export type AppConfig = {
  azure: {
    /** Phase 2.5: which kind of source this run targets. ``adf``
     *  reinterprets ``resource_group``/``workspace_name`` as the ADF
     *  factory's RG + name (no separate env vars). */
    source_type: SourceType;
    tenant_id: string | null;
    client_id: string | null;
    client_secret: "set" | "unset";
    subscription_id: string | null;
    resource_group: string | null;
    workspace_name: string | null;
    dedicated_pool: string | null;
    /** Phase 5: GCP project id (only meaningful when
     *  ``source_type === "bigquery"``). Persisted to
     *  ``SMA_GCP_PROJECT_ID``. */
    gcp_project_id: string | null;
    /** Phase 4.7: Databricks cloud platform discriminator (only
     *  meaningful when ``source_type === "databricks"``). Persisted to
     *  ``SMA_DATABRICKS_PLATFORM``. ``null`` means the env var is unset
     *  and the backend treats it as ``"azure"`` for backwards
     *  compatibility. */
    databricks_platform: "azure" | "aws" | null;
    /** Phase 4.7: Databricks-on-AWS connection settings. A single
     *  Databricks service principal (``databricks_client_id`` +
     *  ``databricks_client_secret``) is used for both account-level
     *  workspace discovery (paired with ``databricks_account_id``) and
     *  per-workspace REST auth. ``databricks_client_secret`` collapses
     *  to ``"set"`` / ``"unset"`` (same contract as ``client_secret``).
     *  All fields are ignored when ``databricks_platform !== "aws"``. */
    databricks_host: string | null;
    databricks_client_id: string | null;
    databricks_client_secret: "set" | "unset";
    databricks_account_id: string | null;
    /** Phase 7 Slice 7-E: Snowflake key-pair JWT auth settings (only
     *  meaningful when ``source_type === "snowflake"``). The OAuth
     *  client secret + refresh token + (optional) pre-minted access
     *  token collapse to ``"set"`` / ``"unset"`` on reads; writes
     *  accept the plaintext via the same write-only contract as
     *  ``client_secret``. ``snowflake_platform`` is a UI-only hint
     *  persisted to ``SMA_SNOWFLAKE_PLATFORM``. */
    snowflake_account: string | null;
    snowflake_user: string | null;
    snowflake_role: string | null;
    snowflake_warehouse: string | null;
    snowflake_oauth_client_id: string | null;
    snowflake_oauth_client_secret: "set" | "unset";
    snowflake_oauth_refresh_token: "set" | "unset";
    snowflake_oauth_token: "set" | "unset";
    snowflake_platform: "aws" | "azure" | "gcp" | null;
  };
  sql: { odbc_driver: string; login_timeout: number; query_timeout: number };
  output_dir: string;
  env_file: string;
  env_file_exists: boolean;
};

export type ConfigCheck = { name: string; ok: boolean; detail: string | null; category?: string | null };
export type WorkspaceSummary = {
  name: string;
  resource_group: string;
  location: string | null;
  sql_endpoint: string | null;
  sql_on_demand_endpoint: string | null;
  is_current: boolean;
};
export type ValidateResponse = {
  ok: boolean;
  checks: ConfigCheck[];
  workspaces?: WorkspaceSummary[] | null;
};

export async function apiListRuns(limit = 50): Promise<RunMeta[]> {
  const r = await fetch(`/api/runs?limit=${limit}`);
  if (!r.ok) throw new Error(`/api/runs: HTTP ${r.status}`);
  return r.json() as Promise<RunMeta[]>;
}

export async function apiGetRun(id: string): Promise<RunMeta> {
  const r = await fetch(`/api/runs/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`/api/runs/${id}: HTTP ${r.status}`);
  return r.json() as Promise<RunMeta>;
}

export async function apiStartRun(
  modules: string[],
  label?: string,
  days?: number,
  scopes?: ScopeRef[],
): Promise<{ id: string }> {
  const r = await fetch("/api/runs", {
    method: "POST",
    headers: API_HEADERS,
    body: JSON.stringify({
      modules,
      label: label ?? null,
      days: days ?? null,
      scopes: scopes ?? null,
    }),
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`POST /api/runs ${r.status}: ${detail}`);
  }
  return r.json() as Promise<{ id: string }>;
}

export async function apiCancelRun(id: string): Promise<{ cancelled: boolean }> {
  const r = await fetch(`/api/runs/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`DELETE /api/runs/${id}: HTTP ${r.status}`);
  return r.json() as Promise<{ cancelled: boolean }>;
}

export async function apiDeleteRunData(id: string): Promise<{ deleted: boolean }> {
  const r = await fetch(`/api/runs/${encodeURIComponent(id)}/data`, {
    method: "DELETE",
    headers: API_HEADERS,
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`DELETE /api/runs/${id}/data ${r.status}: ${detail}`);
  }
  return r.json() as Promise<{ deleted: boolean }>;
}

export interface DeleteAllRunsResult {
  deleted: number;
  cancelled: number;
  total: number;
  failed: string[];
}

export async function apiDeleteAllRuns(): Promise<DeleteAllRunsResult> {
  const r = await fetch("/api/runs", {
    method: "DELETE",
    headers: API_HEADERS,
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`DELETE /api/runs ${r.status}: ${detail}`);
  }
  return r.json() as Promise<DeleteAllRunsResult>;
}

export async function apiGetConfig(): Promise<AppConfig> {
  const r = await fetch("/api/config");
  if (!r.ok) throw new Error(`/api/config: HTTP ${r.status}`);
  return r.json() as Promise<AppConfig>;
}

export async function apiPutConfig(update: unknown): Promise<{ saved_to: string; warnings: string[] }> {
  const r = await fetch("/api/config", {
    method: "PUT",
    headers: API_HEADERS,
    body: JSON.stringify(update),
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`PUT /api/config ${r.status}: ${detail}`);
  }
  return r.json();
}

export async function apiValidateConfig(opts: { live?: boolean } = {}): Promise<ValidateResponse> {
  const qs = opts.live ? "?live=true" : "";
  const r = await fetch(`/api/config/validate${qs}`, { method: "POST", headers: API_HEADERS });
  if (!r.ok) throw new Error(`POST /api/config/validate: HTTP ${r.status}`);
  return r.json() as Promise<ValidateResponse>;
}

export async function apiDiscoverWorkspaces(): Promise<ValidateResponse> {
  const r = await fetch(`/api/config/discover-workspaces`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/config/discover-workspaces: HTTP ${r.status}`);
  return r.json() as Promise<ValidateResponse>;
}

export async function apiDiscoverFactories(): Promise<ValidateResponse> {
  const r = await fetch(`/api/config/discover-factories`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/config/discover-factories: HTTP ${r.status}`);
  return r.json() as Promise<ValidateResponse>;
}

export async function apiDiscoverSqlServers(): Promise<ValidateResponse> {
  const r = await fetch(`/api/config/discover-sql-servers`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/config/discover-sql-servers: HTTP ${r.status}`);
  return r.json() as Promise<ValidateResponse>;
}

export async function apiDiscoverDatabricksWorkspaces(): Promise<ValidateResponse> {
  const r = await fetch(`/api/config/discover-databricks-workspaces`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/config/discover-databricks-workspaces: HTTP ${r.status}`);
  return r.json() as Promise<ValidateResponse>;
}

export async function apiDiscoverBigqueryProjects(): Promise<ValidateResponse> {
  const r = await fetch(`/api/config/discover-bigquery-projects`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/config/discover-bigquery-projects: HTTP ${r.status}`);
  return r.json() as Promise<ValidateResponse>;
}

export async function apiDiscoverSnowflakeDatabases(): Promise<ValidateResponse> {
  const r = await fetch(`/api/config/discover-snowflake-databases`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/config/discover-snowflake-databases: HTTP ${r.status}`);
  return r.json() as Promise<ValidateResponse>;
}

// ---------------------------------------------------------------------------
// Slice 5-I — browser sign-in to GCP for Application Default Credentials.
// ---------------------------------------------------------------------------

export type GcpAuthState = "idle" | "pending" | "ready" | "error";

export interface GcpAuthStatus {
  state: GcpAuthState;
  email?: string | null;
  error?: string | null;
  adc_path?: string | null;
}

export interface GcpAuthLoginResponse {
  ok: boolean;
  state: GcpAuthState;
  message: string;
}

export async function apiGcpAuthLogin(): Promise<GcpAuthLoginResponse> {
  const r = await fetch(`/api/auth/gcp/login`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/auth/gcp/login: HTTP ${r.status}`);
  return r.json() as Promise<GcpAuthLoginResponse>;
}

export async function apiGcpAuthStatus(): Promise<GcpAuthStatus> {
  const r = await fetch(`/api/auth/gcp/status`);
  if (!r.ok) throw new Error(`GET /api/auth/gcp/status: HTTP ${r.status}`);
  return r.json() as Promise<GcpAuthStatus>;
}

export async function apiGcpAuthReset(): Promise<GcpAuthStatus> {
  const r = await fetch(`/api/auth/gcp/reset`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/auth/gcp/reset: HTTP ${r.status}`);
  return r.json() as Promise<GcpAuthStatus>;
}

// ---------------------------------------------------------------------------
// Slice 7-E.2 — browser sign-in to Snowflake (authorisation-code grant).
// Mirrors the Slice 5-I GCP flow but persists the resulting refresh token
// into the project ``.env`` instead of gcloud's ADC file.
// ---------------------------------------------------------------------------

export type SnowflakeAuthState = "idle" | "pending" | "ready" | "error";

export interface SnowflakeAuthStatus {
  state: SnowflakeAuthState;
  account?: string | null;
  error?: string | null;
  env_file?: string | null;
}

export interface SnowflakeAuthLoginResponse {
  ok: boolean;
  state: SnowflakeAuthState;
  message: string;
}

export async function apiSnowflakeAuthLogin(): Promise<SnowflakeAuthLoginResponse> {
  const r = await fetch(`/api/auth/snowflake/login`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`POST /api/auth/snowflake/login: HTTP ${r.status} ${text}`);
  }
  return r.json() as Promise<SnowflakeAuthLoginResponse>;
}

export async function apiSnowflakeAuthStatus(): Promise<SnowflakeAuthStatus> {
  const r = await fetch(`/api/auth/snowflake/status`);
  if (!r.ok) throw new Error(`GET /api/auth/snowflake/status: HTTP ${r.status}`);
  return r.json() as Promise<SnowflakeAuthStatus>;
}

export async function apiSnowflakeAuthReset(): Promise<SnowflakeAuthStatus> {
  const r = await fetch(`/api/auth/snowflake/reset`, {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) throw new Error(`POST /api/auth/snowflake/reset: HTTP ${r.status}`);
  return r.json() as Promise<SnowflakeAuthStatus>;
}

// ---------------------------------------------------------------------------
// Effort card (v2.10) — configurable rate card for migration effort estimates.
// ---------------------------------------------------------------------------

export interface EffortCardResponse {
  source: string;            // "default" or absolute path
  card: Record<string, unknown>;
  available_rule_keys: string[];
  available_phase_keys: string[];
  is_default: boolean;
}

export async function apiGetEffortCard(): Promise<EffortCardResponse> {
  const r = await fetch("/api/effort-card");
  if (!r.ok) throw new Error(`GET /api/effort-card: HTTP ${r.status}`);
  return r.json() as Promise<EffortCardResponse>;
}

export async function apiPutEffortCard(card: Record<string, unknown>): Promise<{ saved_to: string; source: string }> {
  const r = await fetch("/api/effort-card", {
    method: "PUT",
    headers: API_HEADERS,
    body: JSON.stringify({ card }),
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`PUT /api/effort-card ${r.status}: ${detail}`);
  }
  return r.json();
}

export async function apiResetEffortCard(): Promise<{ saved_to: string; source: string }> {
  const r = await fetch("/api/effort-card", { method: "DELETE", headers: API_HEADERS });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`DELETE /api/effort-card ${r.status}: ${detail}`);
  }
  return r.json();
}

// ---------------------------------------------------------------------------
// Run-attribution migration — rewrite legacy run.json files whose primary
// scope is non-Azure (BigQuery / Snowflake-on-AWS / Databricks-on-AWS|GCP)
// but inherited AZURE_TENANT_ID / AZURE_SUBSCRIPTION_ID from .env.
// ---------------------------------------------------------------------------

export interface MigrationStatus {
  needed: boolean;
  pending: string[];
  skipped_count: number;
  errors: string[];
}

export interface MigrationResult {
  updated: string[];
  skipped_count: number;
  errors: string[];
}

export async function apiGetRunAttributionStatus(): Promise<MigrationStatus> {
  const r = await fetch("/api/migrations/run-attribution");
  if (!r.ok) throw new Error(`/api/migrations/run-attribution: HTTP ${r.status}`);
  return r.json() as Promise<MigrationStatus>;
}

export async function apiRunAttributionMigrate(): Promise<MigrationResult> {
  const r = await fetch("/api/migrations/run-attribution", {
    method: "POST",
    headers: API_HEADERS,
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`POST /api/migrations/run-attribution ${r.status}: ${detail}`);
  }
  return r.json() as Promise<MigrationResult>;
}

// ---------------------------------------------------------------------------
// Runs archive (v2.11) — backup / restore all run data as a zip.
// ---------------------------------------------------------------------------

export type RunsImportMode = "skip_existing" | "overwrite" | "rename";

export interface RunsImportResult {
  imported: string[];
  skipped: string[];
  renamed: Record<string, string>;
  warnings: string[];
}

/** Download a zip of every run under the server's `runs_dir`.
 *
 * The browser save dialog is triggered by a temporary anchor element.
 * Secrets in `.env` live outside `runs_dir` so they are never included.
 */
export async function apiExportRunsArchive(opts: { includeEvents?: boolean } = {}): Promise<void> {
  const qs = opts.includeEvents === false ? "?include_events=false" : "";
  const r = await fetch(`/api/runs-archive/export${qs}`);
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`GET /api/runs-archive/export ${r.status}: ${detail}`);
  }
  const blob = await r.blob();
  // Honour the server's filename if present.
  let filename = "sma-runs.zip";
  const cd = r.headers.get("content-disposition") ?? "";
  const m = /filename="([^"]+)"/.exec(cd);
  if (m) filename = m[1];

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function apiImportRunsArchive(
  file: File,
  mode: RunsImportMode = "skip_existing",
): Promise<RunsImportResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);
  const r = await fetch("/api/runs-archive/import", {
    method: "POST",
    headers: { "X-SMA-API": "1" }, // do NOT set Content-Type — let the browser set the multipart boundary
    body: form,
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`POST /api/runs-archive/import ${r.status}: ${detail}`);
  }
  return r.json() as Promise<RunsImportResult>;
}

export async function apiGetDiff(runId: string, base?: string): Promise<unknown> {
  const qs = base ? `?base=${encodeURIComponent(base)}` : "";
  const r = await fetch(`/api/runs/${encodeURIComponent(runId)}/diff${qs}`);
  if (!r.ok) throw new Error(`GET /api/runs/${runId}/diff: HTTP ${r.status}`);
  return r.json();
}

export async function apiGetEstate(opts: { refresh?: boolean } = {}): Promise<EstateReport> {
  const qs = opts.refresh ? "?refresh=1" : "";
  const r = await fetch(`/api/estate${qs}`);
  if (!r.ok) throw new Error(`GET /api/estate: HTTP ${r.status}`);
  return r.json() as Promise<EstateReport>;
}

export async function apiGetEstateWorkspace(key: string): Promise<EstateWorkspace> {
  const r = await fetch(`/api/estate/workspaces/${encodeURIComponent(key)}`);
  if (!r.ok) throw new Error(`GET /api/estate/workspaces/${key}: HTTP ${r.status}`);
  return r.json() as Promise<EstateWorkspace>;
}

export function apiEstateCsvUrl(): string {
  return "/api/estate/export.csv";
}

export async function apiGetRunModule<T>(runId: string, name: string): Promise<T | null> {
  const slug = name.replace(/\.json$/, "");
  const r = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/modules/${encodeURIComponent(slug)}`,
    { cache: "no-store" },
  );
  if (!r.ok) return null;
  return r.json() as Promise<T>;
}


// ---------------------------------------------------------------------------
// Session log buffer.
// ---------------------------------------------------------------------------
export interface LogRecord {
  seq: number;
  ts: string;
  level: string;
  level_no: number;
  logger: string;
  message: string;
  exc: string | null;
}

export interface LogsResponse {
  records: LogRecord[];
  capacity: number;
  handler_level: string;
}

export async function apiGetLogs(params: { level?: string; since_seq?: number; limit?: number } = {}): Promise<LogsResponse> {
  const q = new URLSearchParams();
  if (params.level) q.set('level', params.level);
  if (params.since_seq !== undefined) q.set('since_seq', String(params.since_seq));
  if (params.limit !== undefined) q.set('limit', String(params.limit));
  const qs = q.toString() ? '?' + q.toString() : '';
  const r = await fetch('/api/logs' + qs);
  if (!r.ok) throw new Error('/api/logs: HTTP ' + r.status);
  return r.json() as Promise<LogsResponse>;
}

export async function apiClearLogs(): Promise<{ dropped: number }> {
  const r = await fetch('/api/logs', { method: 'DELETE', headers: API_HEADERS });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error('DELETE /api/logs ' + r.status + ': ' + detail);
  }
  return r.json();
}

