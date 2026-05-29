"""Pydantic models for the snowflake_workloads analyzer.

Phase 7 Slice 7-C. The shapes mirror :mod:`~..bigquery_workloads.models`
where the concepts line up (``SnowflakeJob`` ↔ ``BigQueryJob``, ``Database``
↔ ``Dataset``) so the SPA Dashboard can fan in Snowflake without bespoke
widgets.

Credit-hour / vCore-hour / CU-hour fields on :class:`Warehouse` and
:class:`SnowflakeJob` are carried from the start so Slice 7-D can fill
them in-place without a schema bump.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ObjectSupport = Literal["supported", "partial", "unsupported", "unknown"]
WarehouseSize = Literal[
    "X-SMALL",
    "SMALL",
    "MEDIUM",
    "LARGE",
    "X-LARGE",
    "2X-LARGE",
    "3X-LARGE",
    "4X-LARGE",
    "5X-LARGE",
    "6X-LARGE",
    "UNKNOWN",
]
WarehouseType = Literal["STANDARD", "SNOWPARK-OPTIMIZED", "UNKNOWN"]
ScalingPolicy = Literal["STANDARD", "ECONOMY", "UNKNOWN"]
TableKind = Literal[
    "TABLE",
    "VIEW",
    "MATERIALIZED_VIEW",
    "EXTERNAL_TABLE",
    "DYNAMIC_TABLE",
    "ICEBERG_TABLE",
    "TEMPORARY",
    "TRANSIENT",
    "UNKNOWN",
]
RoutineKind = Literal[
    "FUNCTION",
    "PROCEDURE",
    "UNKNOWN",
]
RoutineLanguage = Literal[
    "SQL",
    "JAVASCRIPT",
    "PYTHON",
    "JAVA",
    "SCALA",
    "UNKNOWN",
]
TaskState = Literal["STARTED", "SUSPENDED", "UNKNOWN"]
PipeState = Literal["RUNNING", "PAUSED", "STOPPED", "UNKNOWN"]
StreamMode = Literal["DEFAULT", "APPEND_ONLY", "INSERT_ONLY", "UNKNOWN"]
JobOutcome = Literal["succeeded", "failed", "cancelled", "in_progress", "unknown"]


class Warehouse(BaseModel):
    """One Snowflake virtual warehouse.

    Credits-per-hour math: Snowflake bills credits proportional to size
    (X-SMALL=1, SMALL=2, MEDIUM=4, LARGE=8, X-LARGE=16, 2X-LARGE=32, ...).
    Slice 7-D fills ``credits_per_hour`` + ``est_vcore_hours_per_hour``
    based on ``size`` and turns ``WAREHOUSE_METERING_HISTORY`` into
    rolling windows. The ``min_cluster_count`` / ``max_cluster_count``
    fields capture multi-cluster warehouses.
    """

    name: str
    size: WarehouseSize = "UNKNOWN"
    type: WarehouseType = "STANDARD"
    state: str | None = None  # STARTED / SUSPENDED / RESIZING
    min_cluster_count: int = 1
    max_cluster_count: int = 1
    scaling_policy: ScalingPolicy = "STANDARD"
    auto_suspend_seconds: int | None = None
    auto_resume: bool | None = None
    owner: str | None = None
    comment: str | None = None
    created_at: datetime | None = None
    resumed_at: datetime | None = None
    # Filled by Slice 7-D.
    credits_per_hour: float | None = None
    est_vcore_hours_per_hour: float | None = None


class Database(BaseModel):
    """One Snowflake database (the top-level namespace inside an account)."""

    name: str
    owner: str | None = None
    is_transient: bool = False
    is_share: bool = False  # imported share vs locally created
    retention_time_days: int | None = None  # Time-Travel window
    comment: str | None = None
    created_at: datetime | None = None
    last_altered_at: datetime | None = None
    schema_count: int = 0


class Schema(BaseModel):
    """One Snowflake schema (the namespace inside a database)."""

    database_name: str
    name: str
    owner: str | None = None
    is_managed_access: bool = False
    is_transient: bool = False
    retention_time_days: int | None = None
    comment: str | None = None
    created_at: datetime | None = None
    last_altered_at: datetime | None = None
    object_count: int = 0


class Table(BaseModel):
    """A table / view / materialized view / external / dynamic / iceberg table."""

    database_name: str
    schema_name: str
    name: str
    full_name: str  # ``DB.SCHEMA.NAME``
    kind: TableKind = "UNKNOWN"
    is_transient: bool = False
    is_temporary: bool = False
    row_count: int | None = None
    bytes: int | None = None
    cluster_by: str | None = None  # the clustering key expression
    retention_time_days: int | None = None
    comment: str | None = None
    created_at: datetime | None = None
    last_altered_at: datetime | None = None
    support: ObjectSupport = "unknown"
    notes: list[str] = Field(default_factory=list)


class Routine(BaseModel):
    """A user-defined function or stored procedure."""

    database_name: str
    schema_name: str
    name: str
    full_name: str  # ``DB.SCHEMA.NAME(arg_types)``
    routine_kind: RoutineKind = "UNKNOWN"
    language: RoutineLanguage = "UNKNOWN"
    argument_count: int = 0
    is_secure: bool = False
    owner: str | None = None
    comment: str | None = None
    created_at: datetime | None = None
    support: ObjectSupport = "unknown"
    notes: list[str] = Field(default_factory=list)


class Stage(BaseModel):
    """An internal or external Snowflake stage (file landing zone)."""

    database_name: str
    schema_name: str
    name: str
    full_name: str
    stage_type: str | None = None  # INTERNAL / EXTERNAL
    cloud: str | None = None  # AWS / AZURE / GCP for external stages
    url: str | None = None
    storage_integration: str | None = None
    owner: str | None = None
    created_at: datetime | None = None
    support: ObjectSupport = "partial"
    notes: list[str] = Field(default_factory=list)


class Stream(BaseModel):
    """A Snowflake stream (change-data-capture cursor over a table)."""

    database_name: str
    schema_name: str
    name: str
    full_name: str
    source_full_name: str | None = None  # underlying table / view / external table
    mode: StreamMode = "DEFAULT"
    stale: bool | None = None
    stale_after: datetime | None = None
    owner: str | None = None
    created_at: datetime | None = None
    support: ObjectSupport = "partial"
    notes: list[str] = Field(default_factory=list)


class Task(BaseModel):
    """A scheduled / triggered Snowflake task (DAG node)."""

    database_name: str
    schema_name: str
    name: str
    full_name: str
    state: TaskState = "UNKNOWN"
    warehouse: str | None = None
    schedule: str | None = None  # cron or interval
    predecessors: list[str] = Field(default_factory=list)
    condition: str | None = None
    owner: str | None = None
    comment: str | None = None
    created_at: datetime | None = None
    support: ObjectSupport = "partial"
    notes: list[str] = Field(default_factory=list)


class Pipe(BaseModel):
    """A Snowpipe ingestion pipe."""

    database_name: str
    schema_name: str
    name: str
    full_name: str
    state: PipeState = "UNKNOWN"
    integration: str | None = None
    pattern: str | None = None
    owner: str | None = None
    comment: str | None = None
    created_at: datetime | None = None
    support: ObjectSupport = "partial"
    notes: list[str] = Field(default_factory=list)


class SnowflakeJob(BaseModel):
    """One Snowflake query / job extracted from ``ACCOUNT_USAGE.QUERY_HISTORY``.

    Credit-attribution: ``credits_used_cloud_services`` is the per-query
    credit slice attributed to cloud-services billing; warehouse
    credits are accumulated separately via
    ``WAREHOUSE_METERING_HISTORY`` in Slice 7-D. ``bytes_scanned`` and
    ``rows_produced`` mirror BigQuery's ``total_billed_bytes`` /
    ``total_processed_bytes`` for cross-cloud comparability.
    """

    query_id: str
    warehouse_name: str | None = None
    warehouse_size: WarehouseSize | None = None
    user_name: str | None = None
    role_name: str | None = None
    database_name: str | None = None
    schema_name: str | None = None
    query_type: str | None = None  # SELECT / INSERT / CREATE_TABLE_AS_SELECT / ...
    execution_status: str | None = None  # SUCCESS / FAIL / INCIDENT / RESUMING_WAREHOUSE
    outcome: JobOutcome = "unknown"
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None
    total_elapsed_ms: int | None = None
    queued_overload_ms: int | None = None
    compilation_ms: int | None = None
    execution_ms: int | None = None
    bytes_scanned: int | None = None
    bytes_written: int | None = None
    rows_produced: int | None = None
    partitions_scanned: int | None = None
    partitions_total: int | None = None
    credits_used_cloud_services: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    # Filled by Slice 7-D.
    est_credits: float | None = None
    est_vcore_hours: float | None = None
    est_cu_hours_fabric_warehouse: float | None = None


class SnowflakeWarehouseWindowStats(BaseModel):
    """Rolling warehouse metering aggregate over a single window.

    Populated by :mod:`.run_stats` (Slice 7-D). Kept here as a
    placeholder shape so the JSON contract is stable across slices.
    """

    window_days: int
    warehouse_name: str
    total_credits: float = 0.0
    credits_used_compute: float = 0.0
    credits_used_cloud_services: float = 0.0
    total_vcore_hours: float = 0.0
    est_cu_hours_fabric_warehouse: float = 0.0
    active_hours: float | None = None


class SnowflakeJobWindowStats(BaseModel):
    """Rolling stats over a single time window (e.g. last 28 days)."""

    window_days: int
    job_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    success_rate: float | None = None
    avg_duration_seconds: float | None = None
    total_bytes_scanned: int = 0
    total_credits_cloud_services: float = 0.0
    est_cu_hours_fabric_warehouse: float = 0.0


class SnowflakeDailyJobStats(BaseModel):
    """One calendar day (UTC) of Snowflake query activity.

    Mirrors :class:`...bigquery_workloads.models.DailyJobStats` so the
    SPA Dashboard renders Snowflake activity with the same chart shape
    as BigQuery / Databricks / Spark.
    """

    date: str  # YYYY-MM-DD (UTC)
    job_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    other_count: int = 0  # incident / resuming_warehouse / unknown
    total_bytes_scanned: int = 0
    total_execution_ms: int = 0
    total_queued_ms: int = 0
    avg_duration_seconds: float | None = None
    p95_duration_seconds: float | None = None
    success_rate: float | None = None
    est_credits: float = 0.0
    est_cu_hours_fabric_warehouse: float = 0.0


class SnowflakeJobBreakdown(BaseModel):
    """Per-dimension job rollup (query_type / user / warehouse / role).

    Mirrors :class:`...bigquery_workloads.models.JobBreakdown` — single
    flat list keyed by ``dimension`` so one CSV holds every breakdown.
    """

    dimension: str  # "query_type" | "user" | "warehouse" | "role" | "status"
    key: str  # the value on that dimension; ``"<unknown>"`` when null
    job_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    total_bytes_scanned: int = 0
    total_execution_ms: int = 0
    avg_duration_seconds: float | None = None
    est_credits: float = 0.0
    est_cu_hours_fabric_warehouse: float = 0.0


class SnowflakeTableUsage(BaseModel):
    """Top-active-tables rollup mirroring the Synapse "SQL Surface" page.

    Two collection paths feed this:

    * **Enterprise+**: ``SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY`` flatten
      of ``BASE_OBJECTS_ACCESSED`` gives true per-table read counts.
    * **Standard / fallback**: when ACCESS_HISTORY is unavailable, the
      analyzer groups jobs by ``(database_name, schema_name)`` from
      ``QUERY_HISTORY`` — ``object_domain`` is set to ``"SCHEMA"`` and
      ``full_name`` reads ``DB.SCHEMA.*`` so consumers can tell the
      difference. ``source`` distinguishes the two for the UI.
    """

    full_name: str  # ``DB.SCHEMA.TABLE`` (or ``DB.SCHEMA.*`` for fallback)
    database_name: str
    schema_name: str
    object_name: str  # ``*`` in fallback mode
    object_domain: str = "TABLE"  # TABLE / VIEW / SCHEMA (fallback) / UNKNOWN
    usage_count: int = 0  # distinct jobs that referenced the object
    total_bytes_scanned: int = 0
    total_rows_produced: int = 0
    total_execution_ms: int = 0
    last_seen: datetime | None = None
    in_catalog: bool = True  # False when not present in result.tables
    table_support: ObjectSupport = "unknown"
    source: str = "access_history"  # access_history | query_history_fallback


class SnowflakeCodeObjectSummary(BaseModel):
    """Per-account roll-up of table / view / routine compatibility.

    Mirrors :class:`...dedicated_pools.models.CodeObjectSummary` so the
    UI can show a single "code-object compatibility" card with the same
    grammar across Synapse and Snowflake (total / by_kind /
    by_support / compatibility_pct + lists of partial / unsupported
    object full_names).
    """

    total: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_support: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, int] = Field(default_factory=dict)
    compatibility_pct: float | None = None
    unsupported_object_names: list[str] = Field(default_factory=list)
    partial_object_names: list[str] = Field(default_factory=list)
    unknown_object_names: list[str] = Field(default_factory=list)


class SnowflakeWorkloadsAnalysis(BaseModel):
    """Top-level result type written as ``snowflake_workloads.json``."""

    account: str  # the Snowflake account identifier
    platform: str | None = None  # aws | azure | gcp (from extras)
    region: str | None = None  # raw ``CURRENT_REGION()`` value
    edition: str | None = None  # STANDARD | ENTERPRISE | BUSINESS_CRITICAL | VPS
    generated_at: datetime
    warehouses: list[Warehouse] = Field(default_factory=list)
    databases: list[Database] = Field(default_factory=list)
    schemas: list[Schema] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    routines: list[Routine] = Field(default_factory=list)
    stages: list[Stage] = Field(default_factory=list)
    streams: list[Stream] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    pipes: list[Pipe] = Field(default_factory=list)
    jobs: list[SnowflakeJob] = Field(default_factory=list)
    warehouse_window_stats: list[SnowflakeWarehouseWindowStats] = Field(default_factory=list)
    job_window_stats: list[SnowflakeJobWindowStats] = Field(default_factory=list)
    daily_stats: list[SnowflakeDailyJobStats] = Field(default_factory=list)
    breakdowns: list[SnowflakeJobBreakdown] = Field(default_factory=list)
    table_usage: list[SnowflakeTableUsage] = Field(default_factory=list)
    code_object_summary: SnowflakeCodeObjectSummary | None = None
    caveats: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    # Roll-ups (filled by the analyzer).
    warehouse_count: int = 0
    database_count: int = 0
    schema_count: int = 0
    table_count: int = 0
    view_count: int = 0
    materialized_view_count: int = 0
    external_table_count: int = 0
    dynamic_table_count: int = 0
    iceberg_table_count: int = 0
    routine_count: int = 0
    stage_count: int = 0
    stream_count: int = 0
    task_count: int = 0
    pipe_count: int = 0
    job_count: int = 0
    unsupported_object_count: int = 0
    partial_object_count: int = 0


__all__ = [
    "Warehouse",
    "Database",
    "Schema",
    "Table",
    "Routine",
    "Stage",
    "Stream",
    "Task",
    "Pipe",
    "SnowflakeJob",
    "SnowflakeJobWindowStats",
    "SnowflakeWarehouseWindowStats",
    "SnowflakeDailyJobStats",
    "SnowflakeJobBreakdown",
    "SnowflakeTableUsage",
    "SnowflakeCodeObjectSummary",
    "SnowflakeWorkloadsAnalysis",
    "ObjectSupport",
    "WarehouseSize",
    "WarehouseType",
    "ScalingPolicy",
    "TableKind",
    "RoutineKind",
    "RoutineLanguage",
    "TaskState",
    "PipeState",
    "StreamMode",
    "JobOutcome",
]
