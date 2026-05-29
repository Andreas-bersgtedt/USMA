from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import SecurityAnalysis


class _FirewallRow(BaseModel):
    resource_kind: str
    name: str
    start_ip: str | None
    end_ip: str | None
    is_allow_all: bool
    is_allow_azure_services: bool


class _CredRow(BaseModel):
    container: str
    container_name: str
    credential_kind: str
    secret_reference: str | None
    has_inline_secret: bool


class _PoolTdeRow(BaseModel):
    pool_name: str
    status: str


class _FindingRow(BaseModel):
    rule_id: str
    severity: str
    title: str
    detail: str | None


def write_reports(
    result: SecurityAnalysis,
    out_dir: Path,
    formats: Iterable[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "security.json"))

    if "csv" in fmts:
        fw_rows = [
            _FirewallRow(
                resource_kind=r.resource_kind,
                name=r.name,
                start_ip=r.start_ip,
                end_ip=r.end_ip,
                is_allow_all=r.is_allow_all,
                is_allow_azure_services=r.is_allow_azure_services,
            )
            for r in result.firewall_rules
        ]
        p = write_csv_rows(out_dir / "security_firewall_rules.csv", fw_rows)
        if p:
            written.append(p)

        cred_rows = [
            _CredRow(
                container=c.container,
                container_name=c.container_name,
                credential_kind=c.credential_kind,
                secret_reference=c.secret_reference,
                has_inline_secret=c.has_inline_secret,
            )
            for c in result.credentials
        ]
        p = write_csv_rows(out_dir / "security_credentials.csv", cred_rows)
        if p:
            written.append(p)

        tde_rows = [
            _PoolTdeRow(pool_name=t.pool_name, status=t.status)
            for t in result.pool_tde_status
        ]
        p = write_csv_rows(out_dir / "security_pool_tde.csv", tde_rows)
        if p:
            written.append(p)

        f_rows = [
            _FindingRow(
                rule_id=f.rule_id,
                severity=f.severity,
                title=f.title,
                detail=f.detail,
            )
            for f in result.findings
        ]
        p = write_csv_rows(out_dir / "security_findings.csv", f_rows)
        if p:
            written.append(p)

    if "markdown" in fmts:
        ws = result.workspace_settings
        lines = [
            "# Unified Solution Migration Analyzer - Security",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Firewall rules: {len(result.firewall_rules)}",
            f"- Credentials inventoried: {len(result.credentials)}",
            f"- AAD admins: {len(result.aad_admins)}",
            f"- Pool TDE checks: {len(result.pool_tde_status)}",
            f"- Findings: {len(result.findings)}",
            "",
        ]
        if ws:
            lines.extend([
                "## Workspace settings",
                f"- AAD-only authentication: `{ws.aad_only_authentication}`",
                f"- Public network access: `{ws.public_network_access}`",
                f"- Minimum TLS version: `{ws.minimum_tls_version}`",
                f"- Encryption at rest: `{ws.encryption_at_rest}`",
                f"- Managed VNet: `{ws.managed_vnet}`",
                "",
            ])
        if result.aad_admins:
            lines.append("## AAD administrators")
            for a in result.aad_admins:
                lines.append(f"- `{a}`")
            lines.append("")
        if result.pool_tde_status:
            lines.append("## Dedicated pool TDE")
            lines.append("| Pool | Status |")
            lines.append("|---|---|")
            for t in result.pool_tde_status:
                lines.append(f"| `{t.pool_name}` | {t.status} |")
            lines.append("")
        if result.findings:
            lines.append("## Findings")
            lines.append("| Severity | Rule | Title |")
            lines.append("|---|---|---|")
            for f in sorted(result.findings, key=lambda x: _severity_order(x.severity)):
                lines.append(f"| {f.severity} | `{f.rule_id}` | {f.title} |")
            lines.append("")
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        written.append(write_markdown(out_dir / "security.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir / "security.html"))

    return written


def _severity_order(sev: str) -> int:
    return {"high": 0, "medium": 1, "low": 2, "info": 3}.get(sev, 4)
