/**
 * ScopeFilter — Phase 3 multi-scope filter pills.
 *
 * Renders a pill row from the authoritative ``RunScope[]`` list returned
 * by ``apiListScopes`` (the same list that drives ``loadModulePerScope``).
 * Pinning the filter to those entries — rather than deriving slugs SPA-side
 * — keeps the join with per-scope artefacts deterministic regardless of how
 * the backend chose its scope directory names.
 *
 * Selection persists in ``sessionStorage`` keyed by run id so the choice
 * survives tab navigation but doesn't leak across runs. Single-scope and
 * empty-scope inputs hide the bar but still return ``{ kind: "all" }`` so
 * callers can wire the same filter path without branching.
 */
import { useCallback, useEffect, useState } from "react";

import type { RunMeta, RunScope, ScopeRef } from "../api/loader";

export type ScopeSelection =
  | { kind: "all" }
  | { kind: "scope"; dir: string };

const STORAGE_PREFIX = "sma:scopeFilter:";

const SOURCE_LABELS: Record<ScopeRef["source_type"], string> = {
  synapse_workspace: "Synapse",
  adf: "ADF",
  databricks: "Databricks",
  bigquery: "BigQuery",
  snowflake: "Snowflake",
  sap_bw: "SAP BW",
};

function storageKey(runId: string | null | undefined): string | null {
  if (!runId) return null;
  return `${STORAGE_PREFIX}${runId}`;
}

function loadSelection(runId: string | null | undefined): ScopeSelection {
  const key = storageKey(runId);
  if (!key) return { kind: "all" };
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return { kind: "all" };
    const parsed = JSON.parse(raw) as ScopeSelection;
    if (parsed && (parsed.kind === "all" || parsed.kind === "scope")) {
      return parsed;
    }
  } catch {
    /* fall through */
  }
  return { kind: "all" };
}

function saveSelection(
  runId: string | null | undefined,
  sel: ScopeSelection,
): void {
  const key = storageKey(runId);
  if (!key) return;
  try {
    if (sel.kind === "all") window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, JSON.stringify(sel));
  } catch {
    /* sessionStorage unavailable — selection is best-effort */
  }
}

/**
 * React hook returning the current scope selection plus a setter.
 *
 * Returns ``{ kind: "all" }`` whenever there are fewer than two scopes,
 * so callers can always trust the filter without checking scope count.
 *
 * @param runId   The current run id, used as the sessionStorage key.
 * @param scopes  Authoritative list of ``RunScope`` entries (typically
 *                from ``apiListScopes`` / ``loadModulePerScope``).
 */
export function useScopeFilter(
  runId: string | null,
  scopes: readonly RunScope[],
): {
  selection: ScopeSelection;
  setSelection: (s: ScopeSelection) => void;
} {
  const multi = scopes.length > 1;
  const [selection, setSelectionState] = useState<ScopeSelection>(() =>
    multi ? loadSelection(runId) : { kind: "all" },
  );

  // Re-hydrate when the run id flips (user switches runs).
  useEffect(() => {
    if (!multi) {
      setSelectionState({ kind: "all" });
      return;
    }
    setSelectionState(loadSelection(runId));
  }, [runId, multi]);

  const setSelection = useCallback(
    (s: ScopeSelection) => {
      // Defensive: if the caller picks a scope that's no longer present
      // (e.g. /scopes refreshed) fall back to "all".
      let next: ScopeSelection = s;
      if (next.kind === "scope") {
        const wantDir = next.dir;
        if (!scopes.some((sc) => sc.dir === wantDir)) {
          next = { kind: "all" };
        }
      }
      setSelectionState(next);
      saveSelection(runId, next);
    },
    [runId, scopes],
  );

  return { selection, setSelection };
}

/** Does a ``RunScope`` (or ``null`` for un-scoped/legacy artefacts)
 * survive the current filter? Un-scoped payloads always pass — they
 * represent single-scope or static-mode runs where there is no sub-scope
 * to pick. */
export function matchesScope(
  scope: RunScope | null,
  selection: ScopeSelection,
): boolean {
  if (selection.kind === "all") return true;
  if (!scope) return true;
  return scope.dir === selection.dir;
}

/** Best-effort match between a ``RunScope`` and a ``ScopeRef`` on
 * ``RunMeta``. Used only to look up a friendlier display name for the
 * pill label — never as a foreign key. */
function findMatchingScopeRef(
  scope: RunScope,
  refs: readonly ScopeRef[],
): ScopeRef | null {
  const sameType = refs.filter((r) => r.source_type === scope.source_type);
  if (sameType.length === 0) return null;
  if (sameType.length === 1) return sameType[0];
  const want = scope.slug.toLowerCase();
  for (const r of sameType) {
    const candidates = [
      r.display_name,
      (r.extras?.workspace_name as string | undefined) ?? "",
      (r.extras?.factory_name as string | undefined) ?? "",
    ];
    if (
      candidates.some(
        (c) =>
          typeof c === "string" &&
          c.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").includes(want),
      )
    ) {
      return r;
    }
  }
  return null;
}

interface ScopeFilterProps {
  /** Authoritative scope list (from ``apiListScopes`` / loader). */
  scopes: readonly RunScope[];
  selection: ScopeSelection;
  onChange: (s: ScopeSelection) => void;
  /** Optional richer labels: pulled from ``RunMeta.scopes`` when available. */
  runMeta?: RunMeta | null;
  /** Per-scope item counts to display as "(<n>)" suffixes. */
  counts?: Record<string, number>;
  /** Total item count across all scopes, shown next to the "All" pill. */
  totalCount?: number;
}

/**
 * Pill row component. Hidden when there are 0 or 1 scopes — pair with the
 * hook to get the always-safe "All" fallback.
 */
export default function ScopeFilter({
  scopes,
  selection,
  onChange,
  runMeta,
  counts,
  totalCount,
}: ScopeFilterProps): JSX.Element | null {
  if (scopes.length <= 1) return null;

  const refs = runMeta?.scopes ?? [];

  return (
    <div
      className="scope-filter"
      role="toolbar"
      aria-label="Filter by scope"
      style={{
        display: "flex",
        gap: 6,
        flexWrap: "wrap",
        alignItems: "center",
        marginBottom: 10,
      }}
    >
      <span className="muted small" style={{ marginRight: 4 }}>
        Scope:
      </span>
      <ScopePill
        active={selection.kind === "all"}
        label={`All${typeof totalCount === "number" ? ` (${totalCount})` : ""}`}
        onClick={() => onChange({ kind: "all" })}
      />
      {scopes.map((s) => {
        const ref = findMatchingScopeRef(s, refs);
        const niceName = ref?.display_name ?? s.slug;
        const sourceLabel = SOURCE_LABELS[s.source_type] ?? s.source_type;
        const baseLabel = `${sourceLabel} · ${niceName}`;
        const count = counts?.[s.dir];
        return (
          <ScopePill
            key={s.dir}
            active={selection.kind === "scope" && selection.dir === s.dir}
            label={typeof count === "number" ? `${baseLabel} (${count})` : baseLabel}
            onClick={() => onChange({ kind: "scope", dir: s.dir })}
          />
        );
      })}
    </div>
  );
}

function ScopePill({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={active ? "pill info" : "pill muted"}
      style={{
        border: "none",
        cursor: "pointer",
        fontWeight: active ? 600 : 400,
      }}
    >
      {label}
    </button>
  );
}
