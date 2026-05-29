from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import GovernanceAnalysis


class _RoleRow(BaseModel):
    scope_kind: str
    scope: str
    role_name: str
    principal_type: str | None
    principal_id: str
    plane: str


class _MpeRow(BaseModel):
    name: str
    target_resource_id: str | None
    group_id: str | None
    provisioning_state: str | None
    connection_state: str | None


class _CmkRow(BaseModel):
    resource_kind: str
    resource_id: str
    enabled: bool
    key_vault_uri: str | None
    key_name: str | None


class _FindingRow(BaseModel):
    rule_id: str
    severity: str
    resource_id: str | None
    title: str
    detail: str | None


def write_reports(
    result: GovernanceAnalysis,
    out_dir: Path,
    formats: Iterable[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "governance.json"))

    if "csv" in fmts:
        rbac_rows = [
            _RoleRow(
                scope_kind=r.scope_kind,
                scope=r.scope,
                role_name=r.role_name,
                principal_type=r.principal_type,
                principal_id=r.principal_id,
                plane=r.plane,
            )
            for r in result.role_assignments
        ]
        p = write_csv_rows(out_dir / "governance_role_assignments.csv", rbac_rows)
        if p:
            written.append(p)

        mpe_rows = [
            _MpeRow(
                name=m.name,
                target_resource_id=m.target_resource_id,
                group_id=m.group_id,
                provisioning_state=m.provisioning_state,
                connection_state=m.connection_state,
            )
            for m in result.managed_private_endpoints
        ]
        p = write_csv_rows(out_dir / "governance_managed_private_endpoints.csv", mpe_rows)
        if p:
            written.append(p)

        cmk_rows = [
            _CmkRow(
                resource_kind=c.resource_kind,
                resource_id=c.resource_id,
                enabled=c.enabled,
                key_vault_uri=c.key_vault_uri,
                key_name=c.key_name,
            )
            for c in result.customer_managed_keys
        ]
        p = write_csv_rows(out_dir / "governance_customer_managed_keys.csv", cmk_rows)
        if p:
            written.append(p)

        finding_rows = [
            _FindingRow(
                rule_id=f.rule_id,
                severity=f.severity,
                resource_id=f.resource_id,
                title=f.title,
                detail=f.detail,
            )
            for f in result.findings
        ]
        p = write_csv_rows(out_dir / "governance_findings.csv", finding_rows)
        if p:
            written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer - Governance",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Resource group: `{result.resource_group}`",
            f"- Role assignments: {len(result.role_assignments)}",
            f"- Managed private endpoints: {len(result.managed_private_endpoints)}",
            f"- Customer-managed keys: {len(result.customer_managed_keys)}",
            f"- Purview account: `{result.purview_account or '(not configured)'}`",
            f"- Purview lineage edges: {len(result.purview_lineage)}",
            f"- Findings: {len(result.findings)}",
            "",
        ]
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")

        if result.findings:
            sev_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
            lines.append("## Findings")
            lines.append("| Severity | Rule | Title |")
            lines.append("|---|---|---|")
            for f in sorted(
                result.findings,
                key=lambda x: (sev_order.get(x.severity, 99), x.rule_id),
            ):
                lines.append(f"| {f.severity} | `{f.rule_id}` | {f.title} |")
            lines.append("")

        if result.role_assignments:
            lines.append("## Role assignments")
            lines.append("| Scope kind | Role | Principal type | Principal id |")
            lines.append("|---|---|---|---|")
            for r in result.role_assignments:
                lines.append(
                    f"| {r.scope_kind} | {r.role_name} | "
                    f"{r.principal_type or ''} | `{r.principal_id}` |"
                )
            lines.append("")

        if result.managed_private_endpoints:
            lines.append("## Managed private endpoints")
            lines.append("| Name | Group | Provisioning | Connection |")
            lines.append("|---|---|---|---|")
            for m in result.managed_private_endpoints:
                lines.append(
                    f"| {m.name} | {m.group_id or ''} | "
                    f"{m.provisioning_state or ''} | {m.connection_state or ''} |"
                )
            lines.append("")

        if result.customer_managed_keys:
            lines.append("## Customer-managed keys")
            lines.append("| Resource | Enabled | Key vault | Key name |")
            lines.append("|---|---|---|---|")
            for c in result.customer_managed_keys:
                lines.append(
                    f"| {c.resource_kind} | {c.enabled} | "
                    f"{c.key_vault_uri or ''} | {c.key_name or ''} |"
                )
            lines.append("")

        written.append(write_markdown(out_dir / "governance.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir / "governance.html"))

    return written
