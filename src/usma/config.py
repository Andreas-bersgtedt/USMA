"""Runtime configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .sources import SourceDescriptor, SourceType


@dataclass(frozen=True)
class AzureConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    subscription_id: str
    resource_group: str
    workspace_name: str
    dedicated_pool: str | None = None
    # Slice 5-H: GCP project id when ``SMA_SOURCE_TYPE=bigquery``.
    # Persisted by ``web/config_io.py`` as ``SMA_GCP_PROJECT_ID``.
    # ``AzureConfig`` keeps its name for backward compatibility with the
    # rest of the codebase; only the BigQuery code paths read this field.
    gcp_project_id: str | None = None


@dataclass(frozen=True)
class SqlConfig:
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    login_timeout: int = 30
    query_timeout: int = 120


@dataclass(frozen=True)
class AppConfig:
    azure: AzureConfig
    sql: SqlConfig
    output_dir: Path
    # Phase-1 addition: list of scopes the run should target. In single-
    # source mode (today) this is auto-populated with a single
    # ``SourceDescriptor`` derived from ``azure.workspace_name`` /
    # ``azure.resource_group``. Multi-scope CLI / API callers populate
    # this directly. Default is an empty tuple for backwards
    # compatibility (existing constructors that don't pass ``scopes``
    # still work).
    scopes: tuple[SourceDescriptor, ...] = field(default_factory=tuple)

    def primary_scope(self) -> SourceDescriptor | None:
        """Return the first scope, or ``None`` if no scopes are configured.

        Used by legacy code paths that need exactly one scope without
        threading the entire ``scopes`` tuple through their signatures.
        """
        return self.scopes[0] if self.scopes else None


_REQUIRED = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SUBSCRIPTION_ID",
    "SYNAPSE_RESOURCE_GROUP",
    "SYNAPSE_WORKSPACE_NAME",
)


def load_config(env_file: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load and validate config from .env / environment variables.

    ``override=True`` is used so that the long-lived ``sma serve`` process
    picks up edits made via ``PUT /api/config`` (which rewrites ``.env``)
    instead of being pinned to whatever values were first loaded at boot.
    """
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=True)

    source_type_raw = (os.getenv("SMA_SOURCE_TYPE") or "synapse_workspace").strip().lower()
    # Phase 4.7: per-platform discriminator for Databricks. Defaults to
    # ``azure`` so legacy ``SMA_SOURCE_TYPE=databricks`` users keep their
    # AAD-federated workflow untouched. ``aws`` switches to the
    # ``DatabricksAwsProvider`` discovery + auth path and relaxes the
    # AAD env-var gate the same way ``SMA_SOURCE_TYPE=bigquery`` does.
    db_platform_raw = (os.getenv("SMA_DATABRICKS_PLATFORM") or "azure").strip().lower()
    is_aws_dbx = source_type_raw == "databricks" and db_platform_raw == "aws"

    # BigQuery scopes use ADC, not an Azure service principal. Relax the
    # AAD env-var gate so a BigQuery-only ``.env`` (just
    # ``SMA_SOURCE_TYPE=bigquery`` + ``SMA_GCP_PROJECT_ID``) is enough
    # to drive the wave runner. The Azure fields default to empty
    # strings; modules that don't ``supports_source(BIGQUERY)`` skip
    # themselves before they ever read the credential.
    if source_type_raw == "bigquery":
        gcp_project_id = (os.getenv("SMA_GCP_PROJECT_ID") or "").strip()
        if not gcp_project_id:
            raise RuntimeError(
                "SMA_GCP_PROJECT_ID is required when SMA_SOURCE_TYPE=bigquery."
            )
        # BigQuery runs MUST NOT inherit ``SYNAPSE_RESOURCE_GROUP`` /
        # ``SYNAPSE_WORKSPACE_NAME`` from a previous Synapse/ADF/Databricks
        # session — those values flow into the run manifest's identity
        # fields and would otherwise show up on the estate page as e.g.
        # "Dag2ADF | My First Project | Bigquery" (the friendly display
        # name from the GCP picker landing in workspace_name, and an old
        # Azure RG landing in resource_group). Pin them to the canonical
        # project id instead so the estate page renders a clean
        # "—  | <project-id> | Bigquery" row.
        azure = AzureConfig(
            tenant_id=os.getenv("AZURE_TENANT_ID", "") or "",
            client_id=os.getenv("AZURE_CLIENT_ID", "") or "",
            client_secret=os.getenv("AZURE_CLIENT_SECRET", "") or "",
            subscription_id=os.getenv("AZURE_SUBSCRIPTION_ID", "") or gcp_project_id,
            resource_group="",
            workspace_name=gcp_project_id,
            dedicated_pool=None,
            gcp_project_id=gcp_project_id,
        )
    elif is_aws_dbx:
        # Databricks-on-AWS has no Azure subscription / RG / AAD app of
        # its own. Same pattern as the BigQuery branch: stub the
        # ``AzureConfig`` fields with empty strings and rely on the
        # AWS provider reading PAT/OAuth/account creds from the
        # environment. Either ``DATABRICKS_HOST`` (explicit-host) or
        # ``DATABRICKS_ACCOUNT_ID`` (Account API) must be set so the
        # CLI / SPA can build at least one scope.
        host = (os.getenv("DATABRICKS_HOST") or "").strip()
        account_id = (os.getenv("DATABRICKS_ACCOUNT_ID") or "").strip()
        if not host and not account_id:
            raise RuntimeError(
                "SMA_DATABRICKS_PLATFORM=aws requires DATABRICKS_HOST "
                "(explicit-host mode) or DATABRICKS_ACCOUNT_ID "
                "(+ account-level OAuth M2M for Account-API mode)."
            )
        azure = AzureConfig(
            tenant_id="",
            client_id="",
            client_secret="",
            subscription_id="",
            resource_group="",
            workspace_name=host or account_id,
            dedicated_pool=None,
            gcp_project_id=None,
        )
    elif source_type_raw == "snowflake":
        # Snowflake auth is OAuth (Slice 7-E.1) -- no Azure SP, no ARM
        # subscription / RG. Same pattern as the BigQuery / Databricks-on-AWS
        # branches: stub the ``AzureConfig`` fields with empty strings and
        # rely on the Snowflake provider reading SNOWFLAKE_* creds from
        # the environment. ``SNOWFLAKE_ACCOUNT`` must be set so the
        # CLI / SPA can build at least one scope.
        account = (os.getenv("SNOWFLAKE_ACCOUNT") or "").strip()
        if not account:
            raise RuntimeError(
                "SMA_SOURCE_TYPE=snowflake requires SNOWFLAKE_ACCOUNT."
            )
        azure = AzureConfig(
            tenant_id="",
            client_id="",
            client_secret="",
            subscription_id="",
            resource_group="",
            workspace_name=account,
            dedicated_pool=None,
            gcp_project_id=None,
        )
    else:
        missing = [k for k in _REQUIRED if not os.getenv(k)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Copy .env.example to .env and populate it."
            )

        azure = AzureConfig(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
            subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
            resource_group=os.environ["SYNAPSE_RESOURCE_GROUP"],
            workspace_name=os.environ["SYNAPSE_WORKSPACE_NAME"],
            dedicated_pool=os.getenv("SYNAPSE_DEDICATED_POOL") or None,
            gcp_project_id=os.getenv("SMA_GCP_PROJECT_ID") or None,
        )
    sql = SqlConfig(
        odbc_driver=os.getenv("SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
        login_timeout=int(os.getenv("SQL_LOGIN_TIMEOUT", "30")),
        query_timeout=int(os.getenv("SQL_QUERY_TIMEOUT", "120")),
    )
    output_dir = Path(os.getenv("SMA_OUTPUT_DIR", "./output")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase-1 / Phase 2.5: auto-populate ``scopes`` from the legacy single-
    # scope env vars. ``SMA_SOURCE_TYPE=adf`` switches the implicit scope
    # to an ADF factory descriptor (re-using SYNAPSE_RESOURCE_GROUP /
    # SYNAPSE_WORKSPACE_NAME as the factory's RG + name). Multi-scope
    # callers replace this via ``dataclasses.replace``.
    if source_type_raw == "adf":
        scopes: tuple[SourceDescriptor, ...] = (_legacy_adf_scope(azure),)
    elif source_type_raw == "databricks":
        if is_aws_dbx:
            scopes = (_legacy_databricks_aws_scope(),)
        else:
            scopes = (_legacy_databricks_scope(azure),)
    elif source_type_raw == "bigquery":
        scopes = (_legacy_bigquery_scope(azure),)
    elif source_type_raw == "snowflake":
        scopes = (_legacy_snowflake_scope(),)
    else:
        scopes = (_legacy_scope(azure),)

    return AppConfig(azure=azure, sql=sql, output_dir=output_dir, scopes=scopes)


def _legacy_scope(azure: AzureConfig) -> SourceDescriptor:
    """Build the implicit single-scope from the legacy env vars."""
    arm_id = (
        f"/subscriptions/{azure.subscription_id}"
        f"/resourceGroups/{azure.resource_group}"
        f"/providers/Microsoft.Synapse/workspaces/{azure.workspace_name}"
    )
    return SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id=arm_id,
        display_name=azure.workspace_name,
        subscription_id=azure.subscription_id,
        resource_group=azure.resource_group,
    )


def _legacy_adf_scope(azure: AzureConfig) -> SourceDescriptor:
    """Build the implicit single ADF scope from the legacy env vars.

    Re-uses ``SYNAPSE_RESOURCE_GROUP`` + ``SYNAPSE_WORKSPACE_NAME`` so
    users only need to flip ``SMA_SOURCE_TYPE=adf`` to switch the
    Configuration page over to an ADF run, without renaming env vars.
    """
    arm_id = (
        f"/subscriptions/{azure.subscription_id}"
        f"/resourceGroups/{azure.resource_group}"
        f"/providers/Microsoft.DataFactory/factories/{azure.workspace_name}"
    )
    return SourceDescriptor(
        type=SourceType.ADF,
        id=arm_id,
        display_name=azure.workspace_name,
        subscription_id=azure.subscription_id,
        resource_group=azure.resource_group,
    )


def _legacy_databricks_scope(azure: AzureConfig) -> SourceDescriptor:
    """Build the implicit single Databricks scope from the legacy env vars.

    Re-uses ``SYNAPSE_RESOURCE_GROUP`` + ``SYNAPSE_WORKSPACE_NAME`` for
    the workspace's RG + display-name. ``workspace_url`` (required by
    ``DatabricksProvider.make_clients``) is taken from
    ``DATABRICKS_WORKSPACE_URL`` when set; otherwise a best-effort ARM
    lookup is performed so the user only has to flip
    ``SMA_SOURCE_TYPE=databricks`` without manually copying the URL
    from the Azure portal.
    """
    arm_id = (
        f"/subscriptions/{azure.subscription_id}"
        f"/resourceGroups/{azure.resource_group}"
        f"/providers/Microsoft.Databricks/workspaces/{azure.workspace_name}"
    )
    workspace_url = (os.getenv("DATABRICKS_WORKSPACE_URL") or "").strip() or None
    if not workspace_url:
        # Best-effort live ARM probe. Stays silent on any failure so
        # ``load_config()`` never blows up just because the credentials
        # aren't ready yet (e.g. ``sma doctor --offline``).
        try:
            from azure.identity import ClientSecretCredential
            from azure.mgmt.databricks import AzureDatabricksManagementClient

            cred = ClientSecretCredential(
                tenant_id=azure.tenant_id,
                client_id=azure.client_id,
                client_secret=azure.client_secret,
            )
            mgmt = AzureDatabricksManagementClient(cred, azure.subscription_id)
            ws = mgmt.workspaces.get(
                resource_group_name=azure.resource_group,
                workspace_name=azure.workspace_name,
            )
            raw = getattr(ws, "workspace_url", None)
            if raw:
                workspace_url = (
                    raw if raw.startswith("https://") else f"https://{raw}"
                )
        except Exception:  # noqa: BLE001
            workspace_url = None

    extras: dict[str, str] = {}
    if workspace_url:
        extras["workspace_url"] = workspace_url
    return SourceDescriptor(
        type=SourceType.DATABRICKS,
        id=arm_id,
        display_name=azure.workspace_name,
        subscription_id=azure.subscription_id,
        resource_group=azure.resource_group,
        extras=extras,
    )


def _legacy_databricks_aws_scope() -> SourceDescriptor:
    """Build the implicit single Databricks-on-AWS scope from env vars.

    Used when ``SMA_SOURCE_TYPE=databricks`` and
    ``SMA_DATABRICKS_PLATFORM=aws``. Prefers ``DATABRICKS_HOST`` (explicit-
    host mode) because it produces a single, deterministic scope without
    a network round-trip; falls back to a synthesised descriptor keyed
    on ``DATABRICKS_ACCOUNT_ID`` so callers can still drive multi-scope
    discovery via the provider's Account-API path. See ADR-0005.
    """
    from .sources.databricks import aws_host_to_descriptor

    host = (os.getenv("DATABRICKS_HOST") or "").strip()
    if host:
        return aws_host_to_descriptor(host)
    account_id = (os.getenv("DATABRICKS_ACCOUNT_ID") or "").strip()
    # Account-id-only descriptor: the provider's ``discover()`` enumerates
    # actual workspaces from the Account API; this placeholder lets the
    # legacy single-scope code paths thread something through.
    return SourceDescriptor(
        type=SourceType.DATABRICKS,
        id=account_id,
        display_name=account_id,
        subscription_id=None,
        resource_group=None,
        extras={"platform": "aws", "account_id": account_id},
    )


def _legacy_bigquery_scope(azure: AzureConfig) -> SourceDescriptor:
    """Build the implicit single BigQuery scope from the env vars.

    BigQuery scopes are keyed on the GCP project id (no ARM URL,
    subscription, or resource group). ``SMA_GCP_PROJECT_ID`` is
    required and validated by ``load_config`` upstream.
    """
    project_id = (azure.gcp_project_id or "").strip()
    return SourceDescriptor(
        type=SourceType.BIGQUERY,
        id=project_id,
        display_name=project_id,
        subscription_id=None,
        resource_group=None,
    )


def _legacy_snowflake_scope() -> SourceDescriptor:
    """Build the implicit single Snowflake scope from the env vars.

    Snowflake scopes are keyed on the account locator. ``SNOWFLAKE_ACCOUNT``
    is required and validated by ``load_config`` upstream. The optional
    ``SMA_SNOWFLAKE_PLATFORM`` (aws/azure/gcp) hint flows through
    ``extras['platform']`` so the estate page groups Snowflake-on-Azure
    under Azure, etc. (defaults to ``aws`` to match the CLI ``--scope``
    parser).
    """
    account = (os.getenv("SNOWFLAKE_ACCOUNT") or "").strip()
    platform = (os.getenv("SMA_SNOWFLAKE_PLATFORM") or "aws").strip().lower() or "aws"
    return SourceDescriptor(
        type=SourceType.SNOWFLAKE,
        id=account,
        display_name=account,
        subscription_id=None,
        resource_group=None,
        extras={"platform": platform},
    )
