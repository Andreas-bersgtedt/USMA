"""Aggregator that maps prior module outputs to Fabric Warehouse recommendations."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ...config import AppConfig
from ...effort import (
    EffortRollup,
    RateCard,
    build_estimates,
    build_rollup,
    days_from_hours,
    default_card,
)
from . import cu_projection, readiness, runbook, rules
from .models import (
    CapacityProjection,
    EffortSummary,
    FabricMappingReport,
    ModuleSummary,
    PhaseEffortSummary,
    ReadinessSummary,
    Recommendation,
    RunbookStep,
)

if TYPE_CHECKING:
    from ...progress import ProgressReporter

log = logging.getLogger(__name__)


_INPUT_FILES: dict[str, str] = {
    "dedicated_pools": "dedicated_pools.json",
    "serverless_pools": "serverless_pools.json",
    "spark_pools": "spark_pools.json",
    "pipelines": "pipelines.json",
    "monitoring": "monitoring.json",
    "storage": "storage.json",
    "databricks_workflows": "databricks_workflows.json",
    "bigquery_workloads": "bigquery_workloads.json",
    "snowflake_workloads": "snowflake_workloads.json",
}

_RULES: dict[str, Callable[[dict[str, Any]], list[Recommendation]]] = {
    "dedicated_pools": rules.rules_for_dedicated_pools,
    "serverless_pools": rules.rules_for_serverless,
    "spark_pools": rules.rules_for_spark,
    "pipelines": rules.rules_for_pipelines,
    "monitoring": rules.rules_for_monitoring,
    "storage": rules.rules_for_storage,
    "databricks_workflows": rules.rules_for_databricks_workflows,
    "bigquery_workloads": rules.rules_for_bigquery_workloads,
    "snowflake_workloads": rules.rules_for_snowflake_workloads,
}


def _summarize(module: str, payload: dict[str, Any], path: Path) -> ModuleSummary:
    counts: dict[str, int] = {}
    for k, v in payload.items():
        if isinstance(v, list):
            counts[k] = len(v)
    return ModuleSummary(module=module, source_file=str(path), counts=counts)


class FabricMappingAnalyzer:
    """Reads JSON outputs of other modules from `cfg.output_dir` and emits a recommendation report."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: "ProgressReporter | None" = None,
        effort_card: RateCard | None = None,
        effort_card_source: str = "default",
    ) -> None:
        from ...progress import NullProgress

        self._cfg = cfg
        self._progress = progress or NullProgress()
        self._effort_card = effort_card or default_card()
        self._effort_card_source = effort_card_source

    def run(self) -> FabricMappingReport:
        report = FabricMappingReport(
            workspace_name=self._cfg.azure.workspace_name,
            generated_at=datetime.now(timezone.utc),
        )

        # v3 \u2014 source platform of the active scope. Drives source-aware
        # phrasing in the pipelines rule + the runbook rollback hints, and
        # is stamped on every emitted Recommendation / RunbookStep so the
        # SPA can label and filter them per source.
        primary = self._cfg.primary_scope()
        source_type: str | None = str(primary.type) if primary is not None else None

        # 1 step per upstream module load + 4 rollups (readiness, tsql, runbook,
        # capacity projection).
        self._progress.start(len(_INPUT_FILES) + 4, label="loading module outputs")

        loaded: dict[str, dict[str, Any]] = {}
        for module, fname in _INPUT_FILES.items():
            path = self._cfg.output_dir / fname
            if not path.is_file():
                log.info("Skipping %s; %s not found.", module, path)
                self._progress.step(label=f"{module} (missing)")
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to load %s: %s", path, exc)
                report.inputs.append(ModuleSummary(
                    module=module, source_file=str(path), notes=[f"load error: {exc}"]
                ))
                self._progress.step(label=f"{module} (error)")
                continue

            loaded[module] = payload
            report.inputs.append(_summarize(module, payload, path))
            try:
                if module == "pipelines":
                    new_recs = rules.rules_for_pipelines(payload, source_type=source_type)
                else:
                    new_recs = _RULES[module](payload)
                # Stamp source_type on every recommendation so the SPA can
                # label / filter and so runbook rollback hints can pick the
                # right phrasing.
                for r in new_recs:
                    if r.source_type is None:
                        r.source_type = source_type
                report.recommendations.extend(new_recs)
            except Exception as exc:  # noqa: BLE001
                log.warning("Rules for %s failed: %s", module, exc)
                report.inputs[-1].notes.append(f"rules error: {exc}")
            self._progress.step(label=module)

        # v2 — readiness score.
        try:
            r = readiness.score_recommendations(report.recommendations)
            report.readiness = ReadinessSummary(
                score=r.score, bucket=r.bucket, counts=r.counts,
                top_blockers=list(r.top_blockers),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("readiness scoring failed: %s", exc)
        self._progress.step(label="readiness")

        # v2 — T-SQL surface compatibility rollup across all dedicated pools.
        # The dedicated_pools module already classifies each code object as
        # compatible / needs_review / incompatible; here we sum across pools
        # so the executive summary can show "% T-SQL compatible".
        try:
            dp = loaded.get("dedicated_pools") or {}
            total = 0
            compatible = 0
            incompatible = 0
            needs_review = 0
            for pool in dp.get("pools") or []:
                for obj in pool.get("code_objects") or []:
                    total += 1
                    compat = obj.get("compatibility") or "compatible"
                    if compat == "compatible":
                        compatible += 1
                    elif compat == "needs_review":
                        needs_review += 1
                    elif compat == "incompatible":
                        incompatible += 1
            if total and report.readiness is not None:
                report.readiness.tsql_compatibility_pct = round(
                    compatible / total * 100, 1,
                )
                report.readiness.tsql_objects_total = total
                report.readiness.tsql_objects_incompatible = incompatible
                report.readiness.tsql_objects_needs_review = needs_review
        except Exception as exc:  # noqa: BLE001
            log.warning("T-SQL compatibility rollup failed: %s", exc)
        self._progress.step(label="tsql_rollup")

        # v2 — sequenced migration runbook.
        try:
            steps = runbook.build_runbook(report.recommendations)
            report.runbook = [
                RunbookStep(
                    phase=s.phase, order=s.order, title=s.title, detail=s.detail,
                    severity=s.severity, effort=s.effort, target=s.target,
                    rollback=s.rollback,
                    source_recommendation_id=s.source_recommendation_id,
                    source_type=s.source_type,
                )
                for s in steps
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("runbook generation failed: %s", exc)
        self._progress.step(label="runbook")

        # v2 — Fabric capacity projection. Combines DW DWU peak with
        # Spark Livy + Pipelines sustained CU-hours so the recommended
        # SKU covers all three workload classes that share a Fabric
        # capacity, not just the dedicated SQL pool peak.
        try:
            mon = loaded.get("monitoring") or {}
            proj = cu_projection.project_capacity(
                mon.get("series") or [],
                spark_payload=loaded.get("spark_pools"),
                pipelines_payload=loaded.get("pipelines"),
                serverless_payload=loaded.get("serverless_pools"),
            )
            if proj is not None:
                report.capacity_projection = CapacityProjection(
                    peak_dwu=proj.peak_dwu,
                    peak_dwu_with_headroom=proj.peak_dwu_with_headroom,
                    estimated_cu=proj.estimated_cu,
                    recommended_sku=proj.recommended_sku,
                    headroom_pct=proj.headroom_pct,
                    notes=list(proj.notes),
                    dwu_cu_contribution=proj.dwu_cu_contribution,
                    spark_cu_contribution=proj.spark_cu_contribution,
                    pipelines_cu_contribution=proj.pipelines_cu_contribution,
                    serverless_cu_contribution=proj.serverless_cu_contribution,
                    serverless_peak_day_cu_hours=proj.serverless_peak_day_cu_hours,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("capacity projection failed: %s", exc)
        self._progress.step(label="capacity_projection")

        # v2.10 — configurable effort estimates per runbook step.
        try:
            estimates = build_estimates(
                report.runbook, report.recommendations, loaded, self._effort_card,
            )
            est_by_order = {e.step_order: e for e in estimates}
            for step in report.runbook:
                est = est_by_order.get(step.order)
                if est is None:
                    continue
                step.effort_hours_p50 = est.p50_hours
                step.effort_hours_p90 = est.p90_hours
                step.effort_breakdown = est.breakdown.to_dict()
            rollup: EffortRollup = build_rollup(
                report.runbook,
                estimates,
                card_source=self._effort_card_source,
                card_version=self._effort_card.version,
            )
            report.effort_summary = EffortSummary(
                total_p50_hours=rollup.total_p50_hours,
                total_p90_hours=rollup.total_p90_hours,
                total_p50_days=days_from_hours(rollup.total_p50_hours),
                total_p90_days=days_from_hours(rollup.total_p90_hours),
                per_phase=[
                    PhaseEffortSummary(
                        phase=p.phase,
                        p50_hours=p.p50_hours,
                        p90_hours=p.p90_hours,
                        step_count=p.step_count,
                        p50_days=days_from_hours(p.p50_hours),
                        p90_days=days_from_hours(p.p90_hours),
                        parallel_p50_hours=p.parallel_p50_hours,
                        parallel_p90_hours=p.parallel_p90_hours,
                        parallel_p50_days=days_from_hours(p.parallel_p50_hours),
                        parallel_p90_days=days_from_hours(p.parallel_p90_hours),
                        max_step_p50_hours=p.max_step_p50_hours,
                        max_step_p90_hours=p.max_step_p90_hours,
                    )
                    for p in rollup.per_phase
                ],
                card_source=rollup.card_source,
                card_version=rollup.card_version,
                parallel_p50_hours=rollup.parallel_p50_hours,
                parallel_p90_hours=rollup.parallel_p90_hours,
                parallel_p50_days=days_from_hours(rollup.parallel_p50_hours),
                parallel_p90_days=days_from_hours(rollup.parallel_p90_hours),
                parallel_workers=rollup.parallel_workers,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("effort estimation failed: %s", exc)

        return report
