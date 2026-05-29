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
  Severity,
} from "../types";

interface WorkspaceBundle {
  ws: EstateWorkspace;
  fm: FabricMappingReport | null;
  cost: CostReport | null;
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
            const [fm, cost] = await Promise.all([
              apiGetRunModule<FabricMappingReport>(ws.latest_run_id, "fabric_mapping"),
              apiGetRunModule<CostReport>(ws.latest_run_id, "cost"),
            ]);
            return { ws, fm, cost } as WorkspaceBundle;
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
                <td>{ws.recommended_fabric_sku ?? "—"}</td>
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

      {bundles.map(({ ws, fm, cost }) => (
        <WorkspacePage key={ws.key} ws={ws} fm={fm} cost={cost} />
      ))}
    </div>
  );
}

function WorkspacePage({
  ws,
  fm,
  cost,
}: {
  ws: EstateWorkspace;
  fm: FabricMappingReport | null;
  cost: CostReport | null;
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
