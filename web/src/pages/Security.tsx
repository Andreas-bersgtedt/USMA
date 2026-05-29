import { useMemo, useState } from "react";
import { loadSecurity } from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty, SeverityPill, StatCard } from "../components/Atoms";
import HelpLink from "../components/HelpLink";

const SEV_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

function YesNo({ value }: { value: boolean | null | undefined }) {
  if (value == null) return <span className="muted">—</span>;
  return value
    ? <span className="pill ok">yes</span>
    : <span className="pill warn">no</span>;
}

export default function Security() {
  const { data, loading } = useAsync(loadSecurity);
  const [filter, setFilter] = useState("");
  const [sev, setSev] = useState("");

  const findings = useMemo(() => {
    if (!data) return [];
    const q = filter.trim().toLowerCase();
    return [...data.findings]
      .filter((f) => {
        if (sev && f.severity !== sev) return false;
        if (!q) return true;
        return [f.rule_id, f.title, f.detail ?? "", f.resource_id ?? ""]
          .join(" ").toLowerCase().includes(q);
      })
      .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  }, [data, filter, sev]);

  if (loading) return <div className="empty">Loading…</div>;
  if (!data) return (
    <>
      <h1>Security <HelpLink slug="14-security" /></h1>
      <Empty>
        No <code>security.json</code> in this run. Run with{" "}
        <code>--include security</code>.
      </Empty>
    </>
  );

  const ws = data.workspace_settings;
  const allowAll = data.firewall_rules.filter((r) => r.is_allow_all).length;
  const tdeDisabled = data.pool_tde_status.filter((p) => p.status === "Disabled").length;
  const inlineSecrets = data.credentials.filter((c) => c.has_inline_secret).length;

  return (
    <>
      <h1>Security <HelpLink slug="14-security" /></h1>

      <div className="grid cols-4">
        <StatCard
          label="Findings"
          value={data.findings.length}
          sub={data.findings.length
            ? `${data.findings.filter((f) => f.severity === "high").length} high · ${data.findings.filter((f) => f.severity === "medium").length} medium`
            : "no findings"}
        />
        <StatCard
          label="Firewall rules"
          value={data.firewall_rules.length}
          sub={allowAll ? `${allowAll} allow-all` : "no allow-all"}
        />
        <StatCard
          label="Pool TDE"
          value={`${data.pool_tde_status.length - tdeDisabled} / ${data.pool_tde_status.length}`}
          sub={tdeDisabled ? `${tdeDisabled} disabled` : "all enabled"}
        />
        <StatCard
          label="Inline secrets"
          value={inlineSecrets}
          sub={`${data.credentials.length} credentials`}
        />
      </div>

      {ws && (
        <section className="section">
          <h2>Workspace settings</h2>
          <table>
            <tbody>
              <tr><td>Workspace</td><td><code>{ws.workspace_name}</code></td></tr>
              <tr><td>AAD-only auth</td><td><YesNo value={ws.aad_only_authentication ?? null} /></td></tr>
              <tr><td>Public network access</td><td>{ws.public_network_access ?? "—"}</td></tr>
              <tr><td>Minimum TLS</td><td>{ws.minimum_tls_version ?? "—"}</td></tr>
              <tr><td>Encryption at rest</td><td>{ws.encryption_at_rest ?? "—"}</td></tr>
              <tr><td>Managed VNet</td><td><YesNo value={ws.managed_vnet ?? null} /></td></tr>
            </tbody>
          </table>
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
                  <tr key={f.rule_id + (f.resource_id ?? "")}>
                    <td><SeverityPill severity={f.severity} /></td>
                    <td><code className="small">{f.rule_id}</code></td>
                    <td>
                      <details>
                        <summary>{f.title}</summary>
                        {f.detail && <div className="small" style={{ marginTop: 6 }}>{f.detail}</div>}
                      </details>
                    </td>
                    <td className="small muted">{f.resource_id ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </section>

      {data.firewall_rules.length > 0 && (
        <section className="section">
          <h2>Firewall rules</h2>
          <table>
            <thead>
              <tr>
                <th>Resource kind</th><th>Name</th><th>Start</th><th>End</th><th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {data.firewall_rules.map((r) => (
                <tr key={r.resource_id + r.name}>
                  <td><code className="small">{r.resource_kind}</code></td>
                  <td><code>{r.name}</code></td>
                  <td className="small muted">{r.start_ip ?? "—"}</td>
                  <td className="small muted">{r.end_ip ?? "—"}</td>
                  <td>
                    {r.is_allow_all && <span className="pill err">allow-all</span>}{" "}
                    {r.is_allow_azure_services && <span className="pill warn">allow-azure</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data.pool_tde_status.length > 0 && (
        <section className="section">
          <h2>Dedicated pool TDE</h2>
          <table>
            <thead>
              <tr><th>Pool</th><th>TDE status</th></tr>
            </thead>
            <tbody>
              {data.pool_tde_status.map((p) => (
                <tr key={p.resource_id}>
                  <td><code>{p.pool_name}</code></td>
                  <td>
                    {p.status === "Enabled"
                      ? <span className="pill ok">{p.status}</span>
                      : p.status === "Disabled"
                        ? <span className="pill err">{p.status}</span>
                        : <span className="pill warn">{p.status}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data.credentials.length > 0 && (
        <section className="section">
          <h2>Credentials inventory</h2>
          <table>
            <thead>
              <tr>
                <th>Container</th><th>Name</th><th>Kind</th><th>Reference</th><th>Inline secret</th>
              </tr>
            </thead>
            <tbody>
              {data.credentials.map((c, i) => (
                <tr key={`${c.container_name}:${c.credential_kind}:${i}`}>
                  <td><code className="small">{c.container}</code></td>
                  <td><code>{c.container_name}</code></td>
                  <td>{c.credential_kind}</td>
                  <td className="small muted">{c.secret_reference ?? "—"}</td>
                  <td>
                    {c.has_inline_secret
                      ? <span className="pill err">inline</span>
                      : <span className="pill ok">vaulted</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data.aad_admins.length > 0 && (
        <section className="section">
          <h2>AAD administrators</h2>
          <ul>
            {data.aad_admins.map((a) => <li key={a}><code>{a}</code></li>)}
          </ul>
        </section>
      )}
    </>
  );
}
