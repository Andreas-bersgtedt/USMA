"""ARM client for storage-account discovery (and Synapse workspace default ADLS)."""
from __future__ import annotations

import logging
import os
import re
from typing import Iterator

from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.synapse import SynapseManagementClient

from ...auth import get_credential
from ...config import AzureConfig
from .models import StorageAccountInventory

log = logging.getLogger(__name__)


def _include_all_default() -> bool:
    """Honour ``SMA_STORAGE_INCLUDE_ALL`` to opt out of workspace filtering."""
    val = os.getenv("SMA_STORAGE_INCLUDE_ALL", "").strip().lower()
    return val in ("1", "true", "yes", "on")


_ACCOUNT_NAME_RE = re.compile(r"[a-z0-9]{3,24}")


class StorageArmClient:
    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        cred = get_credential(azure)
        self._storage = StorageManagementClient(cred, azure.subscription_id)
        self._synapse = SynapseManagementClient(cred, azure.subscription_id)

    # ------------------------------------------------------------------ workspace
    def workspace_default_storage(self) -> tuple[str | None, str | None]:
        """Return (account_name, default_filesystem) for the workspace's primary ADLS Gen2.

        Both values may be ``None`` if the workspace doesn't expose them.
        """
        rg = self._azure.resource_group
        ws = self._azure.workspace_name
        try:
            workspace = self._synapse.workspaces.get(rg, ws)
        except Exception as exc:  # noqa: BLE001
            log.warning("Cannot read workspace %s/%s: %s", rg, ws, exc)
            return None, None

        ddls = getattr(workspace, "default_data_lake_storage", None)
        if ddls is None:
            return None, None
        # `account_url` looks like `https://<acct>.dfs.core.windows.net`.
        account_url = getattr(ddls, "account_url", None) or ""
        account_name: str | None = None
        if account_url:
            account_name = account_url.replace("https://", "").split(".")[0] or None
        filesystem = getattr(ddls, "filesystem", None)
        return account_name, filesystem

    # ------------------------------------------------------------------ accounts
    def list_storage_accounts(self) -> Iterator[StorageAccountInventory]:
        """Yield storage accounts attached to this Synapse workspace.

        Default behaviour (workspace-scoped):

        1. The workspace's default ADLS Gen2 account.
        2. Storage accounts referenced by the workspace's linked services
           (AzureBlobFS / AzureBlobStorage / AzureDataLakeStore* URIs).

        Set ``SMA_STORAGE_INCLUDE_ALL=1`` to fall back to the legacy
        behaviour and inventory every storage account discoverable to the
        credential (subscription scope, then workspace RG, then default
        ADLS as a last resort). Useful for governance audits where you
        want to spot orphaned accounts even if no linked service points
        at them.

        Duplicates (matched by lowercase resource id) are filtered out.
        """
        default_account, default_filesystem, default_rg = self._workspace_default_account_details()
        ws_rg = self._azure.resource_group
        include_all = _include_all_default()
        attached_names = (
            None if include_all else self._attached_account_names(default_account)
        )

        seen: set[str] = set()

        def _emit(acct) -> StorageAccountInventory | None:
            rid = (getattr(acct, "id", None) or "").lower()
            if not rid or rid in seen:
                return None
            if attached_names is not None and acct.name.lower() not in attached_names:
                # Workspace-scoped mode: skip accounts the workspace doesn't reference.
                return None
            seen.add(rid)
            sku = getattr(acct, "sku", None)
            primary = getattr(acct, "primary_endpoints", None)
            return StorageAccountInventory(
                name=acct.name,
                resource_id=acct.id,
                location=getattr(acct, "location", None),
                sku=getattr(sku, "name", None),
                kind=getattr(acct, "kind", None),
                access_tier=getattr(acct, "access_tier", None),
                is_hns_enabled=getattr(acct, "is_hns_enabled", None),
                primary_endpoint_dfs=getattr(primary, "dfs", None),
                primary_endpoint_blob=getattr(primary, "blob", None),
                is_workspace_default=(default_account is not None and acct.name == default_account),
                default_filesystem=default_filesystem if (default_account == acct.name) else None,
                tags=getattr(acct, "tags", None) or {},
            )

        # 1) Subscription-wide.
        sub_count = 0
        try:
            for acct in self._storage.storage_accounts.list():
                inv = _emit(acct)
                if inv is not None:
                    sub_count += 1
                    yield inv
            log.info("storage_accounts.list() returned %d account(s) at subscription scope", sub_count)
        except Exception as exc:  # noqa: BLE001
            log.warning("Subscription-wide storage list failed (%s); falling back to RG scope", exc)

        # 2) Workspace's resource group.
        if ws_rg:
            try:
                rg_count = 0
                for acct in self._storage.storage_accounts.list_by_resource_group(ws_rg):
                    inv = _emit(acct)
                    if inv is not None:
                        rg_count += 1
                        yield inv
                log.info("list_by_resource_group(%s) returned %d additional account(s)", ws_rg, rg_count)
            except Exception as exc:  # noqa: BLE001
                log.warning("list_by_resource_group(%s) failed: %s", ws_rg, exc)

        # 3) Workspace's default ADLS Gen2 (might be in a third RG/subscription).
        if default_account and default_rg:
            try:
                acct = self._storage.storage_accounts.get_properties(default_rg, default_account)
                inv = _emit(acct)
                if inv is not None:
                    log.info("Picked up workspace default storage %s/%s via get_properties",
                             default_rg, default_account)
                    yield inv
            except Exception as exc:  # noqa: BLE001
                log.warning("get_properties(%s, %s) failed: %s", default_rg, default_account, exc)

        if not seen:
            if attached_names is not None:
                log.warning(
                    "No storage accounts discovered for workspace %s/%s. "
                    "Either the workspace has no default ADLS Gen2 and no storage-backed "
                    "linked services, or the credential lacks Microsoft.Storage/storageAccounts/read "
                    "on the relevant resource groups. Set SMA_STORAGE_INCLUDE_ALL=1 to include every "
                    "account in the subscription.",
                    self._azure.resource_group,
                    self._azure.workspace_name,
                )
            else:
                log.warning(
                    "No storage accounts discovered for subscription %s. "
                    "The credential likely lacks Microsoft.Storage/storageAccounts/read at subscription "
                    "or resource-group scope. Grant Reader on the subscription (or at least on the "
                    "workspace RG and the default ADLS RG) and retry.",
                    self._azure.subscription_id,
                )

    # ------------------------------------------------------------------ helpers
    def _attached_account_names(self, default_account: str | None) -> set[str]:
        """Build the set of storage account names attached to the workspace.

        Includes the workspace's default ADLS Gen2 plus any account
        referenced by a linked service. All names are lower-cased.
        """
        names: set[str] = set()
        if default_account:
            names.add(default_account.lower())

        # Pull linked-service payloads via the existing security ARM client
        # so we don't duplicate the Artifacts plumbing.
        try:
            from ..security.arm_client import SecurityArmClient
        except ImportError:
            return names
        try:
            sec = SecurityArmClient(self._azure)
            payloads = sec.iter_linked_service_payloads()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not enumerate linked services for storage filter: %s", exc)
            return names

        for payload in payloads:
            for n in _extract_account_names_from_linked_service(payload):
                names.add(n.lower())

        log.info(
            "Workspace %s/%s references %d storage account(s) via default ADLS + linked services",
            self._azure.resource_group, self._azure.workspace_name, len(names),
        )
        return names
    def _workspace_default_account_details(self) -> tuple[str | None, str | None, str | None]:
        """Return (account_name, filesystem, resource_group) for the workspace default ADLS Gen2."""
        rg = self._azure.resource_group
        ws = self._azure.workspace_name
        try:
            workspace = self._synapse.workspaces.get(rg, ws)
        except Exception as exc:  # noqa: BLE001
            log.warning("Cannot read workspace %s/%s: %s", rg, ws, exc)
            return None, None, None

        ddls = getattr(workspace, "default_data_lake_storage", None)
        if ddls is None:
            return None, None, None
        account_url = getattr(ddls, "account_url", None) or ""
        account_name: str | None = None
        if account_url:
            account_name = account_url.replace("https://", "").split(".")[0] or None
        filesystem = getattr(ddls, "filesystem", None)

        # The Synapse SDK exposes `resource_id` on newer versions; fall back to None.
        rid = getattr(ddls, "resource_id", None)
        default_rg: str | None = None
        if rid:
            # /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<name>
            parts = [p for p in str(rid).split("/") if p]
            try:
                default_rg = parts[parts.index("resourceGroups") + 1]
            except (ValueError, IndexError):
                default_rg = None
        return account_name, filesystem, default_rg


# ---------------------------------------------------------------------------
# Linked-service → storage account extraction
# ---------------------------------------------------------------------------

# Hosts that identify Azure Storage endpoints. The leading sub-domain is the
# account name (e.g. ``acct.dfs.core.windows.net`` → ``acct``).
_STORAGE_HOST_SUFFIXES = (
    ".dfs.core.windows.net",
    ".blob.core.windows.net",
    ".file.core.windows.net",
    ".queue.core.windows.net",
    ".table.core.windows.net",
    ".dfs.core.usgovcloudapi.net",
    ".blob.core.usgovcloudapi.net",
    ".dfs.core.chinacloudapi.cn",
    ".blob.core.chinacloudapi.cn",
)


def _extract_account_names_from_linked_service(payload: dict) -> set[str]:
    """Best-effort: pull storage account names out of a linked-service payload.

    Looks at the common Synapse linked-service types (AzureBlobFS,
    AzureBlobStorage, AzureDataLakeStore, AzureDataLakeStorageGen2). Walks
    the typeProperties dict so we tolerate SDK shape changes.
    """
    out: set[str] = set()
    props = ((payload or {}).get("properties") or {})
    type_props = (props.get("typeProperties") or {})
    if not isinstance(type_props, dict):
        return out

    def _walk(node: object) -> None:
        if isinstance(node, str):
            _scan_string(node, out)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _walk(v)

    _walk(type_props)
    return out


def _scan_string(value: str, out: set[str]) -> None:
    """Pull account names out of URLs and connection strings."""
    if not value:
        return
    lowered = value.lower()
    # Endpoint URLs: https://<acct>.dfs.core.windows.net/...
    for suffix in _STORAGE_HOST_SUFFIXES:
        idx = lowered.find(suffix)
        if idx <= 0:
            continue
        # Walk back from the suffix to the start of the host segment.
        start = idx - 1
        while start >= 0 and (lowered[start].isalnum()):
            start -= 1
        candidate = lowered[start + 1: idx]
        if _ACCOUNT_NAME_RE.fullmatch(candidate):
            out.add(candidate)
    # Connection-string AccountName=acct;
    for m in re.finditer(r"accountname\s*=\s*([a-z0-9]{3,24})", lowered):
        out.add(m.group(1))
