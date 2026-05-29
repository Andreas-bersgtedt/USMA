"""DatabricksProvider — Phase-4 implementation.

Discovers ``Microsoft.Databricks/workspaces`` ARM resources and produces
clients for ``azure-mgmt-databricks`` (control plane) plus the
``databricks-sdk`` ``WorkspaceClient`` (data plane: jobs, clusters,
notebooks). Mirrors the structure of
:class:`usma.sources.adf.provider.AdfProvider`
so the three providers stay diff-able.

Auth model:
  * **Control plane** (ARM): standard ``ClientSecretCredential`` over the
    AAD SP from :class:`Credentials`.
  * **Data plane** (workspace REST): the same SP, federated to the
    Azure Databricks resource (AAD app id
    ``2ff814a6-3304-4ab8-85cb-cd0e6f879c1d``). The SP must be granted
    workspace access (admin role) inside the Databricks workspace
    itself — ARM Reader alone is insufficient.
  * **PAT fallback**: when ``creds.extras['DATABRICKS_TOKEN']`` is set,
    that token is forwarded as the data-plane bearer in lieu of an AAD
    federation. Useful for early bring-up before workspace SP admin is
    granted; not recommended for production.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider

# AAD resource id of the "AzureDatabricks" first-party app — every
# Databricks workspace data-plane token must be scoped here.
DATABRICKS_AAD_RESOURCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"


@dataclass(frozen=True)
class DatabricksClientBundle:
    """SDK clients + identity needed by every Databricks analyzer module.

    Mirrors :class:`AdfClientBundle` / :class:`SynapseClientBundle` so
    callers can swap bundles based on ``descriptor.type`` without
    conditional branches on which-fields-exist.

    ``workspace_url`` is the host portion (no scheme). On Azure that is
    the ARM ``properties.workspaceUrl`` (e.g.
    ``adb-1234567890123456.7.azuredatabricks.net``). On AWS it is the
    ``<deployment-name>.cloud.databricks.com`` host. Modules should
    prepend ``https://`` when constructing REST endpoints.

    Auth fields (only one of these is populated, in this priority order):
    - ``pat_token``: a PAT or pre-resolved OAuth bearer used directly
      as the workspace bearer token (covers Azure SP+AAD path,
      Azure PAT, AWS PAT, and AWS OAuth M2M with pre-resolved token).
    - ``oauth_client_id`` / ``oauth_client_secret``: OAuth M2M
      credentials handed to ``databricks-sdk`` so it manages token
      exchange and refresh on the data-plane client itself (AWS).
    - ``credential``: an ``azure.identity`` credential used to federate
      AAD against the AzureDatabricks first-party app (Azure default).

    ``subscription_id`` / ``resource_group`` are ``None`` on non-Azure
    platforms. ``platform`` is the cloud discriminator and tracks
    :func:`usma.sources.databricks_platform` for the descriptor.
    """

    credential: Any  # ClientSecretCredential | None
    subscription_id: str | None
    resource_group: str | None
    workspace_name: str
    workspace_url: str  # host only, no scheme
    workspace_id: str | None = None
    location: str | None = None
    pat_token: str | None = None
    # Phase 4.7 — AWS / multi-cloud additions
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    platform: str = "azure"  # "azure" | "aws" | "gcp"


class DatabricksProvider(BaseSourceProvider):
    """Discovers ``Microsoft.Databricks/workspaces`` resources."""

    type = SourceType.DATABRICKS
    display_name = "Azure Databricks"
    # PAT in extras is optional — AAD SP federation is the preferred
    # auth model. ``DATABRICKS_HOST`` is *not* required because we
    # discover the workspace URL from ARM properties.
    required_env: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        creds: Credentials,
        subscription_id: str | None = None,
    ) -> list[SourceDescriptor]:
        """Enumerate every Databricks workspace visible to ``creds``.

        Errors (missing subscription, AAD failure, ARM 403) propagate as
        exceptions — the calling shim in ``web/config_io`` wraps them
        into :class:`ConfigCheck` rows for the SPA.
        """
        if not subscription_id:
            raise ValueError(
                "subscription_id is required for Databricks workspace discovery",
            )
        cred = self._make_credential(creds)
        # Probe the ARM token early so bad credentials surface clearly.
        cred.get_token("https://management.azure.com/.default")

        try:
            from azure.mgmt.databricks import AzureDatabricksManagementClient
        except ImportError as exc:
            raise ImportError(
                "The Databricks source requires the optional '[databricks]' extra. "
                "Install it with: pip install -e \".[databricks]\" "
                "(adds azure-mgmt-databricks + databricks-sdk)."
            ) from exc

        mgmt = AzureDatabricksManagementClient(cred, subscription_id)
        descriptors: list[SourceDescriptor] = []
        for workspace in mgmt.workspaces.list_by_subscription():
            descriptors.append(_workspace_to_descriptor(workspace, subscription_id))
        return descriptors

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials,
    ) -> list[ConfigCheck]:
        """Run live ARM + data-plane checks for a single workspace.

        Symmetric with :meth:`AdfProvider.validate` so all providers
        produce the same shape of :class:`ConfigCheck` rows for the SPA.
        """
        checks: list[ConfigCheck] = []
        cred = self._make_credential(creds)

        # --- ARM Reader on the workspace ------------------------------
        workspace_url: str | None = None
        try:
            try:
                from azure.mgmt.databricks import AzureDatabricksManagementClient
            except ImportError as exc:
                raise ImportError(
                    "The Databricks source requires the optional '[databricks]' extra. "
                    "Install it with: pip install -e \".[databricks]\" "
                    "(adds azure-mgmt-databricks + databricks-sdk)."
                ) from exc

            mgmt = AzureDatabricksManagementClient(cred, descriptor.subscription_id)
            ws = mgmt.workspaces.get(
                resource_group_name=descriptor.resource_group,
                workspace_name=descriptor.display_name,
            )
            workspace_url = _read_workspace_url(ws)
            checks.append(ConfigCheck(
                name="Databricks workspace (ARM Reader)",
                ok=True,
                detail=f"{ws.name} in {getattr(ws, 'location', '?')}",
                category="Control plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Databricks workspace (ARM Reader)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
            return checks  # data-plane check would just repeat the same error

        # --- Workspace REST (jobs read) -------------------------------
        # The SP needs admin (or at minimum workspace-user with Can View
        # on jobs) inside the Databricks workspace itself. We probe via
        # current_user.me() because it's the cheapest call that proves
        # the bearer token is accepted.
        if not workspace_url:
            checks.append(ConfigCheck(
                name="Databricks workspace REST (jobs/clusters)",
                ok=False,
                detail=(
                    "ARM did not return a workspaceUrl; data-plane check "
                    "skipped."
                ),
                category="Data plane",
            ))
            return checks

        try:
            client = _make_workspace_client(
                workspace_url=workspace_url,
                cred=cred,
                pat_token=creds.extras.get("DATABRICKS_TOKEN"),
            )
            me = client.current_user.me()
            detail_who = (
                getattr(me, "user_name", None)
                or getattr(me, "display_name", None)
                or "?"
            )
            checks.append(ConfigCheck(
                name="Databricks workspace REST (jobs/clusters)",
                ok=True,
                detail=f"authenticated as {detail_who}",
                category="Data plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Databricks workspace REST (jobs/clusters)",
                ok=False,
                detail=str(exc),
                category="Data plane",
            ))

        return checks

    # ------------------------------------------------------------------
    # Client bundle
    # ------------------------------------------------------------------
    def make_clients(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials,
    ) -> DatabricksClientBundle:
        """Construct the credential + identity bundle Databricks modules need.

        Individual modules build their own SDK clients
        (``WorkspaceClient``, ``AzureDatabricksManagementClient``, ...)
        on demand from the bundle. Keeps ``make_clients`` cheap and
        import-light.
        """
        if not descriptor.subscription_id:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing subscription_id",
            )
        if not descriptor.resource_group:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing resource_group",
            )
        workspace_url = descriptor.extras.get("workspace_url")
        if not workspace_url:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing extras['workspace_url']; "
                "discover the workspace via DatabricksProvider.discover() to populate it.",
            )
        return DatabricksClientBundle(
            credential=self._make_credential(creds),
            subscription_id=descriptor.subscription_id,
            resource_group=descriptor.resource_group,
            workspace_name=descriptor.display_name,
            workspace_url=str(workspace_url),
            workspace_id=descriptor.extras.get("workspace_id"),
            location=descriptor.location,
            pat_token=creds.extras.get("DATABRICKS_TOKEN"),
            platform="azure",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _make_credential(creds: Credentials) -> Any:
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(
            tenant_id=creds.tenant_id,
            client_id=creds.client_id,
            client_secret=creds.client_secret,
        )


def _read_workspace_url(ws: Any) -> str | None:
    """Extract the data-plane host from an ARM Workspace model.

    ``azure-mgmt-databricks`` exposes the URL via
    ``properties.workspace_url`` (snake_case) or
    ``properties.workspaceUrl`` depending on SDK version; both are
    plain hosts without a scheme. Returns ``None`` if neither field is
    present (typically only on workspaces stuck in a provisioning
    state).
    """
    props = getattr(ws, "properties", None) or ws
    for attr in ("workspace_url", "workspaceUrl"):
        val = getattr(props, attr, None)
        if val:
            return str(val)
    return None


def _make_workspace_client(
    *,
    workspace_url: str,
    cred: Any,
    pat_token: str | None,
    oauth_client_id: str | None = None,
    oauth_client_secret: str | None = None,
) -> Any:
    """Build a ``databricks-sdk`` ``WorkspaceClient`` for ``workspace_url``.

    Auth selection (in priority order):
      1. ``pat_token`` — used as the workspace bearer directly. Covers
         the Azure SP+AAD path (the AAD bearer is pre-fetched and
         passed via ``token=``), PAT auth on either cloud, and AWS
         OAuth M2M when the caller chose to pre-resolve.
      2. ``oauth_client_id`` + ``oauth_client_secret`` — OAuth M2M with
         a Databricks service-principal client (AWS path). The SDK
         handles token exchange and refresh internally.
      3. ``cred`` (``azure.identity`` credential) — AAD federation to
         the AzureDatabricks first-party app (Azure default when no
         PAT is configured).
    """
    from databricks.sdk import WorkspaceClient

    host = (
        workspace_url
        if workspace_url.startswith("http")
        else f"https://{workspace_url}"
    )
    if pat_token:
        return WorkspaceClient(host=host, token=pat_token)

    if oauth_client_id and oauth_client_secret:
        # databricks-sdk handles the OAuth M2M token exchange and
        # refresh; passing client_id/client_secret triggers the
        # ``oauth-m2m`` auth_type automatically.
        return WorkspaceClient(
            host=host,
            client_id=oauth_client_id,
            client_secret=oauth_client_secret,
        )

    if cred is None:
        raise ValueError(
            "No workspace credential available — set DATABRICKS_TOKEN, "
            "or DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET (AWS), "
            "or supply an azure.identity credential (Azure)."
        )

    # AAD federation. databricks-sdk's ``credentials_provider`` hook
    # expects a 2-level callable (``provider(cfg) -> HeaderFactory``),
    # which has changed shape across releases. Pre-fetching the bearer
    # token and passing it via ``token=`` sidesteps that contract — the
    # AAD access token has ~60min TTL, which is well beyond any single
    # analyzer run.
    tok = cred.get_token(f"{DATABRICKS_AAD_RESOURCE}/.default")
    return WorkspaceClient(host=host, token=tok.token)


def _workspace_to_descriptor(workspace: Any, subscription_id: str) -> SourceDescriptor:
    """Adapt an ``azure-mgmt-databricks`` Workspace model to a
    :class:`SourceDescriptor`.

    Stashes ``workspace_url`` (data-plane host), ``workspace_id``
    (Databricks org id), ``sku`` and ``managed_resource_group_id`` under
    ``extras`` so the SPA can render a complete scope card without a
    re-fetch.
    """
    arm_id = getattr(workspace, "id", "") or ""
    parts = arm_id.split("/")
    rg = ""
    if "resourceGroups" in parts:
        try:
            rg = parts[parts.index("resourceGroups") + 1]
        except IndexError:
            rg = ""
    name = getattr(workspace, "name", "") or ""
    location = getattr(workspace, "location", None)

    extras: dict[str, Any] = {}
    workspace_url = _read_workspace_url(workspace)
    if workspace_url:
        extras["workspace_url"] = workspace_url
    # workspace_id is the Databricks org id; surfaces in REST URLs and
    # is handy for cross-referencing audit logs.
    props = getattr(workspace, "properties", None) or workspace
    for attr in ("workspace_id", "workspaceId"):
        val = getattr(props, attr, None)
        if val is not None:
            extras["workspace_id"] = str(val)
            break
    sku = getattr(workspace, "sku", None)
    sku_name = getattr(sku, "name", None) if sku is not None else None
    if sku_name:
        extras["sku"] = sku_name
    mrg = (
        getattr(props, "managed_resource_group_id", None)
        or getattr(props, "managedResourceGroupId", None)
    )
    if mrg:
        extras["managed_resource_group_id"] = mrg

    return SourceDescriptor(
        type=SourceType.DATABRICKS,
        id=arm_id or name,
        display_name=name,
        subscription_id=subscription_id,
        resource_group=rg,
        location=location,
        extras={"platform": "azure", **extras},
    )


__all__ = [
    "DatabricksProvider",
    "DatabricksClientBundle",
    "DATABRICKS_AAD_RESOURCE",
]
