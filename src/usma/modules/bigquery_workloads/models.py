"""Pydantic models for the bigquery_workloads analyzer.

Shapes mirror ``databricks_workflows.models`` where the concepts line up
(``BigQueryJob`` ↔ ``WorkflowRun``, ``Dataset`` ↔ ``Workflow`` in the
"top-level inventory bucket" sense) so the SPA Dashboard and Fabric
CU-projection pipeline can fan in BigQuery without bespoke widgets.

Slot-hour → CU-hour fields (``total_slot_ms``, ``slot_hours``, ``est_cu_hours_fabric_spark``)
are carried on :class:`BigQueryJob` from the start so Slice 5-C can fill
them in-place without a schema bump.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TableSupport = Literal["supported", "partial", "unsupported", "unknown"]
TableKind = Literal[
    "TABLE",
    "VIEW",
    "MATERIALIZED_VIEW",
    "EXTERNAL",
    "SNAPSHOT",
    "CLONE",
    "UNKNOWN",
]
RoutineKind = Literal[
    "SCALAR_FUNCTION",
    "PROCEDURE",
    "TABLE_VALUED_FUNCTION",
    "AGGREGATE_FUNCTION",
    "UNKNOWN",
]
RoutineLanguage = Literal["SQL", "JAVASCRIPT", "PYTHON", "UNKNOWN"]
JobType = Literal["QUERY", "LOAD", "EXTRACT", "COPY", "UNKNOWN"]
JobOutcome = Literal["succeeded", "failed", "cancelled", "in_progress", "unknown"]
# BigQuery Data Transfer Service ``TransferConfig.state`` reports the
# state of the *latest run*; the canonical enum is PENDING / RUNNING /
# SUCCEEDED / FAILED / CANCELLED. We additionally accept ENABLED /
# DISABLED / PAUSED for back-compat with earlier fixtures that mirrored
# the config's lifecycle rather than the latest-run state.
TransferState = Literal[
    "ENABLED",
    "DISABLED",
    "PAUSED",
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "UNKNOWN",
]


class Dataset(BaseModel):
    """One BigQuery dataset (the namespace inside a project)."""

    project_id: str
    dataset_id: str
    location: str | None = None
    friendly_name: str | None = None
    description: str | None = None
    default_table_expiration_ms: int | None = None
    default_partition_expiration_ms: int | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    table_count: int = 0
    created_at: datetime | None = None
    last_modified_at: datetime | None = None


class Table(BaseModel):
    """A table / view / materialized view / external table inside a dataset."""

    project_id: str
    dataset_id: str
    table_id: str
    full_table_id: str  # ``project.dataset.table``
    table_type: TableKind = "UNKNOWN"
    partition_field: str | None = None
    partition_type: str | None = None  # DAY / HOUR / MONTH / YEAR / RANGE
    require_partition_filter: bool | None = None
    clustering_fields: list[str] = Field(default_factory=list)
    num_rows: int | None = None
    num_bytes: int | None = None
    created_at: datetime | None = None
    last_modified_at: datetime | None = None
    expiration_at: datetime | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    support: TableSupport = "unknown"
    notes: list[str] = Field(default_factory=list)


class Routine(BaseModel):
    """A user-defined function / stored procedure / table function."""

    project_id: str
    dataset_id: str
    routine_id: str
    routine_type: RoutineKind = "UNKNOWN"
    language: RoutineLanguage = "UNKNOWN"
    arguments_count: int = 0
    created_at: datetime | None = None
    last_modified_at: datetime | None = None
    support: TableSupport = "unknown"
    notes: list[str] = Field(default_factory=list)


class ScheduledQuery(BaseModel):
    """A Data Transfer Service scheduled-query config.

    BigQuery scheduled queries are stored as ``transferConfigs`` whose
    ``data_source_id`` is ``"scheduled_query"``. Display name + schedule
    string are surfaced verbatim from the API.
    """

    name: str  # ``projects/<n>/locations/<loc>/transferConfigs/<id>``
    display_name: str
    location: str
    destination_dataset_id: str | None = None
    schedule: str | None = None
    state: TransferState = "UNKNOWN"
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    user_id: str | None = None
    params_query: str | None = None  # the underlying SQL


class BigQueryJob(BaseModel):
    """One BigQuery job extracted from a Cloud Logging audit-log entry.

    Field shape borrows the audit-log path conventions documented in the
    ``constantino-casado/GCP_logs`` reference implementation (the data
    we care about lives on ``protoPayload.metadata.jobChange.job`` for
    the modern v2 audit logs and ``protoPayload.serviceData.jobCompletedEvent.job``
    for legacy entries — :mod:`.collector` tries both).

    Slot-hour math: ``total_slot_ms`` divided by 3.6e6 gives slot-hours.
    Slice 5-C populates ``slot_hours`` + ``est_cu_hours_fabric_spark``.
    """

    job_id: str
    project_id: str
    location: str | None = None
    user_email: str | None = None
    job_type: JobType = "UNKNOWN"
    statement_type: str | None = None  # SELECT / INSERT / CREATE_TABLE_AS_SELECT / ...
    state: str | None = None  # PENDING / RUNNING / DONE
    outcome: JobOutcome = "unknown"
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float | None = None
    total_slot_ms: int | None = None
    total_billed_bytes: int | None = None
    total_processed_bytes: int | None = None
    # Fully-qualified ``project.dataset.table`` ids of every table the job
    # touched. Powers the per-table usage map (Slice 5-J). Empty list is
    # the canonical "no tables referenced" value — kept distinct from
    # ``None`` so consumers can tell "field unsupported" (older artefacts
    # via ``referenced_table_count`` only) from "job touched zero tables".
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_table_count: int = 0
    cache_hit: bool | None = None
    error_result: str | None = None
    reservation_id: str | None = None
    # Additional INFORMATION_SCHEMA.JOBS_BY_PROJECT columns captured for
    # daily-execution post-processing (analogous to dedicated-pool run
    # history). ``query_text`` is **only** populated when
    # ``SMA_BQ_CAPTURE_QUERY_TEXT=1`` is set — gated because SQL text
    # commonly carries embedded literals / PII and inflates artefact size.
    priority: str | None = None  # INTERACTIVE | BATCH
    labels: dict[str, str] = Field(default_factory=dict)
    parent_job_id: str | None = None  # set for child jobs in scripting / multi-statement
    edition: str | None = None  # STANDARD | ENTERPRISE | ENTERPRISE_PLUS (post-2023)
    query_text: str | None = None  # gated by SMA_BQ_CAPTURE_QUERY_TEXT
    # sqlglot-driven feature slugs detected in ``query_text``. Empty list
    # when query text was not captured or parsing yielded nothing. See
    # :mod:`.query_features` for the slug catalogue.
    features: list[str] = Field(default_factory=list)
    # Filled by Slice 5-C.
    slot_hours: float | None = None
    est_cu_hours_fabric_spark: float | None = None


class TableUsage(BaseModel):
    """Per-table usage rollup derived from ``BigQueryJob.referenced_tables``.

    Analogous to the Synapse "SQL Surface" page for dedicated SQL pools:
    answers the question *"which tables does the BigQuery workload
    actually touch the most?"* by counting distinct jobs and summing
    elapsed-time / slot-hours / scanned bytes per fully-qualified table id.

    A job that references *n* tables contributes one increment to each
    of those *n* tables — i.e. the slot-hour / elapsed / bytes columns
    are intentionally **not** apportioned across tables (BigQuery does
    not give us per-table cost split). Use ``usage_count`` for "what
    gets touched the most" and ``total_slot_hours`` / ``total_elapsed_seconds``
    for "where do the heavy-hitter jobs go" (skewed toward joins-of-many).
    """

    full_table_id: str  # ``project.dataset.table``
    project_id: str
    dataset_id: str
    table_id: str
    usage_count: int = 0  # distinct jobs that referenced the table
    total_elapsed_seconds: float = 0.0  # sum of duration_seconds across referencing jobs
    total_slot_ms: int = 0
    total_slot_hours: float = 0.0
    total_billed_bytes: int = 0
    est_cu_hours_fabric_spark: float = 0.0
    in_catalog: bool = True  # False = referenced but not found in result.tables
    table_support: TableSupport = "unknown"  # mirror of the catalog Table.support


class BigQueryJobWindowStats(BaseModel):
    """Rolling stats over a single time window (e.g. last 28 days)."""

    window_days: int
    job_count: int = 0
    completed_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    success_rate: float | None = None
    avg_duration_seconds: float | None = None
    total_slot_hours: float = 0.0
    total_billed_bytes: int = 0
    avg_slot_hours_per_job: float | None = None
    est_cu_hours_fabric_spark: float = 0.0  # filled by Slice 5-C


class DailyJobStats(BaseModel):
    """One calendar day of job-execution stats (UTC).

    Mirrors the dedicated-pool / pipelines ``daily_status`` shape so the
    SPA Dashboard can render a single timeseries chart for any source.
    ``date`` is ``YYYY-MM-DD`` so it's CSV-safe and ISO-sortable.
    """

    date: str  # YYYY-MM-DD (UTC)
    job_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    other_count: int = 0  # in_progress, unknown
    total_slot_ms: int = 0
    total_slot_hours: float = 0.0
    total_billed_bytes: int = 0
    avg_duration_seconds: float | None = None
    success_rate: float | None = None
    est_cu_hours_fabric_spark: float = 0.0


class JobBreakdown(BaseModel):
    """Per-dimension job rollup (user / statement_type / job_type / reservation).

    ``dimension`` names which axis ``key`` is on so a single CSV
    (``bigquery_jobs_breakdowns.csv``) can hold every breakdown — keeps
    the artefact count manageable as we add new dimensions.
    """

    dimension: str  # "user" | "statement_type" | "job_type" | "reservation" | "edition" | "priority"
    key: str  # the value on that dimension; "<unknown>" when null
    job_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    total_slot_ms: int = 0
    total_slot_hours: float = 0.0
    total_billed_bytes: int = 0
    avg_duration_seconds: float | None = None
    est_cu_hours_fabric_spark: float = 0.0


class QueryFeatureCount(BaseModel):
    """Aggregate count of jobs that exhibit a sqlglot-detected feature.

    Feature slugs are produced by :mod:`.query_features.detect_features`
    (kebab-case). ``job_count`` is the number of distinct jobs in the
    window carrying that feature — slugs do not double-count within a
    single job.
    """

    feature: str
    job_count: int = 0


class BigQueryWorkloadsAnalysis(BaseModel):
    """Top-level result type written as ``bigquery_workloads.json``."""

    project_id: str
    project_number: str | None = None
    location: str | None = None
    generated_at: datetime
    datasets: list[Dataset] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    routines: list[Routine] = Field(default_factory=list)
    scheduled_queries: list[ScheduledQuery] = Field(default_factory=list)
    jobs: list[BigQueryJob] = Field(default_factory=list)
    job_window_stats: list[BigQueryJobWindowStats] = Field(default_factory=list)
    table_usage: list[TableUsage] = Field(default_factory=list)
    daily_stats: list[DailyJobStats] = Field(default_factory=list)
    breakdowns: list[JobBreakdown] = Field(default_factory=list)
    query_features: list[QueryFeatureCount] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    # Roll-ups (filled by the analyzer).
    dataset_count: int = 0
    table_count: int = 0
    view_count: int = 0
    materialized_view_count: int = 0
    external_table_count: int = 0
    routine_count: int = 0
    scheduled_query_count: int = 0
    job_count: int = 0
    unsupported_table_count: int = 0
    partial_table_count: int = 0


__all__ = [
    "Dataset",
    "Table",
    "Routine",
    "ScheduledQuery",
    "BigQueryJob",
    "BigQueryJobWindowStats",
    "BigQueryWorkloadsAnalysis",
    "DailyJobStats",
    "JobBreakdown",
    "QueryFeatureCount",
    "TableUsage",
    "TableSupport",
    "TableKind",
    "RoutineKind",
    "RoutineLanguage",
    "JobType",
    "JobOutcome",
    "TransferState",
]
