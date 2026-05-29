"""Pure-Python security findings rules.

These run over the data already in ``SecurityAnalysis`` (firewall rules,
workspace settings, credentials inventory) — no Azure calls. They are
unit-testable on a hand-built model.
"""
from __future__ import annotations

from .models import (
    CredentialEntry,
    FirewallRule,
    PoolTdeStatus,
    SecurityAnalysis,
    SecurityFinding,
    WorkspaceSecuritySettings,
)


def evaluate(result: SecurityAnalysis) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    findings.extend(_firewall_findings(result.firewall_rules))
    if result.workspace_settings:
        findings.extend(_workspace_findings(result.workspace_settings))
    findings.extend(_aad_admin_findings(result))
    findings.extend(_pool_tde_findings(result.pool_tde_status))
    findings.extend(_credential_findings(result.credentials))
    return findings


def _firewall_findings(rules: list[FirewallRule]) -> list[SecurityFinding]:
    out: list[SecurityFinding] = []
    for r in rules:
        if r.is_allow_all:
            out.append(SecurityFinding(
                rule_id="sec.firewall.allow_all",
                severity="high",
                resource_id=r.resource_id,
                title=f"Firewall rule '{r.name}' allows all IPv4 traffic",
                detail="Range 0.0.0.0 -> 255.255.255.255 disables IP filtering. "
                       "Replace with named ranges or rely on a Managed VNet + Private Endpoints.",
            ))
        elif r.start_ip == "0.0.0.0" and r.end_ip == "0.0.0.0":
            # Synapse-style "allow Azure services" rule.
            out.append(SecurityFinding(
                rule_id="sec.firewall.allow_azure",
                severity="medium",
                resource_id=r.resource_id,
                title=f"Firewall rule '{r.name}' allows Azure services",
                detail="The 0.0.0.0/0.0.0.0 rule grants access to *all* Azure tenants, "
                       "not just yours. Prefer Private Endpoints + a Managed VNet.",
            ))
    return out


def _workspace_findings(s: WorkspaceSecuritySettings) -> list[SecurityFinding]:
    out: list[SecurityFinding] = []
    if s.aad_only_authentication is False:
        out.append(SecurityFinding(
            rule_id="sec.workspace.sql_auth_enabled",
            severity="high",
            resource_id=None,
            title=f"Workspace '{s.workspace_name}' allows SQL authentication",
            detail="Set 'Microsoft Entra-only authentication' to true to disable "
                   "username/password logins.",
        ))
    if s.public_network_access == "Enabled":
        out.append(SecurityFinding(
            rule_id="sec.workspace.public_network_access",
            severity="medium",
            resource_id=None,
            title="Public network access is enabled",
            detail="Disable public network access and front the workspace via "
                   "Private Endpoints in a Managed VNet.",
        ))
    if s.minimum_tls_version and s.minimum_tls_version < "1.2":
        out.append(SecurityFinding(
            rule_id="sec.workspace.tls_below_12",
            severity="high",
            resource_id=None,
            title=f"Minimum TLS version is {s.minimum_tls_version}",
            detail="Raise minimum TLS to 1.2 (current Microsoft baseline).",
        ))
    if s.encryption_at_rest == "Microsoft.Synapse":
        out.append(SecurityFinding(
            rule_id="sec.workspace.encryption_platform_managed",
            severity="info",
            resource_id=None,
            title="Workspace uses platform-managed encryption keys",
            detail="Customer-managed keys (CMK) are not configured. Enable CMK if "
                   "your compliance baseline requires customer-controlled key material.",
        ))
    if s.public_network_access == "Enabled" and s.managed_vnet is not True:
        out.append(SecurityFinding(
            rule_id="sec.workspace.no_managed_vnet",
            severity="medium",
            resource_id=None,
            title="Public network access enabled and managed VNet is not configured",
            detail="Pair public network access with a Managed VNet + Private Endpoints, "
                   "or disable public access entirely.",
        ))
    return out


def _aad_admin_findings(result: SecurityAnalysis) -> list[SecurityFinding]:
    if result.aad_admins:
        return []
    return [SecurityFinding(
        rule_id="sec.workspace.no_aad_admin",
        severity="high",
        resource_id=None,
        title="No Microsoft Entra (AAD) workspace administrator configured",
        detail="Configure an Entra group as the SQL administrator so access can be "
               "managed via group membership and audited centrally.",
    )]


def _pool_tde_findings(pools: list[PoolTdeStatus]) -> list[SecurityFinding]:
    out: list[SecurityFinding] = []
    for p in pools:
        status = (p.status or "").strip().lower()
        if status and status != "enabled":
            out.append(SecurityFinding(
                rule_id="sec.pool.tde_disabled",
                severity="high",
                resource_id=p.resource_id,
                title=f"Transparent Data Encryption {p.status} on dedicated pool '{p.pool_name}'",
                detail="TDE should be Enabled on every dedicated SQL pool.",
            ))
    return out


def _credential_findings(creds: list[CredentialEntry]) -> list[SecurityFinding]:
    out: list[SecurityFinding] = []
    for c in creds:
        if c.has_inline_secret:
            out.append(SecurityFinding(
                rule_id="sec.credentials.inline_secret",
                severity="high",
                resource_id=None,
                title=f"Linked service '{c.container_name}' has an inline secret",
                detail="A password / account-key / SAS token appears to be stored "
                       "literally in the linked-service definition. Move it to "
                       "Azure Key Vault and reference it via AzureKeyVaultSecret.",
            ))
        if c.credential_kind == "AccountKey":
            out.append(SecurityFinding(
                rule_id="sec.credentials.account_key",
                severity="medium",
                resource_id=None,
                title=f"Linked service '{c.container_name}' uses an Account Key",
                detail="Account-key auth is long-lived and broadly scoped. Replace "
                       "with Managed Identity or Service Principal where supported.",
            ))
        if c.credential_kind == "SasToken":
            out.append(SecurityFinding(
                rule_id="sec.credentials.sas_token",
                severity="low",
                resource_id=None,
                title=f"Linked service '{c.container_name}' uses a SAS token",
                detail="Verify expiry and scope; prefer Managed Identity for "
                       "Azure-internal data movement.",
            ))
    return out
