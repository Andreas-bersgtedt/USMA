import {
  loadBigqueryWorkloads,
  loadDatabricksWorkflows,
  loadFabricMapping,
  loadMonitoring,
  loadModulePerScope,
  loadServerless,
  loadSnowflakeWorkloads,
  loadSparkPools,
  loadStorage,
  detectMode,
  getRunIdFromHash,
  type RunScope,
} from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty, PctPill, ScorePill, SeverityPill, StatCard } from "../components/Atoms";
import HelpLink from "../components/HelpLink";
import { ProvenanceBadge, useCurrentRunMeta } from "../components/Provenance";
import ReanalyzeButton from "../components/ReanalyzeButton";
import EstateSummaryCard, { type EstateSummaryEntry } from "../components/EstateSummaryCard";
import ScopeFilter, { matchesScope, useScopeFilter } from "../components/ScopeFilter";
import type { SourceTypeId } from "../components/sourceTypes";
import { areaLabel, effortLabel, moduleLabel, sourceLabel, sourceNoun } from "../lib/labels";
import { useMemo } from "react";
import type { PipelinesReport, Recommendation, Severity, ModuleSummary } from "../types";

/**
 * Phase 3 — Pipelines is the cross-source module (both Synapse and ADF
 * produce ``pipelines.json``), so the Dashboard loads it per-scope and
 * renders one ``PipelinesSection`` per visible scope under a single
 * ``ScopeFilter`` row. Single-scope and legacy runs still render a
 * single section, byte-identical to the old shape.
 */
interface ScopedPipelines {
  scope: RunScope | null;
  payload: PipelinesReport | null;
}

async function loadPipelinesPerScope(): Promise<ScopedPipelines[]> {
  return loadModulePerScope<PipelinesReport>("pipelines");
}

function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtBytesGb(gb: number | null | undefined): string {
  if (gb == null || !Number.isFinite(gb)) return "—";
  if (gb >= 1024) return `${(gb / 1024).toFixed(2)} TB`;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  return `${(gb * 1024).toFixed(0)} MB`;
}

function fmtMb(mb: number | null | undefined): string {
  if (mb == null || !Number.isFinite(mb)) return "—";
  if (mb >= 1024 * 1024) return `${(mb / 1024 / 1024).toFixed(2)} TB`;
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  if (mb >= 10) return `${mb.toFixed(0)} MB`;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  if (mb > 0) return `${mb.toFixed(2)} MB`;
  return `0 MB`;
}

/**
 * Renders a one-line banner under the workspace title when one or more
 * module artefacts on the current run were carried forward from a prior
 * workspace-matched run. Silent in static mode and when nothing was
 * carried, so it never adds noise to a "clean" full run.
 */
function CarryForwardBanner({ runMeta }: { runMeta: import("../api/loader").RunMeta | null }) {
  if (!runMeta) return null;
  const carried = (runMeta.modules ?? []).filter((m) => m.state === "carried");
  if (carried.length === 0) return null;
  const refreshed = (runMeta.modules ?? [])
    .filter((m) => m.state !== "carried" && m.state !== "queued")
    .map((m) => m.name);
  return (
    <div
      className="muted small"
      style={{
        marginBottom: 16,
        padding: "6px 10px",
        borderLeft: "3px solid #888",
        background: "rgba(127,127,127,0.08)",
      }}
    >
      ↺ Incremental run — refreshed{" "}
      <strong>{refreshed.length > 0 ? refreshed.join(", ") : "(none)"}</strong>;
      carried <strong>{carried.length}</strong> module{carried.length === 1 ? "" : "s"} forward
      from prior run(s) (look for the <em>carried</em> pill on each section header).
    </div>
  );
}

// Module-scoped Fabric F-SKU table + picker. Mirrors
// ``_smallest_sku_covering`` (cu_projection.py) and
// ``recommend_fabric_sku`` (databricks_workflows.fabric_compat) so the
// SPA can roll up SKU recommendations without round-tripping through
// the analyzer. Used by both the top-level "Recommended SKU" stat card
// and the per-section Databricks rollup banner.
const FABRIC_F_UNITS = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048];
function pickFabricSku(cu: number): string | null {
  if (!Number.isFinite(cu) || cu <= 0) return null;
  for (const u of FABRIC_F_UNITS) {
    if (u >= cu) return `F${u}`;
  }
  return `F${FABRIC_F_UNITS[FABRIC_F_UNITS.length - 1]}+`;
}

export default function Dashboard() {
  const { data: fm, loading: fmLoading } = useAsync(loadFabricMapping);
  const { data: storage, loading: storageLoading } = useAsync(loadStorage);
  const { data: pipelinesScoped, loading: pipelinesLoading } = useAsync(loadPipelinesPerScope);
  const { data: serverless, loading: serverlessLoading } = useAsync(loadServerless);
  const { data: sparkPools, loading: sparkLoading } = useAsync(loadSparkPools);
  const { data: dbx, loading: dbxLoading } = useAsync(loadDatabricksWorkflows);
  const { data: bq, loading: bqLoading } = useAsync(loadBigqueryWorkloads);
  const { data: snow, loading: snowLoading } = useAsync(loadSnowflakeWorkloads);
  const { data: monitoring, loading: monitoringLoading } = useAsync(loadMonitoring);
  const { data: mode } = useAsync(detectMode);
  const runMeta = useCurrentRunMeta();

  // Pipelines scope-awareness — extract the per-scope entries, the
  // aggregate steady-state CU contribution (used by the SKU stat card,
  // which is a portfolio number and must NOT respect the filter pill),
  // and the filter selection that drives which scope sections render.
  const pipelinesEntries = useMemo<ScopedPipelines[]>(
    () => pipelinesScoped ?? [],
    [pipelinesScoped],
  );
  const pipelinesScopes = useMemo<RunScope[]>(
    () =>
      pipelinesEntries
        .map((e) => e.scope)
        .filter((s): s is RunScope => s !== null),
    [pipelinesEntries],
  );
  const {
    selection: pipelinesSelection,
    setSelection: setPipelinesSelection,
  } = useScopeFilter(runMeta?.id ?? null, pipelinesScopes);
  // Sum total pipelines (across scopes) for the pill total-count badge.
  const pipelinesTotalCount = useMemo(
    () =>
      pipelinesEntries.reduce(
        (acc, e) => acc + (e.payload?.pipelines?.length ?? 0),
        0,
      ),
    [pipelinesEntries],
  );
  const pipelinesScopeCounts = useMemo<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const e of pipelinesEntries) {
      if (!e.scope) continue;
      out[e.scope.dir] = e.payload?.pipelines?.length ?? 0;
    }
    return out;
  }, [pipelinesEntries]);

  // Source-type-aware labeling. Use the first scope's source type as
  // the "primary" platform identity for page chrome. Falls back to null
  // for legacy runs that never recorded a scopes[] array; downstream
  // helpers render generic "scope" / "Scope" wording in that case.
  const primarySourceType: string | null = useMemo(() => {
    const s = runMeta?.scopes?.[0];
    return s?.source_type ?? null;
  }, [runMeta]);
  const primarySourceNoun = sourceNoun(primarySourceType);

  const loading =
    fmLoading || storageLoading || pipelinesLoading || serverlessLoading || sparkLoading || dbxLoading || bqLoading || snowLoading || monitoringLoading;
  if (loading) return <div className="empty">Loading…</div>;


  // Has *any* module produced data? If so, render the dashboard with the
  // sections that exist instead of bailing out because fabric_mapping.json
  // is missing — e.g. when only the `spark_pools` module ran, we still want
  // to show the Spark section instead of a hard error.
  const hasAny = Boolean(
    fm
      || (storage && (storage.dedicated_pool_storage?.length || storage.accounts?.length || storage.capacities?.length))
      || pipelinesEntries.some((e) => e.payload?.run_history && e.payload.run_history.by_pipeline?.length)
      || (sparkPools && (sparkPools.pools?.length || sparkPools.run_stats?.length || sparkPools.spark_runs?.length))
      || (dbx && ((dbx.workflows?.length ?? 0) || (dbx.workflow_run_stats?.length ?? 0) || (dbx.interactive_cluster_usage?.length ?? 0)))
      || (bq && ((bq.jobs?.length ?? 0) || (bq.daily_stats?.length ?? 0) || (bq.tables?.length ?? 0)))
      || (serverless && (serverless.databases?.length || serverless.daily_usage?.length))
      || (monitoring && (monitoring.series?.length || monitoring.dwu_days?.length))
  );

  if (!hasAny) {
    if (mode === "control-plane") {
      const runId = getRunIdFromHash();
      if (!runId) {
        return (
          <Empty>
            No run selected. Pick one from the run picker in the top bar,
            or kick off a new analyzer run from the <code>Run</code> page.
          </Empty>
        );
      }
      return (
        <Empty>
          No module output available for run <code>{runId}</code> yet.
          The run may still be in progress, no modules were selected, or
          the run failed before producing any artifacts. See the{" "}
          <code>Runs</code> page for status.
        </Empty>
      );
    }
    return (
      <Empty>
        No analyzer output found. Run <code>sma analyze-all</code> (or a
        specific module like <code>sma analyze-spark-pools</code>) first,
        then point the SPA at the output directory (see{" "}
        <code>web/README.md</code>).
      </Empty>
    );
  }

  const rd = fm?.readiness ?? null;
  const cp = fm?.capacity_projection ?? null;
  const sevCount = (s: Severity) =>
    rd?.counts?.[s] ?? (fm?.recommendations.filter((r: Recommendation) => r.severity === s).length ?? 0);

  // Databricks SQL warehouse rollup: sum the per-warehouse headroom-adjusted
  // CU figures so the "Recommended SKU" headline keeps working on
  // Databricks-only scopes where ``fabric_mapping.capacity_projection`` is
  // null. Mirrors ``_databricks_sql_daily_cu`` in src/usma/web/estate.py.
  const databricksSqlHeadroomCu = (() => {
    const mappings = dbx?.sql_warehouse_fabric_mappings ?? [];
    let total = 0;
    for (const m of mappings) {
      const avg = m.avg_concurrent_cus;
      if (avg == null || !Number.isFinite(avg)) continue;
      const headroom = m.peak_to_avg_headroom && m.peak_to_avg_headroom > 0
        ? m.peak_to_avg_headroom
        : 4;
      total += avg * headroom;
    }
    return total;
  })();
  const databricksSqlSkus = (dbx?.sql_warehouse_fabric_mappings ?? [])
    .map((m) => m.recommended_sku)
    .filter((s): s is string => !!s);
  const databricksSqlRolledSku = pickFabricSku(databricksSqlHeadroomCu);

  // Steady-state CU from pipeline integration activity (DIU-hr + Orch CU-hr).
  // 1 CU sustained = 24 CU-hr / day, so daily_CU = (Σ est CU-hr ÷ window_days) ÷ 24.
  //
  // Phase 3: this is a portfolio number (feeds the SKU recommendation
  // stat card), so we sum across *every* scope's pipelines payload
  // regardless of the filter pill. The pill only steers which scope's
  // PipelinesSection is rendered.
  const integrationDailyCu = (() => {
    let totalCuHr = 0;
    let window = 0;
    for (const entry of pipelinesEntries) {
      const hist = entry.payload?.run_history;
      if (!hist || !hist.by_pipeline?.length) continue;
      for (const p of hist.by_pipeline) {
        const w = p.windows.find((w) => w.window_days === 7) ?? p.windows[0];
        if (!w) continue;
        totalCuHr += w.est_cu_hours_from_diu ?? 0;
        totalCuHr += w.est_cu_hours_from_vcore ?? 0;
        totalCuHr += w.est_cu_hours_from_orchestration ?? 0;
        window = w.window_days;
      }
    }
    if (window <= 0) return 0;
    return (totalCuHr / window) / 24;
  })();

  // Steady-state CU from Spark Livy job history (notebook sessions + scheduled
  // batches). vCore-hours are already converted to Fabric CU-hours by the
  // analyzer using the documented 1 CU = 2 Spark vCores rate, so we just sum
  // the per-pool / per-kind 7-day window totals.
  const sparkDailyCu = (() => {
    const stats = sparkPools?.run_stats;
    if (!stats || stats.length === 0) return 0;
    let totalCuHr = 0;
    let window = 0;
    for (const s of stats) {
      const w = s.windows.find((w) => w.window_days === 7) ?? s.windows[0];
      if (!w) continue;
      totalCuHr += w.est_cu_hours_fabric_spark ?? 0;
      window = w.window_days;
    }
    if (window <= 0) return 0;
    return (totalCuHr / window) / 24;
  })();

  return (
    <>
      <h1 style={{ margin: "0 0 4px" }}>
        {fm?.workspace_name ?? `(${primarySourceNoun})`} <HelpLink slug="04-dashboard" />
      </h1>
      <div className="muted small" style={{ marginBottom: 16 }}>
        {primarySourceType && (
          <>
            <span className="pill muted" title={primarySourceType}>
              {sourceLabel(primarySourceType)}
            </span>
            {" · "}
          </>
        )}
        {fm
          ? `Generated ${new Date(fm.generated_at).toLocaleString()}`
          : `Partial run — fabric_mapping module did not run, showing available sections.`}
      </div>
      <CarryForwardBanner runMeta={runMeta} />

      {runMeta?.scopes && runMeta.scopes.length > 0 && (() => {
        // Phase 3 — group scopes by source type and render a portfolio
        // readiness rollup. Today every scope shares the run's single
        // readiness score (multi-scope dispatch lands in Phase 2.5),
        // so the per-type readinessPct is identical to the overall.
        const overall = rd?.score ?? 0;
        const counts = new Map<SourceTypeId, number>();
        for (const s of runMeta.scopes) {
          const id = s.source_type as SourceTypeId;
          counts.set(id, (counts.get(id) ?? 0) + 1);
        }
        const entries: EstateSummaryEntry[] = Array.from(counts.entries()).map(
          ([type, scopeCount]) => ({ type, scopeCount, readinessPct: overall }),
        );
        return (
          <div style={{ marginBottom: 16 }}>
            <EstateSummaryCard entries={entries} overallReadinessPct={overall} />
          </div>
        );
      })()}

      {fm && (
        <div className="grid cols-4">
        <StatCard
          label="Readiness score"
          value={rd ? <ScorePill score={rd.score} /> : "—"}
          sub={rd?.bucket}
        />
        <StatCard
          label="T-SQL compatible"
          value={<PctPill pct={rd?.tsql_compatibility_pct} />}
          sub={
            rd?.tsql_objects_total
              ? `${rd.tsql_objects_total} objects · ${rd.tsql_objects_incompatible ?? 0} incompatible · ${rd.tsql_objects_needs_review ?? 0} needs review`
              : "no code objects collected"
          }
        />
        <StatCard
          label="Recommendations"
          value={fm.recommendations.length}
          sub={
            <>
              <span className="pill err">{sevCount("blocker")}</span>{" "}
              <span className="pill warn">{sevCount("warning")}</span>{" "}
              <span className="pill info">{sevCount("info")}</span>
            </>
          }
        />
        <StatCard
          label="Recommended SKU"
          value={
            cp?.recommended_sku
              ?? databricksSqlRolledSku
              ?? "—"
          }
          sub={
            cp
              ? (() => {
                  // v2.6.2 — backend now includes Spark + Pipelines CU
                  // in estimated_cu. Show the breakdown when available.
                  // Adaptive precision: contributions can be sub-CU for
                  // light workloads (esp. serverless), so we keep 2–3 dp
                  // when they would otherwise round to 0.0.
                  const fmtCu = (v: number) => {
                    if (v >= 1) return v.toFixed(1);
                    if (v >= 0.1) return v.toFixed(2);
                    if (v >= 0.01) return v.toFixed(3);
                    if (v > 0) return "<0.01";
                    return v.toFixed(1);
                  };
                  const dwuCu = cp.dwu_cu_contribution ?? 0;
                  const sparkCu = cp.spark_cu_contribution ?? 0;
                  const pipeCu = cp.pipelines_cu_contribution ?? 0;
                  const slessCu = cp.serverless_cu_contribution ?? 0;
                  const slessPeakDayCuH = cp.serverless_peak_day_cu_hours ?? 0;
                  const parts: string[] = [];
                  if (dwuCu > 0) parts.push(`DW ${fmtCu(dwuCu)}`);
                  if (sparkCu > 0) parts.push(`Spark ${fmtCu(sparkCu)}`);
                  if (pipeCu > 0) parts.push(`Pipelines ${fmtCu(pipeCu)}`);
                  if (slessCu > 0) parts.push(`Serverless ${fmtCu(slessCu)}`);
                  const breakdown = parts.length > 0 ? ` · ${parts.join(" + ")} CU` : "";
                  const slessNote = slessPeakDayCuH > 0
                    ? ` · Serverless peak day ≈ ${slessPeakDayCuH.toFixed(2)} CU-h (smoothed over 24 h)`
                    : "";
                  return `${cp.estimated_cu.toFixed(1)} CU (${cp.headroom_pct}% headroom)${breakdown}${slessNote}`;
                })()
              : databricksSqlRolledSku
                ? (() => {
                    const n = databricksSqlSkus.length;
                    const parts = [
                      `${databricksSqlHeadroomCu.toFixed(2)} CU from ${n} Databricks SQL warehouse${n === 1 ? "" : "s"}`,
                    ];
                    if (databricksSqlSkus.length > 0) {
                      parts.push(`per-warehouse: ${databricksSqlSkus.join(", ")}`);
                    }
                    return parts.join(" · ");
                  })()
                : integrationDailyCu > 0 || sparkDailyCu > 0
                  ? `no monitoring data${
                      integrationDailyCu > 0
                        ? ` · ${integrationDailyCu.toFixed(2)} CU/day from pipelines`
                        : ""
                    }${
                      sparkDailyCu > 0
                        ? ` · ${sparkDailyCu.toFixed(2)} CU/day from Spark`
                        : ""
                    }`
                  : "no monitoring data"
          }
        />
      </div>
      )}

      {rd?.top_blockers && rd.top_blockers.length > 0 && (
        <section className="section">
          <h2>Top blockers</h2>
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Effort</th>
                <th>Area</th>
                <th>Title</th>
                <th>Fabric action</th>
              </tr>
            </thead>
            <tbody>
              {rd.top_blockers.map((b: Recommendation) => (
                <tr key={b.id}>
                  <td><SeverityPill severity={b.severity} /></td>
                  <td title={b.effort}>{effortLabel(b.effort)}</td>
                  <td className="small" title={b.area}>{areaLabel(b.area)}</td>
                  <td>{b.title}</td>
                  <td className="small muted">{b.fabric_action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <DwuUtilizationSection monitoring={monitoring} runMeta={runMeta} />
      <StorageSection storage={storage} runMeta={runMeta} />
      {/* Phase 3 — pipelines is the cross-source module; render one
          section per visible scope under a single scope filter row.
          Single-scope and legacy (un-scoped) runs render exactly one
          section, byte-identical to the pre-Phase-3 layout. */}
      {pipelinesScopes.length > 1 && (
        <ScopeFilter
          scopes={pipelinesScopes}
          selection={pipelinesSelection}
          onChange={setPipelinesSelection}
          runMeta={runMeta}
          counts={pipelinesScopeCounts}
          totalCount={pipelinesTotalCount}
        />
      )}
      {pipelinesEntries
        .filter((e) => matchesScope(e.scope, pipelinesSelection))
        .map((e, idx) => (
          <PipelinesSection
            key={e.scope?.dir ?? `flat-${idx}`}
            pipelines={e.payload}
            runMeta={runMeta}
            scope={e.scope}
          />
        ))}
      <SparkPoolsSection sparkPools={sparkPools} runMeta={runMeta} />
      <DatabricksWorkflowsSection databricks={dbx} runMeta={runMeta} />
      <BigQueryWorkloadsSection bq={bq} runMeta={runMeta} />
      <SnowflakeWorkloadsSection snow={snow} runMeta={runMeta} />
      <ServerlessSection serverless={serverless} runMeta={runMeta} />

      {fm && (
        <section className="section">
          <h2>Inputs analyzed</h2>
          <table>
            <thead>
              <tr><th>Module</th><th>Source</th><th>Counts</th></tr>
            </thead>
            <tbody>
              {fm.inputs.map((i: ModuleSummary) => (
                <tr key={i.module}>
                  <td title={i.module}>{moduleLabel(i.module)}</td>
                  <td className="small muted">{i.source_file}</td>
                  <td className="small">
                    {Object.entries(i.counts || {})
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([k, v]) => `${k}=${v}`)
                      .join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// DWU utilization (Azure Monitor historical DWUUsedPercent per pool)
// ---------------------------------------------------------------------------

/**
 * Severity-coloured DWU % badge. Unlike `PctPill` (which is calibrated for
 * "higher is better" metrics like readiness score), high DWU % means
 * the pool is saturated — so we invert the colour ramp.
 */
function DwuPctTag({ value }: { value: number | null }) {
  if (value == null || !Number.isFinite(value)) return <span className="muted">—</span>;
  const cls = value >= 90 ? "err" : value >= 70 ? "warn" : "ok";
  return <span className={`pill ${cls}`}>{value.toFixed(0)}%</span>;
}

/**
 * Renders a 7d (or whatever the monitoring window is) timeseries of
 * `DWUUsedPercent` per dedicated SQL pool plus headline stats (peak %,
 * p95 %, peak DWU, active hours). Reads `monitoring.json` directly — no
 * backend changes needed, the monitoring module already collects this
 * data via Azure Monitor (`DWUUsed`, `DWUUsedPercent`, `DWULimit`).
 *
 * Hides itself when no monitoring artefact exists for the current run
 * (e.g. user skipped `--with monitoring`) or when no DWU series were
 * returned (e.g. paused pools, missing Monitoring Reader RBAC).
 */
function DwuUtilizationSection({
  monitoring,
  runMeta,
}: {
  monitoring: import("../types").MonitoringReport | null;
  runMeta?: import("../api/loader").RunMeta | null;
}) {
  if (!monitoring) return null;
  const series = monitoring.series ?? [];
  // We chart DWUUsedPercent (0–100, pool-size agnostic). Fall back to
  // computing it from DWUUsed/DWULimit if the provider only returned the
  // absolute metrics (older provider versions, custom retention pipelines).
  const pctByPool = new Map<string, Array<[string, number | null]>>();
  const limitByPool = new Map<string, number>();
  const usedByPool = new Map<string, Array<[string, number | null]>>();
  for (const s of series) {
    if (s.resource_kind !== "dedicated_pool") continue;
    if (s.metric_name === "DWUUsedPercent") {
      pctByPool.set(s.resource_name, s.points ?? []);
    } else if (s.metric_name === "DWULimit") {
      // Use max value as the steady-state DWU limit (limit can change if
      // someone scaled the pool inside the window — pick the recent peak
      // so the badge reflects current sizing).
      if (s.max_value != null) limitByPool.set(s.resource_name, s.max_value);
    } else if (s.metric_name === "DWUUsed") {
      usedByPool.set(s.resource_name, s.points ?? []);
    }
  }
  // Synthesise % from absolutes when needed.
  for (const [pool, used] of usedByPool) {
    if (pctByPool.has(pool)) continue;
    const lim = limitByPool.get(pool);
    if (!lim || lim <= 0) continue;
    pctByPool.set(
      pool,
      used.map(([t, v]) => [t, v == null ? null : (v / lim) * 100]),
    );
  }
  const pools = Array.from(pctByPool.keys()).sort();
  if (pools.length === 0) return null;

  const windowStart = new Date(monitoring.window_start);
  const windowEnd = new Date(monitoring.window_end);
  const windowDays = Math.max(
    1,
    Math.round((windowEnd.getTime() - windowStart.getTime()) / 86_400_000),
  );

  // Per-pool stats (peak, p95, avg) over the chart window.
  const stats = pools.map((pool) => {
    const pts = pctByPool.get(pool) ?? [];
    const vals = pts.map(([, v]) => v).filter((v): v is number => v != null && Number.isFinite(v));
    const peak = vals.length ? Math.max(...vals) : null;
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    const sorted = vals.slice().sort((a, b) => a - b);
    const p95 = sorted.length
      ? sorted[Math.min(sorted.length - 1, Math.floor(0.95 * (sorted.length - 1)))]
      : null;
    const limit = limitByPool.get(pool) ?? null;
    // Look up the corresponding dwu_days roll-up for active hours / peak DWU.
    const days = (monitoring.dwu_days ?? []).filter((d) => d.pool_name === pool);
    const activeHours = days.reduce((a, d) => a + (d.active_hours ?? 0), 0);
    const peakDwu = days.length ? Math.max(...days.map((d) => d.peak_dwu ?? 0)) : null;
    return { pool, peak, p95, avg, limit, activeHours, peakDwu };
  });

  // Headline cards: estate totals across all pools.
  const overallPeak = stats.reduce<number | null>(
    (acc, s) => (s.peak == null ? acc : Math.max(acc ?? 0, s.peak)),
    null,
  );
  const overallP95 = (() => {
    const all: number[] = [];
    for (const pool of pools) {
      const pts = pctByPool.get(pool) ?? [];
      for (const [, v] of pts) if (v != null && Number.isFinite(v)) all.push(v);
    }
    if (!all.length) return null;
    all.sort((a, b) => a - b);
    return all[Math.min(all.length - 1, Math.floor(0.95 * (all.length - 1)))];
  })();
  const totalActiveHours = stats.reduce((a, s) => a + s.activeHours, 0);

  return (
    <section className="section">
      <h2>
        DWU utilization
        <ProvenanceBadge meta={runMeta ?? null} module="monitoring" />
        <ReanalyzeButton modules={["monitoring"]} days={windowDays} title="Re-run the monitoring analyzer for this workspace" />
      </h2>
      <div className="small muted" style={{ marginBottom: 8 }}>
        Azure Monitor <code>DWUUsedPercent</code> over the last {windowDays} day
        {windowDays === 1 ? "" : "s"} ({monitoring.interval} samples) — one line
        per dedicated SQL pool. Source: <code>monitoring.json</code>.
      </div>

      <div className="grid cols-4">
        <StatCard
          label="Peak DWU %"
          value={overallPeak != null ? `${overallPeak.toFixed(0)}%` : "—"}
          sub={`across ${pools.length} pool${pools.length === 1 ? "" : "s"}`}
        />
        <StatCard
          label="P95 DWU %"
          value={overallP95 != null ? `${overallP95.toFixed(0)}%` : "—"}
          sub={`window ${windowDays}d · ${monitoring.interval}`}
        />
        <StatCard
          label="Active hours"
          value={totalActiveHours > 0 ? totalActiveHours.toFixed(1) : "—"}
          sub={`pool-hours with DWU > 0`}
        />
        <StatCard
          label="Pools observed"
          value={pools.length}
          sub={pools.length === 1 ? pools[0] : `${pools.slice(0, 3).join(", ")}${pools.length > 3 ? "…" : ""}`}
        />
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          marginTop: 12,
        }}
      >
        <div style={{ flex: "1 1 360px", minWidth: 0 }}>
          <DwuLineChart pools={pools} pctByPool={pctByPool} windowDays={windowDays} mode="window" />
        </div>
        <div style={{ flex: "1 1 360px", minWidth: 0 }}>
          <DwuLineChart pools={pools} pctByPool={pctByPool} windowDays={windowDays} mode="24h" />
        </div>
      </div>

      {stats.length > 0 && (
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Pool</th>
              <th className="num">DWU limit</th>
              <th className="num">Peak DWU</th>
              <th className="num">Peak %</th>
              <th className="num">P95 %</th>
              <th className="num">Avg %</th>
              <th className="num">Active hours</th>
            </tr>
          </thead>
          <tbody>
            {stats.map((s) => (
              <tr key={s.pool}>
                <td><code>{s.pool}</code></td>
                <td className="num">{s.limit != null ? `DW${Math.round(s.limit)}c` : "—"}</td>
                <td className="num">{s.peakDwu != null ? fmtNum(s.peakDwu) : "—"}</td>
                <td className="num"><DwuPctTag value={s.peak ?? null} /></td>
                <td className="num"><DwuPctTag value={s.p95 ?? null} /></td>
                <td className="num">{s.avg != null ? `${s.avg.toFixed(0)}%` : "—"}</td>
                <td className="num">{s.activeHours > 0 ? s.activeHours.toFixed(1) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/**
 * Inline-SVG multi-line chart of DWU % over the monitoring window. One
 * polyline per pool, plus a dashed 100 % reference. Same rendering
 * conventions as DailyRunsChart so the dashboard stays visually
 * consistent.
 */
function DwuLineChart({
  pools,
  pctByPool,
  windowDays,
  mode = "window",
}: {
  pools: string[];
  pctByPool: Map<string, Array<[string, number | null]>>;
  windowDays: number;
  mode?: "window" | "24h";
}) {
  // In 24h mode, restrict the series to the trailing 24 hours so the
  // chart shows fine-grained detail next to the longer window view.
  const cutoffMs = mode === "24h" ? Date.now() - 24 * 60 * 60 * 1000 : -Infinity;

  // Collect every (t, v) across all pools to find the global x-range.
  const allTimes: number[] = [];
  for (const pool of pools) {
    for (const [t] of pctByPool.get(pool) ?? []) {
      const ms = Date.parse(t);
      if (Number.isFinite(ms) && ms >= cutoffMs) allTimes.push(ms);
    }
  }
  if (allTimes.length === 0) {
    return (
      <div style={{ marginTop: 12 }}>
        <div className="small muted" style={{ marginBottom: 4 }}>
          {mode === "24h"
            ? "DWU % over the last 24 hours"
            : "DWU % over time per pool (100 % = DWU limit)"}
        </div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No samples in this window.
        </div>
      </div>
    );
  }
  const tMin = mode === "24h" ? Math.max(Math.min(...allTimes), cutoffMs) : Math.min(...allTimes);
  const tMax = mode === "24h" ? Date.now() : Math.max(...allTimes);
  const tSpan = Math.max(1, tMax - tMin);

  // Y axis: clamp to >= 100 so the reference line is always visible, and
  // round up to nearest 25 so labels are tidy when DWU stays low.
  const observedMax = (() => {
    let m = 0;
    for (const pool of pools) {
      for (const [t, v] of pctByPool.get(pool) ?? []) {
        const ms = Date.parse(t);
        if (!Number.isFinite(ms) || ms < cutoffMs) continue;
        if (v != null && Number.isFinite(v) && v > m) m = v;
      }
    }
    return m;
  })();
  const yMax = Math.max(100, Math.ceil(observedMax / 25) * 25);

  // Layout — mirrors DailyRunsChart.
  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerW = width - padX * 2;
  const innerH = height - padTop - padBottom;

  const xOf = (ms: number) => padX + ((ms - tMin) / tSpan) * innerW;
  const yOf = (v: number) => padTop + innerH - (Math.max(0, Math.min(yMax, v)) / yMax) * innerH;

  // 5 colours cycled — uses CSS variables so dark/light themes work.
  const palette = [
    "var(--accent)",
    "var(--ok)",
    "var(--warn)",
    "var(--err)",
    "var(--muted)",
  ];

  // Y ticks at 0, 25, 50, 75, 100 (and yMax if > 100).
  const yTicks: number[] = [0, 25, 50, 75, 100];
  if (yMax > 100) yTicks.push(yMax);

  // X labels — show ~6 evenly spaced ticks. Format as HH:mm in 24h mode,
  // M/D in window mode.
  const labelCount = mode === "24h" ? 5 : Math.min(6, Math.max(2, windowDays));
  const xLabels: Array<{ x: number; label: string }> = [];
  for (let i = 0; i < labelCount; i++) {
    const ms = tMin + ((tMax - tMin) * i) / (labelCount - 1);
    const d = new Date(ms);
    xLabels.push({
      x: xOf(ms),
      label:
        mode === "24h"
          ? `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
          : `${d.getUTCMonth() + 1}/${d.getUTCDate()}`,
    });
  }

  const axisColor = "var(--border)";
  const textColor = "var(--muted)";

  return (
    <div style={{ marginTop: 12 }}>
      <div className="small muted" style={{ marginBottom: 4 }}>
        {mode === "24h"
          ? "DWU % over the last 24 hours (100 % = DWU limit)"
          : `DWU % over the last ${windowDays} day${windowDays === 1 ? "" : "s"} per pool (100 % = DWU limit)`}
      </div>
      <div style={{ overflowX: "auto" }}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          style={{ maxWidth: width, display: "block" }}
          role="img"
          aria-label={
            mode === "24h"
              ? "Line chart of DWU utilization percent per pool over the last 24 hours"
              : "Line chart of DWU utilization percent per pool over time"
          }
        >
          {/* Y grid + tick labels */}
          {yTicks.map((t, i) => {
            const y = yOf(t);
            const is100 = t === 100;
            return (
              <g key={i}>
                <line
                  x1={padX}
                  x2={width - padX}
                  y1={y}
                  y2={y}
                  stroke={is100 ? "var(--err)" : axisColor}
                  strokeDasharray={t === 0 ? undefined : is100 ? "4,3" : "2,3"}
                  opacity={is100 ? 0.7 : 1}
                />
                <text x={padX - 6} y={y + 3} textAnchor="end" fontSize={10} fill={textColor}>
                  {t}%
                </text>
              </g>
            );
          })}

          {/* X tick labels */}
          {xLabels.map((l, i) => (
            <text
              key={i}
              x={l.x}
              y={height - padBottom + 14}
              textAnchor="middle"
              fontSize={10}
              fill={textColor}
            >
              {l.label}
            </text>
          ))}

          {/* One polyline per pool */}
          {pools.map((pool, idx) => {
            const color = palette[idx % palette.length];
            const pts = pctByPool.get(pool) ?? [];
            // Build path, breaking on null values so gaps render as gaps.
            const segments: string[] = [];
            let inSeg = false;
            for (const [t, v] of pts) {
              const ms = Date.parse(t);
              if (!Number.isFinite(ms) || ms < cutoffMs || v == null || !Number.isFinite(v)) {
                inSeg = false;
                continue;
              }
              const x = xOf(ms).toFixed(2);
              const y = yOf(v).toFixed(2);
              segments.push(`${inSeg ? "L" : "M"}${x},${y}`);
              inSeg = true;
            }
            if (segments.length === 0) return null;
            return (
              <path
                key={pool}
                d={segments.join(" ")}
                fill="none"
                stroke={color}
                strokeWidth={1.5}
                strokeLinejoin="round"
                strokeLinecap="round"
              >
                <title>{pool}</title>
              </path>
            );
          })}

          {/* Average reference line in 24h mode — mean across every
           * non-null sample of every visible pool. */}
          {mode === "24h" && (() => {
            let sum = 0;
            let n = 0;
            for (const pool of pools) {
              for (const [t, v] of pctByPool.get(pool) ?? []) {
                const ms = Date.parse(t);
                if (!Number.isFinite(ms) || ms < cutoffMs) continue;
                if (v == null || !Number.isFinite(v)) continue;
                sum += v;
                n += 1;
              }
            }
            if (n === 0) return null;
            const avg = sum / n;
            const y = yOf(avg);
            return (
              <g>
                <line
                  x1={padX}
                  x2={width - padX}
                  y1={y}
                  y2={y}
                  stroke="var(--accent)"
                  strokeDasharray="5,4"
                  strokeWidth={1.5}
                  opacity={0.85}
                >
                  <title>{`Average DWU: ${avg.toFixed(1)}%`}</title>
                </line>
                <text
                  x={width - padX - 4}
                  y={y - 4}
                  textAnchor="end"
                  fontSize={10}
                  fill="var(--accent)"
                >
                  avg {avg.toFixed(1)}%
                </text>
              </g>
            );
          })()}
        </svg>
      </div>
      {pools.length > 1 && (
        <div className="small muted" style={{ display: "flex", flexWrap: "wrap", gap: 14, marginTop: 4 }}>
          {pools.map((pool, idx) => (
            <span key={pool}>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  background: palette[idx % palette.length],
                  borderRadius: 2,
                  marginRight: 4,
                  verticalAlign: "middle",
                }}
              />
              <code>{pool}</code>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Storage statistics
// ---------------------------------------------------------------------------

function StorageSection({ storage, runMeta }: { storage: import("../types").StorageReport | null; runMeta?: import("../api/loader").RunMeta | null }) {
  if (!storage) return null;
  const pools = storage.dedicated_pool_storage ?? [];
  const accounts = storage.accounts ?? [];
  const capacities = storage.capacities ?? [];
  if (pools.length === 0 && accounts.length === 0 && capacities.length === 0) return null;

  const poolReservedGb = pools.reduce((s, p) => s + (p.reserved_space_gb ?? 0), 0);
  const poolDataGb = pools.reduce((s, p) => s + (p.data_space_gb ?? 0), 0);
  const poolIndexGb = pools.reduce((s, p) => s + (p.index_space_gb ?? 0), 0);
  const totalRows = pools.reduce((s, p) => s + (p.row_count ?? 0), 0);
  const totalTables = pools.reduce((s, p) => s + (p.table_count ?? 0), 0);
  const adlsUsedGb = capacities.reduce((s, c) => s + (c.used_capacity_gb ?? 0), 0);
  const adlsBlobs = capacities.reduce((s, c) => s + (c.blob_count ?? 0), 0);

  return (
    <section className="section">
      <h2>Storage<ProvenanceBadge meta={runMeta ?? null} module="storage" /></h2>
      <div className="grid cols-4">
        <StatCard
          label="Dedicated pool data"
          value={fmtBytesGb(poolDataGb)}
          sub={`${fmtNum(totalTables)} tables · ${fmtNum(totalRows)} rows`}
        />
        <StatCard
          label="Dedicated pool indexes"
          value={fmtBytesGb(poolIndexGb)}
          sub={`reserved ${fmtBytesGb(poolReservedGb)}`}
        />
        <StatCard
          label="ADLS used"
          value={accounts.length ? fmtBytesGb(adlsUsedGb) : "—"}
          sub={
            accounts.length
              ? `${accounts.length} account${accounts.length === 1 ? "" : "s"} · ${fmtNum(adlsBlobs)} blobs`
              : "no storage accounts discovered"
          }
        />
        <StatCard
          label="Storage accounts"
          value={accounts.length}
          sub={accounts.find((a) => a.is_workspace_default)?.name ?? "no workspace default"}
        />
      </div>

      {pools.length > 0 && (
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Pool</th>
              <th className="num">Tables</th>
              <th className="num">Rows</th>
              <th className="num">Data</th>
              <th className="num">Indexes</th>
              <th className="num">Reserved</th>
              <th className="num">% of max</th>
            </tr>
          </thead>
          <tbody>
            {pools.map((p) => (
              <tr key={p.pool_name}>
                <td><code>{p.pool_name}</code></td>
                <td className="num">{fmtNum(p.table_count)}</td>
                <td className="num">{fmtNum(p.row_count)}</td>
                <td className="num">{fmtBytesGb(p.data_space_gb)}</td>
                <td className="num">{fmtBytesGb(p.index_space_gb)}</td>
                <td className="num">{fmtBytesGb(p.reserved_space_gb)}</td>
                <td className="num">{p.used_pct_of_max != null ? `${p.used_pct_of_max.toFixed(2)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Pipeline activity (daily run rate, data movement)
// ---------------------------------------------------------------------------

function PipelinesSection({ pipelines, runMeta, scope }: { pipelines: import("../types").PipelinesReport | null; runMeta?: import("../api/loader").RunMeta | null; scope?: import("../api/loader").RunScope | null }) {
  if (!pipelines) return null;
  const history = pipelines.run_history;
  if (!history || history.by_pipeline.length === 0) return null;

  // Prefer the 7-day window for "daily" stats; fall back to the shortest window.
  const stats = history.by_pipeline.map((p) => {
    const w = p.windows.find((w) => w.window_days === 7) ?? p.windows[0];
    return { pipeline: p.pipeline, has_data_movement: p.has_data_movement, last: p.last_run_at, lastStatus: p.last_run_status, w };
  }).filter((r) => r.w != null);

  const window = stats[0]?.w?.window_days ?? 7;
  const totalRuns = stats.reduce((s, r) => s + (r.w?.run_count ?? 0), 0);
  const totalSucceeded = stats.reduce((s, r) => s + (r.w?.succeeded ?? 0), 0);
  const totalFailed = stats.reduce((s, r) => s + (r.w?.failed ?? 0), 0);
  const totalDataMb = stats.reduce((s, r) => s + (r.w?.total_data_moved_mb ?? 0), 0);
  const totalDiuHours = stats.reduce((s, r) => s + (r.w?.total_diu_hours ?? 0), 0);
  const totalCuHoursDm = stats.reduce((s, r) => s + (r.w?.est_cu_hours_from_diu ?? 0), 0);
  const totalVcoreHours = stats.reduce((s, r) => s + (r.w?.total_vcore_hours ?? 0), 0);
  const totalCuHoursDf = stats.reduce((s, r) => s + (r.w?.est_cu_hours_from_vcore ?? 0), 0);
  const totalCuHoursOrch = stats.reduce((s, r) => s + (r.w?.est_cu_hours_from_orchestration ?? 0), 0);
  const totalNonCopyRuns = stats.reduce((s, r) => s + (r.w?.est_non_copy_activity_runs ?? 0), 0);
  const totalCuHours = totalCuHoursDm + totalCuHoursDf + totalCuHoursOrch;
  const dataMovingPipelines = stats.filter((r) => r.has_data_movement && (r.w?.total_data_moved_mb ?? 0) > 0).length;
  const dataFlowPipelines = stats.filter((r) => (r.w?.total_vcore_hours ?? 0) > 0).length;
  const dailyRuns = totalRuns / window;
  const dailyDataMb = totalDataMb / window;
  // Convert avg daily CU-hours into a steady-state CU equivalent
  // (1 CU sustained = 24 CU-hr / day, so daily_CU = daily_CU-hr / 24).
  const dailyCuHours = window > 0 ? totalCuHours / window : 0;
  const dailyCuEquivalent = dailyCuHours / 24;
  const successRate = totalSucceeded + totalFailed > 0
    ? (totalSucceeded / (totalSucceeded + totalFailed)) * 100
    : null;

  // Top pipelines by daily activity.
  const top = [...stats]
    .sort((a, b) => (b.w!.run_count - a.w!.run_count))
    .slice(0, 10);

  return (
    <section className="section">
      <h2>
        Pipeline activity (last {window} days)
        {scope && (
          <span className="pill muted" style={{ marginLeft: 8 }} title={scope.dir}>
            {sourceLabel(scope.source_type)} · {scope.slug}
          </span>
        )}
        <ProvenanceBadge meta={runMeta ?? null} module="pipelines" />
        <ReanalyzeButton modules={["pipelines"]} days={window} title={`Re-run the pipelines analyzer for this ${sourceNoun(scope?.source_type ?? null)}`} />
      </h2>
      <div className="grid cols-5">
        <StatCard
          label="Daily pipeline runs"
          value={dailyRuns.toFixed(1)}
          sub={`${fmtNum(totalRuns)} total · ${stats.length} pipelines`}
        />
        <StatCard
          label="Success rate"
          value={<PctPill pct={successRate != null ? Math.round(successRate * 10) / 10 : null} />}
          sub={`${fmtNum(totalSucceeded)} ok · ${fmtNum(totalFailed)} failed`}
        />
        <StatCard
          label={`Data moved (last ${window} days)`}
          value={fmtMb(totalDataMb)}
          sub={dataMovingPipelines > 0
            ? `~ ${fmtMb(dailyDataMb)} / day avg · ${dataMovingPipelines} pipeline${dataMovingPipelines === 1 ? "" : "s"} with data activity`
            : "no data-movement activity observed"}
        />
        <StatCard
          label={`Integration capacity (last ${window} days)`}
          value={totalCuHours > 0 ? `${totalCuHours.toFixed(2)} CU-hr` : "—"}
          sub={totalCuHours > 0
            ? `${totalCuHoursDm.toFixed(2)} from data movement (${totalDiuHours.toFixed(2)} DIU-hr) · ${totalCuHoursDf.toFixed(2)} from data flows (${totalVcoreHours.toFixed(2)} vCore-hr${dataFlowPipelines > 0 ? `, ${dataFlowPipelines} DF pipeline${dataFlowPipelines === 1 ? "" : "s"}` : ""}) · ${totalCuHoursOrch.toFixed(2)} from orchestration (${fmtNum(totalNonCopyRuns)} non-copy runs) · ≈ ${dailyCuEquivalent.toFixed(2)} CU sustained`
            : "no integration activity observed"}
        />
        <StatCard
          label="Window"
          value={`${new Date(history.window_start).toLocaleDateString()} → ${new Date(history.window_end).toLocaleDateString()}`}
          sub={`${fmtNum(history.fetched_run_count)} runs · ${fmtNum(history.fetched_activity_run_count)} activity runs${history.truncated ? " (truncated)" : ""}`}
        />
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          marginTop: 12,
        }}
      >
        <div style={{ flex: "1 1 360px", minWidth: 0 }}>
          <DailyRunsChart history={history} />
        </div>
        <div style={{ flex: "1 1 360px", minWidth: 0 }}>
          <HourlyRunsChart history={history} />
        </div>
      </div>

      <table style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Pipeline</th>
            <th className="num">Runs / day</th>
            <th className="num">Success</th>
            <th className="num">Failed</th>
            <th className="num">Data moved / run</th>
            <th className="num">Total moved</th>
            <th className="num">DIU-hr</th>
            <th className="num">CU-hr (DM)</th>
            <th className="num">vCore-hr</th>
            <th className="num">CU-hr (DF)</th>
            <th className="num">CU-hr (Orch)</th>
            <th>Last run</th>
          </tr>
        </thead>
        <tbody>
          {top.map((r) => (
            <tr key={r.pipeline}>
              <td><code>{r.pipeline}</code></td>
              <td className="num">{(r.w!.run_count / window).toFixed(2)}</td>
              <td className="num">{fmtNum(r.w!.succeeded)}</td>
              <td className="num">{fmtNum(r.w!.failed)}</td>
              <td className="num">{r.has_data_movement ? fmtMb(r.w!.avg_data_moved_mb_per_run) : "—"}</td>
              <td className="num">{r.has_data_movement ? fmtMb(r.w!.total_data_moved_mb) : "—"}</td>
              <td className="num">{r.w?.total_diu_hours != null ? r.w.total_diu_hours.toFixed(2) : "—"}</td>
              <td className="num">{r.w?.est_cu_hours_from_diu != null ? r.w.est_cu_hours_from_diu.toFixed(2) : "—"}</td>
              <td className="num">{r.w?.total_vcore_hours != null ? r.w.total_vcore_hours.toFixed(2) : "—"}</td>
              <td className="num">{r.w?.est_cu_hours_from_vcore != null ? r.w.est_cu_hours_from_vcore.toFixed(2) : "—"}</td>
              <td className="num">{(r.w?.est_cu_hours_from_orchestration ?? 0).toFixed(3)}</td>
              <td className="small muted">
                {r.last ? new Date(r.last).toLocaleString() : "—"}
                {r.lastStatus && <> · <SeverityPill severity={r.lastStatus.toLowerCase() === "succeeded" ? "info" : r.lastStatus.toLowerCase() === "failed" ? "blocker" : "warning"} /></>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {stats.length > top.length && (
        <div className="small muted" style={{ marginTop: 6 }}>
          Showing top {top.length} of {stats.length} pipelines by run count.
        </div>
      )}
    </section>
  );
}

/**
 * Stacked bar chart of daily pipeline run outcomes (succeeded / failed /
 * other) across the fetched run-history window. Renders inline SVG so it
 * works in print / static SPA mode without any chart library.
 */
function DailyRunsChart({ history }: { history: import("../types").PipelineRunHistory }) {
  const daily = history.daily_status;
  if (!daily) return null;

  // Fixed 28-day window ending at today (UTC inclusive) — mirrors the
  // Spark daily bars chart so the two visuals line up. The pipeline
  // analyzer's run-history window may be wider (90d max) but capping the
  // chart at 28 days keeps it consistent and avoids huge empty gutters
  // when only the trailing slice has data.
  const windowDays = 28;
  const todayUTC = new Date();
  todayUTC.setUTCHours(0, 0, 0, 0);
  const startDayUTC = new Date(
    todayUTC.getTime() - (windowDays - 1) * 24 * 60 * 60 * 1000,
  );

  const days: Array<{ date: string; succeeded: number; failed: number; other: number; total: number }> = [];
  for (let i = 0; i < windowDays; i++) {
    const d = new Date(startDayUTC.getTime() + i * 24 * 60 * 60 * 1000);
    const iso = d.toISOString().slice(0, 10);
    const v = daily[iso] ?? { succeeded: 0, failed: 0, other: 0 };
    days.push({
      date: iso,
      succeeded: v.succeeded ?? 0,
      failed: v.failed ?? 0,
      other: v.other ?? 0,
      total: (v.succeeded ?? 0) + (v.failed ?? 0) + (v.other ?? 0),
    });
  }
  // Don't render an empty chart when nothing was observed.
  const grandTotal = days.reduce((s, d) => s + d.total, 0);
  if (grandTotal === 0) return null;

  const maxTotal = Math.max(...days.map((d) => d.total), 1);
  // Y-axis tick (nearest "nice" value above maxTotal).
  const niceMax = niceCeil(maxTotal);

  // Layout — mirrors SparkDailyBars (width 720, height 240, padX 56).
  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / days.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));

  const colorOk = "var(--ok)";
  const colorErr = "var(--err)";
  const colorOther = "var(--muted)";
  const axisColor = "var(--border)";
  const textColor = "var(--muted)";

  // Y ticks: 0, niceMax/2, niceMax
  const yTicks = [0, niceMax / 2, niceMax];

  // X tick labels — pick ~6 evenly spaced days.
  const labelEvery = Math.max(1, Math.ceil(days.length / 6));

  return (
    <div style={{ marginTop: 12 }}>
      <div className="small muted" style={{ marginBottom: 4 }}>
        Daily pipeline run outcomes (last {windowDays} days)
      </div>
      <div style={{ overflowX: "auto" }}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          style={{ maxWidth: width, display: "block" }}
          role="img"
          aria-label="Stacked bar chart of daily pipeline run outcomes"
        >
          {/* Y-axis grid + labels */}
          {yTicks.map((t, i) => {
            const y = padTop + innerH - (t / niceMax) * innerH;
            return (
              <g key={i}>
                <line
                  x1={padX}
                  x2={width - padX}
                  y1={y}
                  y2={y}
                  stroke={axisColor}
                  strokeDasharray={i === 0 ? undefined : "2,3"}
                />
                <text x={padX - 6} y={y + 3} textAnchor="end" fontSize={10} fill={textColor}>
                  {Math.round(t)}
                </text>
              </g>
            );
          })}

          {/* Bars */}
          {days.map((d, i) => {
            const x = padX + i * groupW + (groupW - barW) / 2;
            const okH = (d.succeeded / niceMax) * innerH;
            const errH = (d.failed / niceMax) * innerH;
            const otherH = (d.other / niceMax) * innerH;
            const baseY = padTop + innerH;
            const okY = baseY - okH;
            const errY = okY - errH;
            const otherY = errY - otherH;
            const showLabel = i === 0 || i === days.length - 1 || i % labelEvery === 0;
            const title = `${d.date} — ${d.succeeded} ok · ${d.failed} failed${d.other ? ` · ${d.other} other` : ""}`;
            return (
              <g key={d.date}>
                <title>{title}</title>
                {d.succeeded > 0 && (
                  <rect x={x} y={okY} width={barW} height={okH} fill={colorOk} />
                )}
                {d.failed > 0 && (
                  <rect x={x} y={errY} width={barW} height={errH} fill={colorErr} />
                )}
                {d.other > 0 && (
                  <rect x={x} y={otherY} width={barW} height={otherH} fill={colorOther} opacity={0.6} />
                )}
                {showLabel && (
                  <text
                    x={x + barW / 2}
                    y={height - padBottom + 14}
                    textAnchor="middle"
                    fontSize={10}
                    fill={textColor}
                  >
                    {d.date.slice(5)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="small muted" style={{ display: "flex", gap: 14, marginTop: 4 }}>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: colorOk, borderRadius: 2, marginRight: 4, verticalAlign: "middle" }} />Succeeded</span>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: colorErr, borderRadius: 2, marginRight: 4, verticalAlign: "middle" }} />Failed</span>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: colorOther, borderRadius: 2, marginRight: 4, verticalAlign: "middle", opacity: 0.6 }} />Other (in-progress, cancelled, queued)</span>
      </div>
    </div>
  );
}

function niceCeil(n: number): number {
  if (n <= 1) return 1;
  const exp = Math.floor(Math.log10(n));
  const base = Math.pow(10, exp);
  const r = n / base;
  let nice: number;
  if (r <= 1) nice = 1;
  else if (r <= 2) nice = 2;
  else if (r <= 5) nice = 5;
  else nice = 10;
  return nice * base;
}

/**
 * 24h companion to ``DailyRunsChart``. Same stacked-bar conventions but
 * x-axis is per-UTC-hour over the trailing 24 hours, sourced from the
 * backend-precomputed ``hourly_status`` map (pre-seeded with empty bins
 * so the axis is always continuous). Renders an empty-state when older
 * runs are loaded that predate the field.
 */
function HourlyRunsChart({ history }: { history: import("../types").PipelineRunHistory }) {
  const hourly = history.hourly_status;
  if (!hourly) {
    return (
      <div style={{ marginTop: 12 }}>
        <div className="small muted" style={{ marginBottom: 4 }}>
          Hourly pipeline run outcomes (last 24h)
        </div>
        <div className="empty small muted" style={{ padding: 12 }}>
          Hourly buckets not present on this run — re-analyze pipelines to populate.
        </div>
      </div>
    );
  }

  // Sort keys chronologically (ISO-8601 hour strings sort lexically).
  const keys = Object.keys(hourly).sort();
  if (keys.length === 0) return null;

  const hours: Array<{ key: string; label: string; succeeded: number; failed: number; other: number; total: number }> = keys.map((k) => {
    const v = hourly[k] ?? { succeeded: 0, failed: 0, other: 0 };
    const d = new Date(k);
    const label = Number.isNaN(d.getTime())
      ? k.slice(11, 13)
      : `${String(d.getHours()).padStart(2, "0")}:00`;
    return {
      key: k,
      label,
      succeeded: v.succeeded ?? 0,
      failed: v.failed ?? 0,
      other: v.other ?? 0,
      total: (v.succeeded ?? 0) + (v.failed ?? 0) + (v.other ?? 0),
    };
  });

  const grandTotal = hours.reduce((s, h) => s + h.total, 0);
  if (grandTotal === 0) {
    return (
      <div style={{ marginTop: 12 }}>
        <div className="small muted" style={{ marginBottom: 4 }}>
          Hourly pipeline run outcomes (last 24h)
        </div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No pipeline runs in the last 24 hours.
        </div>
      </div>
    );
  }

  const maxTotal = Math.max(...hours.map((h) => h.total), 1);
  const niceMax = niceCeil(maxTotal);

  // Layout — mirrors DailyRunsChart.
  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / hours.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));

  const colorOk = "var(--ok)";
  const colorErr = "var(--err)";
  const colorOther = "var(--muted)";
  const axisColor = "var(--border)";
  const textColor = "var(--muted)";

  const yTicks = [0, niceMax / 2, niceMax];
  const labelEvery = Math.max(1, Math.ceil(hours.length / 8));

  return (
    <div style={{ marginTop: 12 }}>
      <div className="small muted" style={{ marginBottom: 4 }}>
        Hourly pipeline run outcomes (last 24h)
      </div>
      <div style={{ overflowX: "auto" }}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          style={{ maxWidth: width, display: "block" }}
          role="img"
          aria-label="Stacked bar chart of hourly pipeline run outcomes for the last 24 hours"
        >
          {yTicks.map((t, i) => {
            const y = padTop + innerH - (t / niceMax) * innerH;
            return (
              <g key={i}>
                <line
                  x1={padX}
                  x2={width - padX}
                  y1={y}
                  y2={y}
                  stroke={axisColor}
                  strokeDasharray={i === 0 ? undefined : "2,3"}
                />
                <text x={padX - 6} y={y + 3} textAnchor="end" fontSize={10} fill={textColor}>
                  {Math.round(t)}
                </text>
              </g>
            );
          })}
          {hours.map((h, i) => {
            const x = padX + i * groupW + (groupW - barW) / 2;
            const okH = (h.succeeded / niceMax) * innerH;
            const errH = (h.failed / niceMax) * innerH;
            const otherH = (h.other / niceMax) * innerH;
            const baseY = padTop + innerH;
            const okY = baseY - okH;
            const errY = okY - errH;
            const otherY = errY - otherH;
            const showLabel = i === 0 || i === hours.length - 1 || i % labelEvery === 0;
            const title = `${h.label} — ${h.succeeded} ok · ${h.failed} failed${h.other ? ` · ${h.other} other` : ""}`;
            return (
              <g key={h.key}>
                <title>{title}</title>
                {h.succeeded > 0 && (
                  <rect x={x} y={okY} width={barW} height={okH} fill={colorOk} />
                )}
                {h.failed > 0 && (
                  <rect x={x} y={errY} width={barW} height={errH} fill={colorErr} />
                )}
                {h.other > 0 && (
                  <rect x={x} y={otherY} width={barW} height={otherH} fill={colorOther} opacity={0.6} />
                )}
                {showLabel && (
                  <text
                    x={x + barW / 2}
                    y={height - padBottom + 14}
                    textAnchor="middle"
                    fontSize={10}
                    fill={textColor}
                  >
                    {h.label}
                  </text>
                )}
              </g>
            );
          })}
          {/* Average runs-per-hour reference line. */}
          {(() => {
            const avg = grandTotal / hours.length;
            if (avg <= 0) return null;
            const y = padTop + innerH - (avg / niceMax) * innerH;
            return (
              <g>
                <line
                  x1={padX}
                  x2={width - padX}
                  y1={y}
                  y2={y}
                  stroke="var(--accent)"
                  strokeDasharray="5,4"
                  strokeWidth={1.5}
                  opacity={0.85}
                >
                  <title>{`Average: ${avg.toFixed(1)} runs/hour`}</title>
                </line>
                <text
                  x={width - padX - 4}
                  y={y - 4}
                  textAnchor="end"
                  fontSize={10}
                  fill="var(--accent)"
                >
                  avg {avg.toFixed(1)}
                </text>
              </g>
            );
          })()}
        </svg>
      </div>
      <div className="small muted" style={{ display: "flex", gap: 14, marginTop: 4 }}>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: colorOk, borderRadius: 2, marginRight: 4, verticalAlign: "middle" }} />Succeeded</span>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: colorErr, borderRadius: 2, marginRight: 4, verticalAlign: "middle" }} />Failed</span>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: colorOther, borderRadius: 2, marginRight: 4, verticalAlign: "middle", opacity: 0.6 }} />Other</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spark Livy job history (interactive sessions + scheduled batches)
// ---------------------------------------------------------------------------

function DatabricksDailyBars({
  runs,
  windowDays = 28,
}: {
  runs: import("../types").DatabricksWorkflowRun[];
  windowDays?: number;
}) {
  // Per-job stacked daily vCore-hours over the trailing ``windowDays``
  // bins ending today (UTC) inclusive — same bucketing strategy as
  // SparkDailyBars. We also draw a horizontal average line at the mean
  // daily vCore-hour usage so users can eyeball steady-state vs spikes.
  const todayUTC = new Date();
  todayUTC.setUTCHours(0, 0, 0, 0);
  const startDayUTC = new Date(
    todayUTC.getTime() - (windowDays - 1) * 24 * 60 * 60 * 1000,
  );
  const cutoff = startDayUTC;

  const vCoreOf = (r: import("../types").DatabricksWorkflowRun): number => {
    if (typeof r.vcore_hours === "number" && r.vcore_hours > 0) return r.vcore_hours;
    if (typeof r.est_cu_hours_fabric_spark === "number" && r.est_cu_hours_fabric_spark > 0) {
      return r.est_cu_hours_fabric_spark / 0.5;
    }
    return 0;
  };

  const labelOf = (r: import("../types").DatabricksWorkflowRun): string =>
    r.job_name || `job ${r.job_id}`;

  // Filter to the window once. If any run inside the window has resolved
  // vCore-hours we plot vCore-hr; otherwise we fall back to a "runs per
  // day" view so the chart is still useful when cluster shapes couldn't
  // be resolved (e.g. ephemeral job clusters with unknown node_type_id).
  const windowed = runs.filter((r) => {
    if (!r.start_time) return false;
    const t = new Date(r.start_time);
    return !Number.isNaN(t.getTime()) && t >= cutoff;
  });
  if (windowed.length === 0) return null;

  const sumVcore = windowed.reduce((s, r) => s + vCoreOf(r), 0);
  const mode: "vcore" | "runs" = sumVcore > 0 ? "vcore" : "runs";
  const metricOf = (r: import("../types").DatabricksWorkflowRun): number =>
    mode === "vcore" ? vCoreOf(r) : 1;
  const unitLabel = mode === "vcore" ? "vCore-hr" : "runs";
  const metricTitle = mode === "vcore" ? "Databricks vCore-hr per day" : "Databricks runs per day (vCore-hr unavailable — cluster shape not resolved)";

  const jobs = Array.from(new Set(windowed.map(labelOf))).sort();
  if (jobs.length === 0) return null;

  type DayBin = { day: string; perJob: Record<string, number>; total: number };
  const bins = new Map<string, DayBin>();
  for (let i = 0; i < windowDays; i++) {
    const d = new Date(startDayUTC.getTime() + i * 24 * 60 * 60 * 1000);
    const key = d.toISOString().slice(0, 10);
    const perJob: Record<string, number> = {};
    for (const j of jobs) perJob[j] = 0;
    bins.set(key, { day: key, perJob, total: 0 });
  }

  for (const r of windowed) {
    const t = new Date(r.start_time as string);
    const key = t.toISOString().slice(0, 10);
    const bin = bins.get(key);
    if (!bin) continue;
    const v = metricOf(r);
    const label = labelOf(r);
    bin.perJob[label] = (bin.perJob[label] ?? 0) + v;
    bin.total += v;
  }

  const days = Array.from(bins.values()).sort((a, b) => (a.day < b.day ? -1 : 1));
  const total = days.reduce((s, d) => s + d.total, 0);
  if (total <= 0) return null;
  const avg = total / days.length;
  // Y-axis scales to max(daily max, average) so the average line is
  // always visible even when one day dwarfs the rest.
  const maxV = Math.max(0.001, avg, ...days.map((d) => d.total));

  const PALETTE = ["#2f81f7", "#3fb950", "#d29922", "#a371f7", "#db61a2", "#e36b6b", "#1f9ea3", "#bf6a02"];
  const colorFor = (job: string) => PALETTE[jobs.indexOf(job) % PALETTE.length];

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / days.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));
  const avgY = height - padBottom - (avg / maxV) * innerH;
  const fmtMetric = (v: number) =>
    mode === "vcore" ? v.toFixed(2) : v.toFixed(0);

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        {jobs.map((j) => (
          <span key={j} style={{ marginRight: 12, display: "inline-block" }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: colorFor(j), marginRight: 4, verticalAlign: "middle" }} />
            <code>{j}</code>
          </span>
        ))}
        <span style={{ marginRight: 12, display: "inline-block" }}>
          <span style={{ display: "inline-block", width: 14, borderTop: "2px dashed #e36b6b", marginRight: 4, verticalAlign: "middle" }} />
          avg {fmtMetric(avg)} {unitLabel}/day
        </span>
        <span>{metricTitle}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label={`Databricks ${unitLabel} per day per job for the last ${windowDays} days`}
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {(maxV * t).toFixed(maxV >= 10 || mode === "runs" ? 0 : 1)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {days.map((d, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const baseY = height - padBottom;
          const labelEvery = Math.max(1, Math.ceil(days.length / 8));
          let acc = 0;
          return (
            <g key={d.day}>
              {jobs.map((j) => {
                const v = d.perJob[j] ?? 0;
                if (v <= 0) return null;
                const h = (v / maxV) * innerH;
                const y = baseY - acc - h;
                acc += h;
                return (
                  <rect
                    key={j}
                    x={cx - barW / 2}
                    y={y}
                    width={barW}
                    height={h}
                    fill={colorFor(j)}
                  >
                    <title>{`${d.day} — ${j}: ${fmtMetric(v)} ${unitLabel}`}</title>
                  </rect>
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                  {d.day.slice(5)}
                </text>
              )}
            </g>
          );
        })}
        <line
          x1={padX}
          x2={width - padX}
          y1={avgY}
          y2={avgY}
          stroke="#e36b6b"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        >
          <title>{`Average ${fmtMetric(avg)} ${unitLabel}/day`}</title>
        </line>
        <text
          x={width - padX - 4}
          y={avgY - 4}
          textAnchor="end"
          fill="#e36b6b"
          opacity={0.9}
        >
          avg {fmtMetric(avg)}
        </text>
      </svg>
      <div className="small muted" style={{ marginTop: 4 }}>
        Window: last {windowDays} days · {fmtMetric(total)} {unitLabel} total · avg {fmtMetric(avg)}/day ·{" "}
        {jobs
          .map((j) => {
            const s = days.reduce((acc, d) => acc + (d.perJob[j] ?? 0), 0);
            return `${j}: ${fmtMetric(s)}`;
          })
          .join(" · ")}
        {mode === "runs" && (
          <>
            <br />
            <em>vCore-hr unavailable: cluster shape (node_type_id / num_workers) could not be resolved for these runs. See <code>cluster_sizing_caveats</code> in <code>databricks_workflows.json</code>.</em>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Hourly companion to ``DatabricksDailyBars`` covering the trailing 24
 * hours. Per-job stacked bars of vCore-hours bucketed into 1-hour bins
 * ending at the current local hour (inclusive). Falls back to a "runs
 * per hour" view when no run in the window has resolved vCore-hours
 * (e.g. cluster shape not resolved). Shares palette, layout and average
 * line styling with the daily chart.
 */
function DatabricksHourlyBars({
  runs,
}: {
  runs: import("../types").DatabricksWorkflowRun[];
}) {
  const windowHours = 24;
  const now = new Date();
  const currentHourStart = new Date(now);
  currentHourStart.setMinutes(0, 0, 0);
  const firstBinStart = new Date(
    currentHourStart.getTime() - (windowHours - 1) * 60 * 60 * 1000,
  );
  const cutoff = firstBinStart;

  const vCoreOf = (r: import("../types").DatabricksWorkflowRun): number => {
    if (typeof r.vcore_hours === "number" && r.vcore_hours > 0) return r.vcore_hours;
    if (typeof r.est_cu_hours_fabric_spark === "number" && r.est_cu_hours_fabric_spark > 0) {
      return r.est_cu_hours_fabric_spark / 0.5;
    }
    return 0;
  };

  const labelOf = (r: import("../types").DatabricksWorkflowRun): string =>
    r.job_name || `job ${r.job_id}`;

  const windowed = runs.filter((r) => {
    if (!r.start_time) return false;
    const t = new Date(r.start_time);
    return !Number.isNaN(t.getTime()) && t >= cutoff;
  });

  const sumVcore = windowed.reduce((s, r) => s + vCoreOf(r), 0);
  const mode: "vcore" | "runs" = sumVcore > 0 ? "vcore" : "runs";
  const metricOf = (r: import("../types").DatabricksWorkflowRun): number =>
    mode === "vcore" ? vCoreOf(r) : 1;
  const unitLabel = mode === "vcore" ? "vCore-hr" : "runs";
  const headerTitle = mode === "vcore"
    ? "Databricks vCore-hr per hour (last 24h)"
    : "Databricks runs per hour (last 24h)";

  if (windowed.length === 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>{headerTitle}</div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No Databricks workflow runs in the last 24 hours.
        </div>
      </div>
    );
  }

  const jobs = Array.from(new Set(windowed.map(labelOf))).sort();

  type HourBin = { key: string; label: string; perJob: Record<string, number>; total: number };
  const bins: HourBin[] = [];
  for (let i = 0; i < windowHours; i++) {
    const start = new Date(firstBinStart.getTime() + i * 60 * 60 * 1000);
    const key = start.toISOString();
    const label = `${String(start.getHours()).padStart(2, "0")}:00`;
    const perJob: Record<string, number> = {};
    for (const j of jobs) perJob[j] = 0;
    bins.push({ key, label, perJob, total: 0 });
  }
  const indexFor = (ms: number) =>
    Math.floor((ms - firstBinStart.getTime()) / (60 * 60 * 1000));

  for (const r of windowed) {
    const t = new Date(r.start_time as string);
    const idx = indexFor(t.getTime());
    if (idx < 0 || idx >= bins.length) continue;
    const bin = bins[idx];
    const v = metricOf(r);
    const label = labelOf(r);
    bin.perJob[label] = (bin.perJob[label] ?? 0) + v;
    bin.total += v;
  }

  const total = bins.reduce((s, b) => s + b.total, 0);
  if (total <= 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>{headerTitle}</div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No Databricks activity observed in the last 24 hours.
        </div>
      </div>
    );
  }
  const avg = total / bins.length;
  const maxV = Math.max(0.001, avg, ...bins.map((b) => b.total));

  const PALETTE = ["#2f81f7", "#3fb950", "#d29922", "#a371f7", "#db61a2", "#e36b6b", "#1f9ea3", "#bf6a02"];
  const colorFor = (job: string) => PALETTE[jobs.indexOf(job) % PALETTE.length];
  const fmtMetric = (v: number) => (mode === "vcore" ? v.toFixed(2) : v.toFixed(0));

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / bins.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));
  const avgY = height - padBottom - (avg / maxV) * innerH;

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        {jobs.map((j) => (
          <span key={j} style={{ marginRight: 12, display: "inline-block" }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: colorFor(j), marginRight: 4, verticalAlign: "middle" }} />
            <code>{j}</code>
          </span>
        ))}
        <span style={{ marginRight: 12, display: "inline-block" }}>
          <span style={{ display: "inline-block", width: 14, borderTop: "2px dashed #e36b6b", marginRight: 4, verticalAlign: "middle" }} />
          avg {fmtMetric(avg)} {unitLabel}/h
        </span>
        <span>{headerTitle}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label={`Databricks ${unitLabel} per hour per job for the last 24 hours`}
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {(maxV * t).toFixed(maxV >= 10 || mode === "runs" ? 0 : 1)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {bins.map((b, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const baseY = height - padBottom;
          const labelEvery = Math.max(1, Math.ceil(bins.length / 8));
          let acc = 0;
          return (
            <g key={b.key}>
              {jobs.map((j) => {
                const v = b.perJob[j] ?? 0;
                if (v <= 0) return null;
                const h = (v / maxV) * innerH;
                const y = baseY - acc - h;
                acc += h;
                return (
                  <rect
                    key={j}
                    x={cx - barW / 2}
                    y={y}
                    width={barW}
                    height={h}
                    fill={colorFor(j)}
                  >
                    <title>{`${b.label} — ${j}: ${fmtMetric(v)} ${unitLabel}`}</title>
                  </rect>
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        <line
          x1={padX}
          x2={width - padX}
          y1={avgY}
          y2={avgY}
          stroke="#e36b6b"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        >
          <title>{`Average ${fmtMetric(avg)} ${unitLabel}/hour`}</title>
        </line>
        <text
          x={width - padX - 4}
          y={avgY - 4}
          textAnchor="end"
          fill="#e36b6b"
          opacity={0.9}
        >
          avg {fmtMetric(avg)}
        </text>
      </svg>
    </div>
  );
}

function DatabricksWorkflowsSection({
  databricks,
  runMeta,
}: {
  databricks: import("../types").DatabricksWorkflowsReport | null;
  runMeta?: import("../api/loader").RunMeta | null;
}) {
  if (!databricks) return null;
  const workflows = databricks.workflows ?? [];
  const stats = databricks.workflow_run_stats ?? [];
  const interactive = databricks.interactive_cluster_usage ?? [];
  const caveats = databricks.cluster_sizing_caveats ?? [];
  const errors = databricks.errors ?? [];
  const sqlWarehouses = databricks.sql_warehouses ?? [];
  const sqlStats = databricks.sql_warehouse_stats ?? [];
  const sqlDaily = databricks.sql_warehouse_daily_usage ?? [];
  const sqlMappings = databricks.sql_warehouse_fabric_mappings ?? [];

  if (
    workflows.length === 0 &&
    stats.length === 0 &&
    interactive.length === 0 &&
    sqlWarehouses.length === 0
  ) {
    return null;
  }

  // Pick the widest available window (90d preferred) as the headline.
  const pickWindow = (
    ws: import("../types").DatabricksWorkflowRunWindowStats[] | undefined,
    pref = 90,
  ) => {
    if (!ws || ws.length === 0) return null;
    return (
      ws.find((w) => w.window_days === pref) ??
      ws.slice().sort((a, b) => b.window_days - a.window_days)[0]
    );
  };

  type PerJob = {
    jobId: number;
    jobName: string;
    totalRuns: number;
    runs7: number;
    runs28: number;
    runs90: number;
    vcoreHours: number;
    cuHours: number;
    avgVcore: number | null;
    successRate: number | null;
    windowDays: number;
  };
  const perJob: PerJob[] = stats
    .map((s) => {
      const w = pickWindow(s.windows, 90);
      if (!w) return null;
      const w7 = s.windows.find((x) => x.window_days === 7);
      const w28 = s.windows.find((x) => x.window_days === 28);
      const w90 = s.windows.find((x) => x.window_days === 90);
      return {
        jobId: s.job_id,
        jobName: s.job_name || `job ${s.job_id}`,
        totalRuns: s.total_runs_observed,
        runs7: w7?.run_count ?? 0,
        runs28: w28?.run_count ?? 0,
        runs90: w90?.run_count ?? 0,
        vcoreHours: w.total_vcore_hours,
        cuHours: w.est_cu_hours_fabric_spark,
        avgVcore: w.avg_vcore_hours_per_run ?? null,
        successRate: w.success_rate ?? null,
        windowDays: w.window_days,
      } as PerJob;
    })
    .filter((x): x is PerJob => x !== null)
    .sort((a, b) => b.vcoreHours - a.vcoreHours);

  const headlineWindow = perJob[0]?.windowDays ?? 90;
  const totalRuns = perJob.reduce((s, r) => s + (r.runs90 || r.runs28 || r.runs7), 0);
  const jobVcoreHours = perJob.reduce((s, r) => s + r.vcoreHours, 0);
  const jobCuHours = perJob.reduce((s, r) => s + r.cuHours, 0);
  // Roll interactive (all-purpose) cluster usage into the headline tiles
  // — these clusters can dominate consumption even when no scheduled jobs
  // ran, so excluding them produces misleading "no capacity" headlines.
  const interactiveVcoreHours = interactive.reduce((acc, c) => {
    const w = pickWindow(c.windows, headlineWindow);
    return acc + (w?.total_vcore_hours ?? 0);
  }, 0);
  const interactiveCuHours = interactive.reduce((acc, c) => {
    const w = pickWindow(c.windows, headlineWindow);
    return acc + (w?.est_cu_hours_fabric_spark ?? 0);
  }, 0);
  const totalVcoreHours = jobVcoreHours + interactiveVcoreHours;
  const totalCuHours = jobCuHours + interactiveCuHours;
  const totalCompleted = stats.reduce((acc, s) => {
    const w = pickWindow(s.windows, 90);
    return acc + (w?.completed_count ?? 0);
  }, 0);
  const totalSucceeded = stats.reduce((acc, s) => {
    const w = pickWindow(s.windows, 90);
    return acc + (w?.succeeded_count ?? 0);
  }, 0);
  const successPct = totalCompleted > 0 ? (totalSucceeded / totalCompleted) * 100 : null;

  return (
    <section className="section">
      <h2>
        Databricks workflows (last {headlineWindow} days)
        <ProvenanceBadge meta={runMeta ?? null} module="databricks_workflows" />
        <ReanalyzeButton
          modules={["databricks_workflows"]}
          title="Re-run the Databricks workflows analyzer"
        />
      </h2>
      <div className="grid cols-3">
        <StatCard
          label="Workflows discovered"
          value={fmtNum(workflows.length)}
          sub={`${databricks.task_count ?? 0} task${(databricks.task_count ?? 0) === 1 ? "" : "s"}${
            interactive.length > 0
              ? ` · ${interactive.length} interactive cluster${interactive.length === 1 ? "" : "s"}`
              : ""
          }`}
        />
        <StatCard
          label="Workflow runs"
          value={totalRuns > 0 ? fmtNum(totalRuns) : "—"}
          sub={
            totalVcoreHours > 0
              ? `${totalVcoreHours.toFixed(2)} vCore-hr${
                  interactiveVcoreHours > 0
                    ? ` (jobs ${jobVcoreHours.toFixed(2)} · interactive ${interactiveVcoreHours.toFixed(2)})`
                    : ` · ${(headlineWindow > 0 ? totalRuns / headlineWindow : 0).toFixed(1)} runs/day`
                }`
              : "no run history collected"
          }
        />
        <StatCard
          label="Est. Fabric capacity"
          value={totalCuHours > 0 ? `${totalCuHours.toFixed(2)} CU-hr` : "—"}
          sub={
            totalCuHours > 0
              ? `≈ ${(totalCuHours / Math.max(headlineWindow, 1) / 24).toFixed(2)} CU sustained${
                  successPct != null ? ` · ${successPct.toFixed(1)}% success` : ""
                }`
              : "1 CU = 2 Spark vCores"
          }
        />
      </div>

      {perJob.length > 0 && (
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Job</th>
              <th className="num">Runs 7d</th>
              <th className="num">Runs 28d</th>
              <th className="num">Runs 90d</th>
              <th className="num">vCore-hr ({headlineWindow}d)</th>
              <th className="num">CU-hr (Spark)</th>
              <th className="num">Avg vCore-hr / run</th>
              <th className="num">Success %</th>
            </tr>
          </thead>
          <tbody>
            {perJob.map((r) => (
              <tr key={r.jobId}>
                <td><code title={`job_id ${r.jobId}`}>{r.jobName}</code></td>
                <td className="num">{fmtNum(r.runs7)}</td>
                <td className="num">{fmtNum(r.runs28)}</td>
                <td className="num">{fmtNum(r.runs90)}</td>
                <td className="num">{r.vcoreHours.toFixed(2)}</td>
                <td className="num">{r.cuHours.toFixed(2)}</td>
                <td className="num">{r.avgVcore != null ? r.avgVcore.toFixed(2) : "—"}</td>
                <td className="num">
                  {r.successRate != null ? `${(r.successRate * 100).toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {databricks.workflow_runs && databricks.workflow_runs.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            marginTop: 12,
          }}
        >
          <div style={{ flex: "1 1 360px", minWidth: 0 }}>
            <DatabricksDailyBars runs={databricks.workflow_runs} windowDays={28} />
          </div>
          <div style={{ flex: "1 1 360px", minWidth: 0 }}>
            <DatabricksHourlyBars runs={databricks.workflow_runs} />
          </div>
        </div>
      )}

      {interactive.length > 0 && (
        <>
          <h3 style={{ marginTop: 20 }}>Interactive (all-purpose) clusters</h3>
          <table>
            <thead>
              <tr>
                <th>Cluster</th>
                <th>Node type</th>
                <th className="num">Workers</th>
                <th className="num">Total vCores</th>
                <th className="num">vCore-hr 7d</th>
                <th className="num">vCore-hr 28d</th>
                <th className="num">vCore-hr 90d</th>
              </tr>
            </thead>
            <tbody>
              {interactive.map((c) => {
                const w7 = c.windows?.find((x) => x.window_days === 7);
                const w28 = c.windows?.find((x) => x.window_days === 28);
                const w90 = c.windows?.find((x) => x.window_days === 90);
                return (
                  <tr key={c.cluster_id}>
                    <td>
                      <code title={c.cluster_id}>{c.cluster_name ?? c.cluster_id}</code>
                    </td>
                    <td className="small">
                      {c.node_type_id ?? "—"}
                      {c.driver_node_type_id && c.driver_node_type_id !== c.node_type_id
                        ? ` (driver: ${c.driver_node_type_id})`
                        : ""}
                    </td>
                    <td className="num">
                      {c.num_workers != null ? fmtNum(c.num_workers) : "—"}
                      {c.worker_count_source && c.worker_count_source !== "static"
                        ? ` (${c.worker_count_source.replace("autoscale_", "as·")})`
                        : ""}
                    </td>
                    <td className="num">{c.total_vcores != null ? fmtNum(c.total_vcores) : "—"}</td>
                    <td className="num">{w7 ? w7.total_vcore_hours.toFixed(2) : "—"}</td>
                    <td className="num">{w28 ? w28.total_vcore_hours.toFixed(2) : "—"}</td>
                    <td className="num">{w90 ? w90.total_vcore_hours.toFixed(2) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      {sqlWarehouses.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h3 style={{ marginBottom: 6 }}>
            SQL warehouses ({sqlWarehouses.length})
          </h3>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Size</th>
                <th>State</th>
                <th>Photon</th>
                <th>Serverless</th>
                <th className="num">Clusters (min–max)</th>
              </tr>
            </thead>
            <tbody>
              {sqlWarehouses.map((w) => (
                <tr key={w.warehouse_id}>
                  <td>{w.name}</td>
                  <td>{w.warehouse_type ?? "—"}</td>
                  <td>{w.cluster_size ?? "—"}</td>
                  <td>{w.state ?? "—"}</td>
                  <td>{w.enable_photon ? "yes" : "no"}</td>
                  <td>{w.enable_serverless_compute ? "yes" : "no"}</td>
                  <td className="num">
                    {(w.min_num_clusters ?? "?")}–{(w.max_num_clusters ?? "?")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {sqlStats.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ marginBottom: 6 }}>
            SQL warehouse usage (last {sqlStats[0]?.lookback_days ?? 30} days)
          </h3>
          <table>
            <thead>
              <tr>
                <th>Warehouse</th>
                <th className="num">Queries</th>
                <th className="num">Succeeded</th>
                <th className="num">Failed</th>
                <th className="num">Avg dur (s)</th>
                <th className="num">p95 dur (s)</th>
                <th className="num">CPU sec (REST)</th>
                <th className="num">From jobs</th>
                <th className="num">Users</th>
              </tr>
            </thead>
            <tbody>
              {sqlStats.map((s) => (
                <tr key={s.warehouse_id}>
                  <td>{s.warehouse_name ?? s.warehouse_id}</td>
                  <td className="num">{fmtNum(s.query_count)}</td>
                  <td className="num">{fmtNum(s.succeeded_count ?? 0)}</td>
                  <td className="num">{fmtNum(s.failed_count ?? 0)}</td>
                  <td className="num">
                    {s.avg_duration_seconds != null
                      ? s.avg_duration_seconds.toFixed(2)
                      : "—"}
                  </td>
                  <td className="num">
                    {s.p95_duration_seconds != null
                      ? s.p95_duration_seconds.toFixed(2)
                      : "—"}
                  </td>
                  <td className="num">
                    {s.total_cpu_seconds != null
                      ? `${s.total_cpu_seconds.toFixed(1)} (${
                          s.queries_with_cpu_metric ?? 0
                        }/${s.query_count})`
                      : "—"}
                  </td>
                  <td className="num">{fmtNum(s.queries_from_jobs ?? 0)}</td>
                  <td className="num">{fmtNum(s.unique_users ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {sqlDaily.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ marginBottom: 6 }}>
            SQL warehouse daily usage (Unity Catalog system tables)
          </h3>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Warehouse</th>
                <th className="num">Queries</th>
                <th className="num">Task seconds</th>
                <th className="num">DBU-hours</th>
                <th>SKU</th>
              </tr>
            </thead>
            <tbody>
              {sqlDaily.slice(0, 60).map((d, i) => (
                <tr key={`${d.warehouse_id}-${d.usage_date}-${i}`}>
                  <td>{d.usage_date}</td>
                  <td>{d.warehouse_name ?? d.warehouse_id}</td>
                  <td className="num">{fmtNum(d.query_count ?? 0)}</td>
                  <td className="num">
                    {d.total_task_seconds != null
                      ? d.total_task_seconds.toFixed(1)
                      : "—"}
                  </td>
                  <td className="num">
                    {d.dbu_hours != null ? d.dbu_hours.toFixed(3) : "—"}
                  </td>
                  <td>{d.sku_name ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sqlDaily.length > 60 && (
            <div className="small muted" style={{ marginTop: 4 }}>
              showing first 60 of {sqlDaily.length} rows — see{" "}
              <code>databricks_sql_warehouse_usage.csv</code>.
            </div>
          )}
        </div>
      )}

      {sqlMappings.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ marginBottom: 6 }}>
            SQL warehouse → Fabric mapping
          </h3>
          {(() => {
            let cu = 0;
            for (const m of sqlMappings) {
              const avg = m.avg_concurrent_cus;
              if (avg == null || !Number.isFinite(avg)) continue;
              const h = m.peak_to_avg_headroom && m.peak_to_avg_headroom > 0
                ? m.peak_to_avg_headroom
                : 4;
              cu += avg * h;
            }
            const sku = pickFabricSku(cu);
            if (!sku) return null;
            return (
              <div
                className="small"
                style={{
                  marginBottom: 6,
                  padding: "6px 10px",
                  border: "1px solid rgba(0,120,212,.35)",
                  background: "rgba(0,120,212,.06)",
                  borderRadius: 6,
                }}
              >
                <strong>Rolled-up Fabric SKU: {sku}</strong> · {cu.toFixed(2)} CU
                across {sqlMappings.length} warehouse{sqlMappings.length === 1 ? "" : "s"}
                {" "}(sum of avg concurrent CUs × 4× headroom). This also feeds the
                Estate Overview and the dashboard&nbsp;
                <em>Recommended SKU</em> headline when no Synapse capacity
                projection is present.
              </div>
            );
          })()}
          <div className="small muted" style={{ marginBottom: 6 }}>
            Deterministic per-warehouse Fabric Warehouse + F-SKU recommendation.
            Sizing pulls <code>total_cpu_seconds</code> (REST{" "}
            <code>include_metrics</code>) when available, otherwise{" "}
            <code>total_task_seconds</code> from Unity Catalog{" "}
            <code>system.query.history</code> (Serverless path). Average
            concurrent CUs are multiplied by a 4× peak-to-average headroom
            before the smallest covering F-SKU is picked.
          </div>
          <table>
            <thead>
              <tr>
                <th>Warehouse</th>
                <th>Source type</th>
                <th>Fabric target</th>
                <th>F-SKU</th>
                <th>Support</th>
                <th>Confidence</th>
                <th>Evidence</th>
                <th className="num">CPU sec</th>
                <th className="num">Task sec</th>
                <th className="num">DBU-h</th>
                <th className="num">Avg CUs</th>
              </tr>
            </thead>
            <tbody>
              {sqlMappings.map((m) => (
                <tr key={m.warehouse_id}>
                  <td>{m.warehouse_name ?? m.warehouse_id}</td>
                  <td>{m.source_warehouse_type ?? "—"}</td>
                  <td>{m.target_fabric_artifact}</td>
                  <td>
                    <strong>{m.recommended_sku ?? "—"}</strong>
                  </td>
                  <td>{m.support}</td>
                  <td>{m.confidence}</td>
                  <td>{m.evidence_source}</td>
                  <td className="num">
                    {m.total_cpu_seconds != null
                      ? m.total_cpu_seconds.toFixed(1)
                      : "—"}
                  </td>
                  <td className="num">
                    {m.total_task_seconds != null
                      ? m.total_task_seconds.toFixed(1)
                      : "—"}
                  </td>
                  <td className="num">
                    {m.total_dbu_hours != null
                      ? m.total_dbu_hours.toFixed(3)
                      : "—"}
                  </td>
                  <td className="num">
                    {m.avg_concurrent_cus != null
                      ? m.avg_concurrent_cus.toFixed(4)
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sqlMappings.some((m) => (m.notes ?? []).length > 0) && (
            <ul
              className="small muted"
              style={{ marginTop: 6, paddingLeft: 18 }}
            >
              {sqlMappings.flatMap((m) =>
                (m.notes ?? []).map((n, i) => (
                  <li key={`${m.warehouse_id}-note-${i}`}>
                    <strong>{m.warehouse_name ?? m.warehouse_id}:</strong> {n}
                  </li>
                )),
              )}
            </ul>
          )}
        </div>
      )}

      {caveats.length > 0 && (
        <div
          className="small"
          style={{
            marginTop: 12,
            padding: "8px 10px",
            border: "1px solid rgba(210,153,34,.4)",
            background: "rgba(210,153,34,.08)",
            borderRadius: 6,
          }}
        >
          <strong>Cluster sizing caveats ({caveats.length}):</strong>
          <ul style={{ marginTop: 6, marginBottom: 0 }}>
            {caveats.slice(0, 6).map((c, i) => (
              <li key={i}>{c}</li>
            ))}
            {caveats.length > 6 && <li>… {caveats.length - 6} more — see <code>databricks_workflows.json</code>.</li>}
          </ul>
        </div>
      )}

      {errors.length > 0 && (
        <div className="small muted" style={{ marginTop: 6 }}>
          {errors.length} collection error(s) — see <code>databricks_workflows.json</code>.
        </div>
      )}
    </section>
  );
}

function SparkPoolsSection({ sparkPools, runMeta }: { sparkPools: import("../types").SparkPoolsReport | null; runMeta?: import("../api/loader").RunMeta | null }) {
  if (!sparkPools) return null;
  const stats = sparkPools.run_stats ?? [];
  const pools = sparkPools.pools ?? [];
  const errors = sparkPools.errors ?? [];
  const sparkHistoryErrors = errors.filter((e) => e.includes("spark_history"));

  // Render an explicit "no data" diagnostic panel when pools exist but the
  // Livy history collection produced no rows. This is much better UX than
  // silently dropping the section — it tells the user WHY there are no
  // Spark execution stats (typically a 403 on bigDataPools/useCompute/action,
  // or the SMA_SPARK_RUN_HISTORY env-var being disabled).
  if (stats.length === 0) {
    if (pools.length === 0) return null;
    return (
      <section className="section">
        <h2>Spark execution</h2>
        <div className="grid cols-2">
          <StatCard
            label="Spark pools discovered"
            value={fmtNum(pools.length)}
            sub={pools.slice(0, 3).map((p) => p.name).join(", ") + (pools.length > 3 ? `, …` : "")}
          />
          <StatCard
            label="Spark Livy history"
            value={sparkHistoryErrors.length > 0 ? "blocked" : "empty"}
            sub={sparkHistoryErrors.length > 0
              ? `${sparkHistoryErrors.length} Livy fetch error(s) — see below`
              : "no batch jobs or sessions found in the configured window"}
          />
        </div>
        {sparkHistoryErrors.length > 0 && (
          <div
            style={{
              marginTop: 12,
              padding: "10px 12px",
              border: "1px solid rgba(210,153,34,.4)",
              background: "rgba(210,153,34,.08)",
              borderRadius: 6,
              fontSize: 13,
            }}
          >
            <strong>Spark Livy collection failed.</strong> The analyzer could
            list pools but could not read job / session history. Most common
            cause: the service principal lacks{" "}
            <code>Microsoft.Synapse/workspaces/bigDataPools/useCompute/action</code>{" "}
            (grant <em>Synapse Compute Operator</em> or higher on the
            workspace). Run <code>sma doctor</code> to confirm.
            <details style={{ marginTop: 8 }}>
              <summary>Errors ({sparkHistoryErrors.length})</summary>
              <ul style={{ marginTop: 6 }}>
                {sparkHistoryErrors.map((e, i) => (
                  <li key={i}><code>{e}</code></li>
                ))}
              </ul>
            </details>
          </div>
        )}
        {sparkHistoryErrors.length === 0 && (
          <div className="small muted" style={{ marginTop: 8 }}>
            Spark history collection runs by default. To disable, set{" "}
            <code>SMA_SPARK_RUN_HISTORY=0</code>. To widen the window, set{" "}
            <code>SMA_SPARK_RUN_DAYS</code> (default 90).
          </div>
        )}
      </section>
    );
  }

  // Prefer the 7-day window; fall back to the shortest available.
  // Aggregate per-pool. The interactive-vs-scheduled trigger dimension
  // is not displayed: Synapse Studio's Livy telemetry doesn't expose a
  // 100%-reliable discriminator for all run types (the `spark.synapse
  // .context.*` conf keys cover pipeline/SJD-triggered notebook runs
  // but other scheduled paths can still slip through), so we'd rather
  // omit the split than show a number that doesn't match Studio.
  //
  // Success/failure counts are intentionally omitted from the Spark
  // panel: most Spark runs in Synapse are interactive notebook sessions
  // where "failed" includes user-cancelled cells / SIGTERM-on-idle, so
  // the rate is a noisy signal that doesn't help capacity planning.
  type PerPool = {
    pool: string;
    runs: number;
    durationHours: number;
    vcoreHours: number;
    cuHours: number;
    windowDays: number;
  };
  const perPool: PerPool[] = (() => {
    const byPool = new Map<string, PerPool>();
    for (const s of stats) {
      const w = s.windows.find((x) => x.window_days === 7) ?? s.windows[0];
      if (!w) continue;
      let row = byPool.get(s.pool);
      if (!row) {
        row = {
          pool: s.pool, runs: 0,
          durationHours: 0, vcoreHours: 0, cuHours: 0,
          windowDays: w.window_days,
        };
        byPool.set(s.pool, row);
      }
      row.runs += w.run_count;
      row.durationHours += w.total_duration_hours;
      row.vcoreHours += w.total_vcore_hours;
      row.cuHours += w.est_cu_hours_fabric_spark;
    }
    return Array.from(byPool.values());
  })();
  if (perPool.length === 0) return null;

  const window = perPool[0].windowDays;
  const totalRuns = perPool.reduce((s, r) => s + r.runs, 0);
  const totalDurationHours = perPool.reduce((s, r) => s + r.durationHours, 0);
  const totalVcoreHours = perPool.reduce((s, r) => s + r.vcoreHours, 0);
  const totalCuHours = perPool.reduce((s, r) => s + r.cuHours, 0);
  const dailyCuHours = window > 0 ? totalCuHours / window : 0;
  const dailyCuEquivalent = dailyCuHours / 24;

  // Sort by CU-hours desc (largest consumer first).
  const top = [...perPool].sort((a, b) => b.cuHours - a.cuHours);

  return (
    <section className="section">
      <h2>Spark execution (last {window} days)<ProvenanceBadge meta={runMeta ?? null} module="spark_pools" /><ReanalyzeButton modules={["spark_pools"]} days={window} title="Re-run the Spark analyzer for this workspace" /></h2>
      <div className="grid cols-3">
        <StatCard
          label="Spark runs"
          value={fmtNum(totalRuns)}
          sub={`${perPool.length} pool${perPool.length === 1 ? "" : "s"} · ${(window > 0 ? totalRuns / window : 0).toFixed(1)} runs/day`}
        />
        <StatCard
          label="Spark compute"
          value={`${totalVcoreHours.toFixed(2)} vCore-hr`}
          sub={`${totalDurationHours.toFixed(2)} wall-clock hr · ${(window > 0 ? totalVcoreHours / window : 0).toFixed(2)} vCore-hr/day`}
        />
        <StatCard
          label="Est. Fabric capacity"
          value={totalCuHours > 0 ? `${totalCuHours.toFixed(2)} CU-hr` : "—"}
          sub={totalCuHours > 0
            ? `≈ ${dailyCuEquivalent.toFixed(2)} CU sustained (1 CU = 2 Spark vCores)`
            : "no Spark Livy history collected"}
        />
      </div>

      <table style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Pool</th>
            <th className="num">Runs</th>
            <th className="num">Duration hr</th>
            <th className="num">vCore-hr</th>
            <th className="num">CU-hr (Spark)</th>
            <th className="num">Avg vCore-hr / run</th>
          </tr>
        </thead>
        <tbody>
          {top.map((r) => {
            const avgVcore = r.runs > 0 ? r.vcoreHours / r.runs : null;
            return (
              <tr key={r.pool}>
                <td><code>{r.pool}</code></td>
                <td className="num">{fmtNum(r.runs)}</td>
                <td className="num">{r.durationHours.toFixed(2)}</td>
                <td className="num">{r.vcoreHours.toFixed(2)}</td>
                <td className="num">{r.cuHours.toFixed(2)}</td>
                <td className="num">{avgVcore != null ? avgVcore.toFixed(2) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {sparkPools.spark_runs && sparkPools.spark_runs.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            marginTop: 12,
          }}
        >
          <div style={{ flex: "1 1 360px", minWidth: 0 }}>
            <SparkDailyBars runs={sparkPools.spark_runs} windowDays={28} />
          </div>
          <div style={{ flex: "1 1 360px", minWidth: 0 }}>
            <SparkHourlyBars runs={sparkPools.spark_runs} />
          </div>
        </div>
      )}
      {sparkPools.errors && sparkPools.errors.length > 0 && (
        <div className="small muted" style={{ marginTop: 6 }}>
          {sparkPools.errors.length} collection error(s) — see <code>spark_pools.json</code>.
        </div>
      )}
    </section>
  );
}

function SparkDailyBars({
  runs,
  windowDays = 28,
}: {
  runs: import("../types").SparkRunRecord[];
  windowDays?: number;
}) {
  // Per-pool stacked daily vCore-hours. Each day is a single stacked bar
  // with one segment per Spark pool — this is what users want to see for
  // capacity sizing (which pool is driving compute, on which day?).
  //
  // Day bucketing uses UTC-midnight boundaries so the window is exactly
  // ``windowDays`` bins ending **today (UTC) inclusive**. Previously the
  // loop produced bins ``[now-28d, now-1d]`` (28 entries starting at the
  // millisecond ``now - 28d``) and today's runs silently dropped because
  // the lookup key (UTC date of today) did not exist in the bin map.
  const todayUTC = new Date();
  todayUTC.setUTCHours(0, 0, 0, 0);
  const startDayUTC = new Date(
    todayUTC.getTime() - (windowDays - 1) * 24 * 60 * 60 * 1000,
  );
  // Runs are kept when their submitted_at falls on or after the start day
  // (midnight UTC); this avoids losing a partial first day vs. using
  // ``now - 28d`` which slides every second.
  const cutoff = startDayUTC;

  // Per-run CU contribution: prefer the directly measured vCore-hours,
  // fall back to ``est_cu_hours_fabric_spark / 0.5`` (=> vCore-hours
  // equivalent) for interactive sessions where Livy returns CU-hours
  // but no raw vCore-seconds.
  const vCoreOf = (r: import("../types").SparkRunRecord): number => {
    if (typeof r.vcore_hours === "number" && r.vcore_hours > 0) return r.vcore_hours;
    if (typeof r.est_cu_hours_fabric_spark === "number" && r.est_cu_hours_fabric_spark > 0) {
      return r.est_cu_hours_fabric_spark / 0.5;
    }
    return 0;
  };

  // Discover the set of pools that contributed any runs in the window.
  // Include pools even when their per-run vCore-hours are 0 so the
  // legend reflects all observed pools (a Spark pool with only
  // failed-immediately runs still belongs on the chart).
  const pools = (() => {
    const seen = new Set<string>();
    for (const r of runs) {
      if (!r.submitted_at) continue;
      const t = new Date(r.submitted_at);
      if (Number.isNaN(t.getTime()) || t < cutoff) continue;
      seen.add(r.pool);
    }
    return Array.from(seen).sort();
  })();
  if (pools.length === 0) return null;

  // Pre-seed every day so the x-axis is continuous, **including today**.
  type DayBin = { day: string; perPool: Record<string, number>; total: number };
  const bins = new Map<string, DayBin>();
  for (let i = 0; i < windowDays; i++) {
    const d = new Date(startDayUTC.getTime() + i * 24 * 60 * 60 * 1000);
    const key = d.toISOString().slice(0, 10);
    const perPool: Record<string, number> = {};
    for (const p of pools) perPool[p] = 0;
    bins.set(key, { day: key, perPool, total: 0 });
  }

  for (const r of runs) {
    if (!r.submitted_at) continue;
    const t = new Date(r.submitted_at);
    if (Number.isNaN(t.getTime()) || t < cutoff) continue;
    const key = t.toISOString().slice(0, 10);
    const bin = bins.get(key);
    if (!bin) continue;
    const v = vCoreOf(r);
    bin.perPool[r.pool] = (bin.perPool[r.pool] ?? 0) + v;
    bin.total += v;
  }

  const days = Array.from(bins.values()).sort((a, b) => (a.day < b.day ? -1 : 1));
  const maxV = Math.max(0.001, ...days.map((d) => d.total));
  const total = days.reduce((s, d) => s + d.total, 0);
  if (total <= 0) return null;

  // Stable per-pool color (deterministic by index so refreshes don't shuffle).
  const PALETTE = ["#2f81f7", "#3fb950", "#d29922", "#a371f7", "#db61a2", "#e36b6b", "#1f9ea3", "#bf6a02"];
  const colorFor = (pool: string) => PALETTE[pools.indexOf(pool) % PALETTE.length];

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / days.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        {pools.map((p) => (
          <span key={p} style={{ marginRight: 12, display: "inline-block" }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: colorFor(p), marginRight: 4, verticalAlign: "middle" }} />
            <code>{p}</code>
          </span>
        ))}
        <span>Spark vCore-hr per day</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label={`Spark vCore-hours per day per pool for the last ${windowDays} days`}
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {(maxV * t).toFixed(maxV >= 10 ? 0 : 1)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {days.map((d, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const baseY = height - padBottom;
          const labelEvery = Math.max(1, Math.ceil(days.length / 8));
          let acc = 0;
          return (
            <g key={d.day}>
              {pools.map((p) => {
                const v = d.perPool[p] ?? 0;
                if (v <= 0) return null;
                const h = (v / maxV) * innerH;
                const y = baseY - acc - h;
                acc += h;
                return (
                  <rect
                    key={p}
                    x={cx - barW / 2}
                    y={y}
                    width={barW}
                    height={h}
                    fill={colorFor(p)}
                  >
                    <title>{`${d.day} — ${p}: ${v.toFixed(2)} vCore-hr`}</title>
                  </rect>
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                  {d.day.slice(5)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="small muted" style={{ marginTop: 4 }}>
        Window: last {windowDays} days · {total.toFixed(2)} vCore-hr total ·{" "}
        {pools
          .map((p) => {
            const s = days.reduce((acc, d) => acc + (d.perPool[p] ?? 0), 0);
            return `${p}: ${s.toFixed(2)}`;
          })
          .join(" · ")}
      </div>
    </div>
  );
}

/**
 * Hourly companion to ``SparkDailyBars`` covering the trailing 24 hours.
 * Per-pool stacked bars of vCore-hours bucketed into 1-hour bins ending at
 * the current local hour (inclusive). Shares the palette, layout
 * conventions and tooltip style of the 28d chart so the two read as a
 * single split-view.
 */
function SparkHourlyBars({
  runs,
}: {
  runs: import("../types").SparkRunRecord[];
}) {
  const windowHours = 24;
  // Bucket boundary = top of the current local hour. We include the
  // current (in-progress) hour so the rightmost bin shows activity
  // happening "now". Bins are ``[hourStart, hourStart + 1h)``.
  const now = new Date();
  const currentHourStart = new Date(now);
  currentHourStart.setMinutes(0, 0, 0);
  const firstBinStart = new Date(
    currentHourStart.getTime() - (windowHours - 1) * 60 * 60 * 1000,
  );
  const cutoff = firstBinStart;

  const vCoreOf = (r: import("../types").SparkRunRecord): number => {
    if (typeof r.vcore_hours === "number" && r.vcore_hours > 0) return r.vcore_hours;
    if (typeof r.est_cu_hours_fabric_spark === "number" && r.est_cu_hours_fabric_spark > 0) {
      return r.est_cu_hours_fabric_spark / 0.5;
    }
    return 0;
  };

  const pools = (() => {
    const seen = new Set<string>();
    for (const r of runs) {
      if (!r.submitted_at) continue;
      const t = new Date(r.submitted_at);
      if (Number.isNaN(t.getTime()) || t < cutoff) continue;
      seen.add(r.pool);
    }
    return Array.from(seen).sort();
  })();
  if (pools.length === 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>
          Spark vCore-hr per hour (last 24h)
        </div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No Spark runs in the last 24 hours.
        </div>
      </div>
    );
  }

  type HourBin = { key: string; label: string; perPool: Record<string, number>; total: number };
  const bins: HourBin[] = [];
  for (let i = 0; i < windowHours; i++) {
    const start = new Date(firstBinStart.getTime() + i * 60 * 60 * 1000);
    const key = start.toISOString();
    const label = `${String(start.getHours()).padStart(2, "0")}:00`;
    const perPool: Record<string, number> = {};
    for (const p of pools) perPool[p] = 0;
    bins.push({ key, label, perPool, total: 0 });
  }
  const indexFor = (ms: number) =>
    Math.floor((ms - firstBinStart.getTime()) / (60 * 60 * 1000));

  for (const r of runs) {
    if (!r.submitted_at) continue;
    const t = new Date(r.submitted_at);
    if (Number.isNaN(t.getTime()) || t < cutoff) continue;
    const idx = indexFor(t.getTime());
    if (idx < 0 || idx >= bins.length) continue;
    const bin = bins[idx];
    const v = vCoreOf(r);
    bin.perPool[r.pool] = (bin.perPool[r.pool] ?? 0) + v;
    bin.total += v;
  }

  const total = bins.reduce((s, b) => s + b.total, 0);
  if (total <= 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>
          Spark vCore-hr per hour (last 24h)
        </div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No Spark vCore-hours observed in the last 24 hours.
        </div>
      </div>
    );
  }
  const maxV = Math.max(0.001, ...bins.map((b) => b.total));

  const PALETTE = ["#2f81f7", "#3fb950", "#d29922", "#a371f7", "#db61a2", "#e36b6b", "#1f9ea3", "#bf6a02"];
  const colorFor = (pool: string) => PALETTE[pools.indexOf(pool) % PALETTE.length];

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / bins.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        {pools.map((p) => (
          <span key={p} style={{ marginRight: 12, display: "inline-block" }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: colorFor(p), marginRight: 4, verticalAlign: "middle" }} />
            <code>{p}</code>
          </span>
        ))}
        <span>Spark vCore-hr per hour (last 24h)</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label="Spark vCore-hours per hour per pool for the last 24 hours"
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {(maxV * t).toFixed(maxV >= 10 ? 0 : 1)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {bins.map((b, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const baseY = height - padBottom;
          const labelEvery = Math.max(1, Math.ceil(bins.length / 8));
          let acc = 0;
          return (
            <g key={b.key}>
              {pools.map((p) => {
                const v = b.perPool[p] ?? 0;
                if (v <= 0) return null;
                const h = (v / maxV) * innerH;
                const y = baseY - acc - h;
                acc += h;
                return (
                  <rect
                    key={p}
                    x={cx - barW / 2}
                    y={y}
                    width={barW}
                    height={h}
                    fill={colorFor(p)}
                  >
                    <title>{`${b.label} — ${p}: ${v.toFixed(2)} vCore-hr`}</title>
                  </rect>
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        {/* Average vCore-hr per hour reference line. */}
        {(() => {
          const avg = total / bins.length;
          if (avg <= 0) return null;
          const y = height - padBottom - (avg / maxV) * innerH;
          return (
            <g>
              <line
                x1={padX}
                x2={width - padX}
                y1={y}
                y2={y}
                stroke="#c9d1d9"
                strokeDasharray="5,4"
                strokeWidth={1.5}
                opacity={0.85}
              >
                <title>{`Average: ${avg.toFixed(2)} vCore-hr/hour`}</title>
              </line>
              <text
                x={width - padX - 4}
                y={y - 4}
                textAnchor="end"
                fill="currentColor"
                opacity={0.85}
              >
                avg {avg.toFixed(maxV >= 10 ? 1 : 2)}
              </text>
            </g>
          );
        })()}
      </svg>
      <div className="small muted" style={{ marginTop: 4 }}>
        Window: last 24h · {total.toFixed(2)} vCore-hr total
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Serverless SQL query statistics
// ---------------------------------------------------------------------------

function ServerlessSection({ serverless, runMeta }: { serverless: import("../types").ServerlessReport | null; runMeta?: import("../api/loader").RunMeta | null }) {
  if (!serverless) return null;
  const daily = serverless.daily_usage ?? [];
  const dbCount = serverless.databases?.length ?? 0;
  const tableCount = serverless.external_tables?.length ?? 0;
  // If there's no serverless inventory at all, hide the section entirely.
  // The top-queries table now lives on the SQL Surface page; this section
  // only renders the high-level KPIs + daily trend chart.
  if (daily.length === 0 && dbCount === 0 && tableCount === 0) return null;

  // Sort ascending by day so charts read left-to-right.
  const sorted = [...daily].sort((a, b) => a.day.localeCompare(b.day));
  const last28 = sorted.slice(-28);
  const hourly = (serverless.hourly_usage ?? [])
    .slice()
    .sort((a, b) => a.hour.localeCompare(b.hour));

  const totalRequests = sorted.reduce((s, d) => s + (d.request_count ?? 0), 0);
  const totalDataMb = sorted.reduce((s, d) => s + (d.data_processed_mb ?? 0), 0);
  const totalDurationSeconds = sorted.reduce((s, d) => s + (d.duration_seconds ?? 0), 0);
  const windowDays = sorted.length;
  const avgDailyRequests = windowDays > 0 ? totalRequests / windowDays : 0;
  const avgQueryMb = totalRequests > 0 ? totalDataMb / totalRequests : 0;
  const avgQuerySeconds = totalRequests > 0 ? totalDurationSeconds / totalRequests : 0;
  const hasDuration = totalDurationSeconds > 0;

  const fmtDurationShort = (seconds: number): string => {
    if (!Number.isFinite(seconds) || seconds <= 0) return "0 s";
    if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
    if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} h`;
    return `${(seconds / 86400).toFixed(1)} d`;
  };

  if (daily.length === 0) {
    return (
      <section className="section">
        <h2>Serverless SQL</h2>
        <div className="grid cols-3">
          <StatCard label="Databases" value={fmtNum(dbCount)} />
          <StatCard label="External tables" value={fmtNum(tableCount)} />
          <StatCard
            label="Endpoint"
            value={serverless.endpoint_fqdn ? <code className="small">{serverless.endpoint_fqdn}</code> : "—"}
          />
        </div>
        <div className="small muted" style={{ marginTop: 12 }}>
          No daily query history available. <code>sys.dm_exec_requests_history</code> only
          returns queries submitted by the current login unless the principal has
          <code> VIEW SERVER STATE</code> or is a Synapse SQL admin. Grant that
          permission and re-run <code>sma analyze-serverless-pools</code> to populate
          this chart. Top queries (when available) are listed on the
          <strong> SQL Surface</strong> page.
        </div>
      </section>
    );
  }

  return (
    <section className="section">
      <h2>Serverless SQL ({windowDays} days observed)<ProvenanceBadge meta={runMeta ?? null} module="serverless_pools" /><ReanalyzeButton modules={["serverless_pools"]} days={windowDays} /></h2>
      <div className={hasDuration ? "grid cols-5" : "grid cols-4"}>
        <StatCard
          label="Avg daily queries"
          value={avgDailyRequests.toFixed(1)}
          sub={`${fmtNum(totalRequests)} succeeded queries`}
        />
        <StatCard
          label="Total data scanned"
          value={fmtMb(totalDataMb)}
          sub={`${fmtMb(totalDataMb / Math.max(1, windowDays))} / day avg`}
        />
        <StatCard
          label="Avg query data size"
          value={fmtMb(avgQueryMb)}
          sub="data scanned per query"
        />
        {hasDuration && (
          <StatCard
            label="Total execution time"
            value={fmtDurationShort(totalDurationSeconds)}
            sub={`${fmtDurationShort(totalDurationSeconds / Math.max(1, windowDays))} / day avg · ${fmtDurationShort(avgQuerySeconds)} / query avg`}
          />
        )}
        <StatCard
          label="Estimated cost"
          value={
            serverless.cost_estimate
              ? `$${serverless.cost_estimate.estimated_cost_usd.toFixed(2)}`
              : "—"
          }
          sub={
            serverless.cost_estimate
              ? `${serverless.cost_estimate.total_data_processed_tb.toFixed(3)} TB · $${serverless.cost_estimate.list_price_usd_per_tb}/TB list price`
              : "no cost estimate"
          }
        />
      </div>

      {last28.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginTop: 12 }}>
          <div style={{ flex: "1 1 360px", minWidth: 0 }}>
            <ServerlessClusteredBars days={last28} />
          </div>
          <div style={{ flex: "1 1 360px", minWidth: 0 }}>
            <ServerlessHourlyBars hours={hourly} />
          </div>
        </div>
      )}
    </section>
  );
}

function ServerlessHourlyBars({ hours }: { hours: import("../types").ServerlessHourlyUsage[] }) {
  // 24h companion to ServerlessClusteredBars. Pre-seed 24 hourly bins
  // anchored to the top of the current hour so the chart always shows a
  // full window even when the SQL query returned fewer rows.
  const width = 720;
  const height = 220;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;

  const now = new Date();
  const anchor = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), now.getUTCHours(),
  ));
  const bins: { iso: string; label: string; requests: number; mb: number }[] = [];
  for (let i = 23; i >= 0; i--) {
    const d = new Date(anchor.getTime() - i * 3600_000);
    const iso = d.toISOString().replace(/\.\d{3}Z$/, "Z");
    const hh = String(d.getHours()).padStart(2, "0");
    bins.push({ iso, label: `${hh}:00`, requests: 0, mb: 0 });
  }
  const idxByIso = new Map(bins.map((b, i) => [b.iso, i]));
  for (const h of hours) {
    // Normalise the incoming key to second precision to match our bin ISO.
    const normalised = h.hour.replace(/\.\d+Z$/, "Z");
    const i = idxByIso.get(normalised);
    if (i === undefined) continue;
    bins[i].requests += h.request_count ?? 0;
    bins[i].mb += h.data_processed_mb ?? 0;
  }

  const totalRequests = bins.reduce((s, b) => s + b.requests, 0);
  const totalMb = bins.reduce((s, b) => s + b.mb, 0);

  if (totalRequests === 0 && totalMb === 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>Last 24 hours (hourly)</div>
        <div className="small muted" style={{
          border: "1px dashed currentColor", borderRadius: 4, padding: 16,
          opacity: 0.6, textAlign: "center",
        }}>
          No serverless query activity in the last 24h, or this run pre-dates hourly
          bucketing &mdash; re-analyze serverless to populate.
        </div>
      </div>
    );
  }

  const maxRequests = Math.max(1, ...bins.map((b) => b.requests));
  const maxMb = Math.max(1, ...bins.map((b) => b.mb));
  const groupW = (width - padX * 2) / bins.length;
  const barW = Math.max(3, Math.min(14, (groupW - 4) / 2));

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        <span style={{ display: "inline-block", width: 10, height: 10, background: "#2f81f7", marginRight: 4, verticalAlign: "middle" }} />
        Queries (left axis)
        {"  "}
        <span style={{ display: "inline-block", width: 10, height: 10, background: "#f7942f", margin: "0 4px 0 12px", verticalAlign: "middle" }} />
        MB scanned (right axis)
        {"  \u00B7  "}Last 24h
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{ maxWidth: width, fontSize: 10 }} role="img" aria-label="Serverless SQL last 24 hours clustered bar chart">
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={width - padX} x2={width - padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {Math.round(maxRequests * t)}
            </text>
            <text x={width - padX + 6} y={height - padBottom - innerH * t + 3} textAnchor="start" fill="currentColor" opacity={0.7}>
              {fmtMb(maxMb * t)}
            </text>
          </g>
        ))}
        {bins.map((b, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const reqH = (b.requests / maxRequests) * innerH;
          const mbH = (b.mb / maxMb) * innerH;
          const reqX = cx - barW - 1;
          const mbX = cx + 1;
          const baseY = height - padBottom;
          // Label every other bin to avoid overlap at narrow widths.
          const showLabel = i % 2 === 0;
          return (
            <g key={b.iso}>
              <rect x={reqX} y={baseY - reqH} width={barW} height={reqH} fill="#2f81f7">
                <title>{`${b.label}: ${fmtNum(b.requests)} queries`}</title>
              </rect>
              <rect x={mbX} y={baseY - mbH} width={barW} height={mbH} fill="#f7942f">
                <title>{`${b.label}: ${fmtMb(b.mb)} scanned`}</title>
              </rect>
              {showLabel && (
                <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        {/* Average reference lines \u2014 queries on the left axis (blue),
         * MB on the right axis (orange). Each line is drawn against its
         * own series\u2019 scale so it lines up with the matching bars. */}
        {(() => {
          const avgReq = totalRequests / bins.length;
          const avgMb = totalMb / bins.length;
          const baseY = height - padBottom;
          const yReq = baseY - (avgReq / maxRequests) * innerH;
          const yMb = baseY - (avgMb / maxMb) * innerH;
          return (
            <g>
              {avgReq > 0 && (
                <>
                  <line
                    x1={padX}
                    x2={width - padX}
                    y1={yReq}
                    y2={yReq}
                    stroke="#2f81f7"
                    strokeDasharray="5,4"
                    strokeWidth={1.5}
                    opacity={0.85}
                  >
                    <title>{`Average queries: ${avgReq.toFixed(1)} per hour`}</title>
                  </line>
                  <text x={padX + 4} y={yReq - 4} textAnchor="start" fill="#2f81f7">
                    avg {avgReq.toFixed(1)}
                  </text>
                </>
              )}
              {avgMb > 0 && (
                <>
                  <line
                    x1={padX}
                    x2={width - padX}
                    y1={yMb}
                    y2={yMb}
                    stroke="#f7942f"
                    strokeDasharray="5,4"
                    strokeWidth={1.5}
                    opacity={0.85}
                  >
                    <title>{`Average data: ${fmtMb(avgMb)} per hour`}</title>
                  </line>
                  <text x={width - padX - 4} y={yMb - 4} textAnchor="end" fill="#f7942f">
                    avg {fmtMb(avgMb)}
                  </text>
                </>
              )}
            </g>
          );
        })()}
      </svg>
    </div>
  );
}

function ServerlessClusteredBars({ days }: { days: import("../types").ServerlessDailyUsage[] }) {
  // Clustered bar chart: queries vs MB per day. Two y-axes so we don't compare
  // apples to oranges; each bar pair is normalized against its own series max.
  const maxRequests = Math.max(1, ...days.map((d) => d.request_count ?? 0));
  const maxMb = Math.max(1, ...days.map((d) => d.data_processed_mb ?? 0));

  const width = 720;
  const height = 220;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / days.length;
  const barW = Math.max(6, Math.min(28, (groupW - 8) / 2));

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        <span style={{ display: "inline-block", width: 10, height: 10, background: "#2f81f7", marginRight: 4, verticalAlign: "middle" }} />
        Queries (left axis)
        {"  "}
        <span style={{ display: "inline-block", width: 10, height: 10, background: "#f7942f", margin: "0 4px 0 12px", verticalAlign: "middle" }} />
        MB scanned (right axis)
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{ maxWidth: width, fontSize: 10 }} role="img" aria-label={`Serverless SQL last ${days.length} days clustered bar chart`}>
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={width - padX} x2={width - padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {Math.round(maxRequests * t)}
            </text>
            <text x={width - padX + 6} y={height - padBottom - innerH * t + 3} textAnchor="start" fill="currentColor" opacity={0.7}>
              {fmtMb(maxMb * t)}
            </text>
          </g>
        ))}
        {days.map((d, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const reqH = ((d.request_count ?? 0) / maxRequests) * innerH;
          const mbH = ((d.data_processed_mb ?? 0) / maxMb) * innerH;
          const reqX = cx - barW - 1;
          const mbX = cx + 1;
          const baseY = height - padBottom;
          return (
            <g key={d.day}>
              <rect x={reqX} y={baseY - reqH} width={barW} height={reqH} fill="#2f81f7">
                <title>{`${d.day}: ${fmtNum(d.request_count)} queries`}</title>
              </rect>
              <rect x={mbX} y={baseY - mbH} width={barW} height={mbH} fill="#f7942f">
                <title>{`${d.day}: ${fmtMb(d.data_processed_mb)} scanned`}</title>
              </rect>
              <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                {d.day.slice(5)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// =================================================================
// BigQuery workloads — section component + daily-bars chart.
//
// Layout intentionally mirrors ``DatabricksWorkflowsSection`` and
// ``SparkPoolsSection``: a ``<section className="section">`` with a
// 3-card headline (workloads / runs / capacity), then per-dimension
// tables and a daily-bars chart in the same idiom as SparkDailyBars.
// =================================================================

function BigQueryWorkloadsSection({
  bq,
  runMeta,
}: {
  bq: import("../types").BigQueryWorkloadsReport | null;
  runMeta?: import("../api/loader").RunMeta | null;
}) {
  if (!bq) return null;
  const jobs = bq.jobs ?? [];
  const daily = bq.daily_stats ?? [];
  const tables = bq.tables ?? [];
  const tableUsage = bq.table_usage ?? [];
  const breakdowns = bq.breakdowns ?? [];
  const features = bq.query_features ?? [];
  if (jobs.length === 0 && daily.length === 0 && tables.length === 0) return null;

  // Headline totals are computed off the daily_stats rollup (which is
  // already date-bucketed) so the numbers match the chart exactly.
  const totalJobs = daily.reduce((s, d) => s + d.job_count, 0);
  const totalSlotHours = daily.reduce((s, d) => s + (d.total_slot_hours || 0), 0);
  const totalBilledBytes = daily.reduce((s, d) => s + (d.total_billed_bytes || 0), 0);
  const totalCuHours = daily.reduce((s, d) => s + (d.est_cu_hours_fabric_spark || 0), 0);
  const completed = daily.reduce(
    (s, d) => s + d.succeeded_count + d.failed_count,
    0,
  );
  const succeeded = daily.reduce((s, d) => s + d.succeeded_count, 0);
  const successPct = completed > 0 ? (succeeded / completed) * 100 : null;
  const windowDays = daily.length;

  const byDim = new Map<string, import("../types").BigQueryJobBreakdown[]>();
  for (const b of breakdowns) {
    const arr = byDim.get(b.dimension) ?? [];
    arr.push(b);
    byDim.set(b.dimension, arr);
  }
  const topUsers = (byDim.get("user") ?? []).slice(0, 10);
  const topStatementTypes = (byDim.get("statement_type") ?? []).slice(0, 10);

  return (
    <section className="section">
      <h2>
        BigQuery workload{windowDays > 0 ? ` (last ${windowDays} days)` : ""}
        <ProvenanceBadge meta={runMeta ?? null} module="bigquery_workloads" />
        <ReanalyzeButton
          modules={["bigquery_workloads"]}
          title="Re-run the BigQuery workloads analyzer"
        />
      </h2>
      <p className="muted small" style={{ marginTop: 0 }}>
        Project <code>{bq.project_id}</code>
        {bq.location ? <> · {bq.location}</> : null}
        {" · "}
        {fmtNum(bq.dataset_count ?? bq.datasets.length)} datasets ·{" "}
        {fmtNum(bq.table_count ?? tables.length)} tables ·{" "}
        {fmtNum(bq.routine_count ?? bq.routines.length)} routines ·{" "}
        {fmtNum(bq.scheduled_query_count ?? bq.scheduled_queries.length)} scheduled queries
      </p>

      <div className="grid cols-3">
        <StatCard
          label="Jobs (window)"
          value={fmtNum(totalJobs)}
          sub={
            windowDays > 0
              ? `${(totalJobs / Math.max(windowDays, 1)).toFixed(1)} jobs/day${
                  successPct != null ? ` · ${successPct.toFixed(1)}% success` : ""
                }`
              : "no daily rollup"
          }
        />
        <StatCard
          label="Slot-hours"
          value={totalSlotHours > 0 ? fmtNum(totalSlotHours, 2) : "—"}
          sub={
            totalBilledBytes > 0
              ? `${fmtBytesGb(totalBilledBytes / 1024 ** 3)} billed`
              : "BigQuery slot consumption (INFORMATION_SCHEMA)"
          }
        />
        <StatCard
          label="Est. Fabric capacity"
          value={totalCuHours > 0 ? `${totalCuHours.toFixed(2)} CU-hr` : "—"}
          sub={
            totalCuHours > 0
              ? `≈ ${(totalCuHours / Math.max(windowDays, 1) / 24).toFixed(2)} CU sustained · 0.25 CU-hr ≈ 1 slot-hr (heuristic)`
              : "0.25 CU-hr ≈ 1 BigQuery slot-hr (rough parity)"
          }
        />
      </div>

      {(daily.length > 0 || jobs.length > 0) && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            marginTop: 12,
          }}
        >
          {daily.length > 0 && (
            <div style={{ flex: "1 1 360px", minWidth: 0 }}>
              <BigQueryDailyBars days={daily} />
            </div>
          )}
          {jobs.length > 0 && (
            <div style={{ flex: "1 1 360px", minWidth: 0 }}>
              <BigQueryHourlyBars jobs={jobs} />
            </div>
          )}
        </div>
      )}

      <BigQueryStorageView tables={tables} />

      {topUsers.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Top users by slot-hours</h3>
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th className="num">Jobs</th>
                <th className="num">OK</th>
                <th className="num">Failed</th>
                <th className="num">Slot-hr</th>
                <th className="num">Billed (GB)</th>
                <th className="num">Avg duration (s)</th>
              </tr>
            </thead>
            <tbody>
              {topUsers.map((r) => (
                <tr key={r.key}>
                  <td><code>{r.key}</code></td>
                  <td className="num">{fmtNum(r.job_count)}</td>
                  <td className="num">{fmtNum(r.succeeded_count)}</td>
                  <td className="num">{fmtNum(r.failed_count)}</td>
                  <td className="num">{r.total_slot_hours.toFixed(2)}</td>
                  <td className="num">{(r.total_billed_bytes / 1024 ** 3).toFixed(2)}</td>
                  <td className="num">
                    {r.avg_duration_seconds != null
                      ? r.avg_duration_seconds.toFixed(1)
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {topStatementTypes.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Statement-type breakdown</h3>
          <table>
            <thead>
              <tr>
                <th>Statement type</th>
                <th className="num">Jobs</th>
                <th className="num">OK</th>
                <th className="num">Failed</th>
                <th className="num">Slot-hr</th>
                <th className="num">Billed (GB)</th>
              </tr>
            </thead>
            <tbody>
              {topStatementTypes.map((r) => (
                <tr key={r.key}>
                  <td><code>{r.key}</code></td>
                  <td className="num">{fmtNum(r.job_count)}</td>
                  <td className="num">{fmtNum(r.succeeded_count)}</td>
                  <td className="num">{fmtNum(r.failed_count)}</td>
                  <td className="num">{r.total_slot_hours.toFixed(2)}</td>
                  <td className="num">{(r.total_billed_bytes / 1024 ** 3).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tableUsage.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Most-used tables (top 20)</h3>
          <p className="muted small" style={{ marginTop: 0 }}>
            Ranked by distinct jobs that referenced the table — totals are
            not apportioned across joined tables (BigQuery does not expose
            per-table cost split).
          </p>
          <table>
            <thead>
              <tr>
                <th>Table</th>
                <th className="num">Jobs</th>
                <th className="num">Elapsed (s)</th>
                <th className="num">Slot-hr</th>
                <th className="num">Billed (GB)</th>
                <th>Support</th>
                <th>In catalog</th>
              </tr>
            </thead>
            <tbody>
              {tableUsage.slice(0, 20).map((r) => (
                <tr key={r.full_table_id}>
                  <td><code>{r.full_table_id}</code></td>
                  <td className="num">{fmtNum(r.usage_count)}</td>
                  <td className="num">{r.total_elapsed_seconds.toFixed(1)}</td>
                  <td className="num">{r.total_slot_hours.toFixed(2)}</td>
                  <td className="num">{(r.total_billed_bytes / 1024 ** 3).toFixed(2)}</td>
                  <td>{r.table_support ?? "unknown"}</td>
                  <td>{r.in_catalog ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {features.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Query features (sqlglot)</h3>
          <p className="muted small" style={{ marginTop: 0 }}>
            Per-job feature flags extracted from captured query text using
            sqlglot's BigQuery dialect. Set <code>SMA_BQ_CAPTURE_QUERY_TEXT=1</code>
            on the analyzer host to populate <code>query_text</code> (off by
            default — SQL frequently carries embedded literals / PII).
          </p>
          <table>
            <thead>
              <tr>
                <th>Feature</th>
                <th className="num">Jobs</th>
              </tr>
            </thead>
            <tbody>
              {features.slice(0, 25).map((f) => (
                <tr key={f.feature}>
                  <td><code>{f.feature}</code></td>
                  <td className="num">{fmtNum(f.job_count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {(bq.caveats?.length ?? 0) > 0 && (
        <ul className="muted small" style={{ marginTop: 8 }}>
          {bq.caveats!.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
      {(bq.errors?.length ?? 0) > 0 && (
        <ul className="small" style={{ color: "var(--err)", marginTop: 8 }}>
          {bq.errors!.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Daily-bars chart for BigQuery job outcomes — stacked succeeded /
 * failed / cancelled+other per UTC day. Same SVG idiom as
 * ``SparkDailyBars`` (width 720, height 240, padX 56) so the visual
 * weight on the dashboard matches the other sources.
 */
function BigQueryDailyBars({
  days,
}: {
  days: import("../types").BigQueryDailyJobStats[];
}) {
  if (!days || days.length === 0) return null;
  // Trim to the trailing 28 days for visual parity with Spark / Pipelines.
  const trail = days.slice(-28);
  const maxV = Math.max(1, ...trail.map((d) => d.job_count));
  const total = trail.reduce((s, d) => s + d.job_count, 0);
  if (total <= 0) return null;

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / trail.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));
  const colorOk = "var(--ok)";
  const colorErr = "var(--err)";
  const colorOther = "var(--muted)";

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        <span style={{ marginRight: 12 }}>
          <span style={{ display: "inline-block", width: 10, height: 10, background: colorOk, marginRight: 4, verticalAlign: "middle" }} />
          Succeeded
        </span>
        <span style={{ marginRight: 12 }}>
          <span style={{ display: "inline-block", width: 10, height: 10, background: colorErr, marginRight: 4, verticalAlign: "middle" }} />
          Failed
        </span>
        <span style={{ marginRight: 12 }}>
          <span style={{ display: "inline-block", width: 10, height: 10, background: colorOther, marginRight: 4, verticalAlign: "middle", opacity: 0.6 }} />
          Cancelled / other
        </span>
        <span>BigQuery jobs per day (last {trail.length})</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label={`BigQuery jobs per day for the last ${trail.length} days`}
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {Math.round(maxV * t)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {trail.map((d, i) => {
          const x = padX + i * groupW + (groupW - barW) / 2;
          const otherCount = d.cancelled_count + d.other_count;
          const okH = (d.succeeded_count / maxV) * innerH;
          const errH = (d.failed_count / maxV) * innerH;
          const othH = (otherCount / maxV) * innerH;
          const baseY = height - padBottom;
          const okY = baseY - okH;
          const errY = okY - errH;
          const othY = errY - othH;
          const labelEvery = Math.max(1, Math.ceil(trail.length / 7));
          const showLabel = i === 0 || i === trail.length - 1 || i % labelEvery === 0;
          const sr =
            d.success_rate == null
              ? ""
              : ` · ${(d.success_rate * 100).toFixed(0)}% ok`;
          const title =
            `${d.date} — ${d.job_count} jobs · ${d.succeeded_count} ok · ` +
            `${d.failed_count} failed${otherCount ? ` · ${otherCount} other` : ""}${sr} · ` +
            `${d.total_slot_hours.toFixed(2)} slot-hr`;
          return (
            <g key={d.date}>
              <title>{title}</title>
              {d.succeeded_count > 0 && (
                <rect x={x} y={okY} width={barW} height={okH} fill={colorOk} />
              )}
              {d.failed_count > 0 && (
                <rect x={x} y={errY} width={barW} height={errH} fill={colorErr} />
              )}
              {otherCount > 0 && (
                <rect x={x} y={othY} width={barW} height={othH} fill={colorOther} opacity={0.6} />
              )}
              {showLabel && (
                <text
                  x={x + barW / 2}
                  y={height - padBottom + 14}
                  textAnchor="middle"
                  fill="currentColor"
                  opacity={0.8}
                >
                  {d.date.slice(5)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Snowflake workloads — mirrors BigQueryWorkloadsSection in shape so the
// user sees the same "headline cards + daily chart + breakdowns + top
// tables + code-object compatibility" rhythm across sources.
// ---------------------------------------------------------------------------

function SnowflakeWorkloadsSection({
  snow,
  runMeta,
}: {
  snow: import("../types").SnowflakeWorkloadsReport | null;
  runMeta?: import("../api/loader").RunMeta | null;
}) {
  if (!snow) return null;
  const daily = snow.daily_stats ?? [];
  const breakdowns = snow.breakdowns ?? [];
  const tableUsage = snow.table_usage ?? [];
  const code = snow.code_object_summary ?? null;
  const warehouses = snow.warehouses ?? [];
  const jobsCount = snow.job_count ?? (snow.jobs?.length ?? 0);
  if (
    jobsCount === 0
    && daily.length === 0
    && tableUsage.length === 0
    && warehouses.length === 0
    && !code
  ) {
    return null;
  }

  const totalJobs = daily.reduce((s, d) => s + d.job_count, 0);
  const totalCredits = daily.reduce((s, d) => s + (d.est_credits || 0), 0);
  const totalCuHours = daily.reduce(
    (s, d) => s + (d.est_cu_hours_fabric_warehouse || 0),
    0,
  );
  const completed = daily.reduce((s, d) => s + d.succeeded_count + d.failed_count, 0);
  const succeeded = daily.reduce((s, d) => s + d.succeeded_count, 0);
  const successPct = completed > 0 ? (succeeded / completed) * 100 : null;
  const windowDays = daily.length;

  const byDim = new Map<string, import("../types").SnowflakeJobBreakdown[]>();
  for (const b of breakdowns) {
    const arr = byDim.get(b.dimension) ?? [];
    arr.push(b);
    byDim.set(b.dimension, arr);
  }
  const topUsers = (byDim.get("user") ?? []).slice(0, 10);
  const topQueryTypes = (byDim.get("query_type") ?? []).slice(0, 10);
  const perWarehouse = (byDim.get("warehouse") ?? []).slice(0, 10);

  const usageSource = tableUsage[0]?.source ?? null;

  return (
    <section className="section">
      <h2>
        Snowflake workload{windowDays > 0 ? ` (last ${windowDays} days)` : ""}
        <ProvenanceBadge meta={runMeta ?? null} module="snowflake_workloads" />
        <ReanalyzeButton
          modules={["snowflake_workloads"]}
          title="Re-run the Snowflake workloads analyzer"
        />
      </h2>
      <p className="muted small" style={{ marginTop: 0 }}>
        Account <code>{snow.account}</code>
        {snow.platform ? <> · {snow.platform}</> : null}
        {snow.region ? <> · {snow.region}</> : null}
        {snow.edition ? <> · {snow.edition}</> : null}
        {" · "}
        {fmtNum(snow.warehouse_count ?? warehouses.length)} warehouses ·{" "}
        {fmtNum(snow.database_count)} databases ·{" "}
        {fmtNum(snow.table_count)} tables ·{" "}
        {fmtNum(snow.routine_count)} routines
      </p>

      <div className="grid cols-3">
        <StatCard
          label="Jobs (window)"
          value={fmtNum(totalJobs || jobsCount)}
          sub={
            windowDays > 0
              ? `${(totalJobs / Math.max(windowDays, 1)).toFixed(1)} jobs/day${
                  successPct != null ? ` · ${successPct.toFixed(1)}% success` : ""
                }`
              : "no daily rollup"
          }
        />
        <StatCard
          label="Credits"
          value={totalCredits > 0 ? totalCredits.toFixed(2) : "—"}
          sub="1 credit ≈ 1 vCore-hr (Snowflake billing unit)"
        />
        <StatCard
          label="Est. Fabric capacity"
          value={totalCuHours > 0 ? `${totalCuHours.toFixed(2)} CU-hr` : "—"}
          sub={
            totalCuHours > 0
              ? `≈ ${(totalCuHours / Math.max(windowDays, 1) / 24).toFixed(2)} CU sustained · 0.5 CU-hr ≈ 1 credit (heuristic)`
              : "0.5 CU-hr ≈ 1 Snowflake credit (rough parity)"
          }
        />
      </div>

      {(daily.length > 0 || (snow.jobs?.length ?? 0) > 0) && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            marginTop: 12,
          }}
        >
          {daily.length > 0 && (
            <div style={{ flex: "1 1 360px", minWidth: 0 }}>
              <SnowflakeDailyBars days={daily} />
            </div>
          )}
          {(snow.jobs?.length ?? 0) > 0 && (
            <div style={{ flex: "1 1 360px", minWidth: 0 }}>
              <SnowflakeHourlyBars jobs={snow.jobs!} />
            </div>
          )}
        </div>
      )}

      {code && code.total > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Code-object compatibility</h3>
          <p className="muted small" style={{ marginTop: 0 }}>
            {fmtNum(code.total)} objects scanned ·{" "}
            {code.compatibility_pct != null
              ? `${code.compatibility_pct.toFixed(1)}% supported as-is`
              : "compatibility %: n/a"}
          </p>
          <div className="grid cols-3">
            <div>
              <strong className="small">By support</strong>
              <table>
                <tbody>
                  {Object.entries(code.by_support).map(([k, v]) => (
                    <tr key={k}>
                      <td>{k}</td>
                      <td className="num">{fmtNum(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <strong className="small">By kind</strong>
              <table>
                <tbody>
                  {Object.entries(code.by_kind).map(([k, v]) => (
                    <tr key={k}>
                      <td><code>{k}</code></td>
                      <td className="num">{fmtNum(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {Object.keys(code.by_language).length > 0 && (
              <div>
                <strong className="small">Routine languages</strong>
                <table>
                  <tbody>
                    {Object.entries(code.by_language).map(([k, v]) => (
                      <tr key={k}>
                        <td><code>{k}</code></td>
                        <td className="num">{fmtNum(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {code.unsupported_object_names.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary className="small">
                Unsupported objects ({code.unsupported_object_names.length})
              </summary>
              <ul className="small">
                {code.unsupported_object_names.slice(0, 25).map((n) => (
                  <li key={n}><code>{n}</code></li>
                ))}
              </ul>
            </details>
          )}
          {code.partial_object_names.length > 0 && (
            <details style={{ marginTop: 4 }}>
              <summary className="small">
                Partial-support objects ({code.partial_object_names.length})
              </summary>
              <ul className="small">
                {code.partial_object_names.slice(0, 25).map((n) => (
                  <li key={n}><code>{n}</code></li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      {topQueryTypes.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Query-type breakdown</h3>
          <table>
            <thead>
              <tr>
                <th>Query type</th>
                <th className="num">Jobs</th>
                <th className="num">OK</th>
                <th className="num">Failed</th>
                <th className="num">Credits</th>
                <th className="num">Bytes scanned (GB)</th>
                <th className="num">Avg dur (s)</th>
              </tr>
            </thead>
            <tbody>
              {topQueryTypes.map((r) => (
                <tr key={r.key}>
                  <td><code>{r.key}</code></td>
                  <td className="num">{fmtNum(r.job_count)}</td>
                  <td className="num">{fmtNum(r.succeeded_count)}</td>
                  <td className="num">{fmtNum(r.failed_count)}</td>
                  <td className="num">{r.est_credits.toFixed(2)}</td>
                  <td className="num">{(r.total_bytes_scanned / 1024 ** 3).toFixed(2)}</td>
                  <td className="num">
                    {r.avg_duration_seconds != null
                      ? r.avg_duration_seconds.toFixed(1)
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {topUsers.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Top users by credits</h3>
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th className="num">Jobs</th>
                <th className="num">OK</th>
                <th className="num">Failed</th>
                <th className="num">Credits</th>
                <th className="num">Bytes (GB)</th>
              </tr>
            </thead>
            <tbody>
              {topUsers.map((r) => (
                <tr key={r.key}>
                  <td><code>{r.key}</code></td>
                  <td className="num">{fmtNum(r.job_count)}</td>
                  <td className="num">{fmtNum(r.succeeded_count)}</td>
                  <td className="num">{fmtNum(r.failed_count)}</td>
                  <td className="num">{r.est_credits.toFixed(2)}</td>
                  <td className="num">{(r.total_bytes_scanned / 1024 ** 3).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {perWarehouse.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>Per-warehouse activity</h3>
          <table>
            <thead>
              <tr>
                <th>Warehouse</th>
                <th className="num">Jobs</th>
                <th className="num">OK</th>
                <th className="num">Failed</th>
                <th className="num">Credits</th>
                <th className="num">CU-hr</th>
              </tr>
            </thead>
            <tbody>
              {perWarehouse.map((r) => (
                <tr key={r.key}>
                  <td><code>{r.key}</code></td>
                  <td className="num">{fmtNum(r.job_count)}</td>
                  <td className="num">{fmtNum(r.succeeded_count)}</td>
                  <td className="num">{fmtNum(r.failed_count)}</td>
                  <td className="num">{r.est_credits.toFixed(2)}</td>
                  <td className="num">{r.est_cu_hours_fabric_warehouse.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tableUsage.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>
            Top-active tables (top 20)
            {usageSource === "query_history_fallback" ? (
              <span className="muted small" style={{ marginLeft: 8, fontWeight: "normal" }}>
                — DB.SCHEMA grouping (ACCESS_HISTORY unavailable)
              </span>
            ) : usageSource === "access_history" ? (
              <span className="muted small" style={{ marginLeft: 8, fontWeight: "normal" }}>
                — from ACCESS_HISTORY
              </span>
            ) : null}
          </h3>
          <table>
            <thead>
              <tr>
                <th>Object</th>
                <th>Domain</th>
                <th className="num">Jobs</th>
                <th className="num">Bytes (GB)</th>
                <th className="num">Rows</th>
                <th>Support</th>
                <th>In catalog</th>
              </tr>
            </thead>
            <tbody>
              {tableUsage.slice(0, 20).map((r) => (
                <tr key={r.full_name}>
                  <td><code>{r.full_name}</code></td>
                  <td>{r.object_domain}</td>
                  <td className="num">{fmtNum(r.usage_count)}</td>
                  <td className="num">{(r.total_bytes_scanned / 1024 ** 3).toFixed(2)}</td>
                  <td className="num">{fmtNum(r.total_rows_produced)}</td>
                  <td>{r.table_support ?? "unknown"}</td>
                  <td>{r.in_catalog ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {(snow.caveats?.length ?? 0) > 0 && (
        <ul className="muted small" style={{ marginTop: 8 }}>
          {snow.caveats!.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
      {(snow.errors?.length ?? 0) > 0 && (
        <ul className="small" style={{ color: "var(--err)", marginTop: 8 }}>
          {snow.errors!.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Daily-bars chart for Snowflake job outcomes — mirrors BigQueryDailyBars
 * so the SPA renders Snowflake activity with the same visual rhythm as
 * BigQuery / Databricks / Spark.
 */
function SnowflakeDailyBars({
  days,
}: {
  days: import("../types").SnowflakeDailyJobStats[];
}) {
  if (!days || days.length === 0) return null;
  // Trim to the trailing 7 days for visual parity with the hourly companion.
  const trail = days.slice(-7);
  const maxV = Math.max(1, ...trail.map((d) => d.job_count));
  const total = trail.reduce((s, d) => s + d.job_count, 0);
  if (total <= 0) return null;

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / trail.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));
  const colorOk = "var(--ok)";
  const colorErr = "var(--err)";
  const colorOther = "var(--muted)";

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        <span style={{ marginRight: 12 }}>
          <span style={{ display: "inline-block", width: 10, height: 10, background: colorOk, marginRight: 4, verticalAlign: "middle" }} />
          Succeeded
        </span>
        <span style={{ marginRight: 12 }}>
          <span style={{ display: "inline-block", width: 10, height: 10, background: colorErr, marginRight: 4, verticalAlign: "middle" }} />
          Failed
        </span>
        <span style={{ marginRight: 12 }}>
          <span style={{ display: "inline-block", width: 10, height: 10, background: colorOther, marginRight: 4, verticalAlign: "middle", opacity: 0.6 }} />
          Other
        </span>
        <span>Snowflake queries per day (last {trail.length})</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label={`Snowflake queries per day for the last ${trail.length} days`}
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {Math.round(maxV * t)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {trail.map((d, i) => {
          const x = padX + i * groupW + (groupW - barW) / 2;
          const okH = (d.succeeded_count / maxV) * innerH;
          const errH = (d.failed_count / maxV) * innerH;
          const othH = (d.other_count / maxV) * innerH;
          const baseY = height - padBottom;
          const okY = baseY - okH;
          const errY = okY - errH;
          const othY = errY - othH;
          const labelEvery = Math.max(1, Math.ceil(trail.length / 7));
          const showLabel = i === 0 || i === trail.length - 1 || i % labelEvery === 0;
          const sr =
            d.success_rate == null
              ? ""
              : ` · ${(d.success_rate * 100).toFixed(0)}% ok`;
          const title =
            `${d.date} — ${d.job_count} jobs · ${d.succeeded_count} ok · ` +
            `${d.failed_count} failed${d.other_count ? ` · ${d.other_count} other` : ""}${sr} · ` +
            `${d.est_credits.toFixed(2)} credits`;
          return (
            <g key={d.date}>
              <title>{title}</title>
              {d.succeeded_count > 0 && (
                <rect x={x} y={okY} width={barW} height={okH} fill={colorOk} />
              )}
              {d.failed_count > 0 && (
                <rect x={x} y={errY} width={barW} height={errH} fill={colorErr} />
              )}
              {d.other_count > 0 && (
                <rect x={x} y={othY} width={barW} height={othH} fill={colorOther} opacity={0.6} />
              )}
              {showLabel && (
                <text
                  x={x + barW / 2}
                  y={height - padBottom + 14}
                  textAnchor="middle"
                  fill="currentColor"
                  opacity={0.8}
                >
                  {d.date.slice(5)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/**
 * Hourly companion to ``SnowflakeDailyBars`` covering the trailing 24
 * hours. Mirrors ``BigQueryHourlyBars`` shape, palette, and
 * average-line styling so the Snowflake section feels visually identical
 * to BigQuery / Databricks / Spark. Stacks per query type (SELECT,
 * INSERT, MERGE, …) and prefers ``est_credits`` as the metric when any
 * job in the window has a non-zero value; otherwise falls back to a
 * run-count view so the chart still tells a story when QUERY_HISTORY
 * doesn't surface credit attribution.
 */
function SnowflakeHourlyBars({
  jobs,
}: {
  jobs: import("../types").SnowflakeJob[];
}) {
  const windowHours = 24;
  const now = new Date();
  const currentHourStart = new Date(now);
  currentHourStart.setMinutes(0, 0, 0);
  const firstBinStart = new Date(
    currentHourStart.getTime() - (windowHours - 1) * 60 * 60 * 1000,
  );
  const cutoff = firstBinStart;

  const creditsOf = (j: import("../types").SnowflakeJob): number =>
    typeof j.est_credits === "number" && j.est_credits > 0 ? j.est_credits : 0;
  const labelOf = (j: import("../types").SnowflakeJob): string =>
    j.query_type || "OTHER";

  const windowed = jobs.filter((j) => {
    if (!j.start_time) return false;
    const t = new Date(j.start_time);
    return !Number.isNaN(t.getTime()) && t >= cutoff;
  });

  const sumCredits = windowed.reduce((s, j) => s + creditsOf(j), 0);
  const mode: "credits" | "runs" = sumCredits > 0 ? "credits" : "runs";
  const metricOf = (j: import("../types").SnowflakeJob): number =>
    mode === "credits" ? creditsOf(j) : 1;
  const unitLabel = mode === "credits" ? "credits" : "jobs";
  const headerTitle =
    mode === "credits"
      ? "Snowflake credits per hour (last 24h)"
      : "Snowflake queries per hour (last 24h)";

  if (windowed.length === 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>{headerTitle}</div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No Snowflake queries in the last 24 hours.
        </div>
      </div>
    );
  }

  const groups = Array.from(new Set(windowed.map(labelOf))).sort();

  type HourBin = { key: string; label: string; perGroup: Record<string, number>; total: number };
  const bins: HourBin[] = [];
  for (let i = 0; i < windowHours; i++) {
    const start = new Date(firstBinStart.getTime() + i * 60 * 60 * 1000);
    const key = start.toISOString();
    const label = `${String(start.getHours()).padStart(2, "0")}:00`;
    const perGroup: Record<string, number> = {};
    for (const g of groups) perGroup[g] = 0;
    bins.push({ key, label, perGroup, total: 0 });
  }
  const indexFor = (ms: number) =>
    Math.floor((ms - firstBinStart.getTime()) / (60 * 60 * 1000));

  for (const j of windowed) {
    const t = new Date(j.start_time as string);
    const idx = indexFor(t.getTime());
    if (idx < 0 || idx >= bins.length) continue;
    const bin = bins[idx];
    const v = metricOf(j);
    const label = labelOf(j);
    bin.perGroup[label] = (bin.perGroup[label] ?? 0) + v;
    bin.total += v;
  }

  const total = bins.reduce((s, b) => s + b.total, 0);
  if (total <= 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>{headerTitle}</div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No Snowflake activity observed in the last 24 hours.
        </div>
      </div>
    );
  }
  const avg = total / bins.length;
  const maxV = Math.max(0.001, avg, ...bins.map((b) => b.total));

  const PALETTE = ["#2f81f7", "#3fb950", "#d29922", "#a371f7", "#db61a2", "#e36b6b", "#1f9ea3", "#bf6a02"];
  const colorFor = (g: string) => PALETTE[groups.indexOf(g) % PALETTE.length];
  const fmtMetric = (v: number) => (mode === "credits" ? v.toFixed(2) : v.toFixed(0));

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / bins.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));
  const avgY = height - padBottom - (avg / maxV) * innerH;

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        {groups.map((g) => (
          <span key={g} style={{ marginRight: 12, display: "inline-block" }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: colorFor(g), marginRight: 4, verticalAlign: "middle" }} />
            <code>{g}</code>
          </span>
        ))}
        <span style={{ marginRight: 12, display: "inline-block" }}>
          <span style={{ display: "inline-block", width: 14, borderTop: "2px dashed #e36b6b", marginRight: 4, verticalAlign: "middle" }} />
          avg {fmtMetric(avg)} {unitLabel}/h
        </span>
        <span>{headerTitle}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label={`Snowflake ${unitLabel} per hour per query type for the last 24 hours`}
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {(maxV * t).toFixed(maxV >= 10 || mode === "runs" ? 0 : 2)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {bins.map((b, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const baseY = height - padBottom;
          const labelEvery = Math.max(1, Math.ceil(bins.length / 8));
          let acc = 0;
          return (
            <g key={b.key}>
              {groups.map((g) => {
                const v = b.perGroup[g] ?? 0;
                if (v <= 0) return null;
                const h = (v / maxV) * innerH;
                const y = baseY - acc - h;
                acc += h;
                return (
                  <rect
                    key={g}
                    x={cx - barW / 2}
                    y={y}
                    width={barW}
                    height={h}
                    fill={colorFor(g)}
                  >
                    <title>{`${b.label} — ${g}: ${fmtMetric(v)} ${unitLabel}`}</title>
                  </rect>
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        <line
          x1={padX}
          x2={width - padX}
          y1={avgY}
          y2={avgY}
          stroke="#e36b6b"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        >
          <title>{`Average ${fmtMetric(avg)} ${unitLabel}/hour`}</title>
        </line>
        <text
          x={width - padX - 4}
          y={avgY - 4}
          textAnchor="end"
          fill="#e36b6b"
          opacity={0.9}
        >
          avg {fmtMetric(avg)} {unitLabel}/h
        </text>
      </svg>
    </div>
  );
}

/**
 * Hourly companion to ``BigQueryDailyBars`` covering the trailing 24 hours.
 *
 * Mirrors ``DatabricksHourlyBars``/``SparkHourlyBars`` layout, palette,
 * and average-line styling so the BigQuery section feels visually
 * identical to the other workload sections. Stacks per statement-type
 * (DML, SELECT, MERGE, …) which is BigQuery's most useful grouping for
 * "what shape of workload is hitting the warehouse right now?".
 *
 * Metric selection follows the Databricks hourly pattern: prefer
 * ``total_slot_hours`` when any job in the window has a non-zero value;
 * otherwise fall back to a run-count view so the chart still tells a
 * story when only the project's job manifest (no INFORMATION_SCHEMA
 * slot accounting) is available.
 */
function BigQueryHourlyBars({
  jobs,
}: {
  jobs: import("../types").BigQueryJob[];
}) {
  const windowHours = 24;
  const now = new Date();
  const currentHourStart = new Date(now);
  currentHourStart.setMinutes(0, 0, 0);
  const firstBinStart = new Date(
    currentHourStart.getTime() - (windowHours - 1) * 60 * 60 * 1000,
  );
  const cutoff = firstBinStart;

  const slotOf = (j: import("../types").BigQueryJob): number =>
    typeof j.total_slot_hours === "number" && j.total_slot_hours > 0
      ? j.total_slot_hours
      : 0;
  const labelOf = (j: import("../types").BigQueryJob): string =>
    j.statement_type || "OTHER";

  const windowed = jobs.filter((j) => {
    if (!j.start_time) return false;
    const t = new Date(j.start_time);
    return !Number.isNaN(t.getTime()) && t >= cutoff;
  });

  const sumSlots = windowed.reduce((s, j) => s + slotOf(j), 0);
  const mode: "slots" | "runs" = sumSlots > 0 ? "slots" : "runs";
  const metricOf = (j: import("../types").BigQueryJob): number =>
    mode === "slots" ? slotOf(j) : 1;
  const unitLabel = mode === "slots" ? "slot-hr" : "jobs";
  const headerTitle =
    mode === "slots"
      ? "BigQuery slot-hr per hour (last 24h)"
      : "BigQuery jobs per hour (last 24h)";

  if (windowed.length === 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>{headerTitle}</div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No BigQuery jobs in the last 24 hours.
        </div>
      </div>
    );
  }

  const groups = Array.from(new Set(windowed.map(labelOf))).sort();

  type HourBin = { key: string; label: string; perGroup: Record<string, number>; total: number };
  const bins: HourBin[] = [];
  for (let i = 0; i < windowHours; i++) {
    const start = new Date(firstBinStart.getTime() + i * 60 * 60 * 1000);
    const key = start.toISOString();
    const label = `${String(start.getHours()).padStart(2, "0")}:00`;
    const perGroup: Record<string, number> = {};
    for (const g of groups) perGroup[g] = 0;
    bins.push({ key, label, perGroup, total: 0 });
  }
  const indexFor = (ms: number) =>
    Math.floor((ms - firstBinStart.getTime()) / (60 * 60 * 1000));

  for (const j of windowed) {
    const t = new Date(j.start_time as string);
    const idx = indexFor(t.getTime());
    if (idx < 0 || idx >= bins.length) continue;
    const bin = bins[idx];
    const v = metricOf(j);
    const label = labelOf(j);
    bin.perGroup[label] = (bin.perGroup[label] ?? 0) + v;
    bin.total += v;
  }

  const total = bins.reduce((s, b) => s + b.total, 0);
  if (total <= 0) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="small muted" style={{ marginBottom: 6 }}>{headerTitle}</div>
        <div className="empty small muted" style={{ padding: 12 }}>
          No BigQuery activity observed in the last 24 hours.
        </div>
      </div>
    );
  }
  const avg = total / bins.length;
  const maxV = Math.max(0.001, avg, ...bins.map((b) => b.total));

  const PALETTE = ["#2f81f7", "#3fb950", "#d29922", "#a371f7", "#db61a2", "#e36b6b", "#1f9ea3", "#bf6a02"];
  const colorFor = (g: string) => PALETTE[groups.indexOf(g) % PALETTE.length];
  const fmtMetric = (v: number) => (mode === "slots" ? v.toFixed(2) : v.toFixed(0));

  const width = 720;
  const height = 240;
  const padX = 56;
  const padTop = 16;
  const padBottom = 44;
  const innerH = height - padTop - padBottom;
  const groupW = (width - padX * 2) / bins.length;
  const barW = Math.max(4, Math.min(24, groupW - 2));
  const avgY = height - padBottom - (avg / maxV) * innerH;

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <div className="small muted" style={{ marginBottom: 6 }}>
        {groups.map((g) => (
          <span key={g} style={{ marginRight: 12, display: "inline-block" }}>
            <span style={{ display: "inline-block", width: 10, height: 10, background: colorFor(g), marginRight: 4, verticalAlign: "middle" }} />
            <code>{g}</code>
          </span>
        ))}
        <span style={{ marginRight: 12, display: "inline-block" }}>
          <span style={{ display: "inline-block", width: 14, borderTop: "2px dashed #e36b6b", marginRight: 4, verticalAlign: "middle" }} />
          avg {fmtMetric(avg)} {unitLabel}/h
        </span>
        <span>{headerTitle}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        style={{ maxWidth: width, fontSize: 10 }}
        role="img"
        aria-label={`BigQuery ${unitLabel} per hour per statement type for the last 24 hours`}
      >
        <line x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        <line x1={padX} x2={padX} y1={padTop} y2={height - padBottom} stroke="currentColor" opacity={0.3} />
        {[0, 0.5, 1].map((t, i) => (
          <g key={i}>
            <text x={padX - 6} y={height - padBottom - innerH * t + 3} textAnchor="end" fill="currentColor" opacity={0.7}>
              {(maxV * t).toFixed(maxV >= 10 || mode === "runs" ? 0 : 1)}
            </text>
            <line
              x1={padX}
              x2={width - padX}
              y1={height - padBottom - innerH * t}
              y2={height - padBottom - innerH * t}
              stroke="currentColor"
              opacity={i === 0 ? 0.3 : 0.08}
            />
          </g>
        ))}
        {bins.map((b, i) => {
          const cx = padX + groupW * i + groupW / 2;
          const baseY = height - padBottom;
          const labelEvery = Math.max(1, Math.ceil(bins.length / 8));
          let acc = 0;
          return (
            <g key={b.key}>
              {groups.map((g) => {
                const v = b.perGroup[g] ?? 0;
                if (v <= 0) return null;
                const h = (v / maxV) * innerH;
                const y = baseY - acc - h;
                acc += h;
                return (
                  <rect
                    key={g}
                    x={cx - barW / 2}
                    y={y}
                    width={barW}
                    height={h}
                    fill={colorFor(g)}
                  >
                    <title>{`${b.label} — ${g}: ${fmtMetric(v)} ${unitLabel}`}</title>
                  </rect>
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - padBottom + 14} textAnchor="middle" fill="currentColor" opacity={0.8}>
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        <line
          x1={padX}
          x2={width - padX}
          y1={avgY}
          y2={avgY}
          stroke="#e36b6b"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        >
          <title>{`Average ${fmtMetric(avg)} ${unitLabel}/hour`}</title>
        </line>
        <text
          x={width - padX - 4}
          y={avgY - 4}
          textAnchor="end"
          fill="#e36b6b"
          opacity={0.9}
        >
          avg {fmtMetric(avg)} {unitLabel}/h
        </text>
      </svg>
    </div>
  );
}

/**
 * BigQuery storage rollup — per-dataset logical bytes / row count /
 * table count plus a headline total. BigQuery's per-table
 * ``num_bytes`` is **active logical** storage (the value billed under
 * the standard logical-billing model). Physical bytes and long-term
 * vs active split require ``INFORMATION_SCHEMA.TABLE_STORAGE`` which
 * we don't capture today; the table makes the metric explicit so
 * estate planners aren't misled.
 *
 * Layout mirrors the dashboard's ``StorageSection`` (used for Synapse
 * dedicated pools): a 3-card headline followed by a sortable table.
 */
function BigQueryStorageView({
  tables,
}: {
  tables: import("../types").BigQueryTable[];
}) {
  if (!tables || tables.length === 0) return null;

  type DatasetAgg = {
    dataset: string;
    tableCount: number;
    rowCount: number;
    byteCount: number;
    largestTable: { id: string; bytes: number } | null;
  };
  const agg = new Map<string, DatasetAgg>();
  let totalBytes = 0;
  let totalRows = 0;
  let tablesWithBytes = 0;
  for (const t of tables) {
    const ds = t.dataset_id;
    const cur =
      agg.get(ds) ??
      ({
        dataset: ds,
        tableCount: 0,
        rowCount: 0,
        byteCount: 0,
        largestTable: null,
      } as DatasetAgg);
    cur.tableCount += 1;
    if (typeof t.num_rows === "number" && t.num_rows > 0) {
      cur.rowCount += t.num_rows;
      totalRows += t.num_rows;
    }
    if (typeof t.num_bytes === "number" && t.num_bytes > 0) {
      cur.byteCount += t.num_bytes;
      totalBytes += t.num_bytes;
      tablesWithBytes += 1;
      if (!cur.largestTable || t.num_bytes > cur.largestTable.bytes) {
        cur.largestTable = { id: t.table_id, bytes: t.num_bytes };
      }
    }
    agg.set(ds, cur);
  }

  if (totalBytes === 0) {
    return (
      <>
        <h3 style={{ marginTop: 18 }}>Storage</h3>
        <p className="muted small">
          BigQuery did not return <code>num_bytes</code> for any table in the
          catalog (size metadata missing or restricted by IAM). Storage view
          requires <code>bigquery.tables.get</code> on listed tables.
        </p>
      </>
    );
  }

  const rows = Array.from(agg.values()).sort((a, b) => b.byteCount - a.byteCount);
  const totalGiB = totalBytes / 1024 ** 3;
  const totalTiB = totalGiB / 1024;
  const topDataset = rows[0];
  const topPct = totalBytes > 0 ? (topDataset.byteCount / totalBytes) * 100 : 0;

  const fmtSize = (bytes: number): string => {
    if (bytes <= 0) return "0 B";
    const gib = bytes / 1024 ** 3;
    if (gib >= 1024) return `${(gib / 1024).toFixed(2)} TiB`;
    if (gib >= 1) return `${gib.toFixed(2)} GiB`;
    const mib = bytes / 1024 ** 2;
    if (mib >= 1) return `${mib.toFixed(2)} MiB`;
    const kib = bytes / 1024;
    if (kib >= 1) return `${kib.toFixed(2)} KiB`;
    return `${bytes} B`;
  };

  return (
    <>
      <h3 style={{ marginTop: 18 }}>Storage</h3>
      <p className="muted small" style={{ marginTop: 0 }}>
        Active logical storage from <code>bigquery.tables.get</code> (the
        metric billed under BigQuery's logical-byte model). Physical bytes
        and long-term vs active split require
        <code> INFORMATION_SCHEMA.TABLE_STORAGE</code> and are not collected
        here.
      </p>
      <div className="grid cols-3">
        <StatCard
          label="Total logical storage"
          value={totalTiB >= 1 ? `${totalTiB.toFixed(2)} TiB` : `${totalGiB.toFixed(2)} GiB`}
          sub={`${fmtNum(tablesWithBytes)} of ${fmtNum(tables.length)} tables sized`}
        />
        <StatCard
          label="Datasets"
          value={fmtNum(rows.length)}
          sub={`${fmtNum(totalRows)} rows across catalog`}
        />
        <StatCard
          label="Top dataset"
          value={fmtSize(topDataset.byteCount)}
          sub={`${topDataset.dataset} · ${topPct.toFixed(1)}% of estate`}
        />
      </div>
      <table style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Dataset</th>
            <th className="num">Tables</th>
            <th className="num">Rows</th>
            <th className="num">Logical size</th>
            <th className="num">% of estate</th>
            <th>Largest table</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.dataset}>
              <td><code>{r.dataset}</code></td>
              <td className="num">{fmtNum(r.tableCount)}</td>
              <td className="num">{fmtNum(r.rowCount)}</td>
              <td className="num">{fmtSize(r.byteCount)}</td>
              <td className="num">
                {totalBytes > 0
                  ? `${((r.byteCount / totalBytes) * 100).toFixed(1)}%`
                  : "—"}
              </td>
              <td>
                {r.largestTable ? (
                  <>
                    <code>{r.largestTable.id}</code>{" "}
                    <span className="muted small">
                      ({fmtSize(r.largestTable.bytes)})
                    </span>
                  </>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

