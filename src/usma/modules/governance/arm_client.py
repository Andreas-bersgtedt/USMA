"""Best-effort ARM/Purview collectors for the governance module.

All public methods are wrapped in ``try / except`` and return ``[]`` on failure
so callers can tolerate missing SDK extras or insufficient RBAC. They also
respect SMA_GOVERNANCE_DISABLE_LIVE=1 to short-circuit any live calls.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from ...config import AzureConfig
from .models import (
    CustomerManagedKey,
    ManagedPrivateEndpoint,
    PurviewLineageEdge,
    RoleAssignment,
)

log = logging.getLogger(__name__)


def _live_disabled() -> bool:
    return os.getenv("SMA_GOVERNANCE_DISABLE_LIVE", "").strip() in {"1", "true", "yes"}


def _scope_concurrency() -> int:
    try:
        return max(1, int(os.getenv("SMA_GOVERNANCE_SCOPE_CONCURRENCY", "4")))
    except ValueError:
        return 4


class GovernanceArmClient:
    """Wraps azure-mgmt-authorization, azure-mgmt-synapse and azure-mgmt-storage.

    Collectors are unit-testable via dependency injection — any callable returning
    an iterable is accepted in lieu of the real SDK paged result.
    """

    def __init__(self, cfg: AzureConfig) -> None:
        self._cfg = cfg
        # role_definition_id -> friendly role name; populated lazily.
        self._role_name_cache: dict[str, str] = {}

    # -- Role assignments -----------------------------------------------------

    def list_role_assignments(
        self,
        scopes: Iterable[str] | None = None,
    ) -> list[RoleAssignment]:
        """Return RBAC assignments scoped to the workspace and its sub-resources.

        Returns an empty list if the SDK is missing or the request fails — the
        caller should record the message via the analyzer's ``errors`` list.
        """
        if _live_disabled():
            return []
        try:  # pragma: no cover - exercised only when SDK is installed
            from azure.identity import ClientSecretCredential
            from azure.mgmt.authorization import AuthorizationManagementClient
        except ImportError:
            log.debug("azure-mgmt-authorization not installed; skipping RBAC collect")
            return []

        cred = ClientSecretCredential(
            tenant_id=self._cfg.tenant_id,
            client_id=self._cfg.client_id,
            client_secret=self._cfg.client_secret,
        )
        client = AuthorizationManagementClient(cred, self._cfg.subscription_id)

        results: list[RoleAssignment] = []
        seen_ids: set[str] = set()
        scope_list = list(scopes or [
            f"/subscriptions/{self._cfg.subscription_id}/resourceGroups/{self._cfg.resource_group}"
        ])

        # Pull each scope's role assignments in parallel; the friendly-name
        # cache is shared and guarded by a lock.
        cache_lock = threading.Lock()

        def _one(scope: str) -> list[tuple[str, RoleAssignment]]:
            try:
                pages = client.role_assignments.list_for_scope(scope)
            except Exception as exc:  # noqa: BLE001
                log.warning("role_assignments.list_for_scope(%s) failed: %s", scope, exc)
                return []
            scope_results: list[tuple[str, RoleAssignment]] = []
            for ra in pages:
                ra_id = getattr(ra, "id", "") or ""
                model = _role_assignment_to_model(ra, scope)
                if model.role_definition_id:
                    model.role_name = self._resolve_role_name_locked(
                        client, model.role_definition_id,
                        fallback=model.role_name, lock=cache_lock,
                    )
                scope_results.append((ra_id, model))
            return scope_results

        max_workers = min(len(scope_list), _scope_concurrency())
        if max_workers <= 1:
            per_scope = [_one(s) for s in scope_list]
        else:
            with ThreadPoolExecutor(max_workers=max_workers,
                                    thread_name_prefix="sma-gov-rbac") as ex:
                per_scope = list(ex.map(_one, scope_list))

        for scope_results in per_scope:
            for ra_id, model in scope_results:
                if ra_id and ra_id in seen_ids:
                    continue
                if ra_id:
                    seen_ids.add(ra_id)
                results.append(model)
        return results

    def _resolve_role_name_locked(
        self,
        client: object,
        role_definition_id: str,
        *,
        fallback: str,
        lock: threading.Lock,
    ) -> str:
        with lock:
            cached = self._role_name_cache.get(role_definition_id)
        if cached is not None:
            return cached
        name = self._resolve_role_name(client, role_definition_id, fallback=fallback)
        with lock:
            self._role_name_cache.setdefault(role_definition_id, name)
        return name

    def _resolve_role_name(
        self,
        client: object,
        role_definition_id: str,
        *,
        fallback: str,
    ) -> str:
        """Best-effort role-definition-id -> friendly name with in-process cache."""
        if not role_definition_id:
            return fallback
        if role_definition_id in self._role_name_cache:
            return self._role_name_cache[role_definition_id]
        try:  # pragma: no cover - SDK-bound
            rd = client.role_definitions.get_by_id(role_definition_id)  # type: ignore[attr-defined]
            name = getattr(rd, "role_name", None) or getattr(rd, "name", None) or fallback
        except Exception as exc:  # noqa: BLE001
            log.debug("role_definitions.get_by_id(%s) failed: %s", role_definition_id, exc)
            name = fallback
        self._role_name_cache[role_definition_id] = name
        return name

    # -- Managed private endpoints --------------------------------------------

    def list_managed_private_endpoints(self) -> list[ManagedPrivateEndpoint]:
        """Best-effort wrapper around azure-synapse-managedprivateendpoints."""
        if _live_disabled():
            return []
        try:  # pragma: no cover
            from azure.identity import ClientSecretCredential
            from azure.synapse.managedprivateendpoints import ManagedPrivateEndpointsClient
        except ImportError:
            log.debug("azure-synapse-managedprivateendpoints not installed")
            return []

        cred = ClientSecretCredential(
            tenant_id=self._cfg.tenant_id,
            client_id=self._cfg.client_id,
            client_secret=self._cfg.client_secret,
        )
        endpoint = f"https://{self._cfg.workspace_name}.dev.azuresynapse.net"
        client = ManagedPrivateEndpointsClient(endpoint=endpoint, credential=cred)

        out: list[ManagedPrivateEndpoint] = []
        try:
            for mpe in client.managed_private_endpoints.list("default"):
                out.append(_mpe_to_model(mpe))
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to list managed private endpoints: %s", exc)
        return out

    # -- Customer-managed keys -------------------------------------------------

    def collect_customer_managed_keys(self) -> list[CustomerManagedKey]:
        """Read CMK config from the workspace + workspace storage account."""
        if _live_disabled():
            return []
        out: list[CustomerManagedKey] = []
        try:  # pragma: no cover
            from azure.identity import ClientSecretCredential
            from azure.mgmt.synapse import SynapseManagementClient
        except ImportError:
            return []
        cred = ClientSecretCredential(
            tenant_id=self._cfg.tenant_id,
            client_id=self._cfg.client_id,
            client_secret=self._cfg.client_secret,
        )
        client = SynapseManagementClient(cred, self._cfg.subscription_id)
        try:
            ws = client.workspaces.get(self._cfg.resource_group, self._cfg.workspace_name)
            cmk = getattr(ws, "encryption", None) and getattr(ws.encryption, "cmk", None)
            if cmk:
                key = getattr(cmk, "key", None)
                out.append(CustomerManagedKey(
                    resource_id=ws.id,
                    resource_kind="workspace",
                    enabled=getattr(cmk, "status", "Disabled") == "Enabled",
                    key_vault_uri=getattr(key, "key_vault_url", None) if key else None,
                    key_name=getattr(key, "name", None) if key else None,
                ))
        except Exception as exc:  # noqa: BLE001
            log.warning("workspace CMK probe failed: %s", exc)
        return out

    # -- Purview lineage -------------------------------------------------------

    def collect_purview_lineage(self, purview_account: str | None) -> list[PurviewLineageEdge]:
        """Pull lineage edges for the workspace from a Purview account.

        v0 returns an empty list — wiring the Atlas REST API is left for a
        follow-up; the model + flow is defined now so reports / rules can rely
        on the shape.
        """
        if not purview_account or _live_disabled():
            return []
        log.info("Purview lineage collector is a v0 stub for %s", purview_account)
        return []


def _role_assignment_to_model(ra: object, scope: str) -> RoleAssignment:
    """Convert an SDK RoleAssignment to our normalized model."""
    role_def_id = getattr(ra, "role_definition_id", "") or ""
    role_name = role_def_id.rsplit("/", 1)[-1] if role_def_id else ""
    principal_id = getattr(ra, "principal_id", "") or ""
    principal_type = getattr(ra, "principal_type", None)
    return RoleAssignment(
        scope=scope,
        scope_kind=_classify_scope(scope),
        role_name=role_name,
        role_definition_id=role_def_id,
        principal_id=principal_id,
        principal_type=str(principal_type) if principal_type else None,
        principal_display_name=None,
        assignment_id=getattr(ra, "id", "") or "",
        plane="control",
    )


def _mpe_to_model(mpe: object) -> ManagedPrivateEndpoint:
    props = getattr(mpe, "properties", None)
    target = getattr(props, "private_link_resource_id", None) if props else None
    group_id = getattr(props, "group_id", None) if props else None
    provisioning = getattr(props, "provisioning_state", None) if props else None
    conn = getattr(props, "connection_state", None) if props else None
    conn_status = getattr(conn, "status", None) if conn else None
    fqdns_attr = getattr(props, "fqdns", None) if props else None
    return ManagedPrivateEndpoint(
        name=getattr(mpe, "name", "") or "",
        target_resource_id=target,
        target_resource_type=_resource_type(target) if target else None,
        group_id=group_id,
        provisioning_state=str(provisioning) if provisioning else None,
        connection_state=str(conn_status) if conn_status else None,
        fqdns=list(fqdns_attr or []),
    )


def _classify_scope(scope: str) -> str:
    if not scope:
        return "other"
    s = scope.lower()
    if "/providers/microsoft.synapse/workspaces/" in s and "/sqlpools/" in s:
        return "pool"
    if "/providers/microsoft.synapse/workspaces/" in s:
        return "workspace"
    if "/providers/microsoft.storage/storageaccounts/" in s:
        return "storage"
    if "/resourcegroups/" in s and "/providers/" not in s:
        return "resource_group"
    if s.count("/") == 2 and s.startswith("/subscriptions/"):
        return "subscription"
    return "other"


def _resource_type(arm_id: str) -> str | None:
    """Extract the `Provider/type` portion from an ARM id, if present."""
    if not arm_id:
        return None
    parts = arm_id.split("/")
    if "providers" in parts:
        i = parts.index("providers")
        if i + 2 < len(parts):
            return f"{parts[i + 1]}/{parts[i + 2]}"
    return None
