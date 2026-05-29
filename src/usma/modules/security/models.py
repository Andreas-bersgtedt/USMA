from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FirewallRule(BaseModel):
    resource_id: str
    resource_kind: str  # "workspace" | "pool" | "storage"
    name: str
    start_ip: str | None = None
    end_ip: str | None = None
    is_allow_all: bool = False  # 0.0.0.0 -> 255.255.255.255
    is_allow_azure_services: bool = False  # 0.0.0.0/0.0.0.0


class WorkspaceSecuritySettings(BaseModel):
    workspace_name: str
    aad_only_authentication: bool | None = None
    public_network_access: str | None = None  # "Enabled" | "Disabled"
    minimum_tls_version: str | None = None  # "1.0" | "1.1" | "1.2"
    encryption_at_rest: str | None = None  # "Microsoft.Synapse" | "Microsoft.KeyVault"
    managed_vnet: bool | None = None
    notes: str | None = None


class CredentialEntry(BaseModel):
    """Credential / secret reference inventory (type-only — values never captured)."""
    container: str  # "linked_service" | "managed_identity" | "key_vault_reference"
    container_name: str
    credential_kind: str  # "ServicePrincipal" | "AccountKey" | "ManagedIdentity" | "SasToken" | "Other"
    secret_reference: str | None = None  # e.g. AKV name + secret name (no value)
    has_inline_secret: bool = False  # True if a password/key appears literally in the payload
    notes: str | None = None


class PoolTdeStatus(BaseModel):
    """Transparent Data Encryption status for a single dedicated SQL pool."""
    pool_name: str
    resource_id: str
    status: str  # "Enabled" | "Disabled" | "Unknown"


class SecurityFinding(BaseModel):
    """High-level risk finding emitted by ``rules.py``."""
    rule_id: str
    severity: str  # "high" | "medium" | "low" | "info"
    resource_id: str | None
    title: str
    detail: str | None = None


class SecurityAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    workspace_settings: WorkspaceSecuritySettings | None = None
    firewall_rules: list[FirewallRule] = Field(default_factory=list)
    credentials: list[CredentialEntry] = Field(default_factory=list)
    pool_tde_status: list[PoolTdeStatus] = Field(default_factory=list)
    aad_admins: list[str] = Field(default_factory=list)  # display names / object ids
    findings: list[SecurityFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
