"""Best-effort collectors for the security module.

The collectors return empty lists / ``None`` if the underlying SDK is not
installed or an Azure call fails — the analyzer records a string in
``SecurityAnalysis.errors``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from ...auth import get_credential
from ...config import AzureConfig
from .models import CredentialEntry, FirewallRule, PoolTdeStatus, WorkspaceSecuritySettings

log = logging.getLogger(__name__)


def _live_disabled() -> bool:
    return os.getenv("SMA_SECURITY_DISABLE_LIVE", "").strip() in {"1", "true", "yes"}


class SecurityArmClient:
    """ARM-plane collectors for security findings.

    The Synapse management client is cached lazily so the collectors don't
    pay the credential / client construction cost on every method call. The
    cached client is keyed only by ``self`` (the per-run config), so reuse
    across runs is intentionally NOT done.
    """

    def __init__(self, cfg: AzureConfig) -> None:
        self._cfg = cfg
        self._mgmt_client: Any = None  # SynapseManagementClient | None

    # ------------------------------------------------------------------
    # Lazy client cache
    # ------------------------------------------------------------------

    def _management_client(self) -> Any | None:
        """Return a cached ``SynapseManagementClient`` or ``None`` if SDK missing."""
        if self._mgmt_client is not None:
            return self._mgmt_client
        try:  # pragma: no cover - exercised only when azure-mgmt-synapse is present
            from azure.mgmt.synapse import SynapseManagementClient
        except ImportError:
            return None
        try:
            cred = get_credential(self._cfg)
        except Exception as exc:  # noqa: BLE001 - credential errors are best-effort here
            log.warning("could not build credential for security collectors: %s", exc)
            return None
        self._mgmt_client = SynapseManagementClient(cred, self._cfg.subscription_id)
        return self._mgmt_client

    def fetch_workspace_settings(self) -> WorkspaceSecuritySettings | None:
        if _live_disabled():
            return None
        client = self._management_client()
        if client is None:
            return None
        try:
            ws = client.workspaces.get(self._cfg.resource_group, self._cfg.workspace_name)
        except Exception as exc:  # noqa: BLE001 - SDK can raise AzureError or HttpResponseError
            log.warning("workspaces.get failed: %s", exc)
            return None
        encryption_kind = None
        cmk = getattr(getattr(ws, "encryption", None), "cmk", None)
        if cmk:
            encryption_kind = "Microsoft.KeyVault"
        else:
            encryption_kind = "Microsoft.Synapse"
        return WorkspaceSecuritySettings(
            workspace_name=self._cfg.workspace_name,
            aad_only_authentication=bool(getattr(ws, "azure_ad_only_authentication", False)) or None,
            public_network_access=getattr(ws, "public_network_access", None),
            minimum_tls_version=None,  # Synapse doesn't expose this on the workspace; pool-level only.
            encryption_at_rest=encryption_kind,
            managed_vnet=bool(getattr(getattr(ws, "managed_virtual_network_settings", None),
                                      "preventing_data_exfiltration", False))
            if getattr(ws, "managed_virtual_network", None) else None,
        )

    def list_firewall_rules(self) -> list[FirewallRule]:
        if _live_disabled():
            return []
        client = self._management_client()
        if client is None:
            return []
        rules: list[FirewallRule] = []
        try:
            ws_id = (
                f"/subscriptions/{self._cfg.subscription_id}"
                f"/resourceGroups/{self._cfg.resource_group}"
                f"/providers/Microsoft.Synapse/workspaces/{self._cfg.workspace_name}"
            )
            for fr in client.ip_firewall_rules.list_by_workspace(
                self._cfg.resource_group, self._cfg.workspace_name
            ):
                rules.append(_firewall_to_model(fr, ws_id, "workspace"))
        except Exception as exc:  # noqa: BLE001
            log.warning("ip_firewall_rules.list_by_workspace failed: %s", exc)
        return rules

    def list_credentials_inventory(
        self,
        linked_services: Iterable[dict] | None = None,
    ) -> list[CredentialEntry]:
        """Convert a list of linked-service payloads (already pulled by the
        pipelines module) into a credential-type inventory.

        Pipelines analysis already loads linked services; rather than re-fetch
        them, the CLI / fabric_mapping aggregator can pass their payload here.
        """
        out: list[CredentialEntry] = []
        for ls in linked_services or []:
            kind = _classify_linked_service_credentials(ls)
            out.append(CredentialEntry(
                container="linked_service",
                container_name=str(ls.get("name", "<unnamed>")),
                credential_kind=kind,
                secret_reference=_secret_reference(ls),
                has_inline_secret=_has_inline_secret(ls),
            ))
        return out

    def list_pool_tde_status(self) -> list[PoolTdeStatus]:
        """Return Transparent Data Encryption state for each dedicated SQL pool."""
        if _live_disabled():
            return []
        client = self._management_client()
        if client is None:
            return []
        out: list[PoolTdeStatus] = []
        try:
            pools = list(client.sql_pools.list_by_workspace(
                self._cfg.resource_group, self._cfg.workspace_name
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("sql_pools.list_by_workspace failed: %s", exc)
            return out
        for p in pools:
            pool_name = getattr(p, "name", None) or ""
            pool_id = getattr(p, "id", None) or ""
            try:
                tde = client.sql_pool_transparent_data_encryptions.get(
                    self._cfg.resource_group,
                    self._cfg.workspace_name,
                    pool_name,
                    "current",
                )
                status = getattr(tde, "status", None) or "Unknown"
            except Exception as exc:  # noqa: BLE001
                log.warning("TDE probe %s failed: %s", pool_name, exc)
                status = "Unknown"
            out.append(PoolTdeStatus(
                pool_name=pool_name,
                resource_id=pool_id,
                status=str(status),
            ))
        return out

    def fetch_aad_admins(self) -> list[str]:
        """Return Azure AD admin display names / object ids configured for the workspace."""
        if _live_disabled():
            return []
        client = self._management_client()
        if client is None:
            return []
        out: list[str] = []
        for getter in ("workspace_aad_admins", "workspace_sql_aad_admins"):
            ops = getattr(client, getter, None)
            if ops is None:
                continue
            try:
                admin = ops.get(self._cfg.resource_group, self._cfg.workspace_name)
            except Exception as exc:  # noqa: BLE001
                log.debug("%s.get failed: %s", getter, exc)
                continue
            login = getattr(admin, "login", None)
            sid = getattr(admin, "sid", None)
            if login or sid:
                out.append(str(login or sid))
        # De-duplicate while preserving order.
        seen: set[str] = set()
        uniq: list[str] = []
        for v in out:
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        return uniq

    def iter_linked_service_payloads(self) -> list[dict]:
        """Pull linked-service payloads from the Synapse Artifacts plane.

        Returns a list of dict-shaped payloads compatible with
        ``list_credentials_inventory``. Returns an empty list on missing SDK,
        live-disabled, or RBAC failure — caller records the error.
        """
        if _live_disabled():
            return []
        try:  # pragma: no cover
            from azure.synapse.artifacts import ArtifactsClient
        except ImportError:
            return []
        try:
            cred = get_credential(self._cfg)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not build credential for artifacts client: %s", exc)
            return []
        endpoint = f"https://{self._cfg.workspace_name}.dev.azuresynapse.net"
        client = ArtifactsClient(endpoint=endpoint, credential=cred)
        out: list[dict] = []
        try:
            for ls in client.linked_service.get_linked_services_by_workspace():
                # azure-sdk msrest models implement as_dict / serialize.
                payload = None
                if hasattr(ls, "as_dict"):
                    try:
                        payload = ls.as_dict()
                    except Exception:  # noqa: BLE001
                        payload = None
                if payload is None and hasattr(ls, "serialize"):
                    try:
                        payload = ls.serialize(keep_readonly=True)
                    except Exception:  # noqa: BLE001
                        payload = None
                if payload is None:
                    payload = {"name": getattr(ls, "name", None), "properties": {}}
                out.append(payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_linked_services_by_workspace failed: %s", exc)
        return out


def _firewall_to_model(fr: object, parent_id: str, kind: str) -> FirewallRule:
    name = getattr(fr, "name", "") or ""
    start = getattr(fr, "start_ip_address", None)
    end = getattr(fr, "end_ip_address", None)
    return FirewallRule(
        resource_id=parent_id,
        resource_kind=kind,
        name=name,
        start_ip=start,
        end_ip=end,
        is_allow_all=(start == "0.0.0.0" and end == "255.255.255.255"),
        is_allow_azure_services=(start == "0.0.0.0" and end == "0.0.0.0"),
    )


def _classify_linked_service_credentials(ls: dict) -> str:
    """Best-effort credential-kind classification from an LS payload."""
    props = (ls or {}).get("properties", {}) or {}
    type_props = props.get("typeProperties", {}) or {}
    auth_type = (type_props.get("authenticationType") or "").lower()
    if "managedidentity" in auth_type or props.get("type", "").lower().endswith("managedidentity"):
        return "ManagedIdentity"
    if "serviceprincipal" in auth_type or "servicePrincipalId" in type_props:
        return "ServicePrincipal"
    if "accountkey" in auth_type or any(k in type_props for k in ("accountKey", "azureKey")):
        return "AccountKey"
    if "sasuri" in auth_type or "sasToken" in type_props or "sasUri" in type_props:
        return "SasToken"
    return "Other"


def _secret_reference(ls: dict) -> str | None:
    """Extract Key Vault store reference if present (without reading the secret)."""
    props = (ls or {}).get("properties", {}) or {}
    type_props = props.get("typeProperties", {}) or {}
    for v in type_props.values():
        if isinstance(v, dict) and v.get("type") == "AzureKeyVaultSecret":
            store = (v.get("store") or {}).get("referenceName")
            secret = v.get("secretName")
            if store or secret:
                return f"{store or '?'}::{secret or '?'}"
    return None


# Property names that, if present as a literal string in a linked-service
# typeProperties block, indicate a hard-coded credential rather than a
# Key Vault reference.
_INLINE_SECRET_KEYS: frozenset[str] = frozenset({
    "password",
    "accountkey",
    "secret",
    "secretaccesskey",
    "servicePrincipalKey".lower(),
    "sastoken",
    "sasuri",
})


def _has_inline_secret(ls: dict) -> bool:
    """Return True if any well-known secret-bearing field is a literal string."""
    props = (ls or {}).get("properties", {}) or {}
    type_props = props.get("typeProperties", {}) or {}
    for k, v in type_props.items():
        if k.lower() in _INLINE_SECRET_KEYS and isinstance(v, str) and v:
            return True
        # SecureString / nested {"type": "SecureString", "value": "..."} pattern.
        if isinstance(v, dict) and v.get("type") == "SecureString" and v.get("value"):
            return True
    return False
