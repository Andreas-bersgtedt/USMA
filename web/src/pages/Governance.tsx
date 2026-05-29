import { useMemo, useState } from "react";
import { loadGovernance } from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty, SeverityPill, StatCard } from "../components/Atoms";
import HelpLink from "../components/HelpLink";

const SEV_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

const PRIVILEGED_ROLES = new Set([
  "Owner",
  "Contributor",
  "User Access Administrator",
  "Role Based Access Control Administrator",
]);

export default function Governance() {
  const { data, loading } = useAsync(loadGovernance);
  const [findingFilter, setFindingFilter] = useState("");
  const [findingSev, setFindingSev] = useState("");
  const [rbacFilter, setRbacFilter] = useState("");
  const [privilegedOnly, setPrivilegedOnly] = useState(false);

  const findings = useMemo(() => {
    if (!data) return [];
    const q = findingFilter.trim().toLowerCase();
    return [...data.findings]
      .filter((f) => {
        if (findingSev && f.severity !== findingSev) return false;
        if (!q) return true;
        return [f.rule_id, f.title, f.detail ?? "", f.resource_id ?? ""]
          .join(" ").toLowerCase().includes(q);
      })
      .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  }, [data, findingFilter, findingSev]);

  const rbac = useMemo(() => {
    if (!data) return [];
    const q = rbacFilter.trim().toLowerCase();
    return data.role_assignments.filter((r) => {
      if (privilegedOnly && !PRIVILEGED_ROLES.has(r.role_name)) return false;
      if (!q) return true;
      return [r.role_name, r.scope, r.principal_display_name ?? "", r.principal_id, r.principal_type ?? ""]
        .join(" ").toLowerCase().includes(q);
    });
  }, [data, rbacFilter, privilegedOnly]);

  if (loading) return <div className="empty">Loading…</div>;
  if (!data) return (
    <>
      <h1>Governance <HelpLink slug="14-security" /></h1>
      <Empty>
        No <code>governance.json</code> in this run. Run with{" "}
        <code>--include governance</code>.
      </Empty>
    </>
  );

  const privilegedCount = data.role_assignments.filter((r) => PRIVILEGED_ROLES.has(r.role_name)).length;
  const cmkEnabled = data.customer_managed_keys.filter((k) => k.enabled).length;
  const mpePending = data.managed_private_endpoints.filter(
    (m) => (m.connection_state ?? "").toLowerCase() !== "approved",
  ).length;

  return (
    <>
      <h1>Governance <HelpLink slug="14-security" /></h1>

      <div className="grid cols-4">
        <StatCard
          label="Role assignments"
          value={data.role_assignments.length}
          sub={`${privilegedCount} privileged`}
        />
        <StatCard
          label="Managed private endpoints"
          value={data.managed_private_endpoints.length}
          sub={mpePending ? `${mpePending} not approved` : "all approved / n/a"}
        />
        <StatCard
          label="Customer-managed keys"
          value={cmkEnabled}
          sub={`${data.customer_managed_keys.length} configured`}
        />
        <StatCard
          label="Findings"
          value={data.findings.length}
          sub={data.findings.length
            ? `${data.findings.filter((f) => f.severity === "high").length} high · ${data.findings.filter((f) => f.severity === "medium").length} medium`
            : "no findings"}
        />
      </div>

      <section className="section">
        <h2>Findings</h2>
        <div className="toolbar">
          <input
            placeholder="Filter (rule id, title, detail, resource)"
            value={findingFilter}
            onChange={(e) => setFindingFilter(e.target.value)}
          />
          <select value={findingSev} onChange={(e) => setFindingSev(e.target.value)}>
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

      <section className="section">
        <h2>Role assignments</h2>
        <div className="toolbar">
          <input
            placeholder="Filter (role, scope, principal)"
            value={rbacFilter}
            onChange={(e) => setRbacFilter(e.target.value)}
          />
          <label className="small">
            <input
              type="checkbox"
              checked={privilegedOnly}
              onChange={(e) => setPrivilegedOnly(e.target.checked)}
            />{" "}
            Privileged only
          </label>
          <span className="muted small">
            {rbac.length} / {data.role_assignments.length}
          </span>
        </div>
        {rbac.length === 0
          ? <Empty>No assignments match.</Empty>
          : (
            <table>
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Scope kind</th>
                  <th>Principal</th>
                  <th>Type</th>
                  <th>Scope</th>
                </tr>
              </thead>
              <tbody>
                {rbac.map((r) => (
                  <tr key={r.assignment_id}>
                    <td>
                      {PRIVILEGED_ROLES.has(r.role_name)
                        ? <span className="pill warn">{r.role_name}</span>
                        : r.role_name}
                    </td>
                    <td><code className="small">{r.scope_kind}</code></td>
                    <td>{r.principal_display_name ?? <code className="small">{r.principal_id}</code>}</td>
                    <td className="small muted">{r.principal_type ?? "—"}</td>
                    <td className="small muted"><code>{r.scope}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </section>

      {data.managed_private_endpoints.length > 0 && (
        <section className="section">
          <h2>Managed private endpoints</h2>
          <table>
            <thead>
              <tr>
                <th>Name</th><th>Group</th><th>Provisioning</th><th>Connection</th><th>Target</th>
              </tr>
            </thead>
            <tbody>
              {data.managed_private_endpoints.map((m) => (
                <tr key={m.name}>
                  <td><code>{m.name}</code></td>
                  <td>{m.group_id ?? "—"}</td>
                  <td>{m.provisioning_state ?? "—"}</td>
                  <td>
                    {(m.connection_state ?? "").toLowerCase() === "approved"
                      ? <span className="pill ok">{m.connection_state}</span>
                      : <span className="pill warn">{m.connection_state ?? "—"}</span>}
                  </td>
                  <td className="small muted"><code>{m.target_resource_id ?? "—"}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data.customer_managed_keys.length > 0 && (
        <section className="section">
          <h2>Customer-managed keys</h2>
          <table>
            <thead>
              <tr>
                <th>Resource kind</th><th>Enabled</th><th>Key vault</th><th>Key</th><th>Resource</th>
              </tr>
            </thead>
            <tbody>
              {data.customer_managed_keys.map((k) => (
                <tr key={k.resource_id + (k.key_name ?? "")}>
                  <td><code className="small">{k.resource_kind}</code></td>
                  <td>
                    {k.enabled
                      ? <span className="pill ok">enabled</span>
                      : <span className="pill warn">disabled</span>}
                  </td>
                  <td className="small muted">{k.key_vault_uri ?? "—"}</td>
                  <td className="small muted">{k.key_name ?? "—"}</td>
                  <td className="small muted"><code>{k.resource_id}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data.purview_account && (
        <section className="section">
          <h2>Microsoft Purview</h2>
          <p>
            Detected Purview account: <code>{data.purview_account}</code>
            {" "}({data.purview_lineage?.length ?? 0} lineage edges captured).
          </p>
        </section>
      )}
    </>
  );
}
