"""Pure-Python governance findings rules.

Runs over the data already in ``GovernanceAnalysis`` (role assignments,
managed private endpoints, CMK config, Purview presence) — no Azure calls.
Unit-testable on hand-built models.
"""
from __future__ import annotations

from .models import (
    CustomerManagedKey,
    GovernanceAnalysis,
    GovernanceFinding,
    ManagedPrivateEndpoint,
    RoleAssignment,
)

# Privileged roles whose name (post role-definition resolution) is treated as
# elevated. Matched case-insensitively against ``RoleAssignment.role_name``.
_PRIVILEGED_ROLES: frozenset[str] = frozenset({
    "owner",
    "contributor",
    "user access administrator",
    "role based access control administrator",
})


def evaluate(result: GovernanceAnalysis) -> list[GovernanceFinding]:
    """Produce findings from the populated analysis (no live calls)."""
    findings: list[GovernanceFinding] = []
    findings.extend(_rbac_findings(result.role_assignments))
    findings.extend(_mpe_findings(result.managed_private_endpoints))
    findings.extend(_cmk_findings(result.customer_managed_keys))
    findings.extend(_purview_findings(result))
    return findings


def _rbac_findings(role_assignments: list[RoleAssignment]) -> list[GovernanceFinding]:
    out: list[GovernanceFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for ra in role_assignments:
        rn = (ra.role_name or "").strip().lower()
        is_privileged = rn in _PRIVILEGED_ROLES
        # Subscription-scope privileged role -> high.
        if is_privileged and ra.scope_kind == "subscription":
            key = ("sub_priv", ra.principal_id, rn)
            if key not in seen:
                seen.add(key)
                out.append(GovernanceFinding(
                    rule_id="gov.rbac.subscription_privileged",
                    severity="high",
                    resource_id=ra.scope,
                    title=f"Privileged role '{ra.role_name}' at subscription scope",
                    detail=(
                        f"Principal {ra.principal_id} ({ra.principal_type or 'unknown'}) "
                        "has a tenant-wide elevated role. Scope down to the resource "
                        "group or workspace where possible."
                    ),
                ))
        # Workspace-scope privileged role -> medium.
        elif is_privileged and ra.scope_kind == "workspace":
            key = ("ws_priv", ra.principal_id, rn)
            if key not in seen:
                seen.add(key)
                out.append(GovernanceFinding(
                    rule_id="gov.rbac.workspace_privileged",
                    severity="medium",
                    resource_id=ra.scope,
                    title=f"Privileged role '{ra.role_name}' on the Synapse workspace",
                    detail=(
                        f"Principal {ra.principal_id} has '{ra.role_name}' on the "
                        "workspace. Prefer Synapse-specific data-plane roles "
                        "(Synapse Administrator / Contributor / SQL Administrator)."
                    ),
                ))
    return out


def _mpe_findings(endpoints: list[ManagedPrivateEndpoint]) -> list[GovernanceFinding]:
    out: list[GovernanceFinding] = []
    for m in endpoints:
        state = (m.connection_state or "").strip().lower()
        if state and state != "approved":
            out.append(GovernanceFinding(
                rule_id="gov.mpe.not_approved",
                severity="medium",
                resource_id=m.target_resource_id,
                title=f"Managed private endpoint '{m.name}' is {m.connection_state}",
                detail=(
                    "Endpoint will not carry traffic until it is approved on the "
                    "target resource. Approve or remove."
                ),
            ))
        prov = (m.provisioning_state or "").strip().lower()
        if prov in {"failed", "canceled"}:
            out.append(GovernanceFinding(
                rule_id="gov.mpe.provisioning_failed",
                severity="medium",
                resource_id=m.target_resource_id,
                title=f"Managed private endpoint '{m.name}' provisioning {m.provisioning_state}",
                detail="Re-create the endpoint or remove if no longer needed.",
            ))
    return out


def _cmk_findings(keys: list[CustomerManagedKey]) -> list[GovernanceFinding]:
    out: list[GovernanceFinding] = []
    if not keys:
        out.append(GovernanceFinding(
            rule_id="gov.cmk.not_configured",
            severity="info",
            resource_id=None,
            title="Customer-managed keys not configured",
            detail=(
                "Workspace uses platform-managed keys. Enable CMK if your "
                "compliance baseline requires customer-controlled key material."
            ),
        ))
        return out
    for k in keys:
        if not k.enabled:
            out.append(GovernanceFinding(
                rule_id="gov.cmk.disabled",
                severity="medium",
                resource_id=k.resource_id,
                title=f"CMK present but disabled on {k.resource_kind}",
                detail="The encryption block is configured but disabled. Enable or remove.",
            ))
    return out


def _purview_findings(result: GovernanceAnalysis) -> list[GovernanceFinding]:
    if not result.purview_account:
        return [GovernanceFinding(
            rule_id="gov.purview.not_configured",
            severity="info",
            resource_id=None,
            title="Microsoft Purview account not provided",
            detail=(
                "Set SMA_PURVIEW_ACCOUNT to the Purview account name to capture "
                "lineage edges for the workspace; otherwise lineage is skipped."
            ),
        )]
    return []
