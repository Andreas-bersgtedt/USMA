import { loadRunDelta } from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty } from "../components/Atoms";
import HelpLink from "../components/HelpLink";

const STATUS_PILL: Record<string, string> = {
  added: "ok",
  removed: "err",
  changed: "warn",
  unchanged: "muted",
};

export default function Delta() {
  const { data, loading } = useAsync(loadRunDelta);

  if (loading) return <div className="empty">Loading…</div>;
  if (!data)
    return (
      <Empty>
        No <code>run_delta.json</code> found. Run <code>sma analyze-all</code>{" "}
        twice (the second run computes a delta against the previous one).
      </Empty>
    );

  return (
    <>
      <h1>Run delta <HelpLink slug="08-delta" /></h1>
      <div className="muted small" style={{ marginBottom: 12 }}>
        {data.previous_run ? <>Previous: <code>{data.previous_run}</code> · </> : null}
        Current: <code>{data.current_run}</code> · Generated{" "}
        {new Date(data.generated_at).toLocaleString()}
      </div>

      <table>
        <thead>
          <tr>
            <th>Module</th>
            <th>Path</th>
            <th>Status</th>
            <th className="num">Records</th>
            <th className="num">Δ records</th>
            <th className="num">Size (bytes)</th>
            <th>SHA-256</th>
          </tr>
        </thead>
        <tbody>
          {data.artifacts.map((a, i) => (
            <tr key={i}>
              <td><code>{a.module}</code></td>
              <td className="small muted">{a.path}</td>
              <td><span className={`pill ${STATUS_PILL[a.status] ?? "muted"}`}>{a.status}</span></td>
              <td className="num">{a.record_count ?? ""}</td>
              <td className="num">
                {a.record_count_delta != null && a.record_count_delta !== 0 ? (
                  <span className={a.record_count_delta > 0 ? "pill ok" : "pill warn"}>
                    {a.record_count_delta > 0 ? "+" : ""}{a.record_count_delta}
                  </span>
                ) : ""}
              </td>
              <td className="num">{a.size_bytes ?? ""}</td>
              <td className="small muted"><code>{a.sha256?.slice(0, 12)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.notes && data.notes.length > 0 && (
        <section className="section">
          <h2>Notes</h2>
          <ul>{data.notes.map((n, i) => <li key={i} className="small">{n}</li>)}</ul>
        </section>
      )}
    </>
  );
}
