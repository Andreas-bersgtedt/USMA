"""AdfProvider — Phase-2 implementation.

Discovers ``Microsoft.DataFactory/factories`` ARM resources and produces
clients for ``azure-mgmt-datafactory``. Mirrors the structure of
:class:`usma.sources.synapse_workspace.SynapseWorkspaceProvider`
so the two providers stay diff-able as new sources land.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider


@dataclass(frozen=True)
class AdfClientBundle:
    """SDK clients + identity needed by every ADF analyzer module.

    Mirrors :class:`SynapseClientBundle` exactly so callers can swap
    bundles based on ``descriptor.type`` without conditional branches
    on which-fields-exist.
    """

    credential: Any  # ClientSecretCredential
    subscription_id: str
    resource_group: str
    factory_name: str
    location: str | None = None


class AdfProvider(BaseSourceProvider):
    """Discovers ``Microsoft.DataFactory/factories`` resources."""

    type = SourceType.ADF
    display_name = "Azure Data Factory"
    required_env: tuple[str, ...] = ()  # base Credentials are sufficient

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        creds: Credentials,
        subscription_id: str | None = None,
    ) -> list[SourceDescriptor]:
        """Enumerate every ADF factory visible to ``creds`` in the
        specified subscription.

        Errors (missing subscription, AAD failure, ARM 403) are surfaced
        as exceptions — the calling shim in ``web/config_io`` is
        expected to wrap them into :class:`ConfigCheck` rows for the
        SPA. CLI callers see the raw exception.
        """
        if not subscription_id:
            raise ValueError(
                "subscription_id is required for ADF factory discovery",
            )
        cred = self._make_credential(creds)
        # Probe the ARM token early so bad credentials surface clearly.
        cred.get_token("https://management.azure.com/.default")

        from azure.mgmt.datafactory import DataFactoryManagementClient

        mgmt = DataFactoryManagementClient(cred, subscription_id)
        descriptors: list[SourceDescriptor] = []
        for factory in mgmt.factories.list():
            descriptors.append(_factory_to_descriptor(factory, subscription_id))
        return descriptors

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials,
    ) -> list[ConfigCheck]:
        """Run live ARM + data-plane checks for a single factory.

        Symmetric with
        :meth:`SynapseWorkspaceProvider.validate` so both providers
        produce the same shape of :class:`ConfigCheck` rows for the SPA.
        """
        checks: list[ConfigCheck] = []
        cred = self._make_credential(creds)

        # --- ARM Reader on the specific factory -----------------------
        try:
            from azure.mgmt.datafactory import DataFactoryManagementClient

            mgmt = DataFactoryManagementClient(cred, descriptor.subscription_id)
            factory = mgmt.factories.get(
                resource_group_name=descriptor.resource_group,
                factory_name=descriptor.display_name,
            )
            checks.append(ConfigCheck(
                name="ADF factory (ARM Reader)",
                ok=True,
                detail=f"{factory.name} in {factory.location}",
                category="Control plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="ADF factory (ARM Reader)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
            return checks  # data-plane check would just repeat the same error

        # --- ADF pipelines RBAC ---------------------------------------
        try:
            from azure.mgmt.datafactory import DataFactoryManagementClient

            mgmt = DataFactoryManagementClient(cred, descriptor.subscription_id)
            it = mgmt.pipelines.list_by_factory(
                resource_group_name=descriptor.resource_group,
                factory_name=descriptor.display_name,
            )
            sample = next(iter(it), None)
            detail = (
                "factory reachable; "
                + ("pipelines found" if sample is not None else "no pipelines (auth OK)")
            )
            checks.append(ConfigCheck(
                name="ADF pipelines (Data Factory Contributor / Reader)",
                ok=True,
                detail=detail,
                category="Data plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="ADF pipelines (Data Factory Contributor / Reader)",
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
    ) -> AdfClientBundle:
        """Construct the credential + identity bundle ADF modules need.

        Returns the credential and identifying fields; individual
        modules build their own SDK clients
        (``DataFactoryManagementClient``, Monitor, Storage, ...) on
        demand. Keeps ``make_clients`` cheap and import-light.
        """
        if not descriptor.subscription_id:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing subscription_id",
            )
        if not descriptor.resource_group:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing resource_group",
            )
        return AdfClientBundle(
            credential=self._make_credential(creds),
            subscription_id=descriptor.subscription_id,
            resource_group=descriptor.resource_group,
            factory_name=descriptor.display_name,
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


def _factory_to_descriptor(factory: Any, subscription_id: str) -> SourceDescriptor:
    """Adapt an ``azure-mgmt-datafactory`` Factory model to a
    :class:`SourceDescriptor`.

    Stores any factory-specific endpoints (e.g. the public/PE network
    flag, the managed VNet IR if present) under ``extras`` so the SPA
    can show them on the scope card without re-querying ARM.
    """
    arm_id = getattr(factory, "id", "") or ""
    parts = arm_id.split("/")
    rg = ""
    if "resourceGroups" in parts:
        try:
            rg = parts[parts.index("resourceGroups") + 1]
        except IndexError:
            rg = ""
    name = getattr(factory, "name", "") or ""
    location = getattr(factory, "location", None)

    extras: dict[str, Any] = {}
    # public_network_access is "Enabled"/"Disabled"/None depending on
    # which network configuration the factory has.
    pna = getattr(factory, "public_network_access", None)
    if pna is not None:
        extras["public_network_access"] = pna
    # version is typically "2018-06-01" for current ADF; surfacing it is
    # cheap and lets the UI show "v2" without an extra call.
    version = getattr(factory, "version", None)
    if version is not None:
        extras["version"] = version

    return SourceDescriptor(
        type=SourceType.ADF,
        id=arm_id or name,
        display_name=name,
        subscription_id=subscription_id,
        resource_group=rg,
        location=location,
        extras=extras,
    )


__all__ = ["AdfProvider", "AdfClientBundle"]
