"""Unit tests for the security module's pure-Python rules + LS classifier."""
from __future__ import annotations

from datetime import datetime, timezone

from usma.modules.security import rules as _rules
from usma.modules.security.arm_client import (
    _classify_linked_service_credentials,
    _firewall_to_model,
    _secret_reference,
)
from usma.modules.security.models import (
    CredentialEntry,
    FirewallRule,
    SecurityAnalysis,
    WorkspaceSecuritySettings,
)


def _empty_analysis() -> SecurityAnalysis:
    return SecurityAnalysis(
        workspace_name="ws",
        subscription_id="sub",
        resource_group="rg",
        generated_at=datetime.now(timezone.utc),
    )


def test_firewall_allow_all_emits_high_finding() -> None:
    a = _empty_analysis()
    a.firewall_rules.append(FirewallRule(
        resource_id="ws", resource_kind="workspace", name="open",
        start_ip="0.0.0.0", end_ip="255.255.255.255", is_allow_all=True,
    ))
    findings = _rules.evaluate(a)
    assert any(f.rule_id == "sec.firewall.allow_all" and f.severity == "high" for f in findings)


def test_firewall_allow_azure_services_emits_medium_finding() -> None:
    a = _empty_analysis()
    a.firewall_rules.append(FirewallRule(
        resource_id="ws", resource_kind="workspace", name="azure",
        start_ip="0.0.0.0", end_ip="0.0.0.0", is_allow_azure_services=True,
    ))
    findings = _rules.evaluate(a)
    assert any(f.rule_id == "sec.firewall.allow_azure" and f.severity == "medium" for f in findings)


def test_workspace_sql_auth_findings() -> None:
    a = _empty_analysis()
    a.workspace_settings = WorkspaceSecuritySettings(
        workspace_name="ws",
        aad_only_authentication=False,
        public_network_access="Enabled",
        minimum_tls_version="1.0",
    )
    findings = _rules.evaluate(a)
    rule_ids = {f.rule_id for f in findings}
    assert "sec.workspace.sql_auth_enabled" in rule_ids
    assert "sec.workspace.public_network_access" in rule_ids
    assert "sec.workspace.tls_below_12" in rule_ids


def test_workspace_clean_emits_no_findings() -> None:
    a = _empty_analysis()
    a.workspace_settings = WorkspaceSecuritySettings(
        workspace_name="ws",
        aad_only_authentication=True,
        public_network_access="Disabled",
        minimum_tls_version="1.2",
        encryption_at_rest="Microsoft.KeyVault",
        managed_vnet=True,
    )
    a.aad_admins = ["entra-admin-group"]
    findings = _rules.evaluate(a)
    assert findings == []


def test_credential_account_key_finding() -> None:
    a = _empty_analysis()
    a.credentials.append(CredentialEntry(
        container="linked_service",
        container_name="adlsls",
        credential_kind="AccountKey",
    ))
    findings = _rules.evaluate(a)
    assert any(f.rule_id == "sec.credentials.account_key" for f in findings)


def test_classify_linked_service_managed_identity() -> None:
    ls = {"name": "ls", "properties": {"type": "AzureBlobFSManagedIdentity",
                                       "typeProperties": {"authenticationType": "ManagedIdentity"}}}
    assert _classify_linked_service_credentials(ls) == "ManagedIdentity"


def test_classify_linked_service_account_key() -> None:
    ls = {"name": "ls", "properties": {"type": "AzureBlobStorage",
                                       "typeProperties": {"accountKey": {"value": "***"}}}}
    assert _classify_linked_service_credentials(ls) == "AccountKey"


def test_secret_reference_extracts_kv_pointer() -> None:
    ls = {"name": "ls", "properties": {"typeProperties": {
        "password": {"type": "AzureKeyVaultSecret",
                     "store": {"referenceName": "kv1"},
                     "secretName": "db-pwd"}}}}
    assert _secret_reference(ls) == "kv1::db-pwd"


def test_firewall_to_model_marks_allow_all() -> None:
    class _Fr:
        name = "all"
        start_ip_address = "0.0.0.0"
        end_ip_address = "255.255.255.255"
    fr = _firewall_to_model(_Fr(), "ws", "workspace")
    assert fr.is_allow_all is True
    assert fr.is_allow_azure_services is False
