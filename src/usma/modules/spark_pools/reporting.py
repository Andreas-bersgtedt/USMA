from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import SparkAnalysis


def write_reports(result: SparkAnalysis, out_dir: Path, formats: Iterable[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "spark_pools.json"))

    if "csv" in fmts:
        for fname, rows in (
            ("spark_pools.csv", result.pools),
            ("spark_notebooks.csv", result.notebooks),
            ("spark_job_definitions.csv", result.spark_job_definitions),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer - Apache Spark Pools",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Generated: {result.generated_at.isoformat()}",
            f"- Pools: {len(result.pools)}",
            f"- Notebooks: {len(result.notebooks)}",
            f"- Spark job definitions: {len(result.spark_job_definitions)}",
            "",
        ]
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        if result.pools:
            lines.append("## Pools")
            lines.append("| Name | Spark | Node size | Nodes | Autoscale | AutoPause (min) |")
            lines.append("|---|---|---|---:|---|---:|")
            for p in result.pools:
                autoscale = (
                    f"{p.min_node_count}-{p.max_node_count}"
                    if p.auto_scale_enabled and p.min_node_count is not None and p.max_node_count is not None
                    else ("on" if p.auto_scale_enabled else "off")
                )
                pause = p.auto_pause_delay_minutes if p.auto_pause_enabled else "off"
                lines.append(
                    f"| {p.name} | {p.spark_version or ''} | {p.node_size or ''} | "
                    f"{p.node_count if p.node_count is not None else ''} | {autoscale} | {pause} |"
                )
            lines.append("")
        if result.notebooks:
            lines.append("## Notebooks")
            lines.append("| Name | Language | Attached pool | Cells | Source chars |")
            lines.append("|---|---|---|---:|---:|")
            for nb in result.notebooks:
                lines.append(
                    f"| {nb.name} | {nb.language or ''} | {nb.attached_spark_pool or ''} | "
                    f"{nb.cell_count} | {nb.source_size_chars} |"
                )
            lines.append("")
        if result.spark_job_definitions:
            lines.append("## Spark job definitions")
            lines.append("| Name | Language | Target pool | Main file | Class |")
            lines.append("|---|---|---|---|---|")
            for sjd in result.spark_job_definitions:
                lines.append(
                    f"| {sjd.name} | {sjd.language or ''} | {sjd.target_spark_pool or ''} | "
                    f"{sjd.main_definition_file or ''} | {sjd.class_name or ''} |"
                )
            lines.append("")
        written.append(write_markdown(out_dir / "spark_pools.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir))

    return written
