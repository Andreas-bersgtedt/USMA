import { useEffect, useState } from "react";
import {
  apiDeleteAllRuns,
  apiDeleteRunData,
  apiListRuns,
  getRunIdFromHash,
  setRunIdInHash,
  type RunMeta,
} from "../api/loader";
import HelpLink from "../components/HelpLink";

export default function RunsHistory(): JSX.Element {
  const [runs, setRuns] = useState<RunMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [purgingAll, setPurgingAll] = useState(false);

  useEffect(() => {
    apiListRuns(50).then(setRuns).catch((e: Error) => setError(e.message));
  }, []);

  // Refresh every 2s while any run is in flight so the inline progress
  // updates without requiring a manual reload.
  useEffect(() => {
    if (!runs) return;
    const inflight = runs.some(
      (r) => r.status === "queued" || r.status === "running",
    );
    if (!inflight) return;
    const handle = window.setInterval(() => {
      apiListRuns(50).then(setRuns).catch(() => {
        /* ignore polling errors */
      });
    }, 2000);
    return () => window.clearInterval(handle);
  }, [runs]);

  if (error) return <div className="empty">Error: {error}</div>;
  if (!runs) return <div className="empty">Loading…</div>;
  if (runs.length === 0) return <div className="empty">No runs yet. Start one from the Run page.</div>;

  const onDelete = async (id: string) => {
    if (!window.confirm(`Delete run ${id}? This permanently removes all artefacts on disk.`)) {
      return;
    }
    setBusyId(id);
    setActionMsg(null);
    try {
      await apiDeleteRunData(id);
      // If the deleted run was the currently selected one, drop the
      // hash/sessionStorage pin so other pages don't try to load it.
      if (getRunIdFromHash() === id) setRunIdInHash(null);
      const fresh = await apiListRuns(50);
      setRuns(fresh);
      setActionMsg(`Deleted ${id}`);
    } catch (e) {
      setActionMsg(`Delete failed: ${(e as Error).message}`);
    } finally {
      setBusyId(null);
    }
  };

  const onDeleteAll = async () => {
    const answer = window.prompt(
      "This will permanently delete EVERY run on disk, including any " +
        "stalled runs still marked as running. Type DELETE ALL to confirm.",
    );
    if (answer !== "DELETE ALL") {
      return;
    }
    setPurgingAll(true);
    setActionMsg(null);
    try {
      const result = await apiDeleteAllRuns();
      setRunIdInHash(null);
      const fresh = await apiListRuns(50);
      setRuns(fresh);
      const failedNote =
        result.failed.length > 0 ? ` (failed: ${result.failed.join(", ")})` : "";
      setActionMsg(
        `Deleted ${result.deleted}/${result.total} run(s); ` +
          `cancelled ${result.cancelled} stalled${failedNote}.`,
      );
    } catch (e) {
      setActionMsg(`Delete-all failed: ${(e as Error).message}`);
    } finally {
      setPurgingAll(false);
    }
  };

  return (
    <section className="page">
      <h1>Runs <HelpLink slug="10-runs-history" /></h1>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <button
          onClick={onDeleteAll}
          disabled={purgingAll}
          title="Permanently delete every run on disk, including stalled runs still marked as running."
        >
          {purgingAll ? "Deleting all…" : "Delete all runs"}
        </button>
        <span className="muted small">
          Includes stalled runs that never finished.
        </span>
      </div>
      {actionMsg && <p className="muted">{actionMsg}</p>}
      <table className="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Label</th>
            <th>Status</th>
            <th>Started</th>
            <th>Duration</th>
            <th>Progress</th>
            <th>Errors</th>
            <th>Score</th>
            <th>Scopes</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const dur =
              r.finished_at && r.started_at
                ? (
                    (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) /
                    1000
                  ).toFixed(1) + "s"
                : "—";
            const inFlight = r.status === "queued" || r.status === "running";
            // For in-flight runs, derive a high-level progress string from
            // the persisted module progress snapshots.
            let progressCell: JSX.Element | string = "—";
            if (inFlight) {
              const total = r.modules.length;
              const finished = r.modules.filter((m) =>
                ["ok", "failed", "skipped", "cancelled"].includes(m.state),
              ).length;
              const running = r.modules.find((m) => m.state === "running");
              const sub = running?.progress;
              if (sub && sub.total > 0) {
                const pct = Math.min(
                  100,
                  Math.round((sub.current / sub.total) * 100),
                );
                progressCell = (
                  <span title={sub.label ?? running?.name ?? ""}>
                    {finished}/{total} · {running?.name} {sub.current}/{sub.total} ({pct}%)
                  </span>
                );
              } else {
                progressCell = (
                  <span>
                    {finished}/{total}
                    {running ? ` · ${running.name}` : ""}
                  </span>
                );
              }
            }
            return (
              <tr key={r.id}>
                <td>
                  <code>{r.id}</code>
                  {r.carried_from && Object.keys(r.carried_from).length > 0 && (
                    <span
                      className="pill muted small"
                      style={{ marginLeft: 6 }}
                      title={`Incremental run — carried ${Object.keys(r.carried_from).length} module(s) forward: ${Object.keys(r.carried_from).join(", ")}`}
                    >
                      ↺ incremental
                    </span>
                  )}
                </td>
                <td>{r.label ?? <span className="muted">—</span>}</td>
                <td>
                  <span className={`pill ${r.status === "ok" ? "ok" : r.status === "failed" ? "err" : "warn"}`}>
                    {r.status}
                  </span>
                </td>
                <td className="muted">{r.started_at}</td>
                <td>{dur}</td>
                <td>{progressCell}</td>
                <td>{r.errors_count}</td>
                <td>{r.readiness_score != null ? r.readiness_score.toFixed(0) : "—"}</td>
                <td>
                  {r.scopes && r.scopes.length > 0 ? (
                    <span title={r.scopes.map((s) => `${s.source_type}: ${s.display_name}`).join("\n")}>
                      {r.scopes.length}
                      {" "}
                      {Array.from(new Set(r.scopes.map((s) => s.source_type))).map((t) => (
                        <span
                          key={t}
                          className="pill small"
                          style={{ marginLeft: 4 }}
                          title={`Source type: ${t}`}
                        >
                          {t === "synapse_workspace" ? "syn" : t === "adf" ? "adf" : t}
                        </span>
                      ))}
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td style={{ display: "flex", gap: 6 }}>
                  <button
                    onClick={() => {
                      setRunIdInHash(r.id);
                      window.location.hash = `#run=${r.id}`;
                      window.location.pathname = "/";
                    }}
                  >
                    Open
                  </button>
                  <button
                    onClick={() => {
                      setRunIdInHash(r.id);
                      window.location.hash = `#run=${r.id}`;
                      window.location.pathname = "/diff";
                    }}
                    disabled={inFlight}
                    title={
                      inFlight
                        ? "Wait for the run to finish before diffing"
                        : "Compare this run against another"
                    }
                  >
                    Diff
                  </button>
                  <button
                    onClick={() => onDelete(r.id)}
                    disabled={busyId === r.id || inFlight}
                    title={
                      inFlight
                        ? "Cancel the run before deleting"
                        : "Permanently delete this run's data on disk"
                    }
                  >
                    {busyId === r.id ? "Deleting…" : "Delete"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
