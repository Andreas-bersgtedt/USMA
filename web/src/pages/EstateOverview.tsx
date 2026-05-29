import { Link } from "react-router-dom";
import {
  apiEstateCsvUrl,
  apiGetEstate,
  detectMode,
  setRunIdInHash,
} from "../api/loader";
import { Empty, PctPill, ScorePill, SeverityPill, StatCard } from "../components/Atoms";
import HelpLink from "../components/HelpLink";
import { useAsync } from "../hooks/useAsync";
import { areaLabel, effortLabel, sourceLabel } from "../lib/labels";
import type { EstateHistoryPoint, EstateWorkspace } from "../types";

function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtCurrency(n: number | null | undefined, currency: string | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const code = (currency ?? "USD").toUpperCase();
  try {
    return n.toLocaleString(undefined, {
      style: "currency",
      currency: code,
      maximumFractionDigits: 0,
    });
  } catch {
    return `${code} ${fmtNum(n)}`;
  }
}

// Fabric F-SKUs in ascending CU. Pick the smallest one whose CU >= total.
const F_SKUS = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048];

function minFabricSku(totalCu: number | null | undefined): string {
  if (totalCu == null || !Number.isFinite(totalCu) || totalCu <= 0) return "—";
  for (const cu of F_SKUS) {
    if (cu >= totalCu) return `F${cu}`;
  }
  return `F${F_SKUS[F_SKUS.length - 1]}+`;
}

function Sparkline({
  history,
  width = 96,
  height = 24,
}: {
  history: EstateHistoryPoint[];
  width?: number;
  height?: number;
}) {
  const points = history
    .map((h) => h.readiness_score)
    .filter((v): v is number => v != null && Number.isFinite(v));
  if (points.length < 2) {
    return <span className="muted" title="Not enough history">—</span>;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const stepX = points.length === 1 ? 0 : width / (points.length - 1);
  const path = points
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / span) * height;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = points[points.length - 1];
  const trend = last >= points[0] ? "ok" : "warn";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={`sparkline ${trend}`}
      role="img"
      aria-label={`Readiness trend across ${points.length} runs`}
    >
      <path d={path} fill="none" strokeWidth={1.5} />
    </svg>
  );
}

function SourceMixPie({
  slices,
}: {
  slices: { key: string; label: string; count: number }[];
}) {
  const total = slices.reduce((s, x) => s + x.count, 0);
  // Brand-aligned palette; cycle if more than 5 source types.
  const palette = [
    "var(--info)",
    "var(--ok)",
    "var(--warn)",
    "var(--err)",
    "var(--muted)",
  ];
  const r = 38;
  const cx = 44;
  const cy = 44;

  // Single-slice special case — a full circle can't be drawn with one arc
  // path so we render a plain <circle> instead.
  if (slices.length === 1 || total === 0) {
    const only = slices[0];
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <svg width={88} height={88} viewBox="0 0 88 88" role="img" aria-label="Source mix">
          <circle cx={cx} cy={cy} r={r} fill={palette[0]} />
        </svg>
        <ul className="pie-legend">
          <li>
            <span className="pie-swatch" style={{ background: palette[0] }} />
            {only?.label ?? "—"} <span className="muted">({only?.count ?? 0})</span>
          </li>
        </ul>
      </div>
    );
  }

  let cumulative = 0;
  const arcs = slices.map((s, i) => {
    const startAngle = (cumulative / total) * Math.PI * 2 - Math.PI / 2;
    cumulative += s.count;
    const endAngle = (cumulative / total) * Math.PI * 2 - Math.PI / 2;
    const x1 = cx + r * Math.cos(startAngle);
    const y1 = cy + r * Math.sin(startAngle);
    const x2 = cx + r * Math.cos(endAngle);
    const y2 = cy + r * Math.sin(endAngle);
    const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
    const pct = Math.round((s.count / total) * 100);
    return {
      d: `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${largeArc} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`,
      color: palette[i % palette.length],
      key: s.key,
      label: s.label,
      count: s.count,
      pct,
    };
  });

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <svg width={88} height={88} viewBox="0 0 88 88" role="img" aria-label="Source mix">
        {arcs.map((a) => (
          <path key={a.key} d={a.d} fill={a.color}>
            <title>{`${a.label}: ${a.count} (${a.pct}%)`}</title>
          </path>
        ))}
      </svg>
      <ul className="pie-legend">
        {arcs.map((a) => (
          <li key={a.key}>
            <span className="pie-swatch" style={{ background: a.color }} />
            {a.label} <span className="muted">({a.count} · {a.pct}%)</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function EstateOverview() {
  const { data: mode } = useAsync(detectMode);
  const { data: report, loading, error } = useAsync(() => apiGetEstate());

  if (mode && mode !== "control-plane") {
    return (
      <Empty>
        Estate overview is only available in control-plane mode (
        <code>sma serve --with-api</code>). Static-mode SPAs read a single
        run's <code>fabric_mapping.json</code> and have no way to enumerate
        other scopes.
      </Empty>
    );
  }
  if (loading) return <div className="empty">Loading estate…</div>;
  if (error) {
    return (
      <Empty>
        Couldn't load <code>/api/estate</code>: {String(error)}
      </Empty>
    );
  }
  if (!report || report.totals.workspaces === 0) {
    return (
      <Empty>
        No completed runs found yet. Kick off a scan from the{" "}
        <Link to="/run">Run</Link> page — the overview rolls up every run on
        disk, so as soon as one finishes it'll appear here.
      </Empty>
    );
  }

  const t = report.totals;
  const cur = report.workspaces.find((w) => w.actual_currency)?.actual_currency ?? "USD";

  // Source-type mix across scopes, sorted high-to-low for the pie chart.
  const sourceMix = (() => {
    const counts = new Map<string, number>();
    for (const ws of report.workspaces) {
      const key = ws.source_type ?? "unknown";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const slices = [...counts.entries()]
      .sort(([, a], [, b]) => b - a)
      .map(([key, count]) => ({
        key,
        label: key === "unknown" ? "\u2014" : sourceLabel(key),
        count,
      }));
    const sub =
      slices.length === 0
        ? ""
        : slices.length === 1
          ? "all scopes share one source type"
          : `${slices.length} source types in this estate`;
    return { slices, sub };
  })();

  // Group rows by hyperscaler → tenant + subscription so multi-cloud
  // estates read cleanly and Azure / AWS / GCP buckets stay separated.
  const CLOUD_ORDER: Record<string, number> = {
    azure: 0,
    aws: 1,
    gcp: 2,
    on_prem: 3,
    unknown: 4,
  };
  const CLOUD_LABELS: Record<string, string> = {
    azure: "Azure",
    aws: "AWS",
    gcp: "Google Cloud",
    on_prem: "On-premises",
    unknown: "Unknown",
  };
  const cloudGroups = new Map<string, EstateWorkspace[]>();
  for (const ws of report.workspaces) {
    const c = ws.cloud ?? "unknown";
    const arr = cloudGroups.get(c) ?? [];
    arr.push(ws);
    cloudGroups.set(c, arr);
  }
  const orderedCloudGroups = [...cloudGroups.entries()].sort(
    ([a], [b]) =>
      (CLOUD_ORDER[a] ?? 99) - (CLOUD_ORDER[b] ?? 99) || a.localeCompare(b),
  );
  // Within each hyperscaler, group by tenant + subscription so the
  // existing multi-tenant readability is preserved.
  const orderedCloudSubGroups: Array<{
    cloud: string;
    groups: Array<[string, EstateWorkspace[]]>;
  }> = orderedCloudGroups.map(([cloud, scopes]) => {
    const inner = new Map<string, EstateWorkspace[]>();
    for (const ws of scopes) {
      const k = `${ws.tenant_id ?? "—"} · ${ws.subscription_id ?? "—"}`;
      const arr = inner.get(k) ?? [];
      arr.push(ws);
      inner.set(k, arr);
    }
    for (const arr of inner.values()) {
      arr.sort((a, b) =>
        (a.workspace_name || "").localeCompare(b.workspace_name || ""),
      );
    }
    return {
      cloud,
      groups: [...inner.entries()].sort(([a], [b]) => a.localeCompare(b)),
    };
  });

  return (
    <div>
      <h1>
        Estate overview <HelpLink slug="16-overview" />
      </h1>
      <p className="muted">
        Cross-scope, cross-time rollup of every analyzer run on disk.
        Headline numbers come from each scope's most recent successful
        run; trend uses the last 50 runs per scope.
      </p>

      <div className="grid cols-4">
        <StatCard label="Scopes" value={fmtNum(t.workspaces)} />
        <div className="card">
          <div className="label">Source mix</div>
          <SourceMixPie slices={sourceMix.slices} />
          {sourceMix.sub && <div className="sub">{sourceMix.sub}</div>}
        </div>
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
          sub="across latest run per workspace"
        />
        <StatCard
          label="Avg T-SQL compatibility"
          value={
            t.tsql_compatibility_pct_avg == null ? (
              "—"
            ) : (
              <PctPill pct={Math.round(t.tsql_compatibility_pct_avg)} />
            )
          }
        />
        <StatCard
          label="Estimated SKU needed"
          value={minFabricSku(t.projected_fabric_cu_total)}
          sub={
            t.projected_fabric_cu_total == null
              ? "no capacity projection yet"
              : `min F-SKU to cover ${fmtNum(t.projected_fabric_cu_total, 1)} CU`
          }
        />
        <StatCard
          label="Actual monthly spend"
          value={fmtCurrency(t.actual_monthly_cost_total, cur)}
          sub="summed across scopes (latest month per scope)"
        />
        <StatCard
          label="Projected monthly spend (Fabric)"
          value={
            t.fabric_estimated_monthly_cost_1y_ri_total != null
              ? fmtCurrency(t.fabric_estimated_monthly_cost_1y_ri_total, cur)
              : fmtCurrency(t.fabric_estimated_monthly_cost_total, cur)
          }
          sub={
            t.fabric_estimated_monthly_cost_1y_ri_total != null ? (
              <>
                1Y RI &middot; PAYG{" "}
                {fmtCurrency(t.fabric_estimated_monthly_cost_total, cur)}
                {t.fabric_estimated_monthly_cost_3y_ri_total != null && (
                  <>
                    {" \u00b7 "}3Y RI{" "}
                    {fmtCurrency(t.fabric_estimated_monthly_cost_3y_ri_total, cur)}
                  </>
                )}
              </>
            ) : t.fabric_estimated_monthly_cost_3y_ri_total != null ? (
              <>
                PAYG list price &middot; 3Y RI{" "}
                {fmtCurrency(t.fabric_estimated_monthly_cost_3y_ri_total, cur)}
              </>
            ) : (
              "modeled equivalent"
            )
          }
        />
        <StatCard
          label="Estimated migration effort"
          value={
            t.effort_hours_p50_total == null
              ? "\u2014"
              : `${fmtNum(t.effort_hours_p50_total, 0)} h P50`
          }
          sub={
            t.effort_days_p50_total != null && t.effort_days_p90_total != null
              ? `${fmtNum(t.effort_days_p50_total, 0)} / ${fmtNum(t.effort_days_p90_total, 0)} resource-days (P50 / P90)`
              : t.effort_hours_p90_total == null
              ? "sum of runbook P50 across workspaces"
              : `${fmtNum(t.effort_hours_p90_total, 0)} h P90 \u00b7 sum of latest runs`
          }
        />
      </div>

      <div className="toolbar" style={{ margin: "1rem 0 0.25rem 0" }}>
        <h2 style={{ margin: 0 }}>Scopes</h2>
        <span style={{ flex: 1 }} />
        <Link className="btn" to="/report/print" target="_blank" rel="noreferrer">
          Export PDF report
        </Link>
        <a className="btn" href={apiEstateCsvUrl()} download>
          Export CSV
        </a>
      </div>

      {orderedCloudSubGroups.map(({ cloud, groups: subGroups }) => {
        const cloudLabel = CLOUD_LABELS[cloud] ?? cloud;
        const cloudScopeCount = subGroups.reduce((n, [, r]) => n + r.length, 0);
        return (
          <div
            key={`cloud-${cloud}`}
            style={{ marginTop: "1.25rem" }}
            data-cloud={cloud}
          >
            <h3 style={{ margin: "0 0 0.5rem 0" }}>
              <span className={`pill ${cloud === "unknown" ? "muted" : "ok"}`}>
                {cloudLabel}
              </span>{" "}
              <span className="muted" style={{ fontWeight: "normal" }}>
                · {cloudScopeCount} scope{cloudScopeCount === 1 ? "" : "s"}
              </span>
            </h3>
            {subGroups.map(([groupKey, rows]) => (
              <div key={groupKey} style={{ marginTop: "0.75rem" }}>
                <div className="label" style={{ marginBottom: "0.25rem" }}>
                  <strong>Tenant · Subscription:</strong> {groupKey}
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Resource group</th>
                        <th>Scope</th>
                        <th>Source</th>
                        <th>Last scan</th>
                        <th>Score</th>
                        <th>Bucket</th>
                        <th title="Open blockers in latest run">Blk</th>
                        <th title="Open warnings in latest run">Warn</th>
                        <th>T-SQL %</th>
                        <th title="Actual monthly Synapse spend">Actual $/mo (Synapse)</th>
                        <th title="Modeled Fabric capacity monthly spend">Projected $/mo (Fabric)</th>
                        <th title="Recommended Fabric capacity SKU">Fabric SKU</th>
                        <th title="Estimated Fabric capacity units">Proj. CU</th>
                        <th title="Estimated migration effort (runbook P50 / P90 hours)">Effort (h)</th>
                        <th title="Estimated resource-days = ceil((hours / 8) × 1.15)">Days (P50/P90)</th>
                        <th>Trend</th>
                        <th>Runs</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((ws) => (
                        <tr key={ws.key}>
                          <td className="mono">{ws.resource_group ?? "—"}</td>
                          <td>
                            <strong>{ws.workspace_name}</strong>
                          </td>
                          <td>
                            {ws.source_type ? (
                              <span className="pill muted" title={ws.source_type}>
                                {sourceLabel(ws.source_type)}
                              </span>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                          <td className="mono" title={ws.latest_run_id}>
                            {ws.latest_finished_at.replace("T", " ").slice(0, 16)}
                          </td>
                          <td>
                            {ws.readiness_score == null ? (
                              <span className="muted">—</span>
                            ) : (
                              <ScorePill score={Math.round(ws.readiness_score)} />
                            )}
                          </td>
                          <td>{ws.readiness_bucket ?? "—"}</td>
                          <td>{ws.blocker_count}</td>
                          <td>{ws.warning_count}</td>
                          <td>
                            <PctPill
                              pct={
                                ws.tsql_compatibility_pct == null
                                  ? null
                                  : Math.round(ws.tsql_compatibility_pct)
                              }
                            />
                          </td>
                          <td className="num">
                            {fmtCurrency(ws.actual_monthly_cost, ws.actual_currency)}
                          </td>
                          <td className="num">
                            {fmtCurrency(
                              ws.fabric_estimated_monthly_cost,
                              ws.actual_currency,
                            )}
                          </td>
                          <td>{ws.recommended_fabric_sku ?? "—"}</td>
                          <td className="num">{fmtNum(ws.projected_fabric_cu, 1)}</td>
                          <td
                            className="num"
                            title={
                              ws.effort_hours_p90 == null
                                ? undefined
                                : `P90: ${fmtNum(ws.effort_hours_p90, 0)} h`
                            }
                          >
                            {ws.effort_hours_p50 == null
                              ? "\u2014"
                              : `${fmtNum(ws.effort_hours_p50, 0)} / ${fmtNum(ws.effort_hours_p90, 0)}`}
                          </td>
                          <td
                            className="num"
                            title="ceil((hours / 8) × 1.15) — 8 h/day plus 15 % spillage"
                          >
                            {ws.effort_days_p50 == null
                              ? "\u2014"
                              : `${ws.effort_days_p50} / ${ws.effort_days_p90 ?? "\u2014"}`}
                          </td>
                          <td>
                            <Sparkline history={ws.history} />
                          </td>
                          <td className="num">{ws.run_count}</td>
                          <td>
                            <button
                              className="btn"
                              onClick={() => {
                                setRunIdInHash(ws.latest_run_id);
                                window.location.assign("/dashboard");
                              }}
                              title={`Open ${ws.latest_run_id} in dashboard`}
                            >
                              Open latest
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        );
      })}

      <h2 style={{ marginTop: "1.5rem" }}>Top blockers across the estate</h2>
      {report.top_blockers.length === 0 ? (
        <Empty>No open blockers across any workspace.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Area</th>
                <th>Title</th>
                <th>Fabric action</th>
                <th>Effort</th>
                <th>Scopes</th>
                <th>Occurrences</th>
              </tr>
            </thead>
            <tbody>
              {report.top_blockers.map((b, i) => (
                <tr key={`${b.area}|${b.title}|${i}`}>
                  <td>
                    <SeverityPill severity="blocker" />
                  </td>
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
        </div>
      )}
    </div>
  );
}
