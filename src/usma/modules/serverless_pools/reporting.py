"""Reporting for the serverless_pools module."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import ServerlessAnalysis


def write_reports(result: ServerlessAnalysis, out_dir: Path, formats: Iterable[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "serverless_pools.json"))

    if "csv" in fmts:
        for fname, rows in (
            ("serverless_databases.csv", result.databases),
            ("serverless_external_data_sources.csv", result.external_data_sources),
            ("serverless_external_tables.csv", result.external_tables),
            ("serverless_usage.csv", result.usage),
            ("serverless_top_queries.csv", result.top_queries),
            ("serverless_daily_usage.csv", result.daily_usage),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer - Serverless SQL Pool",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Endpoint: `{result.endpoint_fqdn}`",
            f"- Generated: {result.generated_at.isoformat()}",
            f"- Databases: {len(result.databases)}",
            f"- External data sources: {len(result.external_data_sources)}",
            f"- External tables: {len(result.external_tables)}",
            "",
        ]
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        if result.databases:
            lines.append("## Databases")
            lines.append("| Name | Collation | Created |")
            lines.append("|---|---|---|")
            for d in result.databases:
                lines.append(f"| {d.name} | {d.collation or ''} | {d.create_date or ''} |")
            lines.append("")
        if result.external_tables:
            lines.append("## External tables")
            lines.append("| Database | Schema | Table | Data source | Format |")
            lines.append("|---|---|---|---|---|")
            for t in result.external_tables:
                lines.append(f"| {t.database} | {t.schema_name} | {t.table_name} | {t.data_source or ''} | {t.file_format or ''} |")
            lines.append("")
        if result.cost_estimate:
            ce = result.cost_estimate
            lines.append("## Cost estimate (last 30 days)")
            lines.append(f"- Days observed: **{ce.window_days}**")
            lines.append(f"- Data processed: **{ce.total_data_processed_tb} TB**")
            lines.append(f"- List price assumed: **USD {ce.list_price_usd_per_tb} / TB**")
            lines.append(f"- Estimated cost: **USD {ce.estimated_cost_usd}**")
            if ce.notes:
                lines.append(f"- _{ce.notes}_")
            lines.append("")
        if result.daily_usage:
            lines.append("## Daily data processed")
            lines.append("| Day | Requests | Data processed (MB) | Execution time (s) |")
            lines.append("|---|---:|---:|---:|")
            for d in result.daily_usage:
                lines.append(f"| {d.day} | {d.request_count} | {d.data_processed_mb} | {d.duration_seconds} |")
            lines.append("")
        if result.top_queries:
            lines.append("## Top queries by data scanned (last 14d)")
            lines.append("| Start | Login | Status | Duration (s) | Data (MB) |")
            lines.append("|---|---|---|---:|---:|")
            for q in result.top_queries[:50]:
                lines.append(
                    f"| {q.start_time or ''} | {q.login_name or ''} | {q.status or ''} | "
                    f"{q.duration_seconds if q.duration_seconds is not None else ''} | "
                    f"{q.data_processed_mb if q.data_processed_mb is not None else ''} |"
                )
            lines.append("")
        written.append(write_markdown(out_dir / "serverless_pools.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir))

    return written
