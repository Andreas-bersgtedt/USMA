"""DatabricksAwsProvider — Phase 4.7 (Databricks on AWS).

Sibling to :class:`DatabricksProvider` (Azure). Both providers cover
``SourceType.DATABRICKS`` — dispatch is by ``descriptor.extras['platform']``
via :func:`usma.sources.databricks_platform` (see ADR-0005).

Differences from the Azure provider:
  * No ARM control plane. AWS workspaces are scoped to a Databricks
    **account**, not an Azure subscription, so discovery is either via
    the Databricks Account API (``DATABRICKS_ACCOUNT_ID`` plus the
    Databricks service-principal pair) or via an explicit per-workspace
    host (``DATABRICKS_HOST``).
  * No AAD federation. Both account-level and workspace-level REST auth
    use the **same** Databricks service principal
    (``DATABRICKS_CLIENT_ID`` + ``DATABRICKS_CLIENT_SECRET``). The SP
    must be admin on the account *and* added as a user on each
    workspace that should be enumerated and probed.
  * Validation runs the data-plane probe only — no ARM Reader step.

The collector / analyzer modules (``modules/databricks_workflows/``) are
cloud-agnostic and reuse the existing :func:`_make_workspace_client`
helper from ``provider.py``.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider
from .provider import DatabricksClientBundle

__all__ = ["DatabricksAwsProvider", "aws_host_to_descriptor"]


class DatabricksAwsProvider(BaseSourceProvider):
    """Discovers Databricks workspaces on AWS.

    Two discovery modes, both keyed off a **single Databricks service
    principal** (``DATABRICKS_CLIENT_ID`` + ``DATABRICKS_CLIENT_SECRET``):
      * **Account-API** — when ``DATABRICKS_ACCOUNT_ID`` plus the SP pair
        are present in :class:`Credentials.extras`, calls
        ``AccountClient.workspaces.list()`` and emits one descriptor per
        workspace. The SP must be an account admin.
      * **Explicit-host** — fallback for the alpha. When only
        ``DATABRICKS_HOST`` is set, returns a single descriptor
        synthesised from the host. The SP pair (if also present) is then
        used by :meth:`validate` / :meth:`make_clients` for workspace
        REST auth.

    Either mode raises :class:`ValueError` if the required env vars are
    missing — the caller (CLI / SPA shim) wraps that into a UI-friendly
    :class:`ConfigCheck` row.
    """

    type = SourceType.DATABRICKS
    display_name = "Databricks on AWS"
    required_env: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        creds: Credentials | None,
        subscription_id: str | None = None,  # noqa: ARG002 — Azure-only field
    ) -> list[SourceDescriptor]:
        extras: dict[str, str] = dict(creds.extras) if creds is not None else {}
        account_id = extras.get("DATABRICKS_ACCOUNT_ID")
        client_id = extras.get("DATABRICKS_CLIENT_ID")
        client_secret = extras.get("DATABRICKS_CLIENT_SECRET")

        if account_id and client_id and client_secret:
            return self._discover_via_account_api(
                account_id=account_id,
                client_id=client_id,
                client_secret=client_secret,
            )

        host = extras.get("DATABRICKS_HOST")
        if host:
            return [aws_host_to_descriptor(host)]

        raise ValueError(
            "Databricks-on-AWS discovery requires either "
            "DATABRICKS_ACCOUNT_ID + DATABRICKS_CLIENT_ID + "
            "DATABRICKS_CLIENT_SECRET (Account API mode), or "
            "DATABRICKS_HOST (explicit-host mode).",
        )

    @staticmethod
    def _discover_via_account_api(
        *, account_id: str, client_id: str, client_secret: str,
    ) -> list[SourceDescriptor]:
        try:
            from databricks.sdk import AccountClient
        except ImportError as exc:  # pragma: no cover — extras gate
            raise ImportError(
                "The Databricks-on-AWS source requires the optional "
                "'[databricks]' extra. Install it with: "
                "pip install -e \".[databricks]\" (adds databricks-sdk).",
            ) from exc

        account = AccountClient(
            host="https://accounts.cloud.databricks.com",
            account_id=account_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        descriptors: list[SourceDescriptor] = []
        for ws in account.workspaces.list():
            deployment = getattr(ws, "deployment_name", None)
            workspace_id = getattr(ws, "workspace_id", None)
            workspace_name = getattr(ws, "workspace_name", None) or (
                deployment or (str(workspace_id) if workspace_id else "unknown")
            )
            host = (
                f"{deployment}.cloud.databricks.com"
                if deployment
                else getattr(ws, "deployment_url", "") or ""
            )
            extras: dict[str, Any] = {"platform": "aws"}
            if host:
                extras["workspace_url"] = host
            if workspace_id is not None:
                extras["workspace_id"] = str(workspace_id)
            if account_id:
                extras["account_id"] = account_id
            descriptors.append(SourceDescriptor(
                type=SourceType.DATABRICKS,
                id=host or workspace_name,
                display_name=workspace_name,
                subscription_id=None,
                resource_group=None,
                location=getattr(ws, "aws_region", None) or None,
                extras=extras,
            ))
        return descriptors

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials | None,
    ) -> list[ConfigCheck]:
        """Run a single data-plane probe (``current_user.me()``)."""
        from .provider import _make_workspace_client

        workspace_url = descriptor.extras.get("workspace_url") or descriptor.id
        if not workspace_url:
            return [ConfigCheck(
                name="Databricks workspace REST (jobs/clusters)",
                ok=False,
                detail=(
                    "AWS Databricks descriptor is missing both extras['workspace_url'] "
                    "and id; cannot reach the workspace."
                ),
                category="Data plane",
            )]

        extras = dict(creds.extras) if creds is not None else {}
        try:
            client = _make_workspace_client(
                workspace_url=str(workspace_url),
                cred=None,
                pat_token=None,
                oauth_client_id=extras.get("DATABRICKS_CLIENT_ID"),
                oauth_client_secret=extras.get("DATABRICKS_CLIENT_SECRET"),
            )
            me = client.current_user.me()
            detail_who = (
                getattr(me, "user_name", None)
                or getattr(me, "display_name", None)
                or "?"
            )
            return [ConfigCheck(
                name="Databricks workspace REST (jobs/clusters)",
                ok=True,
                detail=f"authenticated as {detail_who} on {workspace_url}",
                category="Data plane",
            )]
        except Exception as exc:  # noqa: BLE001
            return [ConfigCheck(
                name="Databricks workspace REST (jobs/clusters)",
                ok=False,
                detail=str(exc),
                category="Data plane",
            )]

    # ------------------------------------------------------------------
    # Client bundle
    # ------------------------------------------------------------------
    def make_clients(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials | None,
    ) -> DatabricksClientBundle:
        workspace_url = descriptor.extras.get("workspace_url") or descriptor.id
        if not workspace_url:
            raise ValueError(
                f"AWS Databricks descriptor {descriptor.display_name!r} is "
                "missing both extras['workspace_url'] and id.",
            )
        extras = dict(creds.extras) if creds is not None else {}
        return DatabricksClientBundle(
            credential=None,
            subscription_id=None,
            resource_group=None,
            workspace_name=descriptor.display_name,
            workspace_url=str(workspace_url),
            workspace_id=descriptor.extras.get("workspace_id"),
            location=descriptor.location,
            pat_token=None,
            oauth_client_id=extras.get("DATABRICKS_CLIENT_ID"),
            oauth_client_secret=extras.get("DATABRICKS_CLIENT_SECRET"),
            platform="aws",
        )


def aws_host_to_descriptor(host: str) -> SourceDescriptor:
    """Synthesise a :class:`SourceDescriptor` from an AWS workspace host.

    Accepts either ``acme-prod.cloud.databricks.com`` or
    ``https://acme-prod.cloud.databricks.com``. Strips trailing slashes.
    The display name is the first label of the host (deployment name).
    """
    raw = host.strip()
    if not raw:
        raise ValueError("AWS Databricks host cannot be empty")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    netloc = parsed.netloc or parsed.path
    netloc = netloc.split("/", 1)[0].lower()
    if not netloc:
        raise ValueError(f"Could not parse AWS Databricks host from {host!r}")
    deployment = netloc.split(".", 1)[0]
    return SourceDescriptor(
        type=SourceType.DATABRICKS,
        id=netloc,
        display_name=deployment or netloc,
        subscription_id=None,
        resource_group=None,
        location=None,
        extras={"platform": "aws", "workspace_url": netloc},
    )
