import { Fragment, useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { loadDedicatedPools, loadServerless } from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty, SeverityPill } from "../components/Atoms";
import HelpLink from "../components/HelpLink";
import { ServerlessTopQueriesTable } from "../components/ServerlessTopQueriesTable";
import type {
  CodeObject,
  DedicatedTopConsumedObject,
  DedicatedTopQuery,
  TsqlSurfaceGap,
  WorkloadCaptureStats,
} from "../types";

interface Row extends CodeObject {
  pool: string;
  gaps: TsqlSurfaceGap[];
}

const columnHelper = createColumnHelper<Row>();

export default function CodeObjects() {
  const { data, loading } = useAsync(loadDedicatedPools);
  const { data: serverless } = useAsync(loadServerless);
  const [filter, setFilter] = useState("");
  const [compatFilter, setCompatFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [sorting, setSorting] = useState<SortingState>([
    { id: "compatibility", desc: false },
    { id: "gap_count", desc: true },
  ]);
  const [expanded, setExpanded] = useState<string | null>(null);

  const rows: Row[] = useMemo(() => {
    if (!data) return [];
    const out: Row[] = [];
    for (const pool of data.pools) {
      const gapsByObj = new Map<string, TsqlSurfaceGap[]>();
      for (const g of pool.tsql_surface_gaps ?? []) {
        const arr = gapsByObj.get(g.code_object_id) ?? [];
        arr.push(g);
        gapsByObj.set(g.code_object_id, arr);
      }
      for (const o of pool.code_objects ?? []) {
        out.push({
          ...o,
          pool: pool.inventory.name,
          gaps: gapsByObj.get(o.code_object_id ?? "") ?? [],
        });
      }
    }
    return out;
  }, [data]);

  const columns = useMemo(
    () => [
      columnHelper.accessor("pool", { header: "Pool" }),
      columnHelper.accessor((r) => `${r.schema_name}.${r.object_name}`, {
        id: "qualified_name",
        header: "Object",
        cell: (info) => <code>{info.getValue()}</code>,
      }),
      columnHelper.accessor("object_type", {
        header: "Type",
        cell: (i) => <span className="small muted">{i.getValue()}</span>,
      }),
      columnHelper.accessor((r) => r.compatibility ?? "compatible", {
        id: "compatibility",
        header: "Compatibility",
        cell: (i) => <SeverityPill severity={i.getValue() as string} />,
        sortingFn: (a, b) => {
          const order = { incompatible: 0, needs_review: 1, compatible: 2 } as Record<string, number>;
          return (order[a.getValue("compatibility") as string] ?? 3) -
                 (order[b.getValue("compatibility") as string] ?? 3);
        },
      }),
      columnHelper.accessor((r) => r.line_count ?? 0, {
        id: "line_count",
        header: () => <span className="num">Lines</span>,
        cell: (i) => <span className="num">{i.getValue() || ""}</span>,
      }),
      columnHelper.accessor((r) => r.parameter_count ?? 0, {
        id: "param_count",
        header: () => <span className="num">Params</span>,
        cell: (i) => <span className="num">{i.getValue()}</span>,
      }),
      columnHelper.accessor((r) => r.gap_count ?? 0, {
        id: "gap_count",
        header: () => <span className="num">T-SQL gaps</span>,
        cell: (i) => <span className="num">{i.getValue()}</span>,
      }),
    ],
    [],
  );

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return rows.filter((r) => {
      if (compatFilter && (r.compatibility ?? "compatible") !== compatFilter) return false;
      if (typeFilter && r.object_type !== typeFilter) return false;
      if (!q) return true;
      const hay = [
        r.pool, r.schema_name, r.object_name, r.object_type,
        r.code_object_id, r.compatibility,
      ].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [rows, filter, compatFilter, typeFilter]);

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (loading) return <div className="empty">Loading…</div>;
  const serverlessTopQueries = serverless?.top_queries ?? [];
  const hasDedicated = !!data && rows.length > 0;
  if (!hasDedicated && serverlessTopQueries.length === 0)
    return (
      <Empty>
        No code objects or serverless query history collected. Run the{" "}
        <code>dedicated_pools</code> or <code>serverless_pools</code> module.
      </Empty>
    );

  const types = Array.from(new Set(rows.map((r) => r.object_type))).sort();
  const dedicatedTopRows: TopQueryRow[] = [];
  const consumedRows: ConsumedObjectRow[] = [];
  const captureStats: Array<{ pool: string; stats: WorkloadCaptureStats }> = [];
  if (data) {
    for (const p of data.pools) {
      for (const q of p.top_queries ?? []) {
        dedicatedTopRows.push({ ...q, pool: p.inventory.name });
      }
      for (const o of p.top_consumed_objects ?? []) {
        consumedRows.push({ ...o, pool: p.inventory.name });
      }
      if (p.workload_capture_stats) {
        captureStats.push({ pool: p.inventory.name, stats: p.workload_capture_stats });
      }
    }
  }
  // Default ranking: elapsed time desc (more meaningful than raw count for
  // sizing migration priorities). The table itself lets the user toggle.
  consumedRows.sort(
    (a, b) =>
      (b.elapsed_time_ms ?? 0) - (a.elapsed_time_ms ?? 0) ||
      b.usage_count - a.usage_count,
  );

  return (
    <>
      <h1>SQL Surface <HelpLink slug="05-code-objects" /></h1>
      <div className="muted small" style={{ marginBottom: 12 }}>
        {hasDedicated
          ? `${rows.length} object(s) across ${data!.pools.length} pool(s)`
          : "No dedicated SQL pool code objects collected."}
      </div>

      {hasDedicated && (
        <details open className="sql-surface-section" style={sectionStyle}>
          <summary style={summaryStyle}>
            <strong>Code objects</strong>{" "}
            <span className="muted small">({rows.length})</span>
          </summary>
          <div style={{ paddingTop: 12 }}>
            <div className="toolbar">
              <input
                placeholder="Filter (schema, name, id, type)"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
              <select value={compatFilter} onChange={(e) => setCompatFilter(e.target.value)}>
                <option value="">All compatibility</option>
                <option value="incompatible">Incompatible</option>
                <option value="needs_review">Needs review</option>
                <option value="compatible">Compatible</option>
              </select>
              <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
                <option value="">All types</option>
                {types.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <span className="muted small">{filtered.length} match(es)</span>
            </div>

            <table>
              <thead>
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    {hg.headers.map((h) => (
                      <th key={h.id} onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <span className="sort">
                          {h.column.getIsSorted() === "asc" ? "▲" :
                           h.column.getIsSorted() === "desc" ? "▼" : ""}
                        </span>
                      </th>
                    ))}
                    <th />
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((r) => {
                  const orig = r.original;
                  const key = `${orig.pool}.${orig.code_object_id}`;
                  const isOpen = expanded === key;
                  return (
                    <Fragment key={key}>
                      <tr>
                        {r.getVisibleCells().map((c) => (
                          <td key={c.id}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                        ))}
                        <td>
                          <button
                            onClick={() => setExpanded(isOpen ? null : key)}
                            style={{
                              background: "transparent", color: "var(--accent)",
                              border: "none", cursor: "pointer", padding: 0,
                            }}
                          >
                            {isOpen ? "Hide" : "Details"}
                          </button>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr>
                          <td colSpan={r.getVisibleCells().length + 1}>
                            <DetailPanel row={orig} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {dedicatedTopRows.length > 0 && (
        <details className="sql-surface-section" style={sectionStyle}>
          <summary style={summaryStyle}>
            <strong>Top dedicated SQL pool queries by elapsed time</strong>{" "}
            <span className="muted small">({dedicatedTopRows.length})</span>
          </summary>
          <div style={{ paddingTop: 12 }}>
            <p className="muted small" style={{ marginTop: 0 }}>
              Top 100 requests per pool from <code>sys.dm_pdw_exec_requests</code> over the last
              14 days. The DMV is a rolling buffer, so very old requests may already be evicted.
              Elapsed time is the closest proxy to "CPU cost" exposed by the request DMV.
            </p>
            <TopQueriesTable queries={dedicatedTopRows} />
          </div>
        </details>
      )}

      {consumedRows.length > 0 && (
        <details className="sql-surface-section" style={sectionStyle}>
          <summary style={summaryStyle}>
            <strong>Top consumed tables / views (dedicated pool)</strong>{" "}
            <span className="muted small">({consumedRows.length})</span>
          </summary>
          <div style={{ paddingTop: 12 }}>
            <p className="muted small" style={{ marginTop: 0 }}>
              Commands captured from <code>sys.dm_pdw_exec_requests</code> and
              parsed with sqlglot; each resolved table is cross-checked against{" "}
              <code>INFORMATION_SCHEMA</code>. Results are persisted in a per-pool
              workload cache (default 30-day window) so the DMV's rolling buffer
              doesn't gut the ranking between runs. Rank by elapsed time for
              "where does the pool spend time" or by usage count for "what gets
              touched the most".
            </p>
            <TopConsumedObjectsTable rows={consumedRows} captureStats={captureStats} />
          </div>
        </details>
      )}

      {serverlessTopQueries.length > 0 && (
        <details className="sql-surface-section" style={sectionStyle}>
          <summary style={summaryStyle}>
            <strong>Top serverless SQL queries</strong>{" "}
            <span className="muted small">({serverlessTopQueries.length})</span>
          </summary>
          <div style={{ paddingTop: 12 }}>
            <ServerlessTopQueriesTable queries={serverlessTopQueries} />
          </div>
        </details>
      )}
    </>
  );
}

// Collapsible section styling — uses a thin border + padding so each
// `<details>` block reads as a discrete card. `<details open>` keeps the
// section expanded by default while still allowing the user to collapse it.
const sectionStyle: React.CSSProperties = {
  marginTop: 20,
  border: "1px solid rgba(127,127,127,0.25)",
  borderRadius: 6,
  padding: "10px 14px",
};

const summaryStyle: React.CSSProperties = {
  cursor: "pointer",
  userSelect: "none",
  fontSize: "1.05em",
  padding: "2px 0",
};

type TopQueryRow = DedicatedTopQuery & { pool: string };
type ConsumedObjectRow = DedicatedTopConsumedObject & { pool: string };

type TQSortKey = "total_elapsed_ms" | "submit_time" | "login_name" | "pool";

function TopQueriesTable({ queries }: { queries: TopQueryRow[] }): JSX.Element {
  const [sortKey, setSortKey] = useState<TQSortKey>("total_elapsed_ms");
  const [sortDesc, setSortDesc] = useState(true);
  const [filter, setFilter] = useState("");
  const [limit, setLimit] = useState(25);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return queries;
    return queries.filter((row) => {
      const hay = [row.login_name, row.command_text, row.status, row.pool, row.request_id]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [queries, filter]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let av: number | string = 0;
      let bv: number | string = 0;
      if (sortKey === "total_elapsed_ms") {
        av = a.total_elapsed_ms ?? -1;
        bv = b.total_elapsed_ms ?? -1;
      } else if (sortKey === "submit_time") {
        av = a.submit_time ?? "";
        bv = b.submit_time ?? "";
      } else if (sortKey === "login_name") {
        av = a.login_name ?? "";
        bv = b.login_name ?? "";
      } else {
        av = a.pool;
        bv = b.pool;
      }
      if (av < bv) return sortDesc ? 1 : -1;
      if (av > bv) return sortDesc ? -1 : 1;
      return 0;
    });
    return arr;
  }, [filtered, sortKey, sortDesc]);

  const visible = sorted.slice(0, limit);

  const onHeader = (k: TQSortKey) => {
    if (sortKey === k) setSortDesc(!sortDesc);
    else {
      setSortKey(k);
      setSortDesc(true);
    }
  };
  const arrow = (k: TQSortKey) => (sortKey === k ? (sortDesc ? " ▼" : " ▲") : "");

  return (
    <>
      <div className="toolbar" style={{ marginBottom: 8 }}>
        <input
          placeholder="Filter (login, command, status, pool, request id)"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <span className="muted small">
          showing {visible.length} of {sorted.length}
        </span>
      </div>
      <table>
        <thead>
          <tr>
            <th onClick={() => onHeader("submit_time")} style={{ cursor: "pointer" }}>
              Submitted{arrow("submit_time")}
            </th>
            <th onClick={() => onHeader("pool")} style={{ cursor: "pointer" }}>
              Pool{arrow("pool")}
            </th>
            <th onClick={() => onHeader("login_name")} style={{ cursor: "pointer" }}>
              Login{arrow("login_name")}
            </th>
            <th
              onClick={() => onHeader("total_elapsed_ms")}
              className="num"
              style={{ cursor: "pointer" }}
            >
              Elapsed{arrow("total_elapsed_ms")}
            </th>
            <th>Status</th>
            <th>Resource class</th>
            <th>SQL</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((q, idx) => {
            const k = `${q.pool}#${q.request_id ?? `${q.submit_time}#${idx}`}`;
            const isOpen = !!open[k];
            const failed = q.status && q.status.toLowerCase() !== "completed";
            return (
              <Fragment key={k}>
                <tr
                  onClick={() => setOpen({ ...open, [k]: !isOpen })}
                  style={{ cursor: "pointer" }}
                >
                  <td className="small">{fmtDateTime(q.submit_time)}</td>
                  <td className="small">{q.pool}</td>
                  <td className="small">{q.login_name ?? ""}</td>
                  <td className="num">{fmtElapsed(q.total_elapsed_ms)}</td>
                  <td
                    className="small"
                    style={failed ? { color: "#d2691e" } : undefined}
                  >
                    {q.status ?? ""}
                    {failed && q.error_id ? ` (${q.error_id})` : ""}
                  </td>
                  <td className="small muted">{q.resource_class ?? ""}</td>
                  <td className="small" style={{ fontFamily: "monospace" }}>
                    {previewSql(q.command_text)}
                  </td>
                </tr>
                {isOpen && (
                  <tr key={`${k}-expanded`}>
                    <td />
                    <td colSpan={6}>
                      <div className="small muted" style={{ marginBottom: 4 }}>
                        {q.request_id && (
                          <>
                            <strong>Request id:</strong> <code>{q.request_id}</code>
                            {" · "}
                          </>
                        )}
                        {q.session_id && (
                          <>
                            <strong>Session:</strong> <code>{q.session_id}</code>
                            {" · "}
                          </>
                        )}
                        <strong>Started:</strong> {fmtDateTime(q.start_time)}
                        {" · "}
                        <strong>Ended:</strong> {fmtDateTime(q.end_time)}
                        {q.importance && (
                          <>
                            {" · "}
                            <strong>Importance:</strong> {q.importance}
                          </>
                        )}
                        {q.query_label && (
                          <>
                            {" · "}
                            <strong>Label:</strong> {q.query_label}
                          </>
                        )}
                      </div>
                      <pre
                        style={{
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                          margin: 0,
                          padding: "8px 10px",
                          background: "rgba(127,127,127,0.08)",
                          borderRadius: 4,
                          maxHeight: 360,
                          overflow: "auto",
                          fontSize: 12,
                        }}
                      >
                        {q.command_text ?? "(no command text captured)"}
                      </pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {visible.length < sorted.length && (
        <div style={{ marginTop: 8 }}>
          <button onClick={() => setLimit(limit + 50)}>Show more</button>
        </div>
      )}
    </>
  );
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return iso.replace("T", " ").replace(/\.\d+/, "").replace(/Z$/, " UTC");
}

function fmtElapsed(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = s / 60;
  if (m < 60) return `${m.toFixed(1)} min`;
  const h = m / 60;
  return `${h.toFixed(2)} h`;
}

function previewSql(text: string | null | undefined): string {
  if (!text) return "";
  const single = text.replace(/\s+/g, " ").trim();
  return single.length > 117 ? single.slice(0, 117) + "…" : single;
}

/**
 * Compact table for the "top consumed tables / views" section.
 *
 * Defaults to top 10 rows ranked by elapsed time (more meaningful for
 * sizing Fabric capacity than a raw "this name appeared the most"
 * count). The user can toggle to count-based ranking, filter by name /
 * pool, and walk through additional rows with "Show more".
 *
 * Each row carries a `match_kind` badge so the user can tell which
 * counts came from clean 2-part-qualified references vs. heuristic
 * 1-part resolution. Below the table we surface the per-pool capture
 * stats (DMV row count, parse success/fail, cache window) so the
 * reader can judge whether the ranking is trustworthy.
 */
function TopConsumedObjectsTable({
  rows,
  captureStats,
}: {
  rows: ConsumedObjectRow[];
  captureStats: Array<{ pool: string; stats: WorkloadCaptureStats }>;
}): JSX.Element {
  const [filter, setFilter] = useState("");
  const [limit, setLimit] = useState(10);
  const [rankBy, setRankBy] = useState<"elapsed" | "count">("elapsed");

  const q = filter.trim().toLowerCase();
  const filtered = q
    ? rows.filter((r) =>
        `${r.object_name} ${r.object_type} ${r.pool} ${r.match_kind ?? ""}`
          .toLowerCase()
          .includes(q),
      )
    : rows;

  const sorted = useMemo(() => {
    const arr = [...filtered];
    if (rankBy === "elapsed") {
      arr.sort(
        (a, b) =>
          (b.elapsed_time_ms ?? 0) - (a.elapsed_time_ms ?? 0) ||
          b.usage_count - a.usage_count,
      );
    } else {
      arr.sort(
        (a, b) =>
          b.usage_count - a.usage_count ||
          (b.elapsed_time_ms ?? 0) - (a.elapsed_time_ms ?? 0),
      );
    }
    return arr;
  }, [filtered, rankBy]);

  const visible = sorted.slice(0, limit);
  const maxValue =
    rankBy === "elapsed"
      ? Math.max(1, ...visible.map((r) => r.elapsed_time_ms ?? 0))
      : Math.max(1, ...visible.map((r) => r.usage_count));

  return (
    <>
      <div className="toolbar" style={{ marginBottom: 8, gap: 8 }}>
        <input
          placeholder="Filter (schema.object, type, pool, match)"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <label className="small muted" style={{ display: "flex", alignItems: "center", gap: 4 }}>
          Rank by{" "}
          <select
            value={rankBy}
            onChange={(e) => setRankBy(e.target.value as "elapsed" | "count")}
          >
            <option value="elapsed">Elapsed time</option>
            <option value="count">Usage count</option>
          </select>
        </label>
        <span className="muted small">
          showing {visible.length} of {filtered.length}
        </span>
      </div>
      <table>
        <thead>
          <tr>
            <th className="num" style={{ width: 32 }}>#</th>
            <th>Pool</th>
            <th>Object</th>
            <th>Type</th>
            <th>Match</th>
            <th className="num">Usage count</th>
            <th className="num">Elapsed</th>
            <th style={{ width: "20%" }}>Relative</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((r, idx) => {
            const value = rankBy === "elapsed" ? r.elapsed_time_ms ?? 0 : r.usage_count;
            const pct = (value / maxValue) * 100;
            const dim = r.match_kind && r.match_kind !== "qualified" ? 0.7 : 1;
            return (
              <tr key={`${r.pool}.${r.object_name}.${idx}`} style={{ opacity: dim }}>
                <td className="num">{idx + 1}</td>
                <td className="small">{r.pool}</td>
                <td><code>{r.object_name}</code></td>
                <td className="small muted">{r.object_type}</td>
                <td className="small">
                  <MatchKindBadge kind={r.match_kind} />
                </td>
                <td className="num">{r.usage_count.toLocaleString()}</td>
                <td className="num small">{fmtElapsed(r.elapsed_time_ms ?? 0)}</td>
                <td>
                  <div
                    aria-hidden="true"
                    style={{
                      height: 8,
                      width: `${pct}%`,
                      background: "var(--accent, #2f81f7)",
                      borderRadius: 2,
                      minWidth: 2,
                    }}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {visible.length < filtered.length && (
        <div style={{ marginTop: 8 }}>
          <button onClick={() => setLimit(limit + 10)}>Show more</button>
        </div>
      )}
      {captureStats.length > 0 && (
        <CaptureStatsFootnote stats={captureStats} />
      )}
    </>
  );
}

function MatchKindBadge({
  kind,
}: {
  kind?: "qualified" | "unqualified-resolved" | "ambiguous";
}): JSX.Element {
  if (!kind || kind === "qualified") {
    return <span className="small muted" title="2-part schema.name resolved cleanly">qualified</span>;
  }
  if (kind === "unqualified-resolved") {
    return (
      <span
        className="small"
        style={{ color: "#b58900" }}
        title="1-part name, exactly one schema owns it — attributed heuristically"
      >
        unqualified
      </span>
    );
  }
  return (
    <span
      className="small"
      style={{ color: "#d2691e" }}
      title="1-part name found in multiple schemas — treat counts as a suspect cluster"
    >
      ambiguous
    </span>
  );
}

function CaptureStatsFootnote({
  stats,
}: {
  stats: Array<{ pool: string; stats: WorkloadCaptureStats }>;
}): JSX.Element {
  // Aggregate across pools for the headline, but keep per-pool details
  // in a tooltip so the reader can drill in.
  const total = stats.reduce(
    (acc, { stats: s }) => ({
      dmv_rows: acc.dmv_rows + s.dmv_rows,
      parsed_ok: acc.parsed_ok + s.parsed_ok,
      parsed_failed: acc.parsed_failed + s.parsed_failed,
      parsed_empty: acc.parsed_empty + s.parsed_empty,
      cache_requests_total: acc.cache_requests_total + s.cache_requests_total,
    }),
    { dmv_rows: 0, parsed_ok: 0, parsed_failed: 0, parsed_empty: 0, cache_requests_total: 0 },
  );
  const window = stats[0]?.stats.cache_window_days ?? 30;
  const oldest = stats
    .map((s) => s.stats.oldest_cache_entry)
    .filter(Boolean)
    .sort()[0];
  const newest = stats
    .map((s) => s.stats.newest_cache_entry)
    .filter(Boolean)
    .sort()
    .slice(-1)[0];
  const tooltip = stats
    .map(
      ({ pool, stats: s }) =>
        `${pool}: ${s.cache_requests_total} cached / ${s.dmv_rows} dmv / ` +
        `${s.parsed_ok} ok / ${s.parsed_failed} parse-fail`,
    )
    .join("\n");
  return (
    <div className="muted small" style={{ marginTop: 8 }} title={tooltip}>
      <strong>Capture:</strong> {total.cache_requests_total.toLocaleString()} request(s)
      in the {window}-day cache · this run: {total.dmv_rows.toLocaleString()} DMV row(s),{" "}
      {total.parsed_ok} parsed, {total.parsed_failed} failed, {total.parsed_empty} empty
      {oldest && newest && (
        <>
          {" "}· window {fmtDateTime(oldest)} → {fmtDateTime(newest)}
        </>
      )}
    </div>
  );
}

function DetailPanel({ row }: { row: Row }) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="small muted">
        <code>{row.code_object_id}</code>
        {row.create_date && <> · created {row.create_date}</>}
        {row.modify_date && <> · modified {row.modify_date}</>}
        {row.definition_length != null && <> · {row.definition_length} chars</>}
      </div>

      {row.parameters && row.parameters.length > 0 && (
        <div>
          <h3>Parameters ({row.parameter_count})</h3>
          <table>
            <thead>
              <tr>
                <th className="num">#</th><th>Name</th><th>Type</th>
                <th className="num">Max len</th><th>Output</th><th>Default</th>
              </tr>
            </thead>
            <tbody>
              {row.parameters.map((p) => (
                <tr key={p.ordinal}>
                  <td className="num">{p.ordinal}</td>
                  <td><code>{p.parameter_name}</code></td>
                  <td>{p.data_type}</td>
                  <td className="num">{p.max_length ?? ""}</td>
                  <td>{p.is_output ? "yes" : ""}</td>
                  <td>{p.has_default ? "yes" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {row.gaps.length > 0 && (
        <div>
          <h3>T-SQL surface gaps</h3>
          <table>
            <thead>
              <tr><th>Rule</th><th>Severity</th><th className="num">Matches</th><th>Fabric action</th></tr>
            </thead>
            <tbody>
              {row.gaps.map((g, i) => (
                <tr key={i}>
                  <td>{g.label} <span className="muted small">({g.rule_id})</span></td>
                  <td><SeverityPill severity={g.severity} /></td>
                  <td className="num">{g.matches}</td>
                  <td className="small muted">{g.fabric_action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {row.definition && (
        <details>
          <summary>Definition <span className="muted small">(truncated to 50 KB by collector)</span></summary>
          <pre>{row.definition}</pre>
        </details>
      )}
    </div>
  );
}
