/**
 * EstateSummaryCard — Phase 3 dashboard widget showing portfolio-wide
 * readiness across every scope in a run.
 *
 * Phase-0 stub. Not yet wired into the dashboard.
 */
import type { SourceTypeId } from "./sourceTypes";

export interface EstateSummaryEntry {
  type: SourceTypeId;
  scopeCount: number;
  readinessPct: number; // 0..100
}

export interface EstateSummaryCardProps {
  entries: EstateSummaryEntry[];
  overallReadinessPct: number;
}

const TYPE_LABEL: Record<SourceTypeId, string> = {
  synapse_workspace: "Synapse",
  adf: "ADF",
  databricks: "Databricks",
  bigquery: "BigQuery",
  sap_bw: "SAP BW",
  sql_server: "SQL Server",
  snowflake: "Snowflake",
};

export default function EstateSummaryCard({
  entries,
  overallReadinessPct,
}: EstateSummaryCardProps): JSX.Element {
  return (
    <section className="card estate-summary">
      <header>
        <h3>Estate readiness</h3>
        <div className="big-number">{overallReadinessPct.toFixed(0)}%</div>
      </header>
      <table className="table compact">
        <thead>
          <tr>
            <th>Source</th>
            <th>Scopes</th>
            <th>Readiness</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.type}>
              <td>{TYPE_LABEL[e.type] ?? e.type}</td>
              <td>{e.scopeCount}</td>
              <td>{e.readinessPct.toFixed(0)}%</td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr>
              <td colSpan={3} className="muted">
                No scopes in this run.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}
