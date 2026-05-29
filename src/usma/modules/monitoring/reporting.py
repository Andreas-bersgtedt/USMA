from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import MonitoringAnalysis


def write_reports(result: MonitoringAnalysis, out_dir: Path, formats: Iterable[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "monitoring.json"))

    if "csv" in fmts:
        # Write a "summary" CSV (one row per series) — points stay in JSON to keep CSVs tidy.
        summary_rows = [
            _SummaryRow(
                resource_name=s.resource_name,
                metric_name=s.metric_name,
                aggregation=s.aggregation,
                unit=s.unit,
                min_value=s.min_value,
                avg_value=s.avg_value,
                p95_value=s.p95_value,
                max_value=s.max_value,
                point_count=len(s.points),
            )
            for s in result.series
        ]
        p = write_csv_rows(out_dir / "monitoring_summary.csv", summary_rows)
        if p:
            written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer - Monitoring",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Window: `{result.window_start.isoformat()}` -> `{result.window_end.isoformat()}` ({result.interval})",
            f"- Series collected: {len(result.series)}",
            "",
        ]
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        if result.series:
            lines.append("## Metric summary")
            lines.append("| Resource | Metric | Unit | Min | Avg | P95 | Max | Points |")
            lines.append("|---|---|---|---:|---:|---:|---:|---:|")
            for s in sorted(result.series, key=lambda x: (x.resource_name, x.metric_name)):
                lines.append(
                    f"| {s.resource_name} | {s.metric_name} | {s.unit or ''} | "
                    f"{_fmt(s.min_value)} | {_fmt(s.avg_value)} | {_fmt(s.p95_value)} | "
                    f"{_fmt(s.max_value)} | {len(s.points)} |"
                )
            lines.append("")
        written.append(write_markdown(out_dir / "monitoring.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir))

    return written


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value) >= 1000:
        return f"{value:.0f}"
    return f"{value:.2f}"


# Local Pydantic-shaped row for write_csv_rows (which expects BaseModel instances).
class _SummaryRow(BaseModel):
    resource_name: str
    metric_name: str
    aggregation: str
    unit: str | None
    min_value: float | None
    avg_value: float | None
    p95_value: float | None
    max_value: float | None
    point_count: int
