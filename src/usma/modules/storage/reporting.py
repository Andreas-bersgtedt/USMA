"""Report writers for the storage module."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import StorageAnalysis


def _fmt_bytes(b: int | None) -> str:
    if b is None:
        return ""
    if b >= 1024 ** 4:
        return f"{b / (1024 ** 4):.2f} TB"
    if b >= 1024 ** 3:
        return f"{b / (1024 ** 3):.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / (1024 ** 2):.2f} MB"
    return f"{b} B"


def write_reports(result: StorageAnalysis, out_dir: Path, formats: Iterable[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "storage.json"))

    if "csv" in fmts:
        for fname, rows in (
            ("storage_accounts.csv", result.accounts),
            ("storage_capacity.csv", result.capacities),
            ("dedicated_pool_storage.csv", result.dedicated_pool_storage),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer - Storage",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Generated: {result.generated_at.isoformat()}",
            f"- Storage accounts (subscription): {len(result.accounts)}",
            f"- Capacities sampled: {len(result.capacities)}",
            f"- Dedicated SQL pools sized: {len(result.dedicated_pool_storage)}",
            "",
        ]
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")

        if result.dedicated_pool_storage:
            lines.append("## Dedicated SQL pool storage")
            lines.append(
                "| Pool | Tables | Rows | Reserved (MB) | Reserved (GB) | "
                "Data (GB) | Index (GB) | Max (GB) | % of max |"
            )
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
            for p in result.dedicated_pool_storage:
                lines.append(
                    f"| {p.pool_name} | {p.table_count} | {p.row_count} | "
                    f"{p.reserved_space_mb:,.2f} | {p.reserved_space_gb:,.2f} | "
                    f"{p.data_space_gb:,.2f} | {p.index_space_gb:,.2f} | "
                    f"{p.max_size_gb if p.max_size_gb is not None else ''} | "
                    f"{(str(p.used_pct_of_max) + ' %') if p.used_pct_of_max is not None else ''} |"
                )
            lines.append("")
            for p in result.dedicated_pool_storage:
                if p.notes:
                    lines.append(f"> `{p.pool_name}`: " + "; ".join(p.notes))

        if result.accounts:
            lines.append("")
            lines.append("## Storage accounts")
            lines.append("| Name | Kind | SKU | HNS (ADLS Gen2) | Default for workspace |")
            lines.append("|---|---|---|---|---|")
            for a in result.accounts:
                lines.append(
                    f"| {a.name} | {a.kind or ''} | {a.sku or ''} | "
                    f"{'yes' if a.is_hns_enabled else 'no'} | "
                    f"{'**yes**' if a.is_workspace_default else 'no'} |"
                )

        if result.capacities:
            lines.append("")
            lines.append("## Capacity (latest sample, Azure Monitor)")
            lines.append(
                "| Account | Used (GB) | Blob (GB) | Containers | Blobs | Files | File capacity |"
            )
            lines.append("|---|---:|---:|---:|---:|---:|---:|")
            for c in result.capacities:
                lines.append(
                    f"| {c.account_name} | "
                    f"{c.used_capacity_gb if c.used_capacity_gb is not None else ''} | "
                    f"{c.blob_capacity_gb if c.blob_capacity_gb is not None else ''} | "
                    f"{c.container_count if c.container_count is not None else ''} | "
                    f"{c.blob_count if c.blob_count is not None else ''} | "
                    f"{c.file_count if c.file_count is not None else ''} | "
                    f"{_fmt_bytes(c.file_capacity_bytes)} |"
                )

        written.append(write_markdown(out_dir / "storage.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir))

    return written
