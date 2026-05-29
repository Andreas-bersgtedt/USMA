from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import CostAnalysis


class _Row(BaseModel):
    month: str
    resource_kind: str
    resource_name: str | None
    sku: str | None
    cost: float
    currency: str
    usage_quantity: float
    usage_unit: str | None


class _FindingRow(BaseModel):
    rule_id: str
    severity: str
    title: str
    detail: str | None
    resource: str | None


class _KindRow(BaseModel):
    resource_kind: str
    cost: float


def _severity_order(sev: str) -> int:
    return {"high": 0, "medium": 1, "low": 2, "info": 3}.get(sev, 4)


def write_reports(
    result: CostAnalysis,
    out_dir: Path,
    formats: Iterable[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "cost.json"))

    if "csv" in fmts:
        rows = [
            _Row(
                month=r.month,
                resource_kind=r.resource_kind,
                resource_name=r.resource_name,
                sku=r.sku,
                cost=r.cost,
                currency=r.currency,
                usage_quantity=r.usage_quantity,
                usage_unit=r.usage_unit,
            )
            for r in result.rows
        ]
        p = write_csv_rows(out_dir / "cost_rows.csv", rows)
        if p:
            written.append(p)

        kind_rows = [
            _KindRow(resource_kind=k, cost=c)
            for k, c in sorted(result.by_resource_kind.items(), key=lambda kv: -kv[1])
        ]
        p = write_csv_rows(out_dir / "cost_by_resource_kind.csv", kind_rows)
        if p:
            written.append(p)

        f_rows = [
            _FindingRow(
                rule_id=f.rule_id, severity=f.severity, title=f.title,
                detail=f.detail, resource=f.resource,
            )
            for f in sorted(result.findings, key=lambda x: _severity_order(x.severity))
        ]
        p = write_csv_rows(out_dir / "cost_findings.csv", f_rows)
        if p:
            written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer - Cost",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Window: `{result.window_start.date().isoformat()}` -> `{result.window_end.date().isoformat()}`",
            f"- Rows captured: {len(result.rows)}",
            f"- Findings: {len(result.findings)}",
            "",
        ]
        if result.findings:
            lines.append("## Findings")
            lines.append("| Severity | Rule | Title |")
            lines.append("|---|---|---|")
            for f in sorted(result.findings, key=lambda x: _severity_order(x.severity)):
                lines.append(f"| {f.severity} | `{f.rule_id}` | {f.title} |")
            lines.append("")
        if result.monthly_totals:
            lines.append("## Monthly totals")
            lines.append("| Month | Cost |")
            lines.append("|---|---:|")
            for month in sorted(result.monthly_totals):
                lines.append(f"| {month} | {result.monthly_totals[month]:,.2f} |")
            lines.append("")
        if result.by_resource_kind:
            lines.append("## By resource kind")
            lines.append("| Kind | Cost |")
            lines.append("|---|---:|")
            for k in sorted(result.by_resource_kind):
                lines.append(f"| {k} | {result.by_resource_kind[k]:,.2f} |")
            lines.append("")
        fc = result.fabric_comparison
        if fc:
            lines.append("## Fabric capacity comparison")
            lines.append(f"- Synapse latest monthly cost: **{fc.synapse_avg_monthly_cost:,.2f}**")
            lines.append(f"- Fabric SKU: `{fc.fabric_capacity_sku or 'unknown'}`")
            if fc.fabric_estimated_monthly_cost is not None:
                lines.append(f"- Fabric estimated monthly cost: **{fc.fabric_estimated_monthly_cost:,.2f}**")
            if fc.delta_abs is not None:
                pct = f" ({fc.delta_pct:+.1%})" if fc.delta_pct is not None else ""
                lines.append(f"- Delta: **{fc.delta_abs:+,.2f}**{pct}")
            if fc.notes:
                lines.append(f"- _{fc.notes}_")
            lines.append("")
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        written.append(write_markdown(out_dir / "cost.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir / "cost.html"))

    return written
