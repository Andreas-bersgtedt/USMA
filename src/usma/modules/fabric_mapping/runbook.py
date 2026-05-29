"""Generate a sequenced migration runbook from the recommendations list.

The runbook is a *suggested* ordering, not a promise. The phases below mirror how
real Synapse → Fabric migrations tend to play out:

1. **Foundation** — capacity, workspace, RBAC, networking. (Recommendations from
   `monitoring.*` and `pipelines.integration_runtimes`.)
2. **Data plane prep** — collation, distribution-key choices, T-SQL surface fixes.
   (Recommendations from `dedicated_pools.*` except inventory.)
3. **Ingest & shortcuts** — serverless external tables, OneLake shortcuts.
   (Recommendations from `serverless_pools.*`.)
4. **Compute migration** — Spark pools, notebooks, SJDs.
   (Recommendations from `spark_pools.*`.)
5. **Orchestration migration** — pipelines, linked services, triggers.
   (Recommendations from `pipelines.*` except integration_runtimes.)
6. **Verification & cutover** — re-run analyzers post-migration, sign-off.
   (No recommendations — added unconditionally.)

Each step is annotated with severity, effort, target, and explicit rollback notes
where applicable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .models import Recommendation

Phase = Literal[
    "foundation",
    "data_plane_prep",
    "ingest_shortcuts",
    "compute_migration",
    "orchestration_migration",
    "verification",
]


@dataclass(frozen=True)
class RunbookStep:
    phase: Phase
    order: int
    title: str
    detail: str
    severity: str
    effort: str
    target: str | None = None
    rollback: str | None = None
    source_recommendation_id: str | None = None
    # v3 — source platform inherited from the originating recommendation.
    # ``None`` for the unconditional verification step (platform-neutral).
    source_type: str | None = None


_AREA_TO_PHASE: dict[str, Phase] = {
    "monitoring.dwu": "foundation",
    "monitoring.connections": "foundation",
    "pipelines.integration_runtimes": "foundation",
    "dedicated_pools.inventory": "data_plane_prep",
    "dedicated_pools.tables": "data_plane_prep",
    "dedicated_pools.indexes": "data_plane_prep",
    "dedicated_pools.workload_management": "data_plane_prep",
    "dedicated_pools.tsql_surface": "data_plane_prep",
    "dedicated_pools.distribution_advisor": "data_plane_prep",
    "dedicated_pools.materialized_views": "data_plane_prep",
    "dedicated_pools.statistics": "data_plane_prep",
    "serverless_pools.external_tables": "ingest_shortcuts",
    "serverless_pools.cost": "ingest_shortcuts",
    "serverless_pools.queries": "ingest_shortcuts",
    "serverless_pools.cost_attribution": "ingest_shortcuts",
    "spark_pools.runtime": "compute_migration",
    "spark_pools.notebooks": "compute_migration",
    "spark_pools.lint": "compute_migration",
    "spark_pools.libraries": "compute_migration",
    "pipelines.activities": "orchestration_migration",
    "pipelines.linked_services": "orchestration_migration",
    "pipelines.triggers": "orchestration_migration",
    "pipelines.expressions": "orchestration_migration",
    # v2.11 — storage module recommendations land in Foundation (capacity
    # planning) for sizing-related items and in ingest_shortcuts for OneLake
    # shortcut work.
    "storage.dedicated_pool": "foundation",
    "storage.accounts": "ingest_shortcuts",
    # Phase 4 Slice 4-C — Databricks workflows + clusters.
    "databricks_workflows": "orchestration_migration",
    "databricks_workflows.tasks": "orchestration_migration",
    "databricks_workflows.clusters": "compute_migration",
}

_PHASE_ORDER: tuple[Phase, ...] = (
    "foundation", "data_plane_prep", "ingest_shortcuts",
    "compute_migration", "orchestration_migration", "verification",
)


def build_runbook(recommendations: Iterable[Recommendation]) -> list[RunbookStep]:
    grouped: dict[Phase, list[Recommendation]] = {p: [] for p in _PHASE_ORDER}
    for r in recommendations:
        phase = _phase_for_area(r.area)
        grouped[phase].append(r)

    steps: list[RunbookStep] = []
    order = 0
    for phase in _PHASE_ORDER:
        if phase == "verification":
            continue
        # Within each phase, blockers first, then warnings, then info.
        bucket = sorted(
            grouped.get(phase, []),
            key=lambda r: (("blocker", "warning", "info").index(r.severity), r.id),
        )
        for rec in bucket:
            order += 1
            steps.append(RunbookStep(
                phase=phase, order=order,
                title=rec.title,
                detail=rec.fabric_action or rec.detail,
                severity=rec.severity,
                effort=rec.effort,
                target=rec.target,
                rollback=_rollback_hint_for(rec),
                source_recommendation_id=rec.id,
                source_type=rec.source_type,
            ))
    # Verification is always present. Platform-neutral wording so it reads
    # correctly whether the source is a Synapse workspace, an ADF factory,
    # or a future Databricks workspace.
    order += 1
    steps.append(RunbookStep(
        phase="verification", order=order,
        title="Re-run all analyzers against the migrated workload",
        detail="Run `sma analyze-all` against the source scope one more time and against any "
               "lift-and-shift surfaces in Fabric. Diff the recommendations to confirm blockers cleared.",
        severity="info", effort="low",
        rollback="Keep the source scope (workspace / factory) running read-only for the agreed cutover window "
                 "before deleting; have a rollback DNS / connection-string flip ready.",
    ))
    return steps


def _phase_for_area(area: str) -> Phase:
    return _AREA_TO_PHASE.get(area, "data_plane_prep")


def _rollback_hint_for(rec: Recommendation) -> str | None:
    if rec.area.startswith("dedicated_pools"):
        return "Snapshot the dedicated pool before any DDL change; pause+restore is the rollback."
    if rec.area.startswith("pipelines"):
        if rec.source_type == "adf":
            return "Keep the Azure Data Factory pipeline disabled (not deleted) until the Fabric pipeline runs cleanly twice."
        return "Keep the Synapse pipeline disabled (not deleted) until the Fabric pipeline runs cleanly twice."
    if rec.area.startswith("databricks_workflows.clusters"):
        return "Leave the Databricks job clusters / all-purpose clusters in place until the Fabric Spark runtime parity is validated."
    if rec.area.startswith("databricks_workflows"):
        return "Keep the Databricks workflow paused (not deleted) until the Fabric pipeline + notebook port runs cleanly twice."
    if rec.area.startswith("spark_pools"):
        return "Maintain a Synapse Spark pool fallback for 1 sprint after the Fabric notebook cuts over."
    if rec.area.startswith("serverless_pools"):
        return "OneLake shortcuts are read-only — rollback is just deleting the shortcut."
    if rec.area.startswith("storage"):
        return "Keep the source storage account online and read-only until OneLake shortcuts are verified."
    if rec.area.startswith("monitoring"):
        return None
    return None
