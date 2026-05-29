import { useEffect, useState } from "react";
import { apiStartRun, detectMode, setRunIdInHash } from "../api/loader";
import { useSseProgress } from "../hooks/useSseProgress";

/**
 * Inline button that kicks off a targeted analyzer run covering only the
 * modules supplied via the ``modules`` prop. Designed for placement next
 * to a dashboard section heading so users can refresh just that
 * workload (DWU / Pipelines / Spark) without leaving the page or
 * navigating to the Run tab.
 *
 * In static mode (no API server) the button hides itself — there is no
 * backend to drive a new run. In control-plane mode it:
 *
 *   1. POSTs ``/api/runs`` with ``[...modules, "fabric_mapping"]`` (the
 *      always-on top-line refresh) and the supplied lookback ``days``.
 *   2. Subscribes to the SSE stream to surface per-module progress.
 *   3. On completion, points the URL hash at the new run id and reloads
 *      so every dashboard section re-fetches against the fresh data.
 *
 * Carry-forward semantics on the backend ensure modules that were *not*
 * included in this incremental run are inherited from the previous run,
 * so partial refreshes are safe.
 */
export default function ReanalyzeButton({
  modules,
  label,
  days,
  title,
}: {
  modules: string[];
  label?: string;
  days?: number;
  title?: string;
}): JSX.Element | null {
  const [supported, setSupported] = useState<boolean | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { progressByModule, done } = useSseProgress(runId);

  useEffect(() => {
    let cancelled = false;
    detectMode()
      .then((m) => {
        if (!cancelled) setSupported(m === "control-plane");
      })
      .catch(() => {
        if (!cancelled) setSupported(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!done || !runId) return;
    // Land on the freshly-finished run and force a full reload so every
    // dashboard section re-fetches against it.
    setRunIdInHash(runId);
    const t = setTimeout(() => window.location.reload(), 500);
    return () => clearTimeout(t);
  }, [done, runId]);

  if (supported !== true) return null;

  const running = runId !== null && !done;

  const start = async () => {
    setError(null);
    try {
      // Always include fabric_mapping so top-line readiness / cost
      // projections that other tabs depend on stay in sync.
      const all = Array.from(new Set([...modules, "fabric_mapping"]));
      const { id } = await apiStartRun(all, label, days);
      setRunId(id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Average progress across the targeted (non-fabric_mapping) modules.
  const pct = (() => {
    if (!running) return null;
    const tracked = modules
      .map((m) => progressByModule[m])
      .filter((p): p is NonNullable<typeof p> => !!p && p.total > 0);
    if (tracked.length === 0) return null;
    const sum = tracked.reduce(
      (a, p) => a + Math.min(100, (p.current / p.total) * 100),
      0,
    );
    return Math.round(sum / tracked.length);
  })();

  return (
    <span
      style={{
        marginLeft: 8,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        verticalAlign: "middle",
      }}
    >
      <button
        type="button"
        onClick={start}
        disabled={running}
        title={title ?? `Re-run analysis for: ${modules.join(", ")}`}
        style={{ fontSize: "0.85em", padding: "2px 10px" }}
      >
        {running ? "Running…" : "↻ Re-analyze"}
      </button>
      {running && pct != null && (
        <span className="small muted">{pct}%</span>
      )}
      {done && <span className="small muted">done — reloading…</span>}
      {error && (
        <span className="small" style={{ color: "var(--err)" }} title={error}>
          failed
        </span>
      )}
    </span>
  );
}
