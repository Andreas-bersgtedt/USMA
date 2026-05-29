from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ActivitySupport = Literal["supported", "partial", "unsupported", "unknown"]


class Activity(BaseModel):
    pipeline: str
    name: str
    type: str
    depends_on: list[str] = Field(default_factory=list)
    support: ActivitySupport = "supported"
    references_pipeline: str | None = None
    references_dataset: str | None = None
    references_linked_service: str | None = None
    notes: list[str] = Field(default_factory=list)
    # v3 — richer compatibility analysis (see fabric_compat.analyze_activity)
    support_reasons: list[str] = Field(default_factory=list)
    support_caveats: list[str] = Field(default_factory=list)
    fabric_equivalent: str | None = None
    migration_action: str | None = None
    doc_url: str | None = None
    # Mapping Data Flow only: the static ``compute.coreCount`` declared on the
    # ExecuteDataFlow activity (total Spark cluster vCores: driver + workers).
    # ``None`` when the activity uses the default autoresolve IR or expresses
    # ``coreCount`` as a runtime expression. ``run_stats`` falls back to a
    # configurable default in that case.
    dataflow_cores: int | None = None
    # Mapping Data Flow only: ``compute.computeType`` (``General`` /
    # ``MemoryOptimized`` / ``ComputeOptimized``). Informational only.
    dataflow_compute_type: str | None = None


class Pipeline(BaseModel):
    name: str
    folder: str | None = None
    activity_count: int = 0
    activity_types: list[str] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)
    unsupported_activity_count: int = 0
    partial_activity_count: int = 0


class LinkedService(BaseModel):
    name: str
    type: str
    connect_via: str | None = None  # integration runtime reference
    annotations: list[str] = Field(default_factory=list)
    fabric_supported: bool = True


class Dataset(BaseModel):
    name: str
    type: str
    linked_service: str | None = None
    folder: str | None = None


class Trigger(BaseModel):
    name: str
    type: str
    runtime_state: str | None = None
    pipelines: list[str] = Field(default_factory=list)
    # Raw type-specific payload (ScheduleTrigger.recurrence,
    # TumblingWindowTrigger.frequency/interval, etc.). Populated by
    # each collector via ``properties.as_dict()`` so the downstream
    # schedule_mapper can resolve cadence without a per-source branch.
    type_properties: dict[str, Any] | None = None


class IntegrationRuntime(BaseModel):
    name: str
    type: str  # Managed / SelfHosted
    description: str | None = None


# --- v2 -----------------------------------------------------------------------

class ExpressionFinding(BaseModel):
    rule_id: str
    label: str
    severity: str   # info | warning | blocker
    pipeline: str
    activity: str
    expression: str


class ScheduleMapping(BaseModel):
    trigger_name: str
    trigger_type: str
    fabric_kind: str   # recurring | tumbling | event | manual | unknown
    every_n: int | None = None
    interval: str | None = None
    days_of_week: list[str] = Field(default_factory=list)
    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    notes: list[str] = Field(default_factory=list)
    summary: str | None = None


# --- v3 -- pipeline run history -----------------------------------------------

# Rolling windows (in days) reported per pipeline. The widest window also
# determines the fetch range.
RUN_STATS_WINDOWS_DAYS: tuple[int, ...] = (7, 14, 28, 90)


class PipelineRunWindowStats(BaseModel):
    window_days: int
    run_count: int = 0
    succeeded: int = 0
    failed: int = 0
    other: int = 0  # InProgress, Queued, Cancelled, etc.
    success_rate: float | None = None      # succeeded / (succeeded + failed); None when both are zero
    avg_duration_ms: float | None = None
    p95_duration_ms: float | None = None
    avg_data_moved_mb_per_run: float | None = None  # over runs that performed data movement
    total_data_moved_mb: float | None = None        # None when pipeline has no data-movement activities
    # Azure-IR Data Integration Units consumed (sum of billableDuration[].duration
    # in DIUHours across all data-movement activity runs in the window).
    avg_diu_hours_per_run: float | None = None
    total_diu_hours: float | None = None
    # Heuristic Fabric CU-hours equivalent for the consumed DIU-hours
    # (= total_diu_hours * DIU_TO_CU_HOURS, default 1.5). None when no DIU
    # billing was observed for the pipeline in this window.
    est_cu_hours_from_diu: float | None = None
    # Mapping Data Flow Spark compute, in vCore-hours, as reported by
    # ``billableDuration[]`` entries with units coreHour / vCoreHour on
    # ExecuteDataFlow activity runs. ``None`` when no vCore-time billing
    # was observed in this window.
    avg_vcore_hours_per_run: float | None = None
    total_vcore_hours: float | None = None
    # Fabric Spark CU-hours equivalent, assuming a migration target of a
    # Fabric Spark job, at the rate **1 vCore-second = 0.5 CU-second**
    # (= total_vcore_hours * 0.5). None when no vCore billing observed.
    est_cu_hours_from_vcore: float | None = None
    # Data Orchestration: Microsoft charges 0.0056 CU-hours per non-copy
    # activity run (Fabric capacity meters). We estimate the count of
    # non-copy activity runs as (static non-copy activity count in pipeline
    # definition) * (pipeline run_count in window). This ignores ForEach /
    # Until / If branches that may execute the same activity multiple times
    # or skip it, so it is a baseline approximation.
    est_non_copy_activity_runs: int = 0
    est_cu_hours_from_orchestration: float = 0.0
    # v2.6.3 — maximum total CU-hours (DIU + MDF vCore + orchestration)
    # observed in any single UTC day inside this window. Used by the
    # Fabric SKU recommender so the recommended capacity covers the
    # busiest day, not the window average. ``None`` when no runs were
    # observed in this window (vs. ``0.0`` when runs occurred but none
    # produced CU consumption — e.g. all instantaneous failures).
    peak_day_cu_hours: float | None = None


class PipelineRunStats(BaseModel):
    pipeline: str
    has_data_movement: bool = False
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    windows: list[PipelineRunWindowStats] = Field(default_factory=list)


class PipelineRunHistory(BaseModel):
    window_start: datetime
    window_end: datetime
    fetched_run_count: int = 0
    fetched_activity_run_count: int = 0
    truncated: bool = False
    by_pipeline: list[PipelineRunStats] = Field(default_factory=list)
    # Per-UTC-day rollup of run outcomes across *all* pipelines in the
    # fetched window. Keys are ISO date strings (``YYYY-MM-DD``) and
    # values are counts {succeeded, failed, other}. Empty when no runs
    # were fetched. Powers the daily success/failure stacked bar chart.
    daily_status: dict[str, dict[str, int]] = Field(default_factory=dict)
    # Per-UTC-hour rollup covering the trailing 24 hours of the window
    # (24 entries when populated). Keys are ISO hour strings
    # (``YYYY-MM-DDTHH:00:00Z``) and values are counts
    # {succeeded, failed, other}. Powers the 24h companion bar chart on
    # the dashboard alongside ``daily_status``.
    hourly_status: dict[str, dict[str, int]] = Field(default_factory=dict)


class PipelinesAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    artifacts_endpoint: str
    generated_at: datetime
    pipelines: list[Pipeline] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    linked_services: list[LinkedService] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    triggers: list[Trigger] = Field(default_factory=list)
    integration_runtimes: list[IntegrationRuntime] = Field(default_factory=list)
    # v2
    expression_findings: list[ExpressionFinding] = Field(default_factory=list)
    schedule_mappings: list[ScheduleMapping] = Field(default_factory=list)
    # v3 — pipeline run history (None when collection skipped or unavailable)
    run_history: PipelineRunHistory | None = None
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
