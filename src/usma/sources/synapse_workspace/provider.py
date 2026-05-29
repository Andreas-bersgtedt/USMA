"""SynapseWorkspaceProvider — Phase-1 implementation.

Lifts discovery and validation logic from
``src/usma/web/config_io.py`` behind the
``SourceProvider`` abstraction. The legacy ``discover_workspaces`` and
``validate_config_live`` functions become thin shims that delegate to
this provider (see [`web/config_io.py`](../../web/config_io.py)).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider


@dataclass(frozen=True)
class SynapseClientBundle:
    """SDK clients + identity needed by every Synapse analyzer module.

    Returned by :meth:`SynapseWorkspaceProvider.make_clients`. Modules
    pick whichever fields they need; new fields can be added without
    breaking existing callers because the dataclass is positional-free.
    """

    credential: Any  # ClientSecretCredential
    subscription_id: str
    resource_group: str
    workspace_name: str
    location: str | None = None


class SynapseWorkspaceProvider(BaseSourceProvider):
    """Discovers ``Microsoft.Synapse/workspaces`` ARM resources."""

    type = SourceType.SYNAPSE_WORKSPACE
    display_name = "Azure Synapse Workspace"
    required_env: tuple[str, ...] = ()  # base Credentials are sufficient

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        creds: Credentials,
        subscription_id: str | None = None,
    ) -> list[SourceDescriptor]:
        """Enumerate every Synapse workspace visible to ``creds`` in the
        specified subscription.

        Errors (missing subscription, AAD failure, ARM 403) are surfaced
        as exceptions — the calling shim in ``web/config_io`` wraps them
        into :class:`ConfigCheck` rows for the SPA. CLI callers see the
        raw exception.
        """
        if not subscription_id:
            raise ValueError(
                "subscription_id is required for Synapse workspace discovery",
            )
        cred = self._make_credential(creds)
        # Probe the token to surface bad credentials early.
        cred.get_token("https://management.azure.com/.default")

        from azure.mgmt.synapse import SynapseManagementClient

        mgmt = SynapseManagementClient(cred, subscription_id)
        descriptors: list[SourceDescriptor] = []
        for ws in mgmt.workspaces.list():
            descriptors.append(_workspace_to_descriptor(ws, subscription_id))
        return descriptors

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials,
    ) -> list[ConfigCheck]:
        """Run the live ARM + data-plane checks for a single workspace.

        This is the per-scope subset of
        ``web/config_io.validate_config_live``. The shim in
        ``web/config_io.py`` still runs the env-var and "list visible
        workspaces" checks for backwards compatibility, then delegates
        the per-scope checks here.
        """
        checks: list[ConfigCheck] = []
        cred = self._make_credential(creds)

        # --- ARM Reader on the specific workspace ---------------------
        try:
            from azure.mgmt.synapse import SynapseManagementClient

            mgmt = SynapseManagementClient(cred, descriptor.subscription_id)
            ws = mgmt.workspaces.get(
                resource_group_name=descriptor.resource_group,
                workspace_name=descriptor.display_name,
            )
            checks.append(ConfigCheck(
                name="Synapse workspace (ARM Reader)",
                ok=True,
                detail=f"{ws.name} in {ws.location}",
                category="Control plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Synapse workspace (ARM Reader)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))

        # --- Synapse Artifacts (pipelines RBAC) -----------------------
        try:
            from azure.synapse.artifacts import ArtifactsClient

            endpoint = f"https://{descriptor.display_name}.dev.azuresynapse.net"
            art = ArtifactsClient(endpoint=endpoint, credential=cred)
            it = art.pipeline.get_pipelines_by_workspace()
            sample = next(iter(it), None)
            detail = (
                "endpoint reachable; "
                + ("pipelines found" if sample is not None else "no pipelines (auth OK)")
            )
            checks.append(ConfigCheck(
                name="Synapse Artifacts (Synapse Artifact User)",
                ok=True,
                detail=detail,
                category="Data plane",
            ))
        except ImportError:
            checks.append(ConfigCheck(
                name="Synapse Artifacts (Synapse Artifact User)",
                ok=False,
                detail="azure-synapse-artifacts not installed",
                category="Data plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Synapse Artifacts (Synapse Artifact User)",
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
    ) -> SynapseClientBundle:
        """Construct the credential + identity bundle Synapse modules
        need.

        Returns the credential and identifying fields; individual modules
        construct their own SDK clients (SynapseManagementClient,
        ArtifactsClient, SparkClient, MonitorManagementClient, ...) on
        demand from this bundle. This keeps ``make_clients`` cheap and
        avoids importing every Azure SDK just to start a run.
        """
        if not descriptor.subscription_id:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing subscription_id",
            )
        if not descriptor.resource_group:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing resource_group",
            )
        return SynapseClientBundle(
            credential=self._make_credential(creds),
            subscription_id=descriptor.subscription_id,
            resource_group=descriptor.resource_group,
            workspace_name=descriptor.display_name,
            location=descriptor.location,
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


def _workspace_to_descriptor(ws: Any, subscription_id: str) -> SourceDescriptor:
    """Adapt an azure-mgmt-synapse Workspace model to a
    :class:`SourceDescriptor`.

    Stores the SQL endpoints under ``extras`` for the SPA's workspace
    dropdown (preserves the existing ``WorkspaceSummary`` UX).
    """
    arm_id = getattr(ws, "id", "") or ""
    parts = arm_id.split("/")
    rg = ""
    if "resourceGroups" in parts:
        try:
            rg = parts[parts.index("resourceGroups") + 1]
        except IndexError:
            rg = ""
    name = getattr(ws, "name", "") or ""
    location = getattr(ws, "location", None)

    endpoints = getattr(ws, "connectivity_endpoints", None) or {}
    sql_endpoint = endpoints.get("sql") if isinstance(endpoints, dict) else None
    sql_on_demand = endpoints.get("sqlOnDemand") if isinstance(endpoints, dict) else None

    extras: dict[str, Any] = {}
    if sql_endpoint:
        extras["sql_endpoint"] = sql_endpoint
    if sql_on_demand:
        extras["sql_on_demand_endpoint"] = sql_on_demand

    return SourceDescriptor(
        type=SourceType.SYNAPSE_WORKSPACE,
        id=arm_id or name,
        display_name=name,
        subscription_id=subscription_id,
        resource_group=rg,
        location=location,
        extras=extras,
    )


__all__ = ["SynapseWorkspaceProvider", "SynapseClientBundle"]
