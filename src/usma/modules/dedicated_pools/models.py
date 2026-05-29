"""Pydantic models describing the analyzer output."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PoolInventory(BaseModel):
    name: str
    location: str
    sku_name: str | None = None
    sku_capacity: int | None = None  # DWU
    status: str | None = None
    create_date: datetime | None = None
    storage_account_type: str | None = None
    collation: str | None = None
    max_size_bytes: int | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class SchemaInfo(BaseModel):
    schema_name: str
    object_count: int = 0


class TableInfo(BaseModel):
    schema_name: str
    table_name: str
    distribution_policy: str | None = None  # HASH / ROUND_ROBIN / REPLICATE
    distribution_column: str | None = None
    is_partitioned: bool = False
    partition_count: int = 0
    row_count: int | None = None
    reserved_space_mb: float | None = None
    data_space_mb: float | None = None
    index_space_mb: float | None = None
    index_type: str | None = None  # CCI, HEAP, CI


class IndexInfo(BaseModel):
    schema_name: str
    table_name: str
    index_name: str | None = None
    index_type: str
    is_unique: bool = False
    is_primary_key: bool = False
    # v2: leading key column (populated when sys.index_columns is queryable).
    first_key_column: str | None = None


class UsageStat(BaseModel):
    metric: str
    value: float | int | str | None
    unit: str | None = None
    captured_at: datetime | None = None


class SecurityPrincipal(BaseModel):
    name: str
    type: str  # SQL_USER, AAD_USER, AAD_GROUP, ROLE, etc.
    role_memberships: list[str] = Field(default_factory=list)


class WorkloadGroup(BaseModel):
    name: str
    classifier_count: int = 0
    importance: str | None = None
    min_resource_pct: float | None = None
    cap_resource_pct: float | None = None
    request_min_resource_grant_pct: float | None = None


class CodeObjectParameter(BaseModel):
    """One parameter on a stored procedure or function."""
    schema_name: str
    object_name: str
    object_type: str
    parameter_name: str
    data_type: str | None = None
    max_length: int | None = None
    is_output: bool = False
    has_default: bool = False
    ordinal: int = 0


class CodeObject(BaseModel):
    schema_name: str
    object_name: str
    object_type: str  # SQL_STORED_PROCEDURE / VIEW / SQL_SCALAR_FUNCTION / SQL_INLINE_TABLE_VALUED_FUNCTION / SQL_TABLE_VALUED_FUNCTION
    definition: str | None = None
    # v2: stable identifier for cross-run diffs and per-object gap rollups.
    code_object_id: str | None = None
    # v3 (1.3.0): SQL-plane inventory depth.
    create_date: datetime | None = None
    modify_date: datetime | None = None
    line_count: int | None = None
    definition_length: int | None = None
    # True when the captured ``definition`` was truncated by the collector
    # (currently a 50 KB cap). Downstream T-SQL surface scans should treat
    # findings on truncated objects as best-effort.
    definition_truncated: bool = False
    parameter_count: int = 0
    parameters: list[CodeObjectParameter] = Field(default_factory=list)
    uses_ansi_nulls: bool | None = None
    uses_quoted_identifier: bool | None = None
    # Per-object Fabric compatibility verdict, stamped after T-SQL surface scan.
    # Values: "compatible" | "needs_review" | "incompatible". Defaults to
    # "compatible" when no T-SQL surface gaps were found.
    compatibility: str = "compatible"
    gap_severities: list[str] = Field(default_factory=list)
    gap_count: int = 0


# --- v2 -----------------------------------------------------------------------

class ColumnCollation(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    data_type: str | None = None
    max_length: int | None = None
    collation_name: str | None = None
    db_collation: str | None = None
    differs_from_db: bool = False


class MaterializedView(BaseModel):
    schema_name: str
    view_name: str
    create_date: datetime | None = None
    modify_date: datetime | None = None
    definition: str | None = None


class StatisticInfo(BaseModel):
    schema_name: str
    table_name: str
    stat_name: str
    user_created: bool = False
    auto_created: bool = False
    last_updated: datetime | None = None
    rows: int | None = None
    rows_sampled: int | None = None
    modification_counter: int | None = None
    days_since_update: int | None = None


class ColumnStat(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    data_type: str | None = None
    max_length: int | None = None
    is_nullable: bool = True
    row_count: int | None = None
    distinct_count: int | None = None
    null_count: int | None = None
    max_frequency: int | None = None


class DistributionCandidate(BaseModel):
    """Output of :mod:`distribution_advisor` per (schema, table)."""
    schema_name: str
    table_name: str
    column_name: str
    score: int
    reasons: list[str] = Field(default_factory=list)


class TsqlSurfaceGap(BaseModel):
    """One T-SQL surface finding linked back to its code object via stable id."""
    code_object_id: str
    schema_name: str
    object_name: str
    object_type: str
    rule_id: str
    label: str
    severity: str
    matches: int = 0
    fabric_action: str | None = None


class CodeObjectSummary(BaseModel):
    """Per-pool rollup of stored-procedure / function / view inventory + Fabric compatibility.

    Counts are bucketed by both ``object_type`` (procedure / scalar UDF / inline TVF /
    multi-statement TVF / view) and ``compatibility`` (compatible / needs_review /
    incompatible). The ``compatibility_pct`` is ``compatible / total * 100`` rounded
    to one decimal; ``None`` when there are no code objects (paused pool, no DMV access).
    """
    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_compatibility: dict[str, int] = Field(default_factory=dict)
    compatibility_pct: float | None = None
    incompatible_object_ids: list[str] = Field(default_factory=list)
    needs_review_object_ids: list[str] = Field(default_factory=list)


class TopQuery(BaseModel):
    """A single expensive request sampled from ``sys.dm_pdw_exec_requests``.

    Captures the columns most useful for migration triage: identity, status,
    timing, login, and the leading characters of the command text. The DMV
    is a rolling buffer so the visible window is at most ~10 000 requests
    regardless of what we ask for; ``submit_time`` is preserved so callers
    can tell how recent the slowest queries actually were.
    """
    request_id: str | None = None
    session_id: str | None = None
    status: str | None = None
    submit_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    # Renamed from total_elapsed_time so the JSON key is unambiguous about units.
    total_elapsed_ms: int | None = None
    resource_class: str | None = None
    importance: str | None = None
    query_label: str | None = None
    error_id: str | None = None
    login_name: str | None = None
    # Truncated by the collector to keep the JSON manageable; the full text
    # stays inside the dedicated pool DMV (which itself is a rolling buffer).
    command_text: str | None = None


class TopConsumedObject(BaseModel):
    """A table or view that appears frequently in recent workload SQL.

    Derived by parsing the submitted command text from
    ``sys.dm_pdw_exec_requests`` with sqlglot and resolving each
    referenced table/view against ``INFORMATION_SCHEMA``. Results are
    persisted in a per-pool on-disk cache (default 30-day TTL) so the
    DMV's rolling-buffer roll-off doesn't gut visibility across runs.

    ``usage_count`` is the number of distinct requests that touched the
    object inside the cache window. ``elapsed_time_ms`` is the sum of
    ``total_elapsed_time`` for those requests — typically the more
    useful "where does the pool actually spend time" signal.

    ``match_kind`` indicates how the reference was resolved:
        * ``qualified``            — schema.name match in the catalog
        * ``unqualified-resolved`` — 1-part name with exactly one owner
        * ``ambiguous``            — 1-part name that exists in >1 schema
    """
    object_name: str
    object_type: str  # "table" | "view"
    usage_count: int
    elapsed_time_ms: int = 0
    match_kind: str = "qualified"


class WorkloadCaptureStats(BaseModel):
    """Health/coverage signal for the top-consumed-tables collector.

    These numbers tell the reader how trustworthy the ranking is —
    e.g. a run with 4900 DMV rows but 0 parsed_ok almost certainly hit
    a sqlglot bug and the ranking should be ignored.
    """
    dmv_rows: int = 0
    parsed_ok: int = 0
    parsed_failed: int = 0
    parsed_empty: int = 0
    cache_requests_total: int = 0
    cache_window_days: int = 30
    oldest_cache_entry: datetime | None = None
    newest_cache_entry: datetime | None = None


class PoolAnalysis(BaseModel):
    inventory: PoolInventory
    schemas: list[SchemaInfo] = Field(default_factory=list)
    tables: list[TableInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    usage: list[UsageStat] = Field(default_factory=list)
    security: list[SecurityPrincipal] = Field(default_factory=list)
    workload_groups: list[WorkloadGroup] = Field(default_factory=list)
    code_objects: list[CodeObject] = Field(default_factory=list)
    # v2 additions (all default to empty so existing tests + reports still work).
    column_collations: list[ColumnCollation] = Field(default_factory=list)
    materialized_views: list[MaterializedView] = Field(default_factory=list)
    statistics: list[StatisticInfo] = Field(default_factory=list)
    column_stats: list[ColumnStat] = Field(default_factory=list)
    distribution_candidates: list[DistributionCandidate] = Field(default_factory=list)
    tsql_surface_gaps: list[TsqlSurfaceGap] = Field(default_factory=list)
    code_object_summary: CodeObjectSummary | None = None
    top_queries: list[TopQuery] = Field(default_factory=list)
    top_consumed_objects: list[TopConsumedObject] = Field(default_factory=list)
    workload_capture_stats: WorkloadCaptureStats | None = None
    errors: list[str] = Field(default_factory=list)


class WorkspaceAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    pools: list[PoolAnalysis] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
