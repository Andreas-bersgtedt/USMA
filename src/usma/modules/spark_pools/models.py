from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SparkPool(BaseModel):
    name: str
    location: str | None = None
    spark_version: str | None = None
    node_size: str | None = None
    node_size_family: str | None = None
    node_count: int | None = None
    auto_scale_enabled: bool = False
    min_node_count: int | None = None
    max_node_count: int | None = None
    auto_pause_enabled: bool = False
    auto_pause_delay_minutes: int | None = None
    isolated_compute_enabled: bool = False
    session_level_packages_enabled: bool = False
    dynamic_executor_allocation_enabled: bool = False
    provisioning_state: str | None = None
    creation_date: datetime | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class Notebook(BaseModel):
    name: str
    folder: str | None = None
    language: str | None = None
    kernel: str | None = None
    attached_spark_pool: str | None = None
    cell_count: int = 0
    source_size_chars: int = 0
    imports: list[str] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)


class SparkJobDefinition(BaseModel):
    name: str
    folder: str | None = None
    language: str | None = None
    target_spark_pool: str | None = None
    main_definition_file: str | None = None
    class_name: str | None = None
    conf: dict[str, Any] = Field(default_factory=dict)
    args: list[str] = Field(default_factory=list)


# --- v2 -----------------------------------------------------------------------

class NotebookLintFinding(BaseModel):
    notebook: str
    rule_id: str
    label: str
    severity: str
    line: int = 0
    snippet: str = ""


class RuntimeMappingResult(BaseModel):
    pool_name: str
    synapse_version: str | None = None
    fabric_runtime: str | None = None
    fabric_spark: str | None = None
    status: str  # matches | upgrade | deprecated | unknown
    note: str | None = None


class WorkspacePackage(BaseModel):
    """A custom library / package attached to the workspace or a Spark pool."""
    scope: str        # "workspace" | "pool:<pool-name>"
    name: str
    version: str | None = None
    package_type: str | None = None  # whl | jar | tar.gz | requirement


class LivyJobRun(BaseModel):
    """A historical Spark job submission. Optional, populated only when the workspace
    artifacts SDK exposes job-history; otherwise this list stays empty."""
    job_name: str
    submission_id: str | None = None
    state: str | None = None
    submitted_at: datetime | None = None
    duration_seconds: int | None = None
    target_spark_pool: str | None = None
    error_summary: str | None = None


class SparkRunRecord(BaseModel):
    """A single Synapse Spark Livy submission (batch or session), enriched with
    vCore-second / CU-hour cost projection.

    `kind`:
      * ``"scheduled"`` — Livy batch jobs (Spark Job Definitions, pipeline
        triggered SparkJob activities, programmatic Livy submissions).
      * ``"interactive"`` — Livy sessions (notebook-attached, REPL).
    """
    livy_id: int
    kind: Literal["scheduled", "interactive"]
    pool: str
    name: str | None = None
    app_id: str | None = None
    submitter_id: str | None = None
    submitter_name: str | None = None
    artifact_id: str | None = None
    state: str | None = None
    result: str | None = None
    outcome: Literal["succeeded", "failed", "in_progress"] = "in_progress"
    submitted_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    driver_cores: int | None = None
    executor_cores: int | None = None
    num_executors: int | None = None
    total_vcores: int | None = None
    vcore_seconds: float | None = None
    vcore_hours: float | None = None
    est_cu_hours_fabric_spark: float | None = Field(
        default=None,
        description="Fabric CU-hours = vCore-hours × 0.5 (1 CU = 2 Spark vCores).",
    )
    # Pipeline correlation (populated when the Spark conf carries the
    # Synapse-injected `spark.synapse.context.*` keys). These let
    # downstream tooling join Spark runs to the parent pipeline / activity
    # without re-querying the Synapse REST API.
    pipeline_job_id: str | None = None
    activity_run_id: str | None = None
    activity_name: str | None = None
    notebook_name: str | None = None
    notebook_run_id: str | None = None


class SparkRunWindowStats(BaseModel):
    """Roll-up of Spark runs for a single trailing-N-days window."""
    window_days: int
    run_count: int = 0
    succeeded: int = 0
    failed: int = 0
    in_progress: int = 0
    total_duration_hours: float = 0.0
    total_vcore_hours: float = 0.0
    est_cu_hours_fabric_spark: float = 0.0
    avg_vcore_hours_per_run: float | None = None
    # v2.6.3 — maximum CU-hours observed in any single UTC day inside
    # this window (sum across all runs of ``est_cu_hours_fabric_spark``
    # bucketed by ``submitted_at.date()``). Drives the Fabric SKU
    # recommendation, which sizes capacity to the busiest day rather than
    # the window average (Fabric's burndown smoothing window is 24h).
    peak_day_cu_hours: float = 0.0


class SparkPoolRunStats(BaseModel):
    """Per-pool, per-kind aggregation of Spark Livy history."""
    pool: str
    kind: Literal["scheduled", "interactive"]
    windows: list[SparkRunWindowStats] = Field(default_factory=list)


class SparkAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    pools: list[SparkPool] = Field(default_factory=list)
    notebooks: list[Notebook] = Field(default_factory=list)
    spark_job_definitions: list[SparkJobDefinition] = Field(default_factory=list)
    # v2
    notebook_lint_findings: list[NotebookLintFinding] = Field(default_factory=list)
    runtime_mappings: list[RuntimeMappingResult] = Field(default_factory=list)
    libraries: list[WorkspacePackage] = Field(default_factory=list)
    job_runs: list[LivyJobRun] = Field(default_factory=list)
    spark_runs: list[SparkRunRecord] = Field(default_factory=list)
    run_stats: list[SparkPoolRunStats] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
