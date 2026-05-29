"""Pydantic models for the databricks_workflows analyzer."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TaskSupport = Literal["supported", "partial", "unsupported", "unknown"]
RunOutcome = Literal["succeeded", "failed", "in_progress", "unknown"]
AutoscaleWorkerStrategy = Literal["min", "avg", "max"]


class WorkflowTask(BaseModel):
    """One task within a Databricks job (a.k.a. workflow step)."""

    job_id: int
    job_name: str
    task_key: str
    task_type: str  # notebook_task | spark_python_task | sql_task | pipeline_task | run_job_task | ...
    depends_on: list[str] = Field(default_factory=list)
    notebook_path: str | None = None
    python_file: str | None = None
    sql_query_id: str | None = None
    sql_warehouse_id: str | None = None  # ``sql_task.warehouse_id`` when present
    dlt_pipeline_id: str | None = None
    cluster_kind: Literal["job_cluster", "existing_cluster", "serverless", "unknown"] = "unknown"
    cluster_ref: str | None = None  # job_cluster_key or existing_cluster_id
    support: TaskSupport = "unknown"
    notes: list[str] = Field(default_factory=list)


class JobCluster(BaseModel):
    """A job-scoped cluster declared inside a workflow."""

    job_id: int
    job_name: str
    job_cluster_key: str
    spark_version: str | None = None
    node_type_id: str | None = None
    driver_node_type_id: str | None = None
    num_workers: int | None = None
    autoscale_min: int | None = None
    autoscale_max: int | None = None
    data_security_mode: str | None = None
    runtime_engine: str | None = None  # PHOTON / STANDARD


class InteractiveCluster(BaseModel):
    """An all-purpose / interactive cluster on the workspace."""

    cluster_id: str
    cluster_name: str
    state: str | None = None
    spark_version: str | None = None
    node_type_id: str | None = None
    driver_node_type_id: str | None = None
    num_workers: int | None = None
    autoscale_min: int | None = None
    autoscale_max: int | None = None
    data_security_mode: str | None = None
    runtime_engine: str | None = None
    pinned: bool = False
    creator_user_name: str | None = None


class Workflow(BaseModel):
    """A Databricks job (Jobs 2.x = "Workflow")."""

    job_id: int
    name: str
    creator_user_name: str | None = None
    run_as_user_name: str | None = None
    schedule_cron: str | None = None  # quartz_cron_expression
    schedule_timezone: str | None = None
    schedule_pause_status: str | None = None  # UNPAUSED / PAUSED
    max_concurrent_runs: int | None = None
    task_count: int = 0
    task_types: list[str] = Field(default_factory=list)
    job_cluster_count: int = 0
    uses_serverless: bool = False
    tags: dict[str, str] = Field(default_factory=dict)
    format: str | None = None  # SINGLE_TASK / MULTI_TASK
    has_continuous: bool = False


class DatabricksWorkflowsAnalysis(BaseModel):
    """Top-level result type written as ``databricks_workflows.json``."""

    workspace_name: str
    workspace_url: str | None = None
    workspace_id: str | None = None
    subscription_id: str | None = None
    resource_group: str | None = None
    generated_at: datetime
    workflows: list[Workflow] = Field(default_factory=list)
    tasks: list[WorkflowTask] = Field(default_factory=list)
    job_clusters: list[JobCluster] = Field(default_factory=list)
    interactive_clusters: list[InteractiveCluster] = Field(default_factory=list)
    workflow_runs: list["WorkflowRun"] = Field(default_factory=list)
    workflow_run_stats: list["WorkflowRunStats"] = Field(default_factory=list)
    interactive_cluster_usage: list["InteractiveClusterUsage"] = Field(default_factory=list)
    sql_warehouses: list["SqlWarehouse"] = Field(default_factory=list)
    sql_warehouse_queries: list["SqlWarehouseQuery"] = Field(default_factory=list)
    sql_warehouse_stats: list["SqlWarehouseStats"] = Field(default_factory=list)
    sql_warehouse_daily_usage: list["SqlWarehouseDailyUsage"] = Field(default_factory=list)
    sql_warehouse_fabric_mappings: list["SqlWarehouseFabricMapping"] = Field(
        default_factory=list,
    )
    cluster_sizing_caveats: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    # Roll-ups (filled by the analyzer).
    workflow_count: int = 0
    task_count: int = 0
    unsupported_task_count: int = 0
    partial_task_count: int = 0
    sql_warehouse_count: int = 0
    sql_warehouse_query_count: int = 0


class WorkflowRun(BaseModel):
    """One completed (or in-progress) execution of a Databricks job.

    Mirrors :class:`...spark_pools.models.SparkRunRecord` so the same
    downstream pipelines (CU-hour projection, Dashboard rollups) can
    consume Databricks data without bespoke shapes.
    """

    job_id: int
    job_name: str
    run_id: int
    run_name: str | None = None
    run_type: str | None = None  # JOB_RUN | WORKFLOW_RUN | SUBMIT_RUN
    trigger: str | None = None  # PERIODIC | ONE_TIME | RETRY | RUN_JOB_TASK | ...
    state: str | None = None  # life_cycle_state
    result: str | None = None  # result_state
    outcome: RunOutcome = "unknown"
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None  # from execution_duration (ms) → seconds
    cluster_kind: Literal["job_cluster", "existing_cluster", "serverless", "unknown"] = "unknown"
    cluster_id: str | None = None  # from cluster_instance.cluster_id when present
    node_type_id: str | None = None
    driver_node_type_id: str | None = None
    num_workers: int | None = None  # effective worker count used for the math
    worker_count_source: Literal["static", "autoscale_min", "autoscale_avg", "autoscale_max", "unknown"] = "unknown"
    driver_vcores: int | None = None
    worker_vcores: int | None = None
    total_vcores: int | None = None
    vcore_seconds: float | None = None
    vcore_hours: float | None = None
    est_cu_hours_fabric_spark: float | None = None
    run_page_url: str | None = None


class WorkflowRunWindowStats(BaseModel):
    """Rolling stats over a single time window (e.g. last 28 days)."""

    window_days: int
    run_count: int = 0
    completed_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    success_rate: float | None = None  # 0..1
    avg_duration_seconds: float | None = None
    total_vcore_hours: float = 0.0
    avg_vcore_hours_per_run: float | None = None
    est_cu_hours_fabric_spark: float = 0.0


class WorkflowRunStats(BaseModel):
    """Per-job rolling-window aggregate (7/14/28/90 d by default)."""

    job_id: int
    job_name: str
    total_runs_observed: int = 0
    windows: list[WorkflowRunWindowStats] = Field(default_factory=list)


class InteractiveClusterUsage(BaseModel):
    """Reconstructed RUNNING-time usage for one all-purpose cluster.

    Computed from the cluster ``events`` API: we pair start/terminate
    transitions to derive total uptime per window, then multiply by
    the cluster's vCore shape (using the same worker-count strategy as
    workflow runs).
    """

    cluster_id: str
    cluster_name: str | None = None
    node_type_id: str | None = None
    driver_node_type_id: str | None = None
    num_workers: int | None = None
    worker_count_source: Literal["static", "autoscale_min", "autoscale_avg", "autoscale_max", "unknown"] = "unknown"
    driver_vcores: int | None = None
    worker_vcores: int | None = None
    total_vcores: int | None = None
    windows: list[WorkflowRunWindowStats] = Field(default_factory=list)


class SqlWarehouse(BaseModel):
    """A Databricks SQL Warehouse (formerly "SQL endpoint").

    Captured from ``ws.warehouses.list()`` / ``warehouses.get()``. These
    are the compute resources that power the Databricks SQL surface and
    any ``sql_task`` referenced from a Workflow.
    """

    warehouse_id: str
    name: str
    warehouse_type: str | None = None  # CLASSIC | PRO | SERVERLESS (alias for enable_serverless_compute)
    cluster_size: str | None = None  # "2X-Small", "Small", "Large", ...
    state: str | None = None  # STARTING | RUNNING | STOPPING | STOPPED | DELETING
    auto_stop_mins: int | None = None
    enable_serverless_compute: bool | None = None
    enable_photon: bool | None = None
    channel: str | None = None  # CHANNEL_NAME_CURRENT | CHANNEL_NAME_PREVIEW
    min_num_clusters: int | None = None
    max_num_clusters: int | None = None
    num_clusters: int | None = None  # current cluster count
    num_active_sessions: int | None = None
    creator_name: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    spot_instance_policy: str | None = None
    jdbc_url: str | None = None


class SqlWarehouseQuery(BaseModel):
    """One executed query from ``ws.query_history.list(...)``.

    Note: query text is truncated to ``QUERY_TEXT_TRUNCATE_CHARS`` to
    keep report sizes bounded on busy warehouses.
    """

    query_id: str
    warehouse_id: str | None = None
    status: str | None = None  # FINISHED | FAILED | CANCELED | RUNNING | QUEUED
    statement_type: str | None = None  # SELECT | INSERT | DELETE | UPDATE | OTHER | ...
    user_name: str | None = None
    executed_as_user_name: str | None = None
    query_source: str | None = None  # JOB | DASHBOARD | NOTEBOOK | API | ALERT | UNKNOWN
    query_text: str | None = None  # truncated
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None
    # Sum of per-task CPU time across all executor tasks for the query,
    # in seconds. Captured from ``query_metrics.task_total_time_ms``
    # when ``query_history.list`` is called with ``include_metrics=True``.
    # Typically populated for CLASSIC / PRO warehouses; Databricks SQL
    # **Serverless** warehouses omit this metric from the REST API and
    # leave it ``None`` — use the Unity Catalog ``system.query.history``
    # path (see ``SqlWarehouseDailyUsage``) for CPU time on serverless.
    cpu_seconds: float | None = None
    rows_produced: int | None = None
    bytes_read: int | None = None
    bytes_written: int | None = None


class SqlWarehouseStats(BaseModel):
    """Per-warehouse aggregate over the query-history lookback window."""

    warehouse_id: str
    warehouse_name: str | None = None
    lookback_days: int
    query_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    canceled_count: int = 0
    success_rate: float | None = None  # 0..1
    total_duration_seconds: float = 0.0
    avg_duration_seconds: float | None = None
    p50_duration_seconds: float | None = None
    p95_duration_seconds: float | None = None
    total_rows_produced: int = 0
    total_bytes_read: int = 0
    unique_users: int = 0
    queries_from_jobs: int = 0  # ``query_source == "JOB"``
    # CPU-time rollup. ``total_cpu_seconds`` sums ``SqlWarehouseQuery.cpu_seconds``
    # over the lookback window; ``queries_with_cpu_metric`` is the number
    # of queries that actually contributed (i.e. had a non-null value).
    # On a Serverless warehouse both will typically be ``0`` / ``None``
    # because Databricks does not surface task-time metrics over REST for
    # serverless SQL — fall back to ``SqlWarehouseDailyUsage`` for that.
    total_cpu_seconds: float | None = None
    queries_with_cpu_metric: int = 0


class SqlWarehouseDailyUsage(BaseModel):
    """Per-day usage rollup for one SQL warehouse, sourced from Unity
    Catalog system tables (``system.query.history`` + ``system.billing.usage``).

    Populated only when ``SMA_DATABRICKS_SYSTEM_TABLES=1`` and the
    service principal has ``SELECT`` on the relevant system schemas
    (granted by an account admin via Unity Catalog). The queries run
    through ``statement_execution`` against a warehouse the SP can use
    (chosen via ``SMA_DATABRICKS_SYSTEM_TABLES_WAREHOUSE`` or the first
    warehouse returned by ``warehouses.list``).

    This is the canonical source for CPU-time and DBU consumption on
    **Serverless** SQL warehouses, where REST query metrics are omitted.
    """

    warehouse_id: str
    warehouse_name: str | None = None
    usage_date: str  # ISO date (YYYY-MM-DD), UTC
    query_count: int = 0
    total_task_seconds: float | None = None  # ≈ CPU-seconds from system.query.history
    dbu_hours: float | None = None  # from system.billing.usage
    sku_name: str | None = None


class SqlWarehouseFabricMapping(BaseModel):
    """Deterministic per-warehouse Fabric target + sizing recommendation.

    Built by the analyzer after ``SqlWarehouseStats`` and (optionally)
    ``SqlWarehouseDailyUsage`` are populated. The recommendation is a
    pure function of the warehouse type and the observed usage signals,
    so the same input produces the same F-SKU every run.

    Sizing math
    -----------
    ``avg_concurrent_cus`` is the average number of Fabric capacity units
    consumed continuously across the lookback window:

        avg_concurrent_cus = cpu_seconds / (lookback_days * 86400)

    where ``cpu_seconds`` is ``SqlWarehouseStats.total_cpu_seconds`` (REST
    ``include_metrics`` path) when available, falling back to the sum of
    ``SqlWarehouseDailyUsage.total_task_seconds`` (Unity Catalog
    ``system.query.history`` path) on Serverless warehouses where REST
    does not surface task time.

    The recommended F-SKU is the smallest standard Fabric capacity that
    accommodates ``avg_concurrent_cus * peak_to_avg_headroom`` (default
    headroom = 4× to absorb interactive BI bursts that the lookback
    average smooths over).
    """

    warehouse_id: str
    warehouse_name: str | None = None
    source_warehouse_type: str | None = None  # CLASSIC | PRO | SERVERLESS
    target_fabric_artifact: str = "Fabric Warehouse"
    recommended_sku: str | None = None  # "F2" .. "F2048" or "F2048+"; None when no signal
    support: TaskSupport = "unknown"
    confidence: Literal["high", "medium", "low"] = "low"
    # Sizing inputs.
    lookback_days: int | None = None
    total_cpu_seconds: float | None = None  # SqlWarehouseStats.total_cpu_seconds
    queries_with_cpu_metric: int = 0
    total_task_seconds: float | None = None  # Σ SqlWarehouseDailyUsage.total_task_seconds
    total_dbu_hours: float | None = None  # Σ SqlWarehouseDailyUsage.dbu_hours
    avg_concurrent_cus: float | None = None  # derived; basis for SKU pick
    peak_to_avg_headroom: float = 4.0
    evidence_source: Literal["rest_metrics", "system_tables", "both", "none"] = "none"
    notes: list[str] = Field(default_factory=list)
