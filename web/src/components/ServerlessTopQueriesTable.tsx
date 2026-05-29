import { Fragment, useState } from "react";
import type { ServerlessTopQuery } from "../types";

/**
 * Drill-down table for the top-100 serverless SQL queries collected by the
 * analyzer. Used on the SQL Surface page; previously lived inline on the
 * Dashboard. The component owns its own filter/sort/pagination/expand state
 * so it can be dropped into any page without rewiring.
 */

type TopQuerySortKey =
  | "data_processed_mb"
  | "duration_seconds"
  | "start_time"
  | "login_name";

function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
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

export function ServerlessTopQueriesTable({
  queries,
}: {
  queries: ServerlessTopQuery[];
}): JSX.Element {
  const [sortKey, setSortKey] = useState<TopQuerySortKey>("data_processed_mb");
  const [sortDesc, setSortDesc] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState("");
  const [limit, setLimit] = useState(25);

  const lower = filter.trim().toLowerCase();
  const filtered = lower
    ? queries.filter((q) => {
        const haystack = `${q.login_name ?? ""} ${q.command_text ?? ""} ${q.status ?? ""}`.toLowerCase();
        return haystack.includes(lower);
      })
    : queries;

  const sorted = [...filtered].sort((a, b) => {
    const av = (a as Record<string, unknown>)[sortKey];
    const bv = (b as Record<string, unknown>)[sortKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    let cmp: number;
    if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    return sortDesc ? -cmp : cmp;
  });

  const visible = sorted.slice(0, limit);

  const fmtDur = (s: number | null | undefined): string => {
    if (s == null || !Number.isFinite(s)) return "—";
    if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
    if (s < 3600) return `${(s / 60).toFixed(1)} min`;
    return `${(s / 3600).toFixed(1)} h`;
  };

  const fmtStart = (iso: string | null | undefined): string => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
  };

  const headerCell = (
    key: TopQuerySortKey,
    label: string,
    align: "left" | "right" = "left",
  ) => {
    const active = sortKey === key;
    return (
      <th
        style={{ textAlign: align, cursor: "pointer", userSelect: "none" }}
        onClick={() => {
          if (active) setSortDesc((d) => !d);
          else {
            setSortKey(key);
            setSortDesc(true);
          }
        }}
      >
        {label} {active ? (sortDesc ? "▼" : "▲") : ""}
      </th>
    );
  };

  const keyFor = (q: ServerlessTopQuery, idx: number): string =>
    q.request_id || `${q.start_time ?? ""}#${idx}`;

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 8,
        }}
      >
        <span className="muted small">
          Last 14 days · {fmtNum(queries.length)} captured
        </span>
        <input
          type="search"
          placeholder="Filter login / SQL / status…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ minWidth: 220, padding: "4px 8px" }}
          aria-label="Filter top serverless queries"
        />
        <span className="small muted">
          Showing {fmtNum(visible.length)} of {fmtNum(sorted.length)}
          {sorted.length > limit && (
            <>
              {" · "}
              <button
                type="button"
                onClick={() => setLimit((n) => n + 50)}
                style={{
                  background: "none",
                  border: "none",
                  color: "inherit",
                  textDecoration: "underline",
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                Show more
              </button>
            </>
          )}
        </span>
      </div>
      <div className="muted small" style={{ marginBottom: 6 }}>
        Source: <code>sys.dm_exec_requests_history</code> on the on-demand endpoint
        (CSV export: <code>output/serverless_top_queries.csv</code>). Click a row to
        expand the full SQL text; click a column header to re-sort.
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="data-table" style={{ width: "100%", fontSize: 13 }}>
          <thead>
            <tr>
              <th style={{ width: 28 }} />
              {headerCell("start_time", "Started")}
              {headerCell("login_name", "Login")}
              {headerCell("data_processed_mb", "Data scanned", "right")}
              {headerCell("duration_seconds", "Duration", "right")}
              <th>Status</th>
              <th>SQL (preview)</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((q, idx) => {
              const k = keyFor(q, idx);
              const isOpen = !!expanded[k];
              const preview = (q.command_text ?? "").replace(/\s+/g, " ").trim();
              const previewShort =
                preview.length > 120 ? preview.slice(0, 117) + "…" : preview;
              const failed = q.status && q.status.toLowerCase() !== "completed";
              return (
                <Fragment key={k}>
                  <tr
                    onClick={() => setExpanded((e) => ({ ...e, [k]: !e[k] }))}
                    style={{ cursor: "pointer" }}
                  >
                    <td aria-hidden="true" style={{ textAlign: "center", opacity: 0.6 }}>
                      {isOpen ? "▾" : "▸"}
                    </td>
                    <td className="small">{fmtStart(q.start_time)}</td>
                    <td className="small">
                      <code>{q.login_name ?? "—"}</code>
                    </td>
                    <td style={{ textAlign: "right" }}>{fmtMb(q.data_processed_mb)}</td>
                    <td style={{ textAlign: "right" }}>{fmtDur(q.duration_seconds)}</td>
                    <td className="small">
                      {failed ? (
                        <span style={{ color: "#d2691e" }}>
                          {q.status}
                          {q.error_code ? ` (${q.error_code})` : ""}
                        </span>
                      ) : (
                        q.status ?? "—"
                      )}
                    </td>
                    <td
                      className="small"
                      style={{
                        maxWidth: 360,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      <code>{previewShort || "—"}</code>
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
                          <strong>Ended:</strong> {fmtStart(q.end_time)}
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
      </div>
    </div>
  );
}
