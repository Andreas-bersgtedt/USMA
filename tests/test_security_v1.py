"""Tests for the security module v1 additions: TDE, AAD admin,
managed-VNet, encryption, inline secret, and HTML round-trip."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from usma.modules.security import rules as _rules
from usma.modules.security.arm_client import _has_inline_secret
from usma.modules.security.html_report import write_html
from usma.modules.security.models import (
    CredentialEntry,
    FirewallRule,
    PoolTdeStatus,
    SecurityAnalysis,
    WorkspaceSecuritySettings,
)


def _ws(**overrides) -> SecurityAnalysis:
    base = SecurityAnalysis(
        workspace_name="ws",
        subscription_id="sub-1",
        resource_group="rg",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        aad_admins=["sec-admins@example.com"],  # avoid no_aad_admin by default
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_no_aad_admin_emits_high_finding() -> None:
    a = _ws(aad_admins=[])
    findings = _rules.evaluate(a)
    matches = [f for f in findings if f.rule_id == "sec.workspace.no_aad_admin"]
    assert len(matches) == 1
    assert matches[0].severity == "high"


def test_aad_admin_present_silent() -> None:
    a = _ws()
    findings = _rules.evaluate(a)
    assert not any(f.rule_id == "sec.workspace.no_aad_admin" for f in findings)


def test_pool_tde_disabled_high_severity() -> None:
    a = _ws(pool_tde_status=[
        PoolTdeStatus(pool_name="dw1", resource_id="/p/dw1", status="Disabled"),
        PoolTdeStatus(pool_name="dw2", resource_id="/p/dw2", status="Enabled"),
    ])
    findings = _rules.evaluate(a)
    matches = [f for f in findings if f.rule_id == "sec.pool.tde_disabled"]
    assert len(matches) == 1
    assert matches[0].severity == "high"
    assert "dw1" in matches[0].title


def test_pool_tde_enabled_silent() -> None:
    a = _ws(pool_tde_status=[
        PoolTdeStatus(pool_name="dw1", resource_id="/p/dw1", status="Enabled"),
    ])
    assert not any(f.rule_id == "sec.pool.tde_disabled" for f in _rules.evaluate(a))


def test_encryption_platform_managed_emits_info() -> None:
    a = _ws(workspace_settings=WorkspaceSecuritySettings(
        workspace_name="ws",
        aad_only_authentication=True,
        public_network_access="Disabled",
        minimum_tls_version="1.2",
        encryption_at_rest="Microsoft.Synapse",
        managed_vnet=True,
    ))
    findings = _rules.evaluate(a)
    matches = [f for f in findings if f.rule_id == "sec.workspace.encryption_platform_managed"]
    assert len(matches) == 1
    assert matches[0].severity == "info"


def test_no_managed_vnet_with_public_access_emits_medium() -> None:
    a = _ws(workspace_settings=WorkspaceSecuritySettings(
        workspace_name="ws",
        aad_only_authentication=True,
        public_network_access="Enabled",
        minimum_tls_version="1.2",
        managed_vnet=False,
    ))
    findings = _rules.evaluate(a)
    matches = [f for f in findings if f.rule_id == "sec.workspace.no_managed_vnet"]
    assert len(matches) == 1
    assert matches[0].severity == "medium"


def test_inline_secret_detection_password_field() -> None:
    ls = {"name": "sql1", "properties": {
        "type": "AzureSqlDatabase",
        "typeProperties": {
            "connectionString": "Server=...",
            "password": "Pa$$w0rd",  # noqa: S106 - test fixture only
        },
    }}
    assert _has_inline_secret(ls) is True


def test_inline_secret_detection_secure_string() -> None:
    ls = {"name": "sql1", "properties": {
        "type": "AzureSqlDatabase",
        "typeProperties": {
            "connectionString": {"type": "SecureString", "value": "Server=...;Pwd=secret"},
        },
    }}
    assert _has_inline_secret(ls) is True


def test_inline_secret_detection_kv_reference_is_false() -> None:
    ls = {"name": "sql1", "properties": {
        "type": "AzureSqlDatabase",
        "typeProperties": {
            "connectionString": "Server=...",
            "password": {
                "type": "AzureKeyVaultSecret",
                "store": {"referenceName": "akv1", "type": "LinkedServiceReference"},
                "secretName": "sql-pwd",
            },
        },
    }}
    assert _has_inline_secret(ls) is False


def test_inline_secret_credential_finding() -> None:
    a = _ws()
    a.credentials.append(CredentialEntry(
        container="linked_service",
        container_name="sql1",
        credential_kind="Other",
        has_inline_secret=True,
    ))
    findings = _rules.evaluate(a)
    matches = [f for f in findings if f.rule_id == "sec.credentials.inline_secret"]
    assert len(matches) == 1
    assert matches[0].severity == "high"


def test_security_html_round_trip(tmp_path: Path) -> None:
    a = _ws(
        workspace_settings=WorkspaceSecuritySettings(
            workspace_name="ws",
            aad_only_authentication=True,
            public_network_access="Disabled",
            minimum_tls_version="1.2",
            encryption_at_rest="Microsoft.KeyVault",
            managed_vnet=True,
        ),
        firewall_rules=[FirewallRule(
            resource_id="ws", resource_kind="workspace", name="open",
            start_ip="0.0.0.0", end_ip="255.255.255.255", is_allow_all=True,
        )],
        pool_tde_status=[PoolTdeStatus(
            pool_name="dw1", resource_id="/p/dw1", status="Enabled",
        )],
        credentials=[CredentialEntry(
            container="linked_service", container_name="ls1",
            credential_kind="ManagedIdentity",
        )],
    )
    a.findings = _rules.evaluate(a)
    out = write_html(a, tmp_path / "security.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Security" in text
    assert "allow all" in text
    assert "dw1" in text
    assert "ls1" in text
    assert "sec.firewall.allow_all" in text
