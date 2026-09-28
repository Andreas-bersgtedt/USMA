from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import (
    common_spark_window_days,
    observation_window_days,
    daily_usage_for_window,
    write_html,
)
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
            ("spark_daily_usage.csv", result.daily_usage),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)

    if "markdown" in fmts:
        common_days = common_spark_window_days(result.run_stats)
        daily_window_days = common_days or observation_window_days(result)
        daily_usage = daily_usage_for_window(result, daily_window_days)
        lines = [
            "# Unified Solution Migration Analyzer - Apache Spark Pools",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Generated: {result.generated_at.isoformat()}",
            f"- Pools: {len(result.pools)}",
            f"- Notebooks: {len(result.notebooks)}",
            f"- Spark job definitions: {len(result.spark_job_definitions)}",
            "",
            "## Spark accounting and coverage",
            "",
            f"- Accounting basis: `{result.accounting_basis}`",
            f"- Observation period: `{result.observation_start.isoformat() if result.observation_start else 'unknown'}` to "
            f"`{result.observation_end.isoformat() if result.observation_end else 'unknown'}`",
            f"- Collection coverage: "
            f"{'complete' if result.collection_complete is True else 'incomplete' if result.collection_complete is False else 'unknown (legacy or unavailable metadata)'}",
            "- Fabric Spark conversion: 1 CU = 2 Spark vCores; CU-hours are estimates, not measured Fabric usage or Synapse billing.",
            "- Steady-state CU is the average CU-hours per observed day divided by 24; it is CU, not CU/day, and excludes headroom.",
            "",
        ]
        if result.accounting_basis.startswith("fixed_shape"):
            lines.append(
                "The fixed-shape estimate uses the recorded driver/executor shape over the applicable runtime. "
                "It is not measured billed consumption."
            )
            lines.append("")
        if (
            result.collection_complete is not True
            and not any(
                "coverage" in warning.casefold()
                or "missing telem" in warning.casefold()
                for warning in result.accounting_warnings
            )
        ):
            lines.append(
                "- Warning: collection coverage is incomplete or unknown; missing telemetry is not zero usage."
            )
            lines.append("")
        if result.accounting_warnings:
            lines.append("### Accounting warnings")
            for warning in result.accounting_warnings:
                lines.append(f"- {warning}")
            lines.append("")
        if daily_usage:
            lines.extend([
                f"### Daily accounted usage (UTC, {daily_window_days}-day common/observation window)",
                "",
                "| Day | vCore-hours | Estimated Fabric Spark CU-hours |",
                "|---|---:|---:|",
            ])
            for day in daily_usage:
                lines.append(
                    f"| {day.day.isoformat()} | {day.total_vcore_hours:.2f} | "
                    f"{day.est_cu_hours_fabric_spark:.2f} |"
                )
            lines.append("")
        if result.run_stats:
            if common_days is None:
                lines.extend([
                    "### Spark run window summary",
                    "",
                    "No common window is available across all pool/kind groups; mixed-window totals are omitted.",
                    "",
                ])
            else:
                lines.extend([
                    f"### Spark run summary ({common_days}-day common window)",
                    "",
                    "| Pool | Kind | Runs | Accounted vCore-hours | Estimated CU-hours | Avg daily vCore-hours | Avg daily CU-hours | Steady-state CU | Peak-day CU-hours (diagnostic) | Known / unknown usage runs |",
                    "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                ])
                for stat in result.run_stats:
                    window = next(
                        w for w in stat.windows if w.window_days == common_days
                    )
                    avg_daily_vcore = (
                        f"{window.avg_daily_vcore_hours:.2f}"
                        if window.avg_daily_vcore_hours is not None else ""
                    )
                    avg_daily_cu = (
                        f"{window.avg_daily_cu_hours:.2f}"
                        if window.avg_daily_cu_hours is not None else ""
                    )
                    steady_state_cu = (
                        f"{window.steady_state_cu:.3f}"
                        if window.steady_state_cu is not None else ""
                    )
                    peak_day_cu_hours = (
                        f"{window.peak_day_cu_hours:.2f}"
                        if window.peak_day_cu_hours is not None else ""
                    )
                    lines.append(
                        f"| {stat.pool} | {stat.kind} | {window.run_count} | "
                        f"{window.total_vcore_hours:.2f} | {window.est_cu_hours_fabric_spark:.2f} | "
                        f"{avg_daily_vcore} | {avg_daily_cu} | {steady_state_cu} | {peak_day_cu_hours} | "
                        f"{window.known_usage_run_count} / {window.unknown_usage_run_count} |"
                    )
                lines.append("")
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
