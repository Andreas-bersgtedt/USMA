/**
 * Provenance hook + badge — v2.6.1.
 *
 * When a control-plane run carries module artefacts forward from a prior
 * workspace-matched run (because the user only refreshed a subset of
 * modules), the per-module ``carried_from_run_id`` / ``carried_from_started_at``
 * fields on :class:`RunMeta` tell us *which* run originally produced
 * the artefact and *when*. These helpers surface that as a small "from
 * <relative-time> ago" pill so users can never mistake stale data for
 * fresh.
 *
 * In static (CLI output/) mode the current run isn't addressable, so the
 * hook returns ``null`` and the badge renders nothing — the surrounding
 * markup stays unchanged.
 */
import { useEffect, useState } from "react";

import { apiGetRun, detectMode, getRunIdFromHash, type RunMeta } from "../api/loader";

export type ModuleProvenance = {
  carried: boolean;
  /** ISO timestamp at which the artefact was originally produced. */
  sourceStartedAt: string | null;
  /** Run id of the original producer (may differ from the current run). */
  sourceRunId: string | null;
};

/** Look up the current run's meta. Returns ``null`` in static mode or
 * while loading / when no run is selected. Pure side-effect-free read; the
 * SPA caches via the loader, but here we keep it intentionally simple. */
export function useCurrentRunMeta(): RunMeta | null {
  const [meta, setMeta] = useState<RunMeta | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mode = await detectMode();
      if (mode !== "control-plane") return;
      const runId = getRunIdFromHash();
      if (!runId) return;
      try {
        const m = await apiGetRun(runId);
        if (!cancelled) setMeta(m);
      } catch {
        /* swallow — provenance is decorative, not load-bearing */
      }
    })();
    return () => { cancelled = true; };
  }, []);
  return meta;
}

/** Look up provenance for a specific module on the current run. */
export function moduleProvenance(meta: RunMeta | null, module: string): ModuleProvenance {
  if (!meta) return { carried: false, sourceStartedAt: null, sourceRunId: null };
  const ms = meta.modules.find((m) => m.name === module);
  if (!ms) return { carried: false, sourceStartedAt: null, sourceRunId: null };
  if (ms.state !== "carried") return { carried: false, sourceStartedAt: null, sourceRunId: null };
  return {
    carried: true,
    sourceStartedAt: ms.carried_from_started_at ?? null,
    sourceRunId: ms.carried_from_run_id ?? null,
  };
}

/** Render a relative age string like "2h ago" / "3d ago". */
function relativeAge(iso: string | null): string {
  if (!iso) return "from a previous run";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "from a previous run";
  const diffMs = Date.now() - t;
  if (diffMs < 0) return "from a previous run";
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

/** Small inline "carried from prior run" pill. Renders ``null`` when the
 * module was produced by the current run (or in static mode). */
export function ProvenanceBadge({
  meta, module,
}: {
  meta: RunMeta | null;
  module: string;
}) {
  const prov = moduleProvenance(meta, module);
  if (!prov.carried) return null;
  const age = relativeAge(prov.sourceStartedAt);
  const title = prov.sourceStartedAt
    ? `Carried forward from run ${prov.sourceRunId ?? "?"} (generated ${new Date(prov.sourceStartedAt).toLocaleString()})`
    : `Carried forward from run ${prov.sourceRunId ?? "?"}`;
  return (
    <span
      className="pill muted small"
      style={{ marginLeft: 8, verticalAlign: "middle" }}
      title={title}
    >
      ↺ carried · {age}
    </span>
  );
}
