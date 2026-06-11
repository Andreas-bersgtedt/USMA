"""SynapseDedicatedSqlProvider — standalone Dedicated SQL pool (formerly SQL DW).

The resource is ``Microsoft.Sql/servers/<server>/databases/<db>`` with
``sku.tier == 'DataWarehouse'``. There is no parent Synapse workspace,
so the existing :class:`~usma.sources.synapse_workspace.SynapseWorkspaceProvider`
cannot enumerate these databases.

Each :class:`SourceDescriptor` represents one **SQL server** (which may
host zero, one or many DWU databases mixed with regular Azure SQL
databases). DWU-vs-regular filtering happens in
:class:`~usma.modules.dedicated_pools.sql_server_arm_client.SqlServerArmClient`
at analyzer time so the discovery list stays cheap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider


@dataclass(frozen=True)
class SqlServerClientBundle:
    """SDK identity bundle returned by
    :meth:`SynapseDedicatedSqlProvider.make_clients`.
    """

    credential: Any  # ClientSecretCredential
    subscription_id: str
    resource_group: str
    server_name: str
    server_fqdn: str
    location: str | None = None


class SynapseDedicatedSqlProvider(BaseSourceProvider):
    """Discovers ``Microsoft.Sql/servers`` ARM resources that host
    Dedicated SQL pools (formerly SQL DW).
    """

    type = SourceType.SYNAPSE_DEDICATED_SQL
    display_name = "Dedicated SQL pool (formerly SQL DW)"
    required_env: tuple[str, ...] = ()  # base Credentials are sufficient

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        creds: Credentials,
        subscription_id: str | None = None,
    ) -> list[SourceDescriptor]:
        """Enumerate every SQL server visible to ``creds`` in the
        specified subscription.

        DWU-vs-regular filtering happens later (per-server) so this call
        stays a single ARM list. Servers with zero DWU databases will
        produce an empty inventory at analyze time — which the SPA can
        surface as "no Dedicated SQL pools on this server".
        """
        if not subscription_id:
            raise ValueError(
                "subscription_id is required for "
                "Dedicated SQL pool (formerly SQL DW) discovery",
            )
        cred = self._make_credential(creds)
        cred.get_token("https://management.azure.com/.default")

        from azure.mgmt.sql import SqlManagementClient

        mgmt = SqlManagementClient(cred, subscription_id)
        descriptors: list[SourceDescriptor] = []
        for server in mgmt.servers.list():
            descriptors.append(_server_to_descriptor(server, subscription_id))
        return descriptors

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials,
    ) -> list[ConfigCheck]:
        """Live ARM + data-plane checks for a single standalone SQL
        server.

        Three checks:

        1. ARM Reader on the specific server.
        2. ``databases.list_by_server`` returns at least one DWU db
           (warn — not fail — when zero DWU dbs are present so users
           can still see the server in the SPA before provisioning).
        3. AAD SQL access token (``https://database.windows.net/.default``).
        """
        checks: list[ConfigCheck] = []
        cred = self._make_credential(creds)

        # --- ARM Reader on the specific server -------------------------
        server_obj: Any | None = None
        try:
            from azure.mgmt.sql import SqlManagementClient

            mgmt = SqlManagementClient(cred, descriptor.subscription_id)
            server_obj = mgmt.servers.get(
                resource_group_name=descriptor.resource_group,
                server_name=descriptor.display_name,
            )
            checks.append(ConfigCheck(
                name="SQL server (ARM Reader)",
                ok=True,
                detail=f"{server_obj.name} in {server_obj.location}",
                category="Control plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="SQL server (ARM Reader)",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))

        # --- DWU databases present? -----------------------------------
        if server_obj is not None:
            try:
                from azure.mgmt.sql import SqlManagementClient

                mgmt = SqlManagementClient(cred, descriptor.subscription_id)
                dws = [
                    db for db in mgmt.databases.list_by_server(
                        resource_group_name=descriptor.resource_group,
                        server_name=descriptor.display_name,
                    )
                    if _is_dwu_database(db)
                ]
                if dws:
                    checks.append(ConfigCheck(
                        name="Dedicated SQL pools (formerly SQL DW)",
                        ok=True,
                        detail=f"{len(dws)} DWU database(s) found",
                        category="Control plane",
                    ))
                else:
                    checks.append(ConfigCheck(
                        name="Dedicated SQL pools (formerly SQL DW)",
                        ok=False,
                        detail=(
                            "No databases with edition='DataWarehouse' "
                            "on this server. Add a pool or pick a "
                            "different server."
                        ),
                        category="Control plane",
                    ))
            except Exception as exc:  # noqa: BLE001
                checks.append(ConfigCheck(
                    name="Dedicated SQL pools (formerly SQL DW)",
                    ok=False,
                    detail=str(exc),
                    category="Control plane",
                ))

        # --- AAD SQL access token --------------------------------------
        try:
            cred.get_token("https://database.windows.net/.default")
            checks.append(ConfigCheck(
                name="AAD token (SQL)",
                ok=True,
                detail="issued for database.windows.net",
                category="Data plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="AAD token (SQL)",
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
    ) -> SqlServerClientBundle:
        if not descriptor.subscription_id:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing subscription_id",
            )
        if not descriptor.resource_group:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing resource_group",
            )
        server_name = descriptor.display_name
        server_fqdn = (
            descriptor.extras.get("sql_server_fqdn")
            or f"{server_name}.database.windows.net"
        )
        return SqlServerClientBundle(
            credential=self._make_credential(creds),
            subscription_id=descriptor.subscription_id,
            resource_group=descriptor.resource_group,
            server_name=server_name,
            server_fqdn=server_fqdn,
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


def _server_to_descriptor(server: Any, subscription_id: str) -> SourceDescriptor:
    """Adapt an azure-mgmt-sql Server model to a :class:`SourceDescriptor`."""
    arm_id = getattr(server, "id", "") or ""
    parts = arm_id.split("/")
    rg = ""
    if "resourceGroups" in parts:
        try:
            rg = parts[parts.index("resourceGroups") + 1]
        except IndexError:
            rg = ""
    name = getattr(server, "name", "") or ""
    location = getattr(server, "location", None)
    fqdn = getattr(server, "fully_qualified_domain_name", None) or f"{name}.database.windows.net"
    return SourceDescriptor(
        type=SourceType.SYNAPSE_DEDICATED_SQL,
        id=arm_id or name,
        display_name=name,
        subscription_id=subscription_id,
        resource_group=rg,
        location=location,
        extras={"sql_server_fqdn": fqdn},
    )


def _is_dwu_database(db: Any) -> bool:
    """Return True when ``db`` is a Dedicated SQL pool (formerly SQL DW).

    The standalone DWU resource carries ``sku.tier == 'DataWarehouse'``
    (or ``edition == 'DataWarehouse'`` on older API versions); either
    signal is treated as positive. The ``master`` database on every SQL
    server is skipped unconditionally.
    """
    if getattr(db, "name", "") == "master":
        return False
    sku = getattr(db, "sku", None)
    tier = (getattr(sku, "tier", "") or "").strip().lower()
    if tier == "datawarehouse":
        return True
    edition = (getattr(db, "edition", "") or "").strip().lower()
    return edition == "datawarehouse"


__all__ = [
    "SynapseDedicatedSqlProvider",
    "SqlServerClientBundle",
    "_server_to_descriptor",
    "_is_dwu_database",
]
