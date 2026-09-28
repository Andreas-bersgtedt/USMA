import { useEffect, useState } from "react";
import { apiGetEstate, apiGetRunModule } from "../api/loader";
import { Empty, PctPill, ScorePill, SeverityPill, StatCard } from "../components/Atoms";
import { areaLabel, effortLabel, severityLabel } from "../lib/labels";
import type {
  CostReport,
  EstateReport,
  EstateWorkspace,
  FabricMappingReport,
  Recommendation,
  SparkPoolsReport,
  Severity,
} from "../types";
import {
  aggregateSparkWindow,
  filterSparkDailyUsageToWindow,
  getSparkSizingStatus,
  sparkObservationWindowDays,
} from "../lib/sparkAccounting";

interface WorkspaceBundle {
  ws: EstateWorkspace;
  fm: FabricMappingReport | null;
  cost: CostReport | null;
  spark: SparkPoolsReport | null;
}

function fmtNum(n: number | null | undefined, d = 0): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: d });
}

function fmtCurrency(n: number | null | undefined, code: string | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const c = (code ?? "USD").toUpperCase();
  try {
    return n.toLocaleString(undefined, {
      style: "currency",
      currency: c,
      maximumFractionDigits: 0,
    });
  } catch {
    return `${c} ${fmtNum(n)}`;
  }
}

const F_SKUS = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048];
function minFabricSku(totalCu: number | null | undefined): string {
  if (totalCu == null || !Number.isFinite(totalCu) || totalCu <= 0) return "—";
  for (const cu of F_SKUS) if (cu >= totalCu) return `F${cu}`;
  return `F${F_SKUS[F_SKUS.length - 1]}+`;
}

export default function PrintReport() {
  const [report, setReport] = useState<EstateReport | null>(null);
  const [bundles, setBundles] = useState<WorkspaceBundle[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiGetEstate();
        if (cancelled) return;
        setReport(r);
        const results = await Promise.all(
          r.workspaces.map(async (ws) => {
            const [fm, cost, spark] = await Promise.all([
              apiGetRunModule<FabricMappingReport>(ws.latest_run_id, "fabric_mapping"),
              apiGetRunModule<CostReport>(ws.latest_run_id, "cost"),
              apiGetRunModule<SparkPoolsReport>(ws.latest_run_id, "spark_pools"),
            ]);
            return { ws, fm, cost, spark } as WorkspaceBundle;
          }),
        );
        if (!cancelled) setBundles(results);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Open the print dialog once data is settled.
  useEffect(() => {
    if (!loading && report && !error) {
      const t = window.setTimeout(() => window.print(), 400);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [loading, report, error]);

  if (loading) return <div className="empty">Building report…</div>;
  if (error) return <Empty>Couldn't build report: {error}</Empty>;
  if (!report) return <Empty>No estate data yet.</Empty>;

  const t = report.totals;
  const cur = report.workspaces.find((w) => w.actual_currency)?.actual_currency ?? "USD";
  const generated = new Date(report.generated_at).toLocaleString();

  return (
    <div className="report-page">
      <div className="toolbar no-print" style={{ marginBottom: "0.75rem" }}>
        <button className="btn" onClick={() => window.print()}>
          Save as PDF
        </button>
        <span className="muted small" style={{ marginLeft: "0.75rem" }}>
          Use your browser's print dialog and choose <strong>Save as PDF</strong>.
        </span>
      </div>

      <h1>Synapse → Fabric estate report</h1>
      <div className="muted small">Generated {generated}</div>

      <section className="report-section" style={{ marginTop: "1rem" }}>
        <h2>Estate overview</h2>
        <div className="grid cols-4">
          <StatCard label="Workspaces" value={fmtNum(t.workspaces)} />
          <StatCard label="Runs" value={fmtNum(t.runs)} />
          <StatCard label="Tenants" value={fmtNum(t.tenants)} />
          <StatCard label="Subscriptions" value={fmtNum(t.subscriptions)} />
          <StatCard
            label="Ready / w-effort / blocked"
            value={`${t.ready} / ${t.ready_with_effort} / ${t.blocked}`}
          />
          <StatCard
            label="Open blockers"
            value={fmtNum(t.blockers_total)}
            sub="latest run per workspace"
          />
          <StatCard
            label="Avg T-SQL compatibility"
            value={
              t.tsql_compatibility_pct_avg == null
                ? "—"
                : <PctPill pct={Math.round(t.tsql_compatibility_pct_avg)} />
            }
          />
          <StatCard
            label="Estimated SKU needed"
            value={minFabricSku(t.projected_fabric_cu_total)}
            sub={
              t.projected_fabric_cu_total == null
                ? "no capacity projection"
                : `min F-SKU to cover ${fmtNum(t.projected_fabric_cu_total, 1)} CU`
            }
          />
          <StatCard
            label="Actual monthly spend (Synapse)"
            value={fmtCurrency(t.actual_monthly_cost_total, cur)}
          />
          <StatCard
            label="Projected monthly spend (Fabric)"
            value={fmtCurrency(t.fabric_estimated_monthly_cost_total, cur)}
          />
        </div>
      </section>

      <section className="report-section landscape">
        <h2>Workspaces</h2>
        <table>
          <thead>
            <tr>
              <th>Workspace</th>
              <th>Subscription</th>
              <th>Last scan</th>
              <th>Score</th>
              <th>Bucket</th>
              <th>Blk</th>
              <th>Warn</th>
              <th>T-SQL %</th>
              <th>Actual $/mo</th>
              <th>Fabric $/mo</th>
              <th>SKU</th>
            </tr>
          </thead>
          <tbody>
            {report.workspaces.map((ws) => (
              <tr key={ws.key}>
                <td><strong>{ws.workspace_name}</strong></td>
                <td className="mono small">{ws.subscription_id ?? "—"}</td>
                <td className="mono small">
                  {ws.latest_finished_at.replace("T", " ").slice(0, 16)}
                </td>
                <td>{ws.readiness_score == null ? "—" : <ScorePill score={Math.round(ws.readiness_score)} />}</td>
                <td>{ws.readiness_bucket ?? "—"}</td>
                <td>{ws.blocker_count}</td>
                <td>{ws.warning_count}</td>
                <td>
                  <PctPill
                    pct={ws.tsql_compatibility_pct == null ? null : Math.round(ws.tsql_compatibility_pct)}
                  />
                </td>
                <td className="num">{fmtCurrency(ws.actual_monthly_cost, ws.actual_currency)}</td>
                <td className="num">{fmtCurrency(ws.fabric_estimated_monthly_cost, ws.actual_currency)}</td>
                <td>
                  {ws.recommended_fabric_sku ?? "—"}
                  {(ws.capacity_warnings?.length ?? 0) > 0 && (
                    <div>
                      <span
                        className="pill warn"
                        title={ws.capacity_warnings?.join("\n")}
                      >
                        estimate caveat
                      </span>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="report-section">
        <h2>Top blockers across the estate</h2>
        {report.top_blockers.length === 0 ? (
          <p className="muted small">No open blockers.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Area</th>
                <th>Title</th>
                <th>Fabric action</th>
                <th>Effort</th>
                <th>Workspaces</th>
                <th>Occurrences</th>
              </tr>
            </thead>
            <tbody>
              {report.top_blockers.map((b, i) => (
                <tr key={`${b.area}|${b.title}|${i}`}>
                  <td>{areaLabel(b.area)}</td>
                  <td>{b.title}</td>
                  <td>{b.fabric_action ?? "—"}</td>
                  <td>{effortLabel(b.effort)}</td>
                  <td className="num">{b.workspaces}</td>
                  <td className="num">{b.occurrences}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {bundles.map(({ ws, fm, cost, spark }) => (
        <WorkspacePage key={ws.key} ws={ws} fm={fm} cost={cost} spark={spark} />
      ))}
    </div>
  );
}

function WorkspacePage({
  ws,
  fm,
  cost,
  spark,
}: {
  ws: EstateWorkspace;
  fm: FabricMappingReport | null;
  cost: CostReport | null;
  spark: SparkPoolsReport | null;
}) {
  const rd = fm?.readiness;
  const cp = fm?.capacity_projection;
  const sevCount = (s: Severity) =>
    rd?.counts?.[s] ??
    (fm?.recommendations.filter((r) => r.severity === s).length ?? 0);
  const fabricCmp = cost?.fabric_comparison;
  return (
    <div className="report-workspace">
      <h1>{ws.workspace_name}</h1>
      <div className="muted small">
        {ws.tenant_id ? `Tenant ${ws.tenant_id} · ` : ""}
        {ws.subscription_id ? `Subscription ${ws.subscription_id} · ` : ""}
        {ws.resource_group ? `RG ${ws.resource_group}` : ""}
        {ws.latest_finished_at && (
          <> · Latest scan {ws.latest_finished_at.replace("T", " ").slice(0, 16)}</>
        )}
      </div>

      <section className="report-section" style={{ marginTop: "0.75rem" }}>
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
                : "no code objects"
            }
          />
          <StatCard
            label="Recommendations"
            value={fm?.recommendations.length ?? 0}
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
            value={cp?.recommended_sku ?? "—"}
            sub={
              cp
                ? (() => {
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
                    const sparkSummary = aggregateSparkWindow(
                      spark?.run_stats ?? [],
                      undefined,
                      !spark?.accounting_basis,
                    );
                    const sparkSizingCu = cp.spark_steady_state_cu !== undefined
                      ? cp.spark_steady_state_cu
                      : sparkSummary?.steadyStateCu ?? null;
                    const sparkSizing = !spark && cp.spark_steady_state_cu === undefined
                      ? { included: true, warning: null }
                      : getSparkSizingStatus({
                          accountingBasis: spark?.accounting_basis,
                          collectionComplete: spark?.collection_complete,
                          unknownUsageRunCount: sparkSummary?.unknownUsageRunCount ?? null,
                          avgDailyCuHours: sparkSummary?.avgDailyCuHours ?? null,
                          steadyStateCu: sparkSizingCu,
                        });
                    const parts: string[] = [];
                    if (dwuCu > 0) parts.push(`DW ${fmtCu(dwuCu)}`);
                    if (sparkCu > 0 && sparkSizing.included) parts.push(`Spark ${fmtCu(sparkCu)}`);
                    if (pipeCu > 0) parts.push(`Pipelines ${fmtCu(pipeCu)}`);
                    if (slessCu > 0) parts.push(`Serverless ${fmtCu(slessCu)}`);
                    const breakdown = parts.length > 0 ? ` · ${parts.join(" + ")} CU` : "";
                    const slessNote = slessPeakDayCuH > 0
                      ? ` · Serverless peak day ≈ ${slessPeakDayCuH.toFixed(2)} CU-h (smoothed over 24 h)`
                      : "";
                    const sparkWarnings = Array.from(new Set([
                      ...(cp.spark_accounting_warnings ?? []),
                      ...(sparkSizing.warning ? [sparkSizing.warning] : []),
                    ]));
                    const sparkWarning = sparkWarnings.length > 0
                      ? ` · ${sparkWarnings.join(" · ")}`
                      : "";
                    return `${cp.estimated_cu.toFixed(1)} CU (${cp.headroom_pct}% headroom)${breakdown}${slessNote}${sparkWarning}`;
                  })()
                : "no monitoring data"
            }
          />
        </div>
      </section>

      {rd?.top_blockers && rd.top_blockers.length > 0 && (
        <section className="report-section">
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
                  <td className="small">{effortLabel(b.effort)}</td>
                  <td className="small">{areaLabel(b.area)}</td>
                  <td>{b.title}</td>
                  <td className="small muted">{b.fabric_action ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {fm && fm.recommendations.length > 0 && (
        <section className="report-section">
          <h2>All recommendations ({fm.recommendations.length})</h2>
          <table>
            <thead>
              <tr>
                <th>Sev</th>
                <th>Effort</th>
                <th>Area</th>
                <th>Title</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {fm.recommendations.map((r) => (
                <tr key={r.id}>
                  <td><SeverityPill severity={r.severity} /></td>
                  <td className="small">{effortLabel(r.effort)}</td>
                  <td className="small">{areaLabel(r.area)}</td>
                  <td>{r.title}</td>
                  <td className="small muted">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {cost && (
        <section className="report-section">
          <h2>Cost summary</h2>
          <div className="grid cols-4">
            <StatCard
              label="Avg monthly (Synapse)"
              value={fmtCurrency(
                fabricCmp?.synapse_avg_monthly_cost,
                ws.actual_currency,
              )}
            />
            <StatCard
              label="Modeled Fabric SKU"
              value={fabricCmp?.fabric_capacity_sku ?? "—"}
              sub={fmtCurrency(
                fabricCmp?.fabric_estimated_monthly_cost,
                ws.actual_currency,
              )}
            />
            <StatCard
              label="Δ Abs"
              value={fmtCurrency(fabricCmp?.delta_abs, ws.actual_currency)}
            />
            <StatCard
              label="Δ %"
              value={
                fabricCmp?.delta_pct == null
                  ? "—"
                  : `${fabricCmp.delta_pct.toFixed(1)}%`
              }
            />
          </div>
        </section>
      )}

      <SparkAccountingPrintReport spark={spark} capacity={cp} />

      {fm?.runbook && fm.runbook.length > 0 && (
        <section className="report-section">
          <h2>Runbook ({fm.runbook.length} steps)</h2>
          <ol>
            {fm.runbook.map((step, i) => (
              <li key={i} className="small">
                <strong>{severityLabel(step.severity ?? "info")}</strong>
                {" — "}
                {step.title}
                {step.detail ? <span className="muted"> · {step.detail}</span> : null}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

function SparkAccountingPrintReport({
  spark,
  capacity,
}: {
  spark: SparkPoolsReport | null;
  capacity: FabricMappingReport["capacity_projection"];
}) {
  const warnings = Array.from(new Set([
    ...(spark?.accounting_warnings ?? []),
    ...(capacity?.spark_accounting_warnings ?? []),
  ]));
  if (!spark && warnings.length === 0 &&
      !(capacity?.spark_steady_state_cu && capacity.spark_steady_state_cu > 0)) {
    return null;
  }

  const totals = aggregateSparkWindow(
    spark?.run_stats ?? [],
    undefined,
    !spark?.accounting_basis,
  );
  const sparkSizingCu = capacity?.spark_steady_state_cu !== undefined
    ? capacity.spark_steady_state_cu
    : totals?.steadyStateCu ?? null;
  const sparkSizing = !spark && capacity?.spark_steady_state_cu === undefined
    ? { included: true, warning: null }
    : getSparkSizingStatus({
        accountingBasis: spark?.accounting_basis,
        collectionComplete: spark?.collection_complete,
        unknownUsageRunCount: totals?.unknownUsageRunCount ?? null,
        avgDailyCuHours: totals?.avgDailyCuHours ?? null,
        steadyStateCu: sparkSizingCu,
      });
  const dailyWindowDays = totals?.windowDays ?? sparkObservationWindowDays(
    spark?.observation_start,
    spark?.observation_end,
  );
  const dailyUsage = filterSparkDailyUsageToWindow(
    spark?.daily_usage,
    spark?.observation_end,
    dailyWindowDays,
  );
  const basis = spark?.accounting_basis?.trim() || "legacy or unavailable";
  const fixedShape = /fixed.?shape|shape.?estimate/i.test(basis);
  const coverage =
    spark?.collection_complete === true
      ? "complete"
      : spark?.collection_complete === false
        ? "incomplete"
        : "unknown";
  if (
    coverage !== "complete" &&
    !warnings.some((warning) => /coverage|missing telem/i.test(warning))
  ) {
    warnings.push(
      coverage === "incomplete"
        ? "Collection is incomplete; missing telemetry is not zero usage."
        : "Collection coverage is unknown; missing telemetry is not zero usage.",
    );
  }
  if (sparkSizing.warning && !warnings.includes(sparkSizing.warning)) {
    warnings.push(sparkSizing.warning);
  }

  return (
    <section className="report-section">
      <h2>Spark accounting and coverage</h2>
      <p className="small">
        <strong>Basis:</strong>{" "}
        {fixedShape
          ? "fixed-shape estimate from recorded driver/executor shape and runtime; not measured billing"
          : "resource-time estimate; not measured Synapse billing"}{" "}
        <code>{basis}</code>
      </p>
      <p className="small">
        <strong>Observation period:</strong>{" "}
        {spark?.observation_start ?? "unknown"} – {spark?.observation_end ?? "unknown"}
        {" · "}
        <strong>Collection coverage:</strong> {coverage}
      </p>
      <p className="small">
        Fabric Spark sizing assumption: 0.5 estimated CU-hours per accounted
        vCore-hour (1 CU = 2 Spark vCores); this does not establish equal
        performance or billed consumption.
      </p>
      <div className="grid cols-4">
        <StatCard
          label="Avg daily vCore-hours"
          value={totals?.avgDailyVcoreHours == null
            ? "—"
            : fmtNum(totals.avgDailyVcoreHours, 2)}
          sub={totals ? `${totals.windowDays}-day common run window` : "no common run window"}
        />
        <StatCard
          label="Avg daily estimated CU-hours"
          value={totals?.avgDailyCuHours == null
            ? "—"
            : fmtNum(totals.avgDailyCuHours, 2)}
        />
        <StatCard
          label="Spark steady-state CU"
          value={sparkSizing.included ? fmtNum(sparkSizingCu, 3) : "—"}
          sub="pre-headroom; CU, not CU/day"
        />
        <StatCard
          label="Known / unknown usage runs"
          value={totals?.knownUsageRunCount != null &&
              totals.unknownUsageRunCount != null
            ? `${totals.knownUsageRunCount} / ${totals.unknownUsageRunCount}`
            : "—"}
        />
        <StatCard
          label="Peak-day Spark CU-hours"
          value={capacity?.spark_peak_day_cu_hours == null
            ? "—"
            : fmtNum(capacity.spark_peak_day_cu_hours, 2)}
          sub="diagnostic only; not the steady-state baseline"
        />
      </div>
      {capacity?.spark_daily_cu_hours != null && (
        <p className="small muted">
          Capacity projection input: {fmtNum(capacity.spark_daily_cu_hours, 2)} estimated CU-hours/day
          {capacity.spark_window_days != null ? ` over ${capacity.spark_window_days} observed days` : ""}
          {capacity.headroom_pct != null ? ` · ${capacity.headroom_pct}% headroom is applied separately` : ""}.
        </p>
      )}
      {warnings.length > 0 && (
        <ul className="small">
          {warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}
        </ul>
      )}
      {dailyUsage.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>UTC day ({dailyWindowDays}-day common/observation window)</th>
              <th className="num">Accounted vCore-hours</th>
              <th className="num">Estimated Fabric Spark CU-hours</th>
            </tr>
          </thead>
          <tbody>
            {dailyUsage.map((day) => (
              <tr key={day.day}>
                <td>{day.day}</td>
                <td className="num">{fmtNum(day.total_vcore_hours, 2)}</td>
                <td className="num">{fmtNum(day.est_cu_hours_fabric_spark, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
