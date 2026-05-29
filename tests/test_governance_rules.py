"""Unit tests for the governance rules engine and HTML report writer."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from usma.modules.governance.html_report import write_html
from usma.modules.governance.models import (
    CustomerManagedKey,
    GovernanceAnalysis,
    ManagedPrivateEndpoint,
    RoleAssignment,
)
from usma.modules.governance.rules import evaluate


def _empty_analysis(**overrides) -> GovernanceAnalysis:
    base = dict(
        workspace_name="ws",
        subscription_id="sub-1",
        resource_group="rg",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        purview_account="purview1",  # default avoids the not-configured info finding
    )
    base.update(overrides)
    return GovernanceAnalysis(**base)


def _ra(role: str, scope_kind: str, principal: str = "p1") -> RoleAssignment:
    return RoleAssignment(
        scope=f"/scope/{scope_kind}",
        scope_kind=scope_kind,
        role_name=role,
        role_definition_id=f"/providers/Microsoft.Authorization/roleDefinitions/{role}",
        principal_id=principal,
        principal_type="ServicePrincipal",
        assignment_id=f"/ra/{role}/{principal}",
    )


def test_subscription_owner_is_high_severity() -> None:
    a = _empty_analysis(role_assignments=[_ra("Owner", "subscription")])
    findings = evaluate(a)
    ids = {f.rule_id for f in findings}
    assert "gov.rbac.subscription_privileged" in ids
    assert any(f.severity == "high" for f in findings
               if f.rule_id == "gov.rbac.subscription_privileged")


def test_workspace_contributor_is_medium_severity() -> None:
    a = _empty_analysis(role_assignments=[_ra("Contributor", "workspace")])
    findings = evaluate(a)
    matches = [f for f in findings if f.rule_id == "gov.rbac.workspace_privileged"]
    assert len(matches) == 1
    assert matches[0].severity == "medium"


def test_reader_role_is_not_flagged() -> None:
    a = _empty_analysis(role_assignments=[_ra("Reader", "subscription")])
    findings = evaluate(a)
    assert not any(f.rule_id.startswith("gov.rbac.") for f in findings)


def test_rbac_dedupes_same_principal_and_role() -> None:
    a = _empty_analysis(role_assignments=[
        _ra("Owner", "subscription", "p1"),
        _ra("Owner", "subscription", "p1"),  # duplicate
    ])
    findings = [f for f in evaluate(a) if f.rule_id == "gov.rbac.subscription_privileged"]
    assert len(findings) == 1


def test_mpe_pending_flagged_medium() -> None:
    a = _empty_analysis(managed_private_endpoints=[
        ManagedPrivateEndpoint(name="pe1", connection_state="Pending"),
    ])
    findings = [f for f in evaluate(a) if f.rule_id == "gov.mpe.not_approved"]
    assert len(findings) == 1
    assert findings[0].severity == "medium"


def test_mpe_approved_not_flagged() -> None:
    a = _empty_analysis(managed_private_endpoints=[
        ManagedPrivateEndpoint(name="pe1", connection_state="Approved"),
    ])
    assert not any(f.rule_id.startswith("gov.mpe") for f in evaluate(a))


def test_cmk_not_configured_emits_info() -> None:
    a = _empty_analysis()
    findings = [f for f in evaluate(a) if f.rule_id == "gov.cmk.not_configured"]
    assert len(findings) == 1
    assert findings[0].severity == "info"


def test_cmk_disabled_emits_medium() -> None:
    a = _empty_analysis(customer_managed_keys=[CustomerManagedKey(
        resource_id="/ws", resource_kind="workspace", enabled=False,
    )])
    findings = [f for f in evaluate(a) if f.rule_id == "gov.cmk.disabled"]
    assert len(findings) == 1
    assert findings[0].severity == "medium"


def test_cmk_enabled_silent() -> None:
    a = _empty_analysis(customer_managed_keys=[CustomerManagedKey(
        resource_id="/ws", resource_kind="workspace", enabled=True,
    )])
    assert not any(f.rule_id.startswith("gov.cmk") for f in evaluate(a))


def test_purview_not_configured_when_missing() -> None:
    a = _empty_analysis(purview_account=None)
    findings = [f for f in evaluate(a) if f.rule_id == "gov.purview.not_configured"]
    assert len(findings) == 1


def test_html_report_round_trip(tmp_path: Path) -> None:
    a = _empty_analysis(
        role_assignments=[_ra("Owner", "subscription")],
        managed_private_endpoints=[ManagedPrivateEndpoint(
            name="pe1", connection_state="Approved", group_id="blob",
        )],
        customer_managed_keys=[CustomerManagedKey(
            resource_id="/ws", resource_kind="workspace", enabled=True,
        )],
    )
    a.findings = evaluate(a)
    out = write_html(a, tmp_path / "governance.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Governance" in text
    assert "Owner" in text
    assert "gov.rbac.subscription_privileged" in text
    assert "Approved" in text
