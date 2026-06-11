"""Pydantic schemas for the web control plane."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# The module names the API accepts. Sourced from the central analyzer
# registry so adding a new module in :mod:`..modules` automatically
# propagates here without a parallel edit.
from ..modules import KNOWN_MODULES as KNOWN_MODULES  # re-exported

ModuleState = Literal[
    "queued", "running", "ok", "failed", "skipped", "cancelled", "carried",
]
RunState = Literal["queued", "running", "ok", "failed", "cancelled"]


class ModuleProgress(BaseModel):
    """Latest sub-step counters for a running module.

    Persisted on :class:`ModuleStatus` so the runs-history view can show
    in-flight progress without replaying the full SSE stream.
    """
    current: int = 0
    total: int = 0
    label: str | None = None
    message: str | None = None


class ModuleStatus(BaseModel):
    name: str
    state: ModuleState = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    progress: ModuleProgress | None = None
    # When ``state == "carried"`` this is the run id whose artefact was
    # copied forward, plus the wall-clock time at which that artefact was
    # originally produced. ``None`` for modules executed by this run.
    carried_from_run_id: str | None = None
    carried_from_started_at: datetime | None = None


class RunMeta(BaseModel):
    id: str
    label: str | None = None
    status: RunState = "queued"
    started_at: datetime
    finished_at: datetime | None = None
    config_hash: str
    modules: list[ModuleStatus] = Field(default_factory=list)
    readiness_score: float | None = None
    errors_count: int = 0
    # Workspace identity captured at run-start so the Estate Overview can
    # group runs across workspaces / subscriptions / tenants without
    # re-reading every module artefact. Optional for back-compat with
    # runs created before these fields were persisted.
    tenant_id: str | None = None
    subscription_id: str | None = None
    resource_group: str | None = None
    workspace_name: str | None = None
    # Phase 1.5 — list of source scopes targeted by this run.
    # Single-source legacy runs leave this empty (workspace_name above is
    # the single scope). Multi-source runs populate one ScopeRef per
    # targeted Synapse workspace / ADF factory / etc.
    scopes: list["ScopeRef"] = Field(default_factory=list)
    # Map of module name -> source run id for artefacts that were inherited
    # from a previous run (because the user did not select them in this run
    # and a prior workspace-matched run produced them). Empty for fresh runs.
    carried_from: dict[str, str] = Field(default_factory=dict)


class ScopeRef(BaseModel):
    """Wire representation of a :class:`SourceDescriptor` for the SPA.

    Mirrors the persistence layer (``manifest.scopes[]``) so the
    Configuration page and ``StartRunRequest`` can speak the same shape.
    Optional fields stay loose to keep early-bring-up integration easy.
    """
    source_type: Literal[
        "synapse_workspace",
        "adf",
        "databricks",
        "bigquery",
        "snowflake",
        "sap_bw",
        "synapse_dedicated_sql",
    ] = "synapse_workspace"
    id: str = Field(..., description="ARM id or provider-specific stable id")
    display_name: str = Field(..., description="Human-friendly label shown in the UI")
    subscription_id: str | None = None
    resource_group: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class StartRunRequest(BaseModel):
    modules: list[str] = Field(
        ...,
        description="Module names to run, e.g. ['dedicated_pools', 'pipelines'].",
        min_length=1,
    )
    label: str | None = Field(
        None,
        description="Optional human label shown in the run history UI.",
        max_length=120,
    )
    days: int | None = Field(
        None,
        description=(
            "Global lookback window in days for analyzers that fetch run "
            "history (pipelines, spark_pools, monitoring). When set, "
            "overrides the per-analyzer SMA_*_DAYS env vars for this run."
        ),
        ge=1,
        le=365,
    )
    scopes: list[ScopeRef] | None = Field(
        None,
        description=(
            "Phase 1.5 — optional list of source scopes to analyze. "
            "When omitted, the run targets the legacy single Synapse "
            "workspace pinned via SYNAPSE_WORKSPACE_NAME / "
            "SYNAPSE_RESOURCE_GROUP. Persisted on RunMeta.scopes so the "
            "history view can render scope counts + source-type chips."
        ),
    )


class StartRunResponse(BaseModel):
    id: str
    status: RunState


# ---------------------------------------------------------------------------
# Configuration (PR-4)
# ---------------------------------------------------------------------------


class AzureConfigPublic(BaseModel):
    # Phase 2.5 — which source backend a saved ``.env`` targets. The
    # legacy default keeps every existing run pointing at a Synapse
    # workspace; ``adf`` switches the Configuration page (and
    # ``load_config``) over to interpreting ``SYNAPSE_RESOURCE_GROUP``
    # / ``SYNAPSE_WORKSPACE_NAME`` as the ADF factory's RG + name.
    # Phase 6.B — ``synapse_dedicated_sql`` reinterprets the same fields
    # as a standalone ``Microsoft.Sql/servers/<server>`` (see ADR-0009).
    source_type: Literal[
        "synapse_workspace",
        "adf",
        "databricks",
        "bigquery",
        "snowflake",
        "synapse_dedicated_sql",
    ] = "synapse_workspace"

    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: Literal["set", "unset"] = "unset"
    subscription_id: str | None = None
    resource_group: str | None = None
    workspace_name: str | None = None
    dedicated_pool: str | None = None
    # Phase 5 — GCP project id, persisted to SMA_GCP_PROJECT_ID. Only
    # meaningful when source_type == "bigquery"; ignored otherwise.
    gcp_project_id: str | None = None
    # Phase 4.7 — Databricks cloud platform discriminator, persisted to
    # SMA_DATABRICKS_PLATFORM. ``None`` is treated as ``"azure"`` by
    # the backend and only affects runs where source_type == "databricks".
    databricks_platform: Literal["azure", "aws"] | None = None
    # Phase 4.7 — Databricks-on-AWS connection settings, persisted to
    # the ``DATABRICKS_*`` env vars. A single Databricks service principal
    # (``DATABRICKS_CLIENT_ID`` + ``DATABRICKS_CLIENT_SECRET``) is used
    # for both account-level workspace discovery (paired with
    # ``DATABRICKS_ACCOUNT_ID``) and per-workspace REST auth. Plain
    # fields surface as-is; the secret collapses to ``"set"`` /
    # ``"unset"`` (same contract as ``client_secret``). All fields are
    # ignored when ``databricks_platform != "aws"``.
    databricks_host: str | None = None
    databricks_client_id: str | None = None
    databricks_client_secret: Literal["set", "unset"] = "unset"
    databricks_account_id: str | None = None
    # Phase 7 Slice 7-E.1 — Snowflake OAuth auth settings, persisted to
    # the ``SNOWFLAKE_*`` env vars. Plain fields surface as-is; the
    # OAuth client secret + refresh token + (optional) pre-minted access
    # token all collapse to ``"set"`` / ``"unset"``. All fields are
    # ignored unless ``source_type == "snowflake"``. ``snowflake_platform``
    # is a UI-only convenience; the backend infers the cloud from
    # ``CURRENT_REGION()`` at run time, but the SPA captures the user's
    # selection so the discovery preview can show the right label before
    # validation runs.
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_role: str | None = None
    snowflake_warehouse: str | None = None
    snowflake_oauth_client_id: str | None = None
    snowflake_oauth_client_secret: Literal["set", "unset"] = "unset"
    snowflake_oauth_refresh_token: Literal["set", "unset"] = "unset"
    snowflake_oauth_token: Literal["set", "unset"] = "unset"
    snowflake_platform: Literal["aws", "azure", "gcp"] | None = None


class SqlConfigPublic(BaseModel):
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    login_timeout: int = 30
    query_timeout: int = 120


class AppConfigPublic(BaseModel):
    azure: AzureConfigPublic
    sql: SqlConfigPublic
    output_dir: Path
    env_file: Path
    env_file_exists: bool


class AzureConfigUpdate(BaseModel):
    source_type: Literal[
        "synapse_workspace",
        "adf",
        "databricks",
        "bigquery",
        "snowflake",
        "synapse_dedicated_sql",
    ] | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = Field(
        None,
        description="Write-only. When present, written through to .env. "
        "Reads always return 'set' / 'unset'.",
    )
    subscription_id: str | None = None
    resource_group: str | None = None
    workspace_name: str | None = None
    dedicated_pool: str | None = None
    # Phase 5 — GCP project id, persisted to SMA_GCP_PROJECT_ID.
    gcp_project_id: str | None = None
    # Phase 4.7 — Databricks cloud platform. ``"azure"`` (default) keeps
    # the legacy AAD-federated path; ``"aws"`` switches the scope to
    # the DatabricksAwsProvider. Empty string clears the env var.
    databricks_platform: Literal["azure", "aws", ""] | None = None
    # Phase 4.7 — Databricks-on-AWS connection settings. Each field uses
    # the same write-through contract as ``client_secret``: ``None``
    # leaves the env var untouched; ``""`` clears it; any other string
    # is written through verbatim. The OAuth secret is write-only —
    # reads return ``"set"`` / ``"unset"`` on :class:`AzureConfigPublic`.
    databricks_host: str | None = None
    databricks_client_id: str | None = None
    databricks_client_secret: str | None = Field(
        None,
        description="Write-only Databricks SP OAuth secret. "
        "Used for both account discovery and workspace auth.",
    )
    databricks_account_id: str | None = None
    # Phase 7 Slice 7-E.1 — Snowflake OAuth settings. Each field uses the
    # same write-through contract as ``client_secret``: ``None`` leaves
    # the env var untouched; ``""`` clears it; any other string is
    # written through. The OAuth client secret, refresh token, and
    # pre-minted access token are write-only; reads return
    # ``"set"`` / ``"unset"`` on :class:`AzureConfigPublic`.
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_role: str | None = None
    snowflake_warehouse: str | None = None
    snowflake_oauth_client_id: str | None = None
    snowflake_oauth_client_secret: str | None = Field(
        None,
        description="Write-only Snowflake OAuth client secret.",
    )
    snowflake_oauth_refresh_token: str | None = Field(
        None,
        description=(
            "Write-only long-lived Snowflake OAuth refresh token. "
            "Minted out-of-band via the authorisation-code flow; "
            "USMA exchanges it for short-lived access tokens at run time."
        ),
    )
    snowflake_oauth_token: str | None = Field(
        None,
        description=(
            "Write-only optional pre-minted Snowflake OAuth access "
            "token. Bypasses the refresh-token exchange; intended for "
            "CI / tests / break-glass use. Expires after ~10 minutes."
        ),
    )
    snowflake_platform: Literal["aws", "azure", "gcp", ""] | None = None


class SqlConfigUpdate(BaseModel):
    odbc_driver: str | None = None
    login_timeout: int | None = None
    query_timeout: int | None = None


class AppConfigUpdate(BaseModel):
    azure: AzureConfigUpdate | None = None
    sql: SqlConfigUpdate | None = None
    output_dir: Path | None = None


class ConfigCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None
    category: str | None = None


class WorkspaceSummary(BaseModel):
    name: str
    resource_group: str
    location: str | None = None
    sql_endpoint: str | None = None
    sql_on_demand_endpoint: str | None = None
    is_current: bool = False


class ValidateConfigResponse(BaseModel):
    ok: bool
    checks: list[ConfigCheck]
    workspaces: list[WorkspaceSummary] | None = None


class SaveConfigResponse(BaseModel):
    saved_to: Path
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff (PR-8) – pass-through to existing run_manifest helpers
# ---------------------------------------------------------------------------


class RunDiffEnvelope(BaseModel):
    base: str
    head: str
    delta: dict[str, Any]


# ---------------------------------------------------------------------------
# Estate Overview (cross-workspace, cross-time aggregation)
# ---------------------------------------------------------------------------


class EstateHistoryPoint(BaseModel):
    """One run's contribution to a workspace's timeline."""
    run_id: str
    finished_at: datetime
    status: RunState
    readiness_score: float | None = None
    blocker_count: int = 0
    warning_count: int = 0
    actual_monthly_cost: float | None = None


class EstateWorkspace(BaseModel):
    """A single workspace as seen across all of its runs."""
    key: str
    tenant_id: str | None = None
    subscription_id: str | None = None
    resource_group: str | None = None
    workspace_name: str
    run_count: int
    latest_run_id: str
    latest_status: RunState
    latest_finished_at: datetime
    modules_run: list[str] = Field(default_factory=list)
    # v3 — source platform inherited from the latest run's first scope.
    # ``None`` for legacy runs that pre-date the multi-scope dispatcher.
    source_type: str | None = None
    # 5.1.2 — hyperscaler discriminator derived from source_type +
    # (for Databricks) the scope's ``extras['platform']``. One of
    # ``"azure"`` / ``"aws"`` / ``"gcp"`` / ``"on_prem"``; ``None`` for
    # legacy runs whose source platform could not be determined.
    cloud: str | None = None
    # Latest-run metrics
    readiness_score: float | None = None
    readiness_bucket: str | None = None
    blocker_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    tsql_compatibility_pct: float | None = None
    # Capacity / forecast (fabric_mapping)
    projected_fabric_cu: float | None = None
    recommended_fabric_sku: str | None = None
    # Spend / cost (cost module)
    actual_monthly_cost: float | None = None
    actual_currency: str | None = None
    fabric_estimated_monthly_cost: float | None = None
    # Reservation-priced monthly equivalents (live API only; None when unavailable).
    fabric_estimated_monthly_cost_1y_ri: float | None = None
    fabric_estimated_monthly_cost_3y_ri: float | None = None
    fabric_pricing_source: str | None = None  # live | cache | static | offline | none
    fabric_cost_delta_abs: float | None = None
    fabric_cost_delta_pct: float | None = None
    # Estimated migration effort (fabric_mapping.effort_summary)
    effort_hours_p50: float | None = None
    effort_hours_p90: float | None = None
    effort_days_p50: int | None = None
    effort_days_p90: int | None = None
    history: list[EstateHistoryPoint] = Field(default_factory=list)


class EstateTotals(BaseModel):
    workspaces: int = 0
    runs: int = 0
    tenants: int = 0
    subscriptions: int = 0
    ready: int = 0
    ready_with_effort: int = 0
    blocked: int = 0
    blockers_total: int = 0
    tsql_compatibility_pct_avg: float | None = None
    projected_fabric_cu_total: float | None = None
    actual_monthly_cost_total: float | None = None
    fabric_estimated_monthly_cost_total: float | None = None
    fabric_estimated_monthly_cost_1y_ri_total: float | None = None
    fabric_estimated_monthly_cost_3y_ri_total: float | None = None
    effort_hours_p50_total: float | None = None
    effort_hours_p90_total: float | None = None
    effort_days_p50_total: int | None = None
    effort_days_p90_total: int | None = None


class EstateTopBlocker(BaseModel):
    area: str
    title: str
    fabric_action: str | None = None
    effort: str = "medium"
    workspaces: int = 0
    occurrences: int = 0
    example_run_id: str | None = None


class EstateReport(BaseModel):
    generated_at: datetime
    totals: EstateTotals
    workspaces: list[EstateWorkspace] = Field(default_factory=list)
    top_blockers: list[EstateTopBlocker] = Field(default_factory=list)
