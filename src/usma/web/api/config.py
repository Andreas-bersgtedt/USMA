"""Configuration endpoints (.env read/write/validate).

Reads always redact ``AZURE_CLIENT_SECRET`` to ``"set"``/``"unset"``.
Writes accept a plaintext secret only via ``PUT /api/config`` and only
when the X-SMA-API marker is present (enforced globally).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config_io import (
    discover_bigquery_projects,
    discover_databricks_workspaces,
    discover_factories,
    discover_snowflake_databases,
    discover_sql_servers,
    discover_workspaces,
    read_config,
    validate_config,
    validate_config_live,
    write_config,
)
from ..deps import AppState, get_state
from ..schemas import (
    AppConfigPublic,
    AppConfigUpdate,
    SaveConfigResponse,
    ValidateConfigResponse,
)

router = APIRouter()


@router.get("", response_model=AppConfigPublic)
def get_config(state: AppState = Depends(get_state)) -> AppConfigPublic:
    return read_config(state.env_file)


@router.put("", response_model=SaveConfigResponse)
def put_config(
    body: AppConfigUpdate,
    state: AppState = Depends(get_state),
) -> SaveConfigResponse:
    warnings = write_config(state.env_file, body)
    return SaveConfigResponse(saved_to=state.env_file, warnings=warnings)


@router.post("/validate", response_model=ValidateConfigResponse)
def validate(
    live: bool = False,
    state: AppState = Depends(get_state),
) -> ValidateConfigResponse:
    """Validate the saved .env config.

    ``?live=true`` additionally exercises Azure control-plane (ARM) and
    Synapse data-plane (Artifacts REST + SQL ``SELECT 1``) connectivity using
    the configured service principal, so the user can confirm RBAC was granted
    on both planes. The live response also enumerates every Synapse workspace
    visible to the SP at the configured subscription scope.
    """
    if live:
        checks, workspaces = validate_config_live(state.env_file)
        return ValidateConfigResponse(
            ok=all(c.ok for c in checks),
            checks=checks,
            workspaces=workspaces,
        )
    checks = validate_config(state.env_file)
    return ValidateConfigResponse(ok=all(c.ok for c in checks), checks=checks)


@router.post("/discover-workspaces", response_model=ValidateConfigResponse)
def discover(
    state: AppState = Depends(get_state),
) -> ValidateConfigResponse:
    """Enumerate Synapse workspaces the saved SP can see in the subscription.

    Requires only Tenant / Client / Secret / Subscription in ``.env``. Used by
    the Configuration page to populate the workspace dropdown without forcing
    the user to know the resource group or workspace name up front.
    """
    checks, workspaces = discover_workspaces(state.env_file)
    return ValidateConfigResponse(
        ok=all(c.ok for c in checks),
        checks=checks,
        workspaces=workspaces,
    )


@router.post("/discover-factories", response_model=ValidateConfigResponse)
def discover_adf(
    state: AppState = Depends(get_state),
) -> ValidateConfigResponse:
    """Enumerate ADF factories the saved SP can see in the subscription.

    Symmetric with ``/discover-workspaces``. The Configuration page
    calls this endpoint instead when the user toggles **Source type =
    ADF** so the dropdown lists factories rather than workspaces. Each
    :class:`WorkspaceSummary` here represents a factory (``name`` =
    factory name, ``resource_group`` = the factory's RG).
    """
    checks, workspaces = discover_factories(state.env_file)
    return ValidateConfigResponse(
        ok=all(c.ok for c in checks),
        checks=checks,
        workspaces=workspaces,
    )


@router.post("/discover-sql-servers", response_model=ValidateConfigResponse)
def discover_sql(
    state: AppState = Depends(get_state),
) -> ValidateConfigResponse:
    """Enumerate Azure SQL servers the saved SP can see in the subscription.

    Phase 6.B (ADR-0009) — symmetric with ``/discover-workspaces`` and
    ``/discover-factories``, but bound to the standalone Dedicated SQL
    pool topology (``Microsoft.Sql/servers``). The Configuration page
    calls this endpoint when the user toggles **Source type = Dedicated
    SQL pool**. Each :class:`WorkspaceSummary` represents one SQL server
    (``name`` = server name, ``resource_group`` = the server's RG,
    ``sql_endpoint`` = ``<server>.database.windows.net``).
    """
    checks, workspaces = discover_sql_servers(state.env_file)
    return ValidateConfigResponse(
        ok=all(c.ok for c in checks),
        checks=checks,
        workspaces=workspaces,
    )


@router.post("/discover-databricks-workspaces", response_model=ValidateConfigResponse)
def discover_databricks(
    state: AppState = Depends(get_state),
) -> ValidateConfigResponse:
    """Enumerate Azure Databricks workspaces visible to the saved SP.

    Symmetric with ``/discover-workspaces`` and ``/discover-factories``.
    The Configuration page calls this endpoint when the user toggles
    **Source type = Databricks**. Each :class:`WorkspaceSummary` here
    represents a Databricks workspace (``name`` = workspace name,
    ``resource_group`` = the workspace's RG, ``sql_endpoint`` = the
    Databricks ``workspace_url`` for hint display).
    """
    checks, workspaces = discover_databricks_workspaces(state.env_file)
    return ValidateConfigResponse(
        ok=all(c.ok for c in checks),
        checks=checks,
        workspaces=workspaces,
    )


@router.post("/discover-bigquery-projects", response_model=ValidateConfigResponse)
def discover_bigquery(
    state: AppState = Depends(get_state),
) -> ValidateConfigResponse:
    """Enumerate GCP projects visible via Application Default Credentials.

    Phase 5 Slice 5-D — symmetric with ``/discover-workspaces`` and the
    other source-specific discovery routes, but bound to BigQuery's
    ADC-only auth model. No Azure SP credentials are read; the call
    uses whatever ``google.auth.default()`` resolves (typically
    ``GOOGLE_APPLICATION_CREDENTIALS`` or ``gcloud auth
    application-default login``). The Configuration page calls this
    endpoint when the user toggles **Source type = BigQuery**. Each
    :class:`WorkspaceSummary` here represents a GCP project (``name`` =
    project id, ``resource_group`` = GCP project number).
    """
    checks, workspaces = discover_bigquery_projects(state.env_file)
    return ValidateConfigResponse(
        ok=all(c.ok for c in checks),
        checks=checks,
        workspaces=workspaces,
    )


@router.post("/discover-snowflake-databases", response_model=ValidateConfigResponse)
def discover_snowflake(
    state: AppState = Depends(get_state),
) -> ValidateConfigResponse:
    """Probe the configured Snowflake account via key-pair JWT.

    Phase 7 Slice 7-E — symmetric with ``/discover-bigquery-projects``
    and the other source-specific discovery routes, but bound to
    Snowflake's key-pair JWT auth model. No Azure SP credentials are
    read; the call uses the ``SNOWFLAKE_*`` env vars persisted by
    ``PUT /api/config``. A Snowflake "scope" is the single account the
    credentials authorise, so the response surfaces one
    :class:`WorkspaceSummary` (``name`` = account locator,
    ``resource_group`` = cloud platform, ``location`` = region).
    """
    checks, workspaces = discover_snowflake_databases(state.env_file)
    return ValidateConfigResponse(
        ok=all(c.ok for c in checks),
        checks=checks,
        workspaces=workspaces,
    )
