import { useCallback, useEffect, useMemo, useState } from "react";
import { loadModulePerScope, type RunScope } from "../api/loader";
import { useAsync } from "../hooks/useAsync";
import { Empty, SeverityPill } from "../components/Atoms";
import HelpLink from "../components/HelpLink";
import ScopeFilter, { matchesScope, useScopeFilter } from "../components/ScopeFilter";
import { useCurrentRunMeta } from "../components/Provenance";
import { effortLabel, phaseLabel, sourceLabel } from "../lib/labels";
import type { FabricMappingReport, RunbookStep } from "../types";

/**
 * Phase 3 — runbook steps gain an inline scope tag in multi-scope runs.
 *
 * We keep the underlying `RunbookStep` shape untouched and tag each step
 * with the dir + a friendly label as separate fields prefixed with `_`,
 * so any code path that already iterates `data.runbook` keeps working.
 */
type ScopedRunbookStep = RunbookStep & {
  _scopeDir: string | null;
  _scopeLabel: string | null;
};

interface RunbookBundle {
  steps: ScopedRunbookStep[];
  scopes: RunScope[];
  /** First scope's report — drives the effort-summary card when "All" is picked. */
  primary: FabricMappingReport | null;
  /** Per-scope reports keyed by scope dir — used when a scope is filtered. */
  byDir: Record<string, FabricMappingReport>;
  workspaceName: string | null;
}

async function loadRunbookBundle(): Promise<RunbookBundle> {
  const entries = await loadModulePerScope<FabricMappingReport>("fabric_mapping");
  const steps: ScopedRunbookStep[] = [];
  const byDir: Record<string, FabricMappingReport> = {};
  let primary: FabricMappingReport | null = null;
  let workspaceName: string | null = null;
  for (const { scope, payload } of entries) {
    if (!payload) continue;
    if (!primary) primary = payload;
    if (!workspaceName) workspaceName = payload.workspace_name ?? null;
    const scopeDir = scope?.dir ?? null;
    const scopeLabel = scope ? `${sourceLabel(scope.source_type)} · ${scope.slug}` : null;
    if (scope) byDir[scope.dir] = payload;
    for (const step of payload.runbook ?? []) {
      steps.push({ ...step, _scopeDir: scopeDir, _scopeLabel: scopeLabel });
    }
  }
  const scopes = entries
    .map((e) => e.scope)
    .filter((s): s is RunScope => s !== null);
  return { steps, scopes, primary, byDir, workspaceName };
}

// v2.11 — step status is persisted in localStorage so a planner can
// open the SPA, tick steps off across multiple sessions, and not lose
// state. We key on (run_id, source_recommendation_id ?? phase-order).
type StepStatus = "not-started" | "in-progress" | "done" | "skipped";

interface StepStateRecord {
  status: StepStatus;
  skipReason?: string;
}

const STATUS_LABEL: Record<StepStatus, string> = {
  "not-started": "Not started",
  "in-progress": "In progress",
  done: "Done",
  skipped: "Skipped",
};

const STORAGE_KEY_PREFIX = "sma.runbook.status.";

function loadAllStates(runKey: string): Record<string, StepStateRecord> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY_PREFIX + runKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function saveAllStates(runKey: string, states: Record<string, StepStateRecord>) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY_PREFIX + runKey, JSON.stringify(states));
  } catch {
    /* quota — best effort */
  }
}

function stepKey(s: RunbookStep): string {
  return s.source_recommendation_id || `${s.phase}-${s.order}`;
}

function escapeMarkdownCell(value: string | null | undefined): string {
  if (!value) return "";
  return value.replace(/\|/g, "\\|").replace(/\r?\n/g, " ").trim();
}

function buildMarkdown(
  steps: ScopedRunbookStep[],
  states: Record<string, StepStateRecord>,
  heading: string,
): string {
  const lines: string[] = [];
  lines.push(`# ${heading}`);
  lines.push("");
  const hasScope = steps.some((s) => s._scopeLabel);
  lines.push(
    hasScope
      ? "| # | Status | Severity | Effort | P50 h | P90 h | Step | Target | Scope |"
      : "| # | Status | Severity | Effort | P50 h | P90 h | Step | Target |",
  );
  lines.push(
    hasScope
      ? "|---|---|---|---|---|---|---|---|---|"
      : "|---|---|---|---|---|---|---|---|",
  );
  for (const s of steps) {
    const key = stepKey(s);
    const st = states[key]?.status ?? "not-started";
    const label = STATUS_LABEL[st];
    const skipNote = st === "skipped" && states[key]?.skipReason
      ? ` _(reason: ${escapeMarkdownCell(states[key]?.skipReason)})_`
      : "";
    const base =
      `| ${s.order} | ${label}${skipNote} | ${s.severity} | ${s.effort} | ${
        s.effort_hours_p50 != null ? s.effort_hours_p50.toFixed(1) : ""
      } | ${s.effort_hours_p90 != null ? s.effort_hours_p90.toFixed(1) : ""} | ${
        escapeMarkdownCell(s.title)
      } | ${escapeMarkdownCell(s.target)}`;
    lines.push(hasScope ? `${base} | ${escapeMarkdownCell(s._scopeLabel)} |` : `${base} |`);
  }
  return lines.join("\n");
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  return false;
}

export default function Runbook() {
  const { data, loading } = useAsync(loadRunbookBundle);
  const runMeta = useCurrentRunMeta();
  const scopes = data?.scopes ?? [];
  const { selection, setSelection } = useScopeFilter(runMeta?.id ?? null, scopes);

  // Steps surviving the scope filter. Un-scoped steps (legacy single-scope
  // runs) always pass — `matchesScope(null, …)` is true by design.
  const filteredSteps = useMemo<ScopedRunbookStep[]>(() => {
    if (!data) return [];
    return data.steps.filter((s) =>
      matchesScope(
        s._scopeDir ? ({ dir: s._scopeDir } as RunScope) : null,
        selection,
      ),
    );
  }, [data, selection]);

  // Per-pill counts honour the active scope-agnostic step set — pills
  // always reflect the run's total layout, not whatever's currently
  // selected (otherwise picking a scope would zero out every other pill).
  const scopeCounts = useMemo<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const s of data?.steps ?? []) {
      if (!s._scopeDir) continue;
      out[s._scopeDir] = (out[s._scopeDir] ?? 0) + 1;
    }
    return out;
  }, [data]);

  // When a single scope is picked, surface that scope's effort summary +
  // workspace name; "all" falls back to the primary report's summary so
  // the existing single-scope UX is unchanged.
  const activeReport = useMemo<FabricMappingReport | null>(() => {
    if (!data) return null;
    if (selection.kind === "scope" && data.byDir[selection.dir]) {
      return data.byDir[selection.dir];
    }
    return data.primary;
  }, [data, selection]);

  const workspaceName =
    activeReport?.workspace_name ?? data?.workspaceName ?? null;
  const runKey = workspaceName || activeReport?.generated_at || "default";
  const [states, setStates] = useState<Record<string, StepStateRecord>>({});

  useEffect(() => {
    if (!data) return;
    setStates(loadAllStates(runKey));
  }, [data, runKey]);

  const persist = useCallback(
    (next: Record<string, StepStateRecord>) => {
      setStates(next);
      saveAllStates(runKey, next);
    },
    [runKey],
  );

  const setStatus = useCallback(
    (key: string, status: StepStatus) => {
      const prev = states[key];
      let next: StepStateRecord;
      if (status === "skipped") {
        const reason = window.prompt(
          "Why are you skipping this step? (required, kept in this browser)",
          prev?.skipReason ?? "",
        );
        if (reason == null) return;
        const trimmed = reason.trim();
        if (!trimmed) return;
        next = { status, skipReason: trimmed };
      } else {
        next = { status };
      }
      persist({ ...states, [key]: next });
    },
    [states, persist],
  );

  const byPhase = useMemo(() => {
    const out = new Map<string, ScopedRunbookStep[]>();
    for (const s of filteredSteps) {
      const arr = out.get(s.phase) ?? [];
      arr.push(s);
      out.set(s.phase, arr);
    }
    for (const arr of out.values()) arr.sort((a, b) => a.order - b.order);
    return out;
  }, [filteredSteps]);

  if (loading) return <div className="empty">Loading…</div>;
  if (!data || data.steps.length === 0)
    return <Empty>No runbook generated.</Empty>;

  const es = activeReport?.effort_summary;

  const copyWhole = async () => {
    const ok = await copyToClipboard(
      buildMarkdown(
        filteredSteps,
        states,
        `Migration runbook — ${workspaceName ?? "workspace"}`,
      ),
    );
    if (!ok) window.alert("Clipboard unavailable.");
  };

  return (
    <>
      <h1>
        Migration runbook <HelpLink slug="07-runbook" />
        <button type="button" className="small" style={{ marginLeft: 12 }} onClick={copyWhole}>
          Copy whole runbook as Markdown
        </button>
      </h1>
      <ScopeFilter
        scopes={scopes}
        selection={selection}
        onChange={setSelection}
        runMeta={runMeta}
        counts={scopeCounts}
        totalCount={data.steps.length}
      />
      {es && (
        <section className="card" style={{ margin: "0.5rem 0 1rem 0" }}>
          <div className="label" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
            <span>
              Estimated effort:{" "}
              <strong>{es.total_p50_hours.toFixed(1)} h P50</strong>{" /"}
              {" "}<strong>{es.total_p90_hours.toFixed(1)} h P90</strong>
              {es.total_p50_days != null && es.total_p90_days != null && (
                <>
                  {" — "}
                  <strong>{es.total_p50_days} P50</strong>
                  {" / "}
                  <strong>{es.total_p90_days} P90</strong>{" resource-days"}
                </>
              )}
            </span>
            {es.parallel_p50_hours != null && es.parallel_p90_hours != null && (
              <span className="small">
                {" · with "}
                <strong>{es.parallel_workers ?? 2}</strong>
                {" workers in parallel: "}
                <strong>{es.parallel_p50_hours.toFixed(1)} h P50</strong>
                {" / "}
                <strong>{es.parallel_p90_hours.toFixed(1)} h P90</strong>
                {es.parallel_p50_days != null && es.parallel_p90_days != null && (
                  <>
                    {" ("}<strong>{es.parallel_p50_days}</strong>
                    {" / "}<strong>{es.parallel_p90_days}</strong> calendar-days)
                  </>
                )}
              </span>
            )}
            <span className="small muted">
              · rate card{" "}
              {es.card_source === "default" ? (
                <span className="pill info">shipped default</span>
              ) : (
                <code title={es.card_source}>override</code>
              )}{" "}
              (v{es.card_version})
              {" · "}
              <a href="/configuration">edit on Configuration ▸ Effort card</a>
              {" · "}
              <HelpLink slug="19-effort" />
            </span>
            {es.total_p50_days != null && (
              <span
                className="small muted"
                title="Resource-days = ceil((hours / 8) × 1.15) — 8 h/day plus 15 % spillage."
                style={{ width: "100%" }}
              >
                Days computed as <code>ceil((hours / 8) × 1.15)</code> — 8 h/day plus 15 % spillage.
                Parallel days assume phases stay sequential but steps within a phase parallelise across the workers shown.
              </span>
            )}
          </div>
          {es.per_phase.length > 0 && (
            <table className="small" style={{ marginTop: 6 }}>
              <thead>
                <tr>
                  <th>Phase</th>
                  <th className="num">Steps</th>
                  <th className="num">P50 (h)</th>
                  <th className="num">P90 (h)</th>
                  <th className="num" title="Resource-days at P50">P50 (d)</th>
                  <th className="num" title="Resource-days at P90">P90 (d)</th>
                  <th className="num" title="Parallel P50 hours">∥ P50 (h)</th>
                  <th className="num" title="Parallel P90 hours">∥ P90 (h)</th>
                </tr>
              </thead>
              <tbody>
                {es.per_phase.map((p) => (
                  <tr key={p.phase}>
                    <td>{phaseLabel(p.phase)}</td>
                    <td className="num">{p.step_count}</td>
                    <td className="num">{p.p50_hours.toFixed(1)}</td>
                    <td className="num">{p.p90_hours.toFixed(1)}</td>
                    <td className="num">{p.p50_days ?? "—"}</td>
                    <td className="num">{p.p90_days ?? "—"}</td>
                    <td className="num">{p.parallel_p50_hours != null ? p.parallel_p50_hours.toFixed(1) : "—"}</td>
                    <td className="num">{p.parallel_p90_hours != null ? p.parallel_p90_hours.toFixed(1) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
      {Array.from(byPhase.entries()).map(([phase, steps]) => {
        const total = steps.length;
        const done = steps.filter((s) => states[stepKey(s)]?.status === "done").length;
        const skipped = steps.filter((s) => states[stepKey(s)]?.status === "skipped").length;
        const remainingHours = steps
          .filter((s) => {
            const st = states[stepKey(s)]?.status;
            return st !== "done" && st !== "skipped";
          })
          .reduce((acc, s) => acc + (s.effort_hours_p50 ?? 0), 0);
        const copyPhase = async () => {
          const ok = await copyToClipboard(
            buildMarkdown(steps, states, `Runbook phase — ${phaseLabel(phase)}`),
          );
          if (!ok) window.alert("Clipboard unavailable.");
        };
        // Surface the scope column in the All view of a multi-scope run
        // so a planner can tell at a glance which workspace/factory each
        // step is for. Hidden otherwise to avoid wasted column width.
        const showScopeCol = scopes.length > 1 && selection.kind === "all";
        return (
          <section className="section" key={phase}>
            <h2 title={phase} style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              {phaseLabel(phase)}
              <span className="small muted">
                — {done}/{total} done
                {skipped > 0 ? ` · ${skipped} skipped` : ""}
                {" · "}
                est. <strong>{remainingHours.toFixed(1)} h</strong> P50 remaining
              </span>
              <button type="button" className="small" onClick={copyPhase}>
                Copy phase as Markdown
              </button>
            </h2>
            <table>
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Status</th>
                  <th>Step</th>
                  <th>Severity</th>
                  <th>Effort</th>
                  <th className="num" title="50th-percentile estimate">P50 (h)</th>
                  <th className="num" title="90th-percentile estimate">P90 (h)</th>
                  <th>Target</th>
                  {showScopeCol && <th>Scope</th>}
                </tr>
              </thead>
              <tbody>
                {steps.map((s) => {
                  const breakdown = s.effort_breakdown;
                  const tooltip = breakdown?.components?.length
                    ? breakdown.components.join(" + ")
                    : undefined;
                  const key = stepKey(s);
                  const record = states[key];
                  const status: StepStatus = record?.status ?? "not-started";
                  const rowClass =
                    status === "done" ? "row-done"
                    : status === "skipped" ? "row-skipped"
                    : status === "in-progress" ? "row-active"
                    : "";
                  return (
                    <tr
                      key={`${phase}-${s.order}`}
                      className={rowClass}
                      style={
                        status === "done"
                          ? { opacity: 0.55 }
                          : status === "skipped"
                          ? { opacity: 0.5, textDecoration: "line-through" }
                          : undefined
                      }
                    >
                      <td className="num">{s.order}</td>
                      <td className="small">
                        <select
                          value={status}
                          onChange={(e) => setStatus(key, e.target.value as StepStatus)}
                          title={record?.skipReason ? `Skip reason: ${record.skipReason}` : undefined}
                        >
                          <option value="not-started">Not started</option>
                          <option value="in-progress">In progress</option>
                          <option value="done">Done</option>
                          <option value="skipped">Skipped…</option>
                        </select>
                        {status === "skipped" && record?.skipReason && (
                          <div className="muted" style={{ marginTop: 4, maxWidth: 220 }}>
                            {record.skipReason}
                          </div>
                        )}
                      </td>
                      <td>
                        <details>
                          <summary>{s.title}</summary>
                          <div className="small" style={{ marginTop: 6 }}>{s.detail}</div>
                          {s.rollback && (
                            <div className="small muted" style={{ marginTop: 6 }}>
                              <strong>Rollback:</strong> {s.rollback}
                            </div>
                          )}
                          {breakdown?.components && breakdown.components.length > 0 && (
                            <div className="small muted" style={{ marginTop: 6 }}>
                              <strong>Effort breakdown:</strong> {breakdown.components.join(" + ")}
                              {breakdown.capped ? " (capped)" : ""}
                            </div>
                          )}
                        </details>
                      </td>
                      <td><SeverityPill severity={s.severity} /></td>
                      <td title={s.effort}>{effortLabel(s.effort)}</td>
                      <td className="num" title={tooltip}>
                        {s.effort_hours_p50 != null ? s.effort_hours_p50.toFixed(1) : ""}
                      </td>
                      <td className="num" title={tooltip}>
                        {s.effort_hours_p90 != null ? s.effort_hours_p90.toFixed(1) : ""}
                      </td>
                      <td className="small muted">{s.target}</td>
                      {showScopeCol && (
                        <td className="small">
                          {s._scopeLabel ? (
                            <span className="pill muted">{s._scopeLabel}</span>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        );
      })}
    </>
  );
}

