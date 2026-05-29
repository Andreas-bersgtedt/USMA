import { useEffect, useState } from "react";
import { apiListRuns, getRunIdFromHash, setRunIdInHash, type RunMeta } from "../api/loader";

/**
 * Compact run picker. Lives in the top bar in control-plane mode; lets
 * the user choose which run the rest of the SPA reads from.
 */
export function RunPicker(): JSX.Element | null {
  const [runs, setRuns] = useState<RunMeta[] | null>(null);
  const [selected, setSelected] = useState<string | null>(getRunIdFromHash());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    apiListRuns(50)
      .then((list) => {
        if (!alive) return;
        setRuns(list);
        if (!selected && list.length > 0) {
          // Auto-select the most recent run. Reload so any pages that
          // already mounted (e.g. Dashboard with no runId in hash) refetch
          // against the run-id-aware /api/runs/<id>/modules/<module>
          // endpoint instead of the static ./fabric_mapping.json path.
          setRunIdInHash(list[0].id);
          window.location.reload();
          return;
        }
      })
      .catch((err: Error) => alive && setError(err.message));
    return () => {
      alive = false;
    };
  }, [selected]);

  if (error) return <span className="muted">runs: {error}</span>;
  if (runs == null) return <span className="muted">loading runs…</span>;
  if (runs.length === 0) return <span className="muted">no runs yet</span>;

  return (
    <select
      value={selected ?? ""}
      onChange={(e) => {
        const v = e.target.value || null;
        setSelected(v);
        setRunIdInHash(v);
        // Force a reload so cached loader results clear.
        window.location.reload();
      }}
      className="run-picker"
      aria-label="Select run"
    >
      {runs.map((r) => (
        <option key={r.id} value={r.id}>
          {r.label ? `${r.label} — ` : ""}{r.id} ({r.status})
        </option>
      ))}
    </select>
  );
}
