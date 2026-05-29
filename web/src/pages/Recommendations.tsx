import { useMemo, useState } from "react";
import { loadModulePerScope, type RunScope } from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty, SeverityPill, StatCard } from "../components/Atoms";
import HelpLink from "../components/HelpLink";
import ScopeFilter, {
  matchesScope,
  useScopeFilter,
} from "../components/ScopeFilter";
import { useCurrentRunMeta } from "../components/Provenance";
import { areaLabel, effortLabel, impactLabel, sourceLabel } from "../lib/labels";
import type { FabricMappingReport, Recommendation } from "../types";

const SEV_ORDER = { blocker: 0, warning: 1, info: 2 } as Record<string, number>;
const IMPACT_ORDER = { high: 0, medium: 1, low: 2, unknown: 3 } as Record<string, number>;

type GroupMode = "none" | "area" | "target";

function ImpactPill({ impact }: { impact?: string | null }) {
  const value = impact ?? "unknown";
  const cls =
    value === "high" ? "err"
    : value === "medium" ? "warn"
    : value === "low" ? "ok"
    : "muted";
  return <span className={`pill ${cls}`} title={`Business impact: ${impactLabel(value)}`}>{impactLabel(value)}</span>;
}

function RecRow({ r }: { r: ScopedRecommendation }) {
  return (
    <tr>
      <td><SeverityPill severity={r.severity} /></td>
      <td><ImpactPill impact={r.impact} /></td>
      <td title={r.effort}>{effortLabel(r.effort)}</td>
      <td className="small" title={r.area}>{areaLabel(r.area)}</td>
      <td className="small muted">
        {r._scopeLabel ? (
          <span
            className="pill muted"
            style={{ marginRight: 6 }}
            title={`Scope: ${r._scopeLabel}`}
          >
            {r._scopeLabel}
          </span>
        ) : null}
        {r.target}
      </td>
      <td>
        <details>
          <summary>{r.title}</summary>
          <div className="small" style={{ marginTop: 6 }}>{r.detail}</div>
          {r.impact_detail ? (
            <div className="small muted" style={{ marginTop: 4 }}>
              <strong>Impact:</strong> {r.impact_detail}
            </div>
          ) : null}
        </details>
      </td>
      <td className="small muted">{r.fabric_action}</td>
    </tr>
  );
}

/** Recommendation decorated with the scope it was produced in. */
type ScopedRecommendation = Recommendation & {
  _scopeDir: string | null;
  _scopeLabel: string | null;
};

type RecsBundle = {
  /** Every scoped recommendation across every scope, in load order. */
  all: ScopedRecommendation[];
  /** Scopes carried by the per-scope loader (empty in static / single-scope). */
  scopes: RunScope[];
  /** workspace_name to surface in the page header when single-scope. */
  workspaceName: string | null;
};

async function loadRecsBundle(): Promise<RecsBundle | null> {
  const perScope = await loadModulePerScope<FabricMappingReport>("fabric_mapping");
  const all: ScopedRecommendation[] = [];
  const scopes: RunScope[] = [];
  let workspaceName: string | null = null;
  for (const { scope, payload } of perScope) {
    if (!payload) continue;
    if (scope) scopes.push(scope);
    if (!workspaceName && payload.workspace_name) workspaceName = payload.workspace_name;
    const scopeLabel = scope ? `${sourceLabel(scope.source_type)} · ${scope.slug}` : null;
    for (const r of payload.recommendations ?? []) {
      all.push({
        ...r,
        _scopeDir: scope?.dir ?? null,
        _scopeLabel: scopeLabel,
      });
    }
  }
  if (all.length === 0 && scopes.length === 0 && workspaceName === null) {
    return null;
  }
  return { all, scopes, workspaceName };
}

export default function Recommendations() {
  const { data, loading } = useAsync(loadRecsBundle);
  const runMeta = useCurrentRunMeta();
  const [filter, setFilter] = useState("");
  const [sev, setSev] = useState("");
  const [area, setArea] = useState("");
  const [impact, setImpact] = useState("");
  const [groupBy, setGroupBy] = useState<GroupMode>("none");

  const scopes = data?.scopes ?? [];
  const { selection, setSelection } = useScopeFilter(runMeta?.id ?? null, scopes);

  // Per-scope counts for the pill row, computed against the active filter
  // (severity / area / search) so users see how many items each scope
  // would surface if picked.
  const filterFn = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (r: ScopedRecommendation): boolean => {
      if (sev && r.severity !== sev) return false;
      if (area && r.area !== area) return false;
      if (impact && (r.impact ?? "unknown") !== impact) return false;
      if (!q) return true;
      return [r.id, r.title, r.detail, r.area, r.target ?? "", r.fabric_action ?? "", r.impact_detail ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(q);
    };
  }, [filter, sev, area, impact]);

  const scopeCounts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of data?.all ?? []) {
      if (!filterFn(r)) continue;
      if (!r._scopeDir) continue;
      out[r._scopeDir] = (out[r._scopeDir] ?? 0) + 1;
    }
    return out;
  }, [data, filterFn]);

  const filtered = useMemo<ScopedRecommendation[]>(() => {
    if (!data) return [];
    return data.all
      .filter((r) => {
        if (!matchesScope(r._scopeDir ? ({ dir: r._scopeDir } as RunScope) : null, selection))
          return false;
        return filterFn(r);
      })
      .sort(
        (a, b) =>
          (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9) ||
          (IMPACT_ORDER[a.impact ?? "unknown"] ?? 9) - (IMPACT_ORDER[b.impact ?? "unknown"] ?? 9) ||
          a.area.localeCompare(b.area),
      );
  }, [data, filterFn, selection]);

  const counts = useMemo(() => {
    const out = { blocker: 0, warning: 0, info: 0, high: 0, medium: 0, low: 0, unknown: 0 };
    for (const r of filtered) {
      out[r.severity as "blocker" | "warning" | "info"] += 1;
      const imp = (r.impact ?? "unknown") as keyof typeof out;
      out[imp] += 1;
    }
    return out;
  }, [filtered]);

  const grouped = useMemo(() => {
    if (groupBy === "none") return null;
    const map = new Map<string, ScopedRecommendation[]>();
    for (const r of filtered) {
      const key = groupBy === "area" ? areaLabel(r.area) : (r.target?.trim() || "(no target)");
      const list = map.get(key) ?? [];
      list.push(r);
      map.set(key, list);
    }
    return Array.from(map.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [filtered, groupBy]);

  if (loading) return <div className="empty">Loading…</div>;
  if (!data || data.all.length === 0)
    return <Empty>No recommendations to show.</Empty>;

  const areas = Array.from(new Set(data.all.map((r) => r.area))).sort();

  return (
    <>
      <h1>Recommendations <HelpLink slug="06-recommendations" /></h1>

      <ScopeFilter
        scopes={scopes}
        selection={selection}
        onChange={setSelection}
        runMeta={runMeta}
        counts={scopeCounts}
        totalCount={data.all.filter(filterFn).length}
      />

      {/* v2.11 — severity + impact rollup tiles, computed against the
          current filter so the numbers reflect what the user is looking at. */}
      <div className="grid" style={{ marginBottom: 12, display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 8 }}>
        <StatCard label="Blockers" value={counts.blocker} />
        <StatCard label="Warnings" value={counts.warning} />
        <StatCard label="Info" value={counts.info} />
        <StatCard label="High impact" value={counts.high} />
        <StatCard label="Medium" value={counts.medium} />
        <StatCard label="Low / unknown" value={counts.low + counts.unknown} />
      </div>

      <div className="toolbar">
        <input
          placeholder="Filter (id, title, detail, target)"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <select value={sev} onChange={(e) => setSev(e.target.value)}>
          <option value="">All severities</option>
          <option value="blocker">Blocker</option>
          <option value="warning">Warning</option>
          <option value="info">Info</option>
        </select>
        <select value={impact} onChange={(e) => setImpact(e.target.value)}>
          <option value="">All impact</option>
          <option value="high">High impact</option>
          <option value="medium">Medium impact</option>
          <option value="low">Low impact</option>
          <option value="unknown">Unknown impact</option>
        </select>
        <select value={area} onChange={(e) => setArea(e.target.value)}>
          <option value="">All areas</option>
          {areas.map((a) => <option key={a} value={a}>{areaLabel(a)}</option>)}
        </select>
        <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupMode)}>
          <option value="none">No grouping</option>
          <option value="area">Group by area</option>
          <option value="target">Group by target</option>
        </select>
        <span className="muted small">
          {filtered.length} / {data.all.length}
        </span>
      </div>

      {grouped ? (
        grouped.map(([key, items]) => (
          <details key={key} open style={{ marginBottom: 12 }}>
            <summary>
              <strong>{key}</strong>{" "}
              <span className="muted small">
                — {items.length} item{items.length === 1 ? "" : "s"}
              </span>
            </summary>
            <table>
              <thead>
                <tr>
                  <th>Severity</th><th>Impact</th><th>Effort</th><th>Area</th><th>Target</th>
                  <th>Title</th><th>Fabric action</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => <RecRow key={r.id} r={r} />)}
              </tbody>
            </table>
          </details>
        ))
      ) : (
        <table>
          <thead>
            <tr>
              <th>Severity</th><th>Impact</th><th>Effort</th><th>Area</th><th>Target</th>
              <th>Title</th><th>Fabric action</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => <RecRow key={r.id} r={r} />)}
          </tbody>
        </table>
      )}
    </>
  );
}

