import { useMemo, useState } from "react";
import { loadCost } from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty, SeverityPill, StatCard } from "../components/Atoms";
import HelpLink from "../components/HelpLink";

const SEV_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

function fmtCurrency(n: number | null | undefined, currency = "USD"): string {
  if (n == null || !Number.isFinite(n)) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `${currency} ${n.toFixed(2)}`;
  }
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

export default function Cost() {
  const { data, loading } = useAsync(loadCost);
  const [filter, setFilter] = useState("");
  const [sev, setSev] = useState("");

  const findings = useMemo(() => {
    if (!data) return [];
    const q = filter.trim().toLowerCase();
    return [...data.findings]
      .filter((f) => {
        if (sev && f.severity !== sev) return false;
        if (!q) return true;
        return [f.rule_id, f.title, f.detail ?? "", f.resource ?? ""]
          .join(" ").toLowerCase().includes(q);
      })
      .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  }, [data, filter, sev]);

  if (loading) return <div className="empty">Loading…</div>;
  if (!data) return (
    <>
      <h1>Cost <HelpLink slug="15-faq" /></h1>
      <Empty>
        No <code>cost.json</code> in this run. Run with <code>--include cost</code>
        and ensure the <strong>Cost Management Reader</strong> role plus
        <code> pip install -e ".[cost]"</code>.
      </Empty>
    </>
  );

  const status = data.collection_status;
  const months = Object.keys(data.monthly_totals).sort();
  const totalCost = months.reduce((s, m) => s + (data.monthly_totals[m] ?? 0), 0);
  const avgMonthly = months.length ? totalCost / months.length : 0;
  const currency = data.rows[0]?.currency || "USD";
  const fc = data.fabric_comparison ?? null;
  const byKind = Object.entries(data.by_resource_kind).sort((a, b) => b[1] - a[1]);
  const byResource = Object.entries(data.by_resource_name).sort((a, b) => b[1] - a[1]).slice(0, 25);

  // Month-over-month deltas.
  const monthRows = months.map((m, i) => {
    const cur = data.monthly_totals[m] ?? 0;
    const prev = i > 0 ? (data.monthly_totals[months[i - 1]] ?? 0) : null;
    const delta = prev != null && prev > 0 ? ((cur - prev) / prev) * 100 : null;
    return { month: m, cost: cur, delta };
  });

  return (
    <>
      <h1>Cost <HelpLink slug="15-faq" /></h1>
      {status !== "ok" && (
        <div className="empty" style={{ marginBottom: 12 }}>
          Collection status: <code>{status}</code>
          {status === "sdk_missing" && <> — install <code>pip install -e ".[cost]"</code>.</>}
          {status === "live_disabled" && <> — set <code>SMA_COST_DISABLE_LIVE=0</code> to enable.</>}
          {status === "empty_window" && <> — Cost Management returned no rows for this window.</>}
          {status === "error" && <> — see <code>errors[]</code> in <code>cost.json</code>.</>}
        </div>
      )}

      <div className="grid cols-4">
        <StatCard
          label="Months observed"
          value={months.length}
          sub={months.length ? `${months[0]} → ${months[months.length - 1]}` : "—"}
        />
        <StatCard
          label="Total cost (window)"
          value={fmtCurrency(totalCost, currency)}
          sub={`${data.rows.length} rows`}
        />
        <StatCard
          label="Avg monthly"
          value={fmtCurrency(avgMonthly, currency)}
          sub={`${Object.keys(data.by_resource_kind).length} resource kinds`}
        />
        <StatCard
          label="Findings"
          value={data.findings.length}
          sub={data.findings.length
            ? `${data.findings.filter((f) => f.severity === "high").length} high · ${data.findings.filter((f) => f.severity === "medium").length} medium`
            : "no findings"}
        />
      </div>

      {fc && (
        <section className="section">
          <h2>Fabric comparison</h2>
          <div className="grid cols-3">
            <StatCard
              label="Synapse latest month"
              value={fmtCurrency(fc.synapse_avg_monthly_cost, currency)}
            />
            <StatCard
              label="Fabric estimate"
              value={fmtCurrency(fc.fabric_estimated_monthly_cost, currency)}
              sub={fc.fabric_capacity_sku || "—"}
            />
            <StatCard
              label="Delta"
              value={fmtPct(fc.delta_pct)}
              sub={fc.delta_abs != null ? fmtCurrency(fc.delta_abs, currency) : "—"}
            />
          </div>
          {fc.notes && <p className="small muted">{fc.notes}</p>}
        </section>
      )}

      <section className="section">
        <h2>Findings</h2>
        <div className="toolbar">
          <input
            placeholder="Filter (rule id, title, detail, resource)"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <select value={sev} onChange={(e) => setSev(e.target.value)}>
            <option value="">All severities</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
            <option value="info">Info</option>
          </select>
          <span className="muted small">
            {findings.length} / {data.findings.length}
          </span>
        </div>
        {findings.length === 0
          ? <Empty>No findings.</Empty>
          : (
            <table>
              <thead>
                <tr>
                  <th>Severity</th><th>Rule</th><th>Title</th><th>Resource</th>
                </tr>
              </thead>
              <tbody>
                {findings.map((f) => (
                  <tr key={f.rule_id + (f.resource ?? "")}>
                    <td><SeverityPill severity={f.severity} /></td>
                    <td><code className="small">{f.rule_id}</code></td>
                    <td>
                      <details>
                        <summary>{f.title}</summary>
                        {f.detail && <div className="small" style={{ marginTop: 6 }}>{f.detail}</div>}
                      </details>
                    </td>
                    <td className="small muted">{f.resource ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </section>

      <section className="section">
        <h2>Monthly totals</h2>
        {monthRows.length === 0
          ? <Empty>No monthly totals.</Empty>
          : (
            <table>
              <thead>
                <tr>
                  <th>Month</th>
                  <th className="num">Cost</th>
                  <th className="num">vs prev</th>
                </tr>
              </thead>
              <tbody>
                {monthRows.map((r) => (
                  <tr key={r.month}>
                    <td><code>{r.month}</code></td>
                    <td className="num">{fmtCurrency(r.cost, currency)}</td>
                    <td className="num">{fmtPct(r.delta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </section>

      <section className="section">
        <h2>By resource kind</h2>
        {byKind.length === 0
          ? <Empty>No rows.</Empty>
          : (
            <table>
              <thead>
                <tr><th>Kind</th><th className="num">Cost</th><th className="num">% of total</th></tr>
              </thead>
              <tbody>
                {byKind.map(([k, v]) => (
                  <tr key={k}>
                    <td><code>{k}</code></td>
                    <td className="num">{fmtCurrency(v, currency)}</td>
                    <td className="num">{totalCost > 0 ? `${((v / totalCost) * 100).toFixed(1)}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </section>

      <section className="section">
        <h2>Top resources</h2>
        {byResource.length === 0
          ? <Empty>No rows.</Empty>
          : (
            <table>
              <thead>
                <tr><th>Resource</th><th className="num">Cost</th></tr>
              </thead>
              <tbody>
                {byResource.map(([k, v]) => (
                  <tr key={k}>
                    <td className="small"><code>{k}</code></td>
                    <td className="num">{fmtCurrency(v, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        {Object.keys(data.by_resource_name).length > byResource.length && (
          <div className="small muted" style={{ marginTop: 6 }}>
            Showing top {byResource.length} of {Object.keys(data.by_resource_name).length} resources.
          </div>
        )}
      </section>
    </>
  );
}
