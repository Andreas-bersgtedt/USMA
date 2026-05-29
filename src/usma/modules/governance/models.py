from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RoleAssignment(BaseModel):
    """A single Azure RBAC role assignment, normalized across scopes."""
    scope: str  # Full ARM scope (subscription/RG/resource).
    scope_kind: str  # "subscription" | "resource_group" | "workspace" | "pool" | "storage" | "other"
    role_name: str
    role_definition_id: str
    principal_id: str
    principal_type: str | None = None  # User | Group | ServicePrincipal | ManagedIdentity
    principal_display_name: str | None = None
    assignment_id: str
    plane: str = "control"  # "control" or "data"


class ManagedPrivateEndpoint(BaseModel):
    name: str
    target_resource_id: str | None = None
    target_resource_type: str | None = None
    group_id: str | None = None  # e.g. "blob", "dfs", "sql"
    provisioning_state: str | None = None
    connection_state: str | None = None  # Approved | Pending | Rejected | Disconnected
    fqdns: list[str] = Field(default_factory=list)


class CustomerManagedKey(BaseModel):
    """Workspace-level CMK config, when configured."""
    resource_id: str
    resource_kind: str  # "workspace" | "storage" | "pool"
    enabled: bool = False
    key_vault_uri: str | None = None
    key_name: str | None = None
    key_version: str | None = None
    user_assigned_identity_id: str | None = None
    notes: str | None = None


class PurviewLineageEdge(BaseModel):
    """A single lineage edge captured from a Purview account, if configured."""
    source_qualified_name: str
    source_type: str
    target_qualified_name: str
    target_type: str
    process_qualified_name: str | None = None
    process_type: str | None = None
    captured_at: datetime | None = None


class GovernanceFinding(BaseModel):
    """High-level governance risk emitted by ``rules.py``."""
    rule_id: str
    severity: str  # "high" | "medium" | "low" | "info"
    resource_id: str | None = None
    title: str
    detail: str | None = None


class GovernanceAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    role_assignments: list[RoleAssignment] = Field(default_factory=list)
    managed_private_endpoints: list[ManagedPrivateEndpoint] = Field(default_factory=list)
    customer_managed_keys: list[CustomerManagedKey] = Field(default_factory=list)
    purview_account: str | None = None
    purview_lineage: list[PurviewLineageEdge] = Field(default_factory=list)
    findings: list[GovernanceFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
