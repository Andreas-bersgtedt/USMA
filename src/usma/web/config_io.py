"""Read / write the .env file used by ``load_config``.

The web Configuration page is the single trusted writer. Reads always
redact ``AZURE_CLIENT_SECRET`` to ``"set"`` / ``"unset"``; writes
accept a plaintext secret only when the request includes the
``X-SMA-API`` marker (enforced globally by app middleware).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key

from .schemas import (
    AppConfigPublic,
    AppConfigUpdate,
    AzureConfigPublic,
    ConfigCheck,
    SqlConfigPublic,
    WorkspaceSummary,
)

log = logging.getLogger(__name__)

# Env-var name <-> dotted-path (azure.tenant_id, sql.odbc_driver, ...)
_AZURE_KEYS = {
    "AZURE_TENANT_ID": "tenant_id",
    "AZURE_CLIENT_ID": "client_id",
    "AZURE_SUBSCRIPTION_ID": "subscription_id",
    "SYNAPSE_RESOURCE_GROUP": "resource_group",
    "SYNAPSE_WORKSPACE_NAME": "workspace_name",
    "SYNAPSE_DEDICATED_POOL": "dedicated_pool",
}
_SQL_KEYS = {
    "SQL_ODBC_DRIVER": "odbc_driver",
    "SQL_LOGIN_TIMEOUT": "login_timeout",
    "SQL_QUERY_TIMEOUT": "query_timeout",
}
_OUTPUT_KEY = "SMA_OUTPUT_DIR"
_SOURCE_TYPE_KEY = "SMA_SOURCE_TYPE"
_GCP_PROJECT_ID_KEY = "SMA_GCP_PROJECT_ID"
# Phase 4.7 — Databricks cloud platform discriminator.
_DATABRICKS_PLATFORM_KEY = "SMA_DATABRICKS_PLATFORM"

# Phase 4.7 — Databricks-on-AWS connection env vars. Split into plain
# (returned verbatim by ``read_config``) and secret (collapsed to
# ``set``/``unset`` and written through only when present). A single
# Databricks service principal (``DATABRICKS_CLIENT_ID`` +
# ``DATABRICKS_CLIENT_SECRET``) handles both account-level workspace
# discovery and per-workspace REST auth — there is no separate PAT or
# account-only SP.
_DATABRICKS_AWS_PLAIN_KEYS = {
    "DATABRICKS_HOST": "databricks_host",
    "DATABRICKS_CLIENT_ID": "databricks_client_id",
    "DATABRICKS_ACCOUNT_ID": "databricks_account_id",
}
_DATABRICKS_AWS_SECRET_KEYS = {
    "DATABRICKS_CLIENT_SECRET": "databricks_client_secret",
}

# Phase 7 Slice 7-E.1 — Snowflake OAuth auth env vars. The provider
# reads ``SNOWFLAKE_ACCOUNT`` / ``SNOWFLAKE_USER`` / ``SNOWFLAKE_ROLE`` /
# ``SNOWFLAKE_WAREHOUSE`` (plain) plus ``SNOWFLAKE_OAUTH_CLIENT_ID`` /
# ``SNOWFLAKE_OAUTH_CLIENT_SECRET`` / ``SNOWFLAKE_OAUTH_REFRESH_TOKEN``
# (secrets used in the refresh-token grant against
# ``<account>.snowflakecomputing.com/oauth/token-request``) and an
# optional ``SNOWFLAKE_OAUTH_TOKEN`` pre-minted access token escape
# hatch. ``snowflake_platform`` is a UI-only hint persisted to
# ``SMA_SNOWFLAKE_PLATFORM`` so the SPA can render the preview without
# re-running the live ``CURRENT_REGION()`` probe.
_SNOWFLAKE_PLAIN_KEYS = {
    "SNOWFLAKE_ACCOUNT": "snowflake_account",
    "SNOWFLAKE_USER": "snowflake_user",
    "SNOWFLAKE_ROLE": "snowflake_role",
    "SNOWFLAKE_WAREHOUSE": "snowflake_warehouse",
    "SNOWFLAKE_OAUTH_CLIENT_ID": "snowflake_oauth_client_id",
}
_SNOWFLAKE_SECRET_KEYS = {
    "SNOWFLAKE_OAUTH_CLIENT_SECRET": "snowflake_oauth_client_secret",
    "SNOWFLAKE_OAUTH_REFRESH_TOKEN": "snowflake_oauth_refresh_token",
    "SNOWFLAKE_OAUTH_TOKEN": "snowflake_oauth_token",
}
_SNOWFLAKE_PLATFORM_KEY = "SMA_SNOWFLAKE_PLATFORM"
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def read_config(env_file: Path) -> AppConfigPublic:
    values = dotenv_values(env_file) if env_file.exists() else {}
    source_type_raw = (values.get(_SOURCE_TYPE_KEY) or "synapse_workspace").strip().lower()
    source_type = source_type_raw if source_type_raw in {"synapse_workspace", "adf", "databricks", "bigquery", "snowflake"} else "synapse_workspace"
    # Phase 4.7 — Databricks cloud platform. ``None`` (env var absent)
    # is the canonical "unset = azure" signal and surfaces as ``None``
    # in the public payload so the SPA can render an inferred default.
    dbx_platform_raw = (values.get(_DATABRICKS_PLATFORM_KEY) or "").strip().lower()
    dbx_platform: str | None = (
        dbx_platform_raw if dbx_platform_raw in {"azure", "aws"} else None
    )
    azure = AzureConfigPublic(
        source_type=source_type,  # type: ignore[arg-type]
        tenant_id=values.get("AZURE_TENANT_ID"),
        client_id=values.get("AZURE_CLIENT_ID"),
        client_secret="set" if values.get("AZURE_CLIENT_SECRET") else "unset",
        subscription_id=values.get("AZURE_SUBSCRIPTION_ID"),
        resource_group=values.get("SYNAPSE_RESOURCE_GROUP"),
        workspace_name=values.get("SYNAPSE_WORKSPACE_NAME"),
        dedicated_pool=values.get("SYNAPSE_DEDICATED_POOL"),
        gcp_project_id=values.get(_GCP_PROJECT_ID_KEY),
        databricks_platform=dbx_platform,  # type: ignore[arg-type]
        databricks_host=values.get("DATABRICKS_HOST"),
        databricks_client_id=values.get("DATABRICKS_CLIENT_ID"),
        databricks_client_secret=(
            "set" if values.get("DATABRICKS_CLIENT_SECRET") else "unset"
        ),
        databricks_account_id=values.get("DATABRICKS_ACCOUNT_ID"),
        snowflake_account=values.get("SNOWFLAKE_ACCOUNT"),
        snowflake_user=values.get("SNOWFLAKE_USER"),
        snowflake_role=values.get("SNOWFLAKE_ROLE"),
        snowflake_warehouse=values.get("SNOWFLAKE_WAREHOUSE"),
        snowflake_oauth_client_id=values.get("SNOWFLAKE_OAUTH_CLIENT_ID"),
        snowflake_oauth_client_secret=(
            "set" if values.get("SNOWFLAKE_OAUTH_CLIENT_SECRET") else "unset"
        ),
        snowflake_oauth_refresh_token=(
            "set" if values.get("SNOWFLAKE_OAUTH_REFRESH_TOKEN") else "unset"
        ),
        snowflake_oauth_token=(
            "set" if values.get("SNOWFLAKE_OAUTH_TOKEN") else "unset"
        ),
        snowflake_platform=(
            (lambda v: v if v in {"aws", "azure", "gcp"} else None)(
                (values.get(_SNOWFLAKE_PLATFORM_KEY) or "").strip().lower()
            )  # type: ignore[arg-type]
        ),
    )
    sql = SqlConfigPublic(
        odbc_driver=values.get("SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
        login_timeout=int(values.get("SQL_LOGIN_TIMEOUT") or 30),
        query_timeout=int(values.get("SQL_QUERY_TIMEOUT") or 120),
    )
    output_dir = Path(values.get(_OUTPUT_KEY) or "./output")
    return AppConfigPublic(
        azure=azure,
        sql=sql,
        output_dir=output_dir,
        env_file=env_file,
        env_file_exists=env_file.exists(),
    )


def write_config(env_file: Path, update: AppConfigUpdate) -> list[str]:
    """Persist ``update`` to ``env_file``. Returns warnings."""
    warnings: list[str] = []
    env_file = env_file.resolve()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    if not env_file.exists():
        env_file.touch(mode=0o600)
        warnings.append(f"created new {env_file}")
    else:
        # Best-effort restrict perms on POSIX.
        try:
            env_file.chmod(0o600)
        except OSError:
            pass

    if update.azure is not None:
        # source_type lives outside _AZURE_KEYS because it maps to a SMA_*
        # variable (not an AZURE_*/SYNAPSE_* var). Persist before the other
        # azure fields so a partial save still records the user's intent.
        if update.azure.source_type is not None:
            set_key(str(env_file), _SOURCE_TYPE_KEY, update.azure.source_type, quote_mode="never")
        # gcp_project_id lives outside _AZURE_KEYS because it maps to a
        # SMA_* variable (the GCP project id, used only when
        # source_type == "bigquery"). Persist before the AZURE_*/SYNAPSE_*
        # fields so a partial save still records the user's intent.
        if update.azure.gcp_project_id is not None:
            if update.azure.gcp_project_id == "":
                unset_key(str(env_file), _GCP_PROJECT_ID_KEY)
            else:
                set_key(str(env_file), _GCP_PROJECT_ID_KEY,
                        update.azure.gcp_project_id, quote_mode="never")
        # Phase 4.7 — Databricks cloud platform discriminator. Empty
        # string clears the env var (the backend then treats absence as
        # ``"azure"`` for backwards compatibility).
        if update.azure.databricks_platform is not None:
            if update.azure.databricks_platform == "":
                unset_key(str(env_file), _DATABRICKS_PLATFORM_KEY)
            else:
                set_key(
                    str(env_file), _DATABRICKS_PLATFORM_KEY,
                    update.azure.databricks_platform, quote_mode="never",
                )
        for env_key, attr in _AZURE_KEYS.items():
            value = getattr(update.azure, attr)
            if value is None:
                continue
            set_key(str(env_file), env_key, value, quote_mode="never")
        # Secret handling.
        if update.azure.client_secret is not None:
            if update.azure.client_secret == "":
                unset_key(str(env_file), "AZURE_CLIENT_SECRET")
            else:
                set_key(str(env_file), "AZURE_CLIENT_SECRET",
                        update.azure.client_secret, quote_mode="never")
                warnings.append("AZURE_CLIENT_SECRET written to .env")
        # Phase 4.7 — Databricks-on-AWS plain fields. ``None`` leaves the
        # var untouched; ``""`` clears it; any other string is written
        # through. The SPA toggles cloud platforms in-place so empty
        # values must actually clear the env var rather than persist a
        # zero-length string.
        for env_key, attr in _DATABRICKS_AWS_PLAIN_KEYS.items():
            value = getattr(update.azure, attr)
            if value is None:
                continue
            if value == "":
                unset_key(str(env_file), env_key)
            else:
                set_key(str(env_file), env_key, value, quote_mode="never")
        # Secret-bearing AWS fields. Same contract as ``client_secret``.
        for env_key, attr in _DATABRICKS_AWS_SECRET_KEYS.items():
            value = getattr(update.azure, attr)
            if value is None:
                continue
            if value == "":
                unset_key(str(env_file), env_key)
            else:
                set_key(str(env_file), env_key, value, quote_mode="never")
                warnings.append(f"{env_key} written to .env")
        # Phase 7 Slice 7-E — Snowflake plain fields. ``None`` leaves the
        # var untouched; ``""`` clears it; any other string is written
        # through.
        for env_key, attr in _SNOWFLAKE_PLAIN_KEYS.items():
            value = getattr(update.azure, attr)
            if value is None:
                continue
            if value == "":
                unset_key(str(env_file), env_key)
            else:
                set_key(str(env_file), env_key, value, quote_mode="never")
        # Snowflake OAuth secrets (client secret + refresh token +
        # optional pre-minted access token). Same write-only contract
        # as ``client_secret``.
        for env_key, attr in _SNOWFLAKE_SECRET_KEYS.items():
            value = getattr(update.azure, attr)
            if value is None:
                continue
            if value == "":
                unset_key(str(env_file), env_key)
            else:
                set_key(str(env_file), env_key, value, quote_mode="never")
                warnings.append(f"{env_key} written to .env")
        # Snowflake cloud-platform hint (UI-only; the backend infers the
        # cloud from CURRENT_REGION() at run time, but the SPA wants to
        # render the picked label before validation runs).
        if update.azure.snowflake_platform is not None:
            if update.azure.snowflake_platform == "":
                unset_key(str(env_file), _SNOWFLAKE_PLATFORM_KEY)
            else:
                set_key(
                    str(env_file), _SNOWFLAKE_PLATFORM_KEY,
                    update.azure.snowflake_platform, quote_mode="never",
                )
    if update.sql is not None:
        for env_key, attr in _SQL_KEYS.items():
            value = getattr(update.sql, attr)
            if value is None:
                continue
            set_key(str(env_file), env_key, str(value), quote_mode="never")
    if update.output_dir is not None:
        set_key(str(env_file), _OUTPUT_KEY, str(update.output_dir), quote_mode="never")
    return warnings


def validate_config(env_file: Path) -> list[ConfigCheck]:
    """Return per-field readiness checks the SPA renders as a list."""
    values = dotenv_values(env_file) if env_file.exists() else {}
    checks: list[ConfigCheck] = []

    def _check(name: str, ok: bool, detail: str | None = None) -> None:
        checks.append(ConfigCheck(name=name, ok=ok, detail=detail, category="Configuration"))

    _check(".env file present", env_file.exists(), str(env_file))
    for env_key in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_SUBSCRIPTION_ID"):
        v = values.get(env_key)
        if not v:
            _check(env_key, False, "missing")
        elif _GUID_RE.match(v):
            _check(env_key, True)
        else:
            _check(env_key, False, "not a GUID")
    _check(
        "AZURE_CLIENT_SECRET",
        bool(values.get("AZURE_CLIENT_SECRET")),
        "set" if values.get("AZURE_CLIENT_SECRET") else "missing",
    )
    for env_key in ("SYNAPSE_RESOURCE_GROUP", "SYNAPSE_WORKSPACE_NAME"):
        v = values.get(env_key)
        _check(env_key, bool(v), v if v else "missing")

    # Output dir
    out_dir = Path(values.get(_OUTPUT_KEY) or "./output")
    _check("output_dir writable", _is_writable(out_dir), str(out_dir.resolve()))

    return checks


_REQUIRED_LIVE = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SUBSCRIPTION_ID",
    "SYNAPSE_RESOURCE_GROUP",
    "SYNAPSE_WORKSPACE_NAME",
)

# Minimum env vars required to enumerate Synapse workspaces visible to the SP.
# The workspace name + resource group are intentionally *not* required — they
# are the values being discovered.
_REQUIRED_DISCOVER = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_SUBSCRIPTION_ID",
)


def discover_workspaces(env_file: Path) -> tuple[list[ConfigCheck], list[WorkspaceSummary]]:
    """List Synapse workspaces visible to the SP in the configured subscription.

    Phase-1 shim: the actual ARM call is delegated to
    :class:`usma.sources.synapse_workspace.SynapseWorkspaceProvider`
    via the :data:`SOURCE_REGISTRY`. This function continues to return
    the legacy ``(list[ConfigCheck], list[WorkspaceSummary])`` tuple so
    the SPA's Configuration page UX is unchanged.
    """
    from ..sources import (
        Credentials as ProviderCredentials,
        SourceType,
        get_provider,
    )

    checks: list[ConfigCheck] = []
    workspaces: list[WorkspaceSummary] = []
    values = dotenv_values(env_file) if env_file.exists() else {}

    missing = [k for k in _REQUIRED_DISCOVER if not values.get(k)]
    if missing:
        checks.append(ConfigCheck(
            name="Workspace discovery",
            ok=False,
            detail=(
                "missing required fields: "
                + ", ".join(missing)
                + " — fill in Tenant, Client, Secret, Subscription and Save first"
            ),
            category="Control plane",
        ))
        return checks, workspaces

    creds = ProviderCredentials(
        tenant_id=values["AZURE_TENANT_ID"],
        client_id=values["AZURE_CLIENT_ID"],
        client_secret=values["AZURE_CLIENT_SECRET"],
    )
    subscription = values["AZURE_SUBSCRIPTION_ID"]
    current_workspace = values.get("SYNAPSE_WORKSPACE_NAME") or None

    provider = get_provider(SourceType.SYNAPSE_WORKSPACE)
    try:
        descriptors = provider.discover(creds, subscription_id=subscription)
    except Exception as exc:  # noqa: BLE001
        # AAD failures and ARM 403s both surface here. Splitting them
        # back out into separate ConfigCheck rows would require either
        # parsing the exception or threading per-step state through the
        # provider — both more code than this Phase-1 shim warrants.
        checks.append(ConfigCheck(
            name="Synapse workspaces visible (subscription Reader)",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    # The provider succeeded, which means it already validated the AAD
    # token. Surface that as a passing check so the SPA's progress grid
    # stays informative.
    checks.append(ConfigCheck(
        name="AAD token (ARM)", ok=True,
        detail="https://management.azure.com/.default",
        category="Control plane",
    ))

    workspaces = [
        WorkspaceSummary(
            name=d.display_name,
            resource_group=d.resource_group or "",
            location=d.location,
            sql_endpoint=d.extras.get("sql_endpoint"),
            sql_on_demand_endpoint=d.extras.get("sql_on_demand_endpoint"),
            is_current=bool(current_workspace) and d.display_name == current_workspace,
        )
        for d in descriptors
    ]
    checks.append(ConfigCheck(
        name="Synapse workspaces visible (subscription Reader)",
        ok=True,
        detail=f"{len(workspaces)} workspace(s) visible to the SP",
        category="Control plane",
    ))
    return checks, workspaces


def discover_factories(env_file: Path) -> tuple[list[ConfigCheck], list[WorkspaceSummary]]:
    """List ADF factories visible to the SP in the configured subscription.

    Symmetric with :func:`discover_workspaces`. Returns the same
    ``(checks, workspaces)`` tuple shape so the SPA Configuration page
    can reuse the existing dropdown component — each :class:`WorkspaceSummary`
    here represents an ADF factory (``name`` = factory name,
    ``resource_group`` = the factory's RG) rather than a Synapse workspace.
    """
    from ..sources import (
        Credentials as ProviderCredentials,
        SourceType,
        get_provider,
    )

    checks: list[ConfigCheck] = []
    workspaces: list[WorkspaceSummary] = []
    values = dotenv_values(env_file) if env_file.exists() else {}

    missing = [k for k in _REQUIRED_DISCOVER if not values.get(k)]
    if missing:
        checks.append(ConfigCheck(
            name="Factory discovery",
            ok=False,
            detail=(
                "missing required fields: "
                + ", ".join(missing)
                + " — fill in Tenant, Client, Secret, Subscription and Save first"
            ),
            category="Control plane",
        ))
        return checks, workspaces

    creds = ProviderCredentials(
        tenant_id=values["AZURE_TENANT_ID"],
        client_id=values["AZURE_CLIENT_ID"],
        client_secret=values["AZURE_CLIENT_SECRET"],
    )
    subscription = values["AZURE_SUBSCRIPTION_ID"]
    current_factory = values.get("SYNAPSE_WORKSPACE_NAME") or None

    provider = get_provider(SourceType.ADF)
    try:
        descriptors = provider.discover(creds, subscription_id=subscription)
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="ADF factories visible (subscription Reader)",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    checks.append(ConfigCheck(
        name="AAD token (ARM)", ok=True,
        detail="https://management.azure.com/.default",
        category="Control plane",
    ))

    workspaces = [
        WorkspaceSummary(
            name=d.display_name,
            resource_group=d.resource_group or "",
            location=d.location,
            sql_endpoint=None,
            sql_on_demand_endpoint=None,
            is_current=bool(current_factory) and d.display_name == current_factory,
        )
        for d in descriptors
    ]
    checks.append(ConfigCheck(
        name="ADF factories visible (subscription Reader)",
        ok=True,
        detail=f"{len(workspaces)} factory(ies) visible to the SP",
        category="Control plane",
    ))
    return checks, workspaces


def discover_databricks_workspaces(env_file: Path) -> tuple[list[ConfigCheck], list[WorkspaceSummary]]:
    """List Databricks workspaces visible to the configured credentials.

    Branches on ``SMA_DATABRICKS_PLATFORM``:

    * ``"aws"`` \u2014 uses :class:`DatabricksAwsProvider`. No Azure SP is
      required; the function builds a synthetic :class:`Credentials`
      whose ``extras`` carry the ``DATABRICKS_*`` env vars the AWS
      provider reads. Account-API mode kicks in when
      ``DATABRICKS_ACCOUNT_ID`` + the two account OAuth secrets are
      present; otherwise the provider falls back to single-workspace
      mode via ``DATABRICKS_HOST``.
    * anything else (absent / ``"azure"``) \u2014 the legacy Azure path,
      which gates on the four ``AZURE_*`` SP env vars and enumerates
      workspaces under ``AZURE_SUBSCRIPTION_ID``.

    Each :class:`WorkspaceSummary` represents one workspace (``name`` =
    workspace name, ``resource_group`` = the workspace's Azure RG on
    Azure / empty string on AWS, ``sql_endpoint`` = the Databricks
    ``workspace_url`` for hint display).
    """
    from ..sources import (
        Credentials as ProviderCredentials,
        SourceType,
        get_provider,
    )

    checks: list[ConfigCheck] = []
    workspaces: list[WorkspaceSummary] = []
    values = dotenv_values(env_file) if env_file.exists() else {}

    platform_raw = (values.get(_DATABRICKS_PLATFORM_KEY) or "").strip().lower()
    if platform_raw == "aws":
        return _discover_databricks_aws_workspaces(values)

    missing = [k for k in _REQUIRED_DISCOVER if not values.get(k)]
    if missing:
        checks.append(ConfigCheck(
            name="Databricks workspace discovery",
            ok=False,
            detail=(
                "missing required fields: "
                + ", ".join(missing)
                + " — fill in Tenant, Client, Secret, Subscription and Save first"
            ),
            category="Control plane",
        ))
        return checks, workspaces

    creds = ProviderCredentials(
        tenant_id=values["AZURE_TENANT_ID"],
        client_id=values["AZURE_CLIENT_ID"],
        client_secret=values["AZURE_CLIENT_SECRET"],
    )
    subscription = values["AZURE_SUBSCRIPTION_ID"]
    current_workspace = values.get("SYNAPSE_WORKSPACE_NAME") or None

    provider = get_provider(SourceType.DATABRICKS)
    try:
        descriptors = provider.discover(creds, subscription_id=subscription)
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="Databricks workspaces visible (subscription Reader)",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    checks.append(ConfigCheck(
        name="AAD token (ARM)", ok=True,
        detail="https://management.azure.com/.default",
        category="Control plane",
    ))

    workspaces = [
        WorkspaceSummary(
            name=d.display_name,
            resource_group=d.resource_group or "",
            location=d.location,
            sql_endpoint=d.extras.get("workspace_url"),
            sql_on_demand_endpoint=None,
            is_current=bool(current_workspace) and d.display_name == current_workspace,
        )
        for d in descriptors
    ]
    checks.append(ConfigCheck(
        name="Databricks workspaces visible (subscription Reader)",
        ok=True,
        detail=f"{len(workspaces)} workspace(s) visible to the SP",
        category="Control plane",
    ))
    return checks, workspaces


def _discover_databricks_aws_workspaces(
    values: dict[str, str | None],
) -> tuple[list[ConfigCheck], list[WorkspaceSummary]]:
    """Helper for the AWS branch of :func:`discover_databricks_workspaces`.

    Builds a synthetic :class:`Credentials` whose ``extras`` carry the
    ``DATABRICKS_*`` env vars, then delegates to
    :class:`DatabricksAwsProvider`. Account-API mode requires the triple
    (``DATABRICKS_ACCOUNT_ID``, ``DATABRICKS_CLIENT_ID``,
    ``DATABRICKS_CLIENT_SECRET``) \u2014 the same Databricks service
    principal authenticates against both the account and each
    workspace. The fallback explicit-host mode requires only
    ``DATABRICKS_HOST``.
    """
    from ..sources import Credentials as ProviderCredentials
    from ..sources.databricks import provider_for_platform

    checks: list[ConfigCheck] = []
    workspaces: list[WorkspaceSummary] = []

    account_id = values.get("DATABRICKS_ACCOUNT_ID")
    client_id = values.get("DATABRICKS_CLIENT_ID")
    client_secret = values.get("DATABRICKS_CLIENT_SECRET")
    host = values.get("DATABRICKS_HOST")
    has_account_creds = bool(account_id and client_id and client_secret)
    if not has_account_creds and not host:
        checks.append(ConfigCheck(
            name="Databricks-on-AWS workspace discovery",
            ok=False,
            detail=(
                "set DATABRICKS_ACCOUNT_ID + DATABRICKS_CLIENT_ID + "
                "DATABRICKS_CLIENT_SECRET (single Databricks SP) for "
                "Account API discovery, or DATABRICKS_HOST for "
                "single-workspace mode"
            ),
            category="Control plane",
        ))
        return checks, workspaces

    extras: dict[str, str] = {}
    for env_key in (
        "DATABRICKS_HOST",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_ACCOUNT_ID",
    ):
        v = values.get(env_key)
        if v:
            extras[env_key] = v

    # The AWS provider ignores the four AAD fields; pass empty strings.
    creds = ProviderCredentials(
        tenant_id="", client_id="", client_secret="", extras=extras,
    )

    provider = provider_for_platform("aws")
    try:
        descriptors = provider.discover(creds, subscription_id=None)
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="Databricks-on-AWS workspaces visible",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    mode = "Account API (Databricks SP)" if has_account_creds else "explicit host"
    checks.append(ConfigCheck(
        name="Databricks-on-AWS discovery",
        ok=True,
        detail=f"{mode}",
        category="Control plane",
    ))
    current_host = host or None
    for d in descriptors:
        workspace_url = d.extras.get("workspace_url") or d.id
        workspaces.append(WorkspaceSummary(
            name=d.display_name,
            resource_group="",
            location=d.location,
            sql_endpoint=workspace_url,
            sql_on_demand_endpoint=None,
            is_current=bool(current_host) and (
                workspace_url == current_host
                or workspace_url == current_host.replace("https://", "").replace("http://", "")
            ),
        ))
    checks.append(ConfigCheck(
        name="Databricks-on-AWS workspaces visible",
        ok=True,
        detail=f"{len(workspaces)} workspace(s) discovered",
        category="Control plane",
    ))
    return checks, workspaces


def discover_bigquery_projects(env_file: Path) -> tuple[list[ConfigCheck], list[WorkspaceSummary]]:
    """List GCP projects visible via Application Default Credentials.

    Phase 5 Slice 5-D — symmetric with :func:`discover_workspaces` /
    :func:`discover_factories` / :func:`discover_databricks_workspaces`
    but bound to BigQuery's ADC-only auth model: no Azure SP credentials
    are required (or used). The Cloud Resource Manager ``search_projects``
    call uses whatever credentials ``google.auth.default()`` resolves
    (``GOOGLE_APPLICATION_CREDENTIALS`` → ``gcloud auth application-default
    login`` → WIF → metadata server). Each :class:`WorkspaceSummary` here
    represents a GCP project (``name`` = project id, ``resource_group`` =
    GCP project number when known). The currently-configured project
    (``SMA_GCP_PROJECT_ID``) is flagged via ``is_current``.
    """
    from ..sources import SourceType, get_provider

    checks: list[ConfigCheck] = []
    workspaces: list[WorkspaceSummary] = []
    values = dotenv_values(env_file) if env_file.exists() else {}
    current_project = values.get(_GCP_PROJECT_ID_KEY) or None

    try:
        provider = get_provider(SourceType.BIGQUERY)
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="BigQuery projects visible (Cloud Resource Manager)",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    try:
        # creds=None → BigQueryProvider.discover() uses google.auth.default()
        # (ADC). subscription_id is ignored by the provider (# noqa: ARG002).
        descriptors = provider.discover(None)
    except Exception as exc:  # noqa: BLE001
        # ADC missing, Cloud Resource Manager 403, missing [bigquery] extra,
        # etc. all surface here. Surface the raw error so the SPA can
        # display it inline.
        checks.append(ConfigCheck(
            name="BigQuery projects visible (Cloud Resource Manager)",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    checks.append(ConfigCheck(
        name="ADC credentials (google.auth.default)", ok=True,
        detail="GOOGLE_APPLICATION_CREDENTIALS or gcloud ADC",
        category="Control plane",
    ))

    workspaces = [
        WorkspaceSummary(
            # GCP project **id** (e.g. ``my-first-project-123456``) — this
            # is the only field BigQuery / Logging / Cloud Resource Manager
            # APIs accept. The friendly display name (``My First Project``)
            # rides in ``location`` so the SPA can render it next to the id
            # in the dropdown without taking it for the actual project id.
            name=d.id,
            resource_group=str(d.extras.get("project_number") or ""),
            location=d.display_name if d.display_name and d.display_name != d.id else None,
            sql_endpoint=None,
            sql_on_demand_endpoint=None,
            is_current=bool(current_project) and d.id == current_project,
        )
        for d in descriptors
    ]
    checks.append(ConfigCheck(
        name="BigQuery projects visible (Cloud Resource Manager)",
        ok=True,
        detail=f"{len(workspaces)} project(s) visible via ADC",
        category="Control plane",
    ))
    return checks, workspaces


def discover_snowflake_databases(env_file: Path) -> tuple[list[ConfigCheck], list[WorkspaceSummary]]:
    """Probe the configured Snowflake account via OAuth.

    Phase 7 Slice 7-E.1 — symmetric with :func:`discover_bigquery_projects`.
    Unlike Synapse / ADF / Databricks-on-Azure (which enumerate every
    workspace in a subscription), a Snowflake "scope" is the **single
    account** the credentials authorise. We delegate to
    :meth:`SnowflakeProvider.discover` which mints an access token via
    the refresh-token grant, opens a connection, runs
    ``CURRENT_REGION()`` to fill the cloud-platform discriminator, and
    returns one :class:`SourceDescriptor`. That descriptor surfaces as a
    single :class:`WorkspaceSummary` so the SPA can render the account
    name + cloud platform + region in its dropdown.
    """
    from ..sources import SourceType, get_provider

    checks: list[ConfigCheck] = []
    workspaces: list[WorkspaceSummary] = []
    values = dotenv_values(env_file) if env_file.exists() else {}
    current_account = (values.get("SNOWFLAKE_ACCOUNT") or "").strip() or None

    try:
        provider = get_provider(SourceType.SNOWFLAKE)
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="Snowflake account reachable",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    try:
        # creds=None → SnowflakeProvider.discover() resolves the
        # SNOWFLAKE_* env vars itself.
        descriptors = provider.discover(None)
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="Snowflake account reachable",
            ok=False,
            detail=str(exc),
            category="Control plane",
        ))
        return checks, workspaces

    checks.append(ConfigCheck(
        name="Snowflake OAuth",
        ok=True,
        detail="OAuth access token resolved from SNOWFLAKE_* env vars",
        category="Control plane",
    ))

    workspaces = [
        WorkspaceSummary(
            # Snowflake account locator (e.g. ``myorg-myacct``) — the only
            # field the connector / SQL identifiers accept. Region +
            # platform ride in ``location`` so the SPA can render them
            # next to the account id.
            name=d.id,
            resource_group=str(d.extras.get("platform") or ""),
            location=(
                f"{d.extras.get('region')} ({d.extras.get('platform')})"
                if d.extras.get("region") and d.extras.get("platform")
                else d.extras.get("region") or d.extras.get("platform")
            ),
            sql_endpoint=None,
            sql_on_demand_endpoint=None,
            is_current=bool(current_account) and d.id == current_account,
        )
        for d in descriptors
    ]
    checks.append(ConfigCheck(
        name="Snowflake account reachable",
        ok=True,
        detail=f"{len(workspaces)} account(s) reachable via key-pair JWT",
        category="Control plane",
    ))
    return checks, workspaces


def validate_config_live(env_file: Path) -> tuple[list[ConfigCheck], list[WorkspaceSummary]]:
    """Field checks + live connectivity checks (Azure control plane + Synapse data plane).

    Each live check is wrapped: a connection failure becomes ``ok=False`` rather
    than raising. The intent is to give the SPA a clear PASS/FAIL grid that
    distinguishes 'env vars OK' from 'data plane RBAC is granted'.

    Also enumerates Synapse workspaces visible to the service principal at the
    configured subscription scope (returns ``[]`` if the SP lacks subscription-
    level read or the SDK call fails).
    """
    checks = validate_config(env_file)
    workspaces: list[WorkspaceSummary] = []
    values = dotenv_values(env_file) if env_file.exists() else {}
    source_type_raw = (values.get(_SOURCE_TYPE_KEY) or "synapse_workspace").strip().lower()

    # Phase 5 Slice 5-D — BigQuery branch. ADC-only: no AAD env vars are
    # required, so bypass the _REQUIRED_LIVE gate entirely. Delegate to
    # BigQueryProvider.validate() and skip the entire Azure/Synapse data-
    # plane gauntlet (irrelevant for a GCP project).
    if source_type_raw == "bigquery":
        from ..sources import SourceDescriptor, SourceType, get_provider

        project_id = (values.get(_GCP_PROJECT_ID_KEY) or "").strip()
        if not project_id:
            checks.append(ConfigCheck(
                name="BigQuery project configured",
                ok=False,
                detail=f"missing {_GCP_PROJECT_ID_KEY} — pick a project and Save first",
                category="Control plane",
            ))
            return checks, workspaces
        try:
            provider = get_provider(SourceType.BIGQUERY)
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="BigQuery provider",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
            return checks, workspaces
        descriptor = SourceDescriptor(
            type=SourceType.BIGQUERY,
            id=project_id,
            display_name=project_id,
        )
        for pc in provider.validate(descriptor, None):
            checks.append(ConfigCheck(
                name=pc.name,
                ok=pc.ok,
                detail=pc.detail or None,
                category=pc.category,
            ))
        # Best-effort: also enumerate visible projects so the SPA dropdown
        # can refresh in one round-trip.
        try:
            descriptors = provider.discover(None)
            workspaces = [
                WorkspaceSummary(
                    name=d.display_name,
                    resource_group=str(d.extras.get("project_number") or ""),
                    location=None,
                    sql_endpoint=None,
                    sql_on_demand_endpoint=None,
                    is_current=d.id == project_id,
                )
                for d in descriptors
            ]
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="BigQuery projects visible (Cloud Resource Manager)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
        return checks, workspaces

    # Phase 7 Slice 7-E — Snowflake branch. Like BigQuery: no AAD env
    # vars are required, so bypass the _REQUIRED_LIVE gate entirely.
    # Delegate to SnowflakeProvider.validate() and skip the entire
    # Azure/Synapse data-plane gauntlet (irrelevant for a Snowflake
    # account).
    if source_type_raw == "snowflake":
        from ..sources import Credentials, SourceDescriptor, SourceType, get_provider

        account = (values.get("SNOWFLAKE_ACCOUNT") or "").strip()
        if not account:
            checks.append(ConfigCheck(
                name="Snowflake account configured",
                ok=False,
                detail="missing SNOWFLAKE_ACCOUNT — fill in the Snowflake fields and Save first",
                category="Control plane",
            ))
            return checks, workspaces
        try:
            provider = get_provider(SourceType.SNOWFLAKE)
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Snowflake provider",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
            return checks, workspaces
        descriptor = SourceDescriptor(
            type=SourceType.SNOWFLAKE,
            id=account,
            display_name=account,
        )
        # Build a Credentials object so the provider sees the .env
        # values without depending on os.environ (the web server's
        # environment is set at startup and does NOT auto-refresh when
        # the user edits .env via the Configuration page).
        extras: dict[str, str] = {}
        for env_key, extras_key in {
            **_SNOWFLAKE_PLAIN_KEYS,
            **_SNOWFLAKE_SECRET_KEYS,
        }.items():
            val = (values.get(env_key) or "").strip()
            if val:
                extras[extras_key] = val
        sf_creds = Credentials(
            tenant_id="",
            client_id="",
            client_secret="",
            extras=extras,
        )
        for pc in provider.validate(descriptor, sf_creds):
            checks.append(ConfigCheck(
                name=pc.name,
                ok=pc.ok,
                detail=pc.detail or None,
                category=pc.category,
            ))
        # Best-effort: also refresh the account preview so the SPA can
        # render the cloud platform + region from the just-completed
        # validation pass in one round-trip.
        try:
            descriptors = provider.discover(sf_creds)
            workspaces = [
                WorkspaceSummary(
                    name=d.id,
                    resource_group=str(d.extras.get("platform") or ""),
                    location=(
                        f"{d.extras.get('region')} ({d.extras.get('platform')})"
                        if d.extras.get("region") and d.extras.get("platform")
                        else d.extras.get("region") or d.extras.get("platform")
                    ),
                    sql_endpoint=None,
                    sql_on_demand_endpoint=None,
                    is_current=d.id == account,
                )
                for d in descriptors
            ]
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Snowflake account reachable",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
        return checks, workspaces

    if any(not values.get(k) for k in _REQUIRED_LIVE):
        checks.append(ConfigCheck(
            name="Live connectivity",
            ok=False,
            detail="skipped — fill in all required fields and Save before running live checks",
            category="Control plane",
        ))
        return checks, workspaces

    # Phase 2.5 — ADF branch. When SMA_SOURCE_TYPE=adf, delegate the live
    # connectivity checks to AdfProvider.validate() and skip the entire
    # Synapse data-plane / SQL gauntlet (irrelevant for an ADF factory).
    source_type_raw = (values.get(_SOURCE_TYPE_KEY) or "synapse_workspace").strip().lower()
    if source_type_raw == "adf":
        from ..sources import (
            Credentials as ProviderCredentials,
            SourceDescriptor,
            SourceType,
            get_provider,
        )

        creds = ProviderCredentials(
            tenant_id=values["AZURE_TENANT_ID"],
            client_id=values["AZURE_CLIENT_ID"],
            client_secret=values["AZURE_CLIENT_SECRET"],
        )
        sub = values["AZURE_SUBSCRIPTION_ID"]
        rg = values["SYNAPSE_RESOURCE_GROUP"]
        factory = values["SYNAPSE_WORKSPACE_NAME"]
        descriptor = SourceDescriptor(
            type=SourceType.ADF,
            id=(
                f"/subscriptions/{sub}/resourceGroups/{rg}"
                f"/providers/Microsoft.DataFactory/factories/{factory}"
            ),
            display_name=factory,
            subscription_id=sub,
            resource_group=rg,
        )
        provider = get_provider(SourceType.ADF)
        # Provider.validate() returns the dataclass ``ConfigCheck`` from
        # ``sources/__init__.py``; the API schema expects the Pydantic
        # ``ConfigCheck`` from ``web/schemas.py``. Same shape, different
        # type — convert so FastAPI's response_model serialiser accepts it.
        for pc in provider.validate(descriptor, creds):
            checks.append(ConfigCheck(
                name=pc.name,
                ok=pc.ok,
                detail=pc.detail or None,
                category=pc.category,
            ))
        # Best-effort: also enumerate visible factories so the SPA dropdown
        # can refresh in one round-trip.
        try:
            descriptors = provider.discover(creds, subscription_id=sub)
            workspaces = [
                WorkspaceSummary(
                    name=d.display_name,
                    resource_group=d.resource_group or "",
                    location=d.location,
                    sql_endpoint=None,
                    sql_on_demand_endpoint=None,
                    is_current=d.display_name == factory,
                )
                for d in descriptors
            ]
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="ADF factories visible (subscription Reader)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
        return checks, workspaces

    if source_type_raw == "databricks":
        # Phase 4 Slice 4-D — Databricks branch. Delegate to
        # DatabricksProvider.validate() and skip the entire Synapse data-
        # plane / SQL gauntlet (irrelevant for a Databricks workspace).
        from ..sources import (
            Credentials as ProviderCredentials,
            SourceDescriptor,
            SourceType,
            get_provider,
        )

        creds = ProviderCredentials(
            tenant_id=values["AZURE_TENANT_ID"],
            client_id=values["AZURE_CLIENT_ID"],
            client_secret=values["AZURE_CLIENT_SECRET"],
        )
        sub = values["AZURE_SUBSCRIPTION_ID"]
        rg = values["SYNAPSE_RESOURCE_GROUP"]
        ws_name = values["SYNAPSE_WORKSPACE_NAME"]
        descriptor = SourceDescriptor(
            type=SourceType.DATABRICKS,
            id=(
                f"/subscriptions/{sub}/resourceGroups/{rg}"
                f"/providers/Microsoft.Databricks/workspaces/{ws_name}"
            ),
            display_name=ws_name,
            subscription_id=sub,
            resource_group=rg,
        )
        provider = get_provider(SourceType.DATABRICKS)
        for pc in provider.validate(descriptor, creds):
            checks.append(ConfigCheck(
                name=pc.name,
                ok=pc.ok,
                detail=pc.detail or None,
                category=pc.category,
            ))
        try:
            descriptors = provider.discover(creds, subscription_id=sub)
            workspaces = [
                WorkspaceSummary(
                    name=d.display_name,
                    resource_group=d.resource_group or "",
                    location=d.location,
                    sql_endpoint=d.extras.get("workspace_url"),
                    sql_on_demand_endpoint=None,
                    is_current=d.display_name == ws_name,
                )
                for d in descriptors
            ]
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Databricks workspaces visible (subscription Reader)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
        return checks, workspaces

    tenant = values["AZURE_TENANT_ID"]
    client_id = values["AZURE_CLIENT_ID"]
    secret = values["AZURE_CLIENT_SECRET"]
    subscription = values["AZURE_SUBSCRIPTION_ID"]
    rg = values["SYNAPSE_RESOURCE_GROUP"]
    workspace = values["SYNAPSE_WORKSPACE_NAME"]
    pool = values.get("SYNAPSE_DEDICATED_POOL") or None
    odbc_driver = values.get("SQL_ODBC_DRIVER") or "ODBC Driver 18 for SQL Server"
    login_timeout = int(values.get("SQL_LOGIN_TIMEOUT") or 30)

    # Build a credential once and reuse.
    try:
        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(tenant_id=tenant, client_id=client_id, client_secret=secret)
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="AAD credential", ok=False, detail=str(exc), category="Control plane",
        ))
        return checks, workspaces

    # --- Control plane -----------------------------------------------------
    arm_token = None
    try:
        arm_token = cred.get_token("https://management.azure.com/.default")
        checks.append(ConfigCheck(
            name="AAD token (ARM)", ok=True,
            detail="https://management.azure.com/.default", category="Control plane",
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="AAD token (ARM)", ok=False, detail=str(exc), category="Control plane",
        ))

    if arm_token is not None:
        try:
            from azure.mgmt.synapse import SynapseManagementClient
            mgmt = SynapseManagementClient(cred, subscription)
            ws = mgmt.workspaces.get(resource_group_name=rg, workspace_name=workspace)
            checks.append(ConfigCheck(
                name="Synapse workspace (ARM Reader)", ok=True,
                detail=f"{ws.name} in {ws.location}", category="Control plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Synapse workspace (ARM Reader)", ok=False,
                detail=str(exc), category="Control plane",
            ))

        # Enumerate every Synapse workspace the SP can see in the subscription.
        # Best-effort: a missing role at subscription scope yields an empty
        # list rather than a hard error.
        try:
            from azure.mgmt.synapse import SynapseManagementClient
            mgmt = SynapseManagementClient(cred, subscription)
            for ws in mgmt.workspaces.list():
                workspaces.append(_workspace_summary(ws, current_workspace=workspace))
            checks.append(ConfigCheck(
                name="Synapse workspaces visible (subscription Reader)",
                ok=True,
                detail=f"{len(workspaces)} workspace(s) visible to the SP",
                category="Control plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Synapse workspaces visible (subscription Reader)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))

    # --- Data plane: Synapse Artifacts (pipelines RBAC) --------------------
    try:
        from azure.synapse.artifacts import ArtifactsClient
        endpoint = f"https://{workspace}.dev.azuresynapse.net"
        art = ArtifactsClient(endpoint=endpoint, credential=cred)
        # Touch the iterator just enough to force a real REST call + auth.
        it = art.pipeline.get_pipelines_by_workspace()
        sample = next(iter(it), None)
        detail = f"endpoint reachable; {'pipelines found' if sample is not None else 'no pipelines (auth OK)'}"
        checks.append(ConfigCheck(
            name="Synapse Artifacts (Synapse Artifact User)", ok=True,
            detail=detail, category="Data plane",
        ))
    except ImportError:
        checks.append(ConfigCheck(
            name="Synapse Artifacts (Synapse Artifact User)", ok=False,
            detail="azure-synapse-artifacts not installed", category="Data plane",
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="Synapse Artifacts (Synapse Artifact User)", ok=False,
            detail=str(exc), category="Data plane",
        ))

    # --- Data plane: Spark Livy (Synapse Compute Operator on a pool) -------
    # The spark_pools module's Livy job-history collection requires the
    # Synapse RBAC action `Microsoft.Synapse/workspaces/bigDataPools/useCompute/action`,
    # which is granted by the **Synapse Compute Operator** role (or higher,
    # e.g. Synapse Administrator). The Synapse Artifact User role is *not*
    # sufficient for Livy — it only covers the artifact catalog. We probe the
    # first available Spark pool with a tiny `get_spark_batch_jobs(size=1)`
    # call; a 403 here means the role assignment is missing.
    try:
        from azure.mgmt.synapse import SynapseManagementClient
        from azure.synapse.spark import SparkClient

        endpoint = f"https://{workspace}.dev.azuresynapse.net"
        mgmt = SynapseManagementClient(cred, subscription)
        pool_names = [
            p.name for p in mgmt.big_data_pools.list_by_workspace(rg, workspace)
        ]
        if not pool_names:
            checks.append(ConfigCheck(
                name="Spark Livy (Synapse Compute Operator)", ok=True,
                detail="no Spark pools in workspace — check skipped",
                category="Data plane",
            ))
        else:
            probe_pool = pool_names[0]
            spark = SparkClient(
                credential=cred, endpoint=endpoint,
                spark_pool_name=probe_pool,
                livy_api_version="2019-11-01-preview",
            )
            try:
                spark.spark_batch.get_spark_batch_jobs(
                    from_parameter=0, size=1, detailed=False,
                )
                checks.append(ConfigCheck(
                    name="Spark Livy (Synapse Compute Operator)", ok=True,
                    detail=(
                        f"useCompute granted on pool '{probe_pool}' "
                        f"({len(pool_names)} pool(s) probed)"
                    ),
                    category="Data plane",
                ))
            finally:
                try:
                    spark.close()
                except Exception:  # noqa: BLE001
                    pass
    except ImportError:
        checks.append(ConfigCheck(
            name="Spark Livy (Synapse Compute Operator)", ok=False,
            detail="azure-synapse-spark not installed", category="Data plane",
        ))
    except Exception as exc:  # noqa: BLE001
        # Common case: 403 with required action
        # `Microsoft.Synapse/workspaces/bigDataPools/useCompute/action`.
        # Surface verbatim so the user sees exactly which role to grant.
        checks.append(ConfigCheck(
            name="Spark Livy (Synapse Compute Operator)", ok=False,
            detail=str(exc), category="Data plane",
        ))

    # --- Data plane: SQL token + serverless SELECT 1 -----------------------
    sql_token_ok = False
    try:
        cred.get_token("https://database.windows.net/.default")
        sql_token_ok = True
        checks.append(ConfigCheck(
            name="AAD token (SQL)", ok=True,
            detail="https://database.windows.net/.default", category="Data plane",
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(ConfigCheck(
            name="AAD token (SQL)", ok=False, detail=str(exc), category="Data plane",
        ))

    if sql_token_ok:
        serverless_fqdn = f"{workspace}-ondemand.sql.azuresynapse.net"
        ok, detail = _try_sql_select1(cred, serverless_fqdn, "master", odbc_driver, login_timeout)
        checks.append(ConfigCheck(
            name=f"Serverless SQL SELECT 1 ({serverless_fqdn})",
            ok=ok, detail=detail, category="Data plane",
        ))

        if pool:
            dedicated_fqdn = f"{workspace}.sql.azuresynapse.net"
            ok, detail = _try_sql_select1(cred, dedicated_fqdn, pool, odbc_driver, login_timeout)
            checks.append(ConfigCheck(
                name=f"Dedicated SQL SELECT 1 ({dedicated_fqdn} / {pool})",
                ok=ok, detail=detail, category="Data plane",
            ))

    return checks, workspaces


def _workspace_summary(ws, current_workspace: str | None) -> WorkspaceSummary:
    """Adapt an azure-mgmt-synapse Workspace model to our DTO."""
    arm_id = getattr(ws, "id", "") or ""
    # ARM ids look like /subscriptions/<sub>/resourceGroups/<rg>/providers/...
    parts = arm_id.split("/")
    rg = ""
    if "resourceGroups" in parts:
        try:
            rg = parts[parts.index("resourceGroups") + 1]
        except IndexError:
            rg = ""
    endpoints = getattr(ws, "connectivity_endpoints", None) or {}
    sql_endpoint = endpoints.get("sql") if isinstance(endpoints, dict) else None
    sql_on_demand = endpoints.get("sqlOnDemand") if isinstance(endpoints, dict) else None
    name = getattr(ws, "name", "") or ""
    return WorkspaceSummary(
        name=name,
        resource_group=rg,
        location=getattr(ws, "location", None),
        sql_endpoint=sql_endpoint,
        sql_on_demand_endpoint=sql_on_demand,
        is_current=bool(current_workspace) and name == current_workspace,
    )


def _try_sql_select1(
    cred,
    server_fqdn: str,
    database: str,
    odbc_driver: str,
    login_timeout: int,
) -> tuple[bool, str]:
    """Connect via pyodbc + AAD access token and run SELECT 1. Never raises."""
    try:
        import struct

        import pyodbc  # type: ignore

        from .._odbc import resolve_odbc_driver
    except ImportError as exc:
        return False, f"pyodbc / _odbc not importable: {exc}"
    try:
        driver = resolve_odbc_driver(odbc_driver)
        token = cred.get_token("https://database.windows.net/.default").token
        encoded = token.encode("utf-16-le")
        token_struct = struct.pack("=i", len(encoded)) + encoded
        conn_str = (
            f"Driver={{{driver}}};"
            f"Server=tcp:{server_fqdn},1433;"
            f"Database={database};"
            f"Encrypt=yes;TrustServerCertificate=no;"
            f"Connection Timeout={login_timeout};"
        )
        with pyodbc.connect(conn_str, attrs_before={1256: token_struct}, timeout=login_timeout) as cn:
            cur = cn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        return True, "connected and ran SELECT 1"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)



def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False
