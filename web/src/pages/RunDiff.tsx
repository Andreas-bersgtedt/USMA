import { useEffect, useState } from "react";
import { apiGetDiff, apiListRuns, getRunIdFromHash, type RunMeta } from "../api/loader";
import HelpLink from "../components/HelpLink";

type DiffEntry = {
  name: string;
  status: "added" | "removed" | "changed" | "unchanged";
  prev_sha: string | null;
  curr_sha: string | null;
  prev_size: number | null;
  curr_size: number | null;
  prev_generated_at: string | null;
  curr_generated_at: string | null;
  prev_record_count: number | null;
  curr_record_count: number | null;
  size_delta: number | null;
  record_count_delta: number | null;
};

type DiffResponse = {
  base: string | null;
  head: string;
  summary: Record<string, number>;
  delta: DiffEntry[];
};

function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

function fmtDelta(n: number | null | undefined): JSX.Element | string {
  if (n === null || n === undefined) return "—";
  if (n === 0) return "0";
  const sign = n > 0 ? "+" : "";
  const cls = n > 0 ? "ok" : "err";
  return <span className={`pill ${cls}`}>{sign}{n.toLocaleString()}</span>;
}

function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

/**
 * Phase 3 — split scope-prefixed artifact names into a {scope, module}
 * pair so the table can render each side as its own column. Flat names
 * (legacy single-scope runs) come back with ``scope: null``.
 */
function splitArtifactName(name: string): { scope: string | null; module: string } {
  const ix = name.indexOf("/");
  if (ix < 0) return { scope: null, module: name };
  return { scope: name.slice(0, ix), module: name.slice(ix + 1) };
}

export default function RunDiff(): JSX.Element {
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [head, setHead] = useState<string | null>(getRunIdFromHash());
  const [base, setBase] = useState<string | undefined>(undefined);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hideUnchanged, setHideUnchanged] = useState(true);

  useEffect(() => {
    apiListRuns(50)
      .then((list) => {
        setRuns(list);
        if (!head && list[0]) setHead(list[0].id);
      })
      .catch((e: Error) => setError(e.message));
  }, [head]);

  useEffect(() => {
    if (!head) return;
    setDiff(null);
    apiGetDiff(head, base).then((d) => setDiff(d as DiffResponse)).catch((e: Error) => setError(e.message));
  }, [head, base]);

  if (error) return <div className="empty">Error: {error}</div>;

  return (
    <section className="page">
      <h1>Run delta <HelpLink slug="11-diff-page" /></h1>
      <div className="actions">
        <label>
          <span>Head</span>{" "}
          <select value={head ?? ""} onChange={(e) => setHead(e.target.value || null)}>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>{r.id}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Base</span>{" "}
          <select value={base ?? ""} onChange={(e) => setBase(e.target.value || undefined)}>
            <option value="">previous run (auto)</option>
            {runs.filter((r) => r.id !== head).map((r) => (
              <option key={r.id} value={r.id}>{r.id}</option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={hideUnchanged}
            onChange={(e) => setHideUnchanged(e.target.checked)}
          />
          <span>Hide unchanged</span>
        </label>
      </div>

      {!diff && head && <div className="empty">Loading…</div>}

      {diff && (
        <>
          <div className="card">
            <div className="label">Summary</div>
            <ul>
              {Object.entries(diff.summary).map(([k, v]) => (
                <li key={k}><code>{k}</code>: {v}</li>
              ))}
            </ul>
            <div className="muted">base: {diff.base ?? "—"} → head: {diff.head}</div>
          </div>

          <table className="table" style={{ marginTop: "1rem" }}>
            <thead>
              <tr>
                <th>Artifact</th>
                <th>Scope</th>
                <th>Status</th>
                <th>Records (before)</th>
                <th>Records (after)</th>
                <th>Δ records</th>
                <th>Size (after)</th>
                <th>Δ size</th>
              </tr>
            </thead>
            <tbody>
              {diff.delta
                .filter((d) => !hideUnchanged || d.status !== "unchanged")
                .map((d, i) => {
                  const { scope, module } = splitArtifactName(d.name);
                  return (
                  <tr key={i}>
                    <td><code>{module}</code></td>
                    <td>
                      {scope ? (
                        <span className="pill muted" title={scope}>{scope}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <span
                        className={`pill ${
                          d.status === "added"
                            ? "ok"
                            : d.status === "removed"
                              ? "err"
                              : d.status === "changed"
                                ? "warn"
                                : "muted"
                        }`}
                      >
                        {d.status}
                      </span>
                    </td>
                    <td>{fmtNum(d.prev_record_count)}</td>
                    <td>{fmtNum(d.curr_record_count)}</td>
                    <td>{fmtDelta(d.record_count_delta)}</td>
                    <td>{fmtBytes(d.curr_size)}</td>
                    <td>{fmtDelta(d.size_delta)}</td>
                  </tr>
                  );
                })}
              {diff.delta.filter((d) => !hideUnchanged || d.status !== "unchanged").length === 0 && (
                <tr>
                  <td colSpan={8} className="muted" style={{ textAlign: "center" }}>
                    No differences{hideUnchanged ? " (unchanged rows hidden)" : ""}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
