from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .html_report import write_html
from .models import Activity, PipelinesAnalysis


def _write_activities_csv(path: Path, rows: list[Activity]) -> Path | None:
    """Write activities CSV with list fields flattened (semicolon-delimited)."""
    if not rows:
        return None
    fieldnames = list(rows[0].model_dump(mode="json").keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            d = r.model_dump(mode="json")
            for k, v in list(d.items()):
                if isinstance(v, list):
                    d[k] = "; ".join(str(x) for x in v)
            writer.writerow(d)
    return path


def _write_run_history_csvs(result: PipelinesAnalysis, out_dir: Path) -> list[Path]:
    """Emit per-(pipeline, window) and per-pipeline summary CSVs."""
    history = result.run_history
    if history is None or not history.by_pipeline:
        return []
    detail_path = out_dir / "pipeline_run_stats.csv"
    fields = [
        "pipeline", "has_data_movement", "last_run_at", "last_run_status",
        "window_days", "run_count", "succeeded", "failed", "other",
        "success_rate", "avg_duration_ms", "p95_duration_ms",
        "avg_data_moved_mb_per_run", "total_data_moved_mb",
        "avg_diu_hours_per_run", "total_diu_hours", "est_cu_hours_from_diu",
        "avg_vcore_hours_per_run", "total_vcore_hours", "est_cu_hours_from_vcore",
        "est_non_copy_activity_runs", "est_cu_hours_from_orchestration",
    ]
    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for stats in history.by_pipeline:
            for w in stats.windows:
                writer.writerow({
                    "pipeline": stats.pipeline,
                    "has_data_movement": stats.has_data_movement,
                    "last_run_at": stats.last_run_at.isoformat() if stats.last_run_at else "",
                    "last_run_status": stats.last_run_status or "",
                    "window_days": w.window_days,
                    "run_count": w.run_count,
                    "succeeded": w.succeeded,
                    "failed": w.failed,
                    "other": w.other,
                    "success_rate": "" if w.success_rate is None else f"{w.success_rate:.4f}",
                    "avg_duration_ms": "" if w.avg_duration_ms is None else f"{w.avg_duration_ms:.0f}",
                    "p95_duration_ms": "" if w.p95_duration_ms is None else f"{w.p95_duration_ms:.0f}",
                    "avg_data_moved_mb_per_run": (
                        "" if w.avg_data_moved_mb_per_run is None
                        else f"{w.avg_data_moved_mb_per_run:.2f}"
                    ),
                    "total_data_moved_mb": (
                        "" if w.total_data_moved_mb is None
                        else f"{w.total_data_moved_mb:.2f}"
                    ),
                    "avg_diu_hours_per_run": (
                        "" if w.avg_diu_hours_per_run is None
                        else f"{w.avg_diu_hours_per_run:.4f}"
                    ),
                    "total_diu_hours": (
                        "" if w.total_diu_hours is None
                        else f"{w.total_diu_hours:.4f}"
                    ),
                    "est_cu_hours_from_diu": (
                        "" if w.est_cu_hours_from_diu is None
                        else f"{w.est_cu_hours_from_diu:.4f}"
                    ),
                    "avg_vcore_hours_per_run": (
                        "" if w.avg_vcore_hours_per_run is None
                        else f"{w.avg_vcore_hours_per_run:.4f}"
                    ),
                    "total_vcore_hours": (
                        "" if w.total_vcore_hours is None
                        else f"{w.total_vcore_hours:.4f}"
                    ),
                    "est_cu_hours_from_vcore": (
                        "" if w.est_cu_hours_from_vcore is None
                        else f"{w.est_cu_hours_from_vcore:.4f}"
                    ),
                    "est_non_copy_activity_runs": w.est_non_copy_activity_runs,
                    "est_cu_hours_from_orchestration": f"{w.est_cu_hours_from_orchestration:.4f}",
                })

    # Per-pipeline headline (pick the 28-day window when available).
    summary_path = out_dir / "pipeline_run_summary.csv"
    summary_fields = [
        "pipeline", "has_data_movement", "last_run_at", "last_run_status",
        "headline_window_days", "run_count", "succeeded", "failed",
        "success_rate", "avg_duration_ms", "avg_data_moved_mb_per_run",
        "total_diu_hours", "est_cu_hours_from_diu",
        "total_vcore_hours", "est_cu_hours_from_vcore",
        "est_non_copy_activity_runs", "est_cu_hours_from_orchestration",
    ]
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        for stats in history.by_pipeline:
            headline = next(
                (w for w in stats.windows if w.window_days == 28),
                stats.windows[-1] if stats.windows else None,
            )
            if headline is None:
                continue
            writer.writerow({
                "pipeline": stats.pipeline,
                "has_data_movement": stats.has_data_movement,
                "last_run_at": stats.last_run_at.isoformat() if stats.last_run_at else "",
                "last_run_status": stats.last_run_status or "",
                "headline_window_days": headline.window_days,
                "run_count": headline.run_count,
                "succeeded": headline.succeeded,
                "failed": headline.failed,
                "success_rate": "" if headline.success_rate is None else f"{headline.success_rate:.4f}",
                "avg_duration_ms": "" if headline.avg_duration_ms is None else f"{headline.avg_duration_ms:.0f}",
                "avg_data_moved_mb_per_run": (
                    "" if headline.avg_data_moved_mb_per_run is None
                    else f"{headline.avg_data_moved_mb_per_run:.2f}"
                ),
                "total_diu_hours": (
                    "" if headline.total_diu_hours is None
                    else f"{headline.total_diu_hours:.4f}"
                ),
                "est_cu_hours_from_diu": (
                    "" if headline.est_cu_hours_from_diu is None
                    else f"{headline.est_cu_hours_from_diu:.4f}"
                ),
                "total_vcore_hours": (
                    "" if headline.total_vcore_hours is None
                    else f"{headline.total_vcore_hours:.4f}"
                ),
                "est_cu_hours_from_vcore": (
                    "" if headline.est_cu_hours_from_vcore is None
                    else f"{headline.est_cu_hours_from_vcore:.4f}"
                ),
                "est_non_copy_activity_runs": headline.est_non_copy_activity_runs,
                "est_cu_hours_from_orchestration": f"{headline.est_cu_hours_from_orchestration:.4f}",
            })
    return [detail_path, summary_path]


def write_reports(result: PipelinesAnalysis, out_dir: Path, formats: Iterable[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "pipelines.json"))

    if "csv" in fmts:
        # Activities have list-typed columns (reasons, caveats) — flatten those.
        p = _write_activities_csv(out_dir / "pipeline_activities.csv", result.activities)
        if p:
            written.append(p)
        for fname, rows in (
            ("pipelines.csv", result.pipelines),
            ("linked_services.csv", result.linked_services),
            ("datasets.csv", result.datasets),
            ("triggers.csv", result.triggers),
            ("integration_runtimes.csv", result.integration_runtimes),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)
        # Run history (if collected).
        if result.run_history and result.run_history.by_pipeline:
            written.extend(_write_run_history_csvs(result, out_dir))

    if "markdown" in fmts:
        unsupported_acts = [a for a in result.activities if a.support == "unsupported"]
        partial_acts = [a for a in result.activities if a.support == "partial"]
        unsupported_ls = [ls for ls in result.linked_services if not ls.fabric_supported]
        lines = [
            "# Unified Solution Migration Analyzer - Pipelines",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Artifacts endpoint: `{result.artifacts_endpoint}`",
            f"- Generated: {result.generated_at.isoformat()}",
            "",
            f"- Pipelines: {len(result.pipelines)}",
            f"- Activities (flattened): {len(result.activities)}  "
            f"(unsupported: **{len(unsupported_acts)}**, partial: {len(partial_acts)})",
            f"- Linked services: {len(result.linked_services)}  "
            f"(Fabric-unsupported: **{len(unsupported_ls)}**)",
            f"- Datasets: {len(result.datasets)}",
            f"- Triggers: {len(result.triggers)}",
            f"- Integration runtimes: {len(result.integration_runtimes)}",
            "",
        ]
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        if result.pipelines:
            lines.append("## Pipelines")
            lines.append("| Name | Folder | Activities | Unsupported | Partial | Activity types |")
            lines.append("|---|---|---:|---:|---:|---|")
            for p in result.pipelines:
                lines.append(
                    f"| {p.name} | {p.folder or ''} | {p.activity_count} | "
                    f"{p.unsupported_activity_count} | {p.partial_activity_count} | "
                    f"{', '.join(p.activity_types)} |"
                )
            lines.append("")
        if unsupported_acts:
            lines.append("## Fabric-unsupported activities")
            for a in unsupported_acts:
                lines.append(f"### `{a.pipeline}` &rarr; `{a.name}` ({a.type})")
                if a.fabric_equivalent:
                    lines.append(f"- **Fabric equivalent:** {a.fabric_equivalent}")
                else:
                    lines.append("- **Fabric equivalent:** _none_")
                if a.migration_action:
                    lines.append(f"- **Action:** {a.migration_action}")
                for r in a.support_reasons:
                    lines.append(f"  - Reason: {r}")
                for c in a.support_caveats:
                    lines.append(f"  - Caveat: {c}")
                if a.doc_url:
                    lines.append(f"- Docs: <{a.doc_url}>")
                lines.append("")
        if partial_acts:
            lines.append("## Partially-compatible activities")
            lines.append("> These activities exist in Fabric Data Factory but with reduced functionality, narrower connector support, or different config — *what* is partial is listed per activity.")
            lines.append("")
            for a in partial_acts:
                lines.append(f"### `{a.pipeline}` &rarr; `{a.name}` ({a.type})")
                if a.fabric_equivalent:
                    lines.append(f"- **Fabric equivalent:** {a.fabric_equivalent}")
                if a.migration_action:
                    lines.append(f"- **Action:** {a.migration_action}")
                for r in a.support_reasons:
                    lines.append(f"  - Reason: {r}")
                for c in a.support_caveats:
                    lines.append(f"  - Caveat (this instance): {c}")
                if a.doc_url:
                    lines.append(f"- Docs: <{a.doc_url}>")
                lines.append("")
        if unsupported_ls:
            lines.append("## Fabric-unsupported linked services")
            lines.append("| Name | Type |")
            lines.append("|---|---|")
            for ls in unsupported_ls:
                lines.append(f"| {ls.name} | {ls.type} |")
            lines.append("")
        if result.linked_services:
            lines.append("## Linked services")
            lines.append("| Name | Type | Connect via | Fabric supported |")
            lines.append("|---|---|---|---|")
            for ls in result.linked_services:
                lines.append(f"| {ls.name} | {ls.type} | {ls.connect_via or ''} | {'yes' if ls.fabric_supported else 'no'} |")
            lines.append("")
        if result.integration_runtimes:
            lines.append("## Integration runtimes")
            lines.append("| Name | Type |")
            lines.append("|---|---|")
            for ir in result.integration_runtimes:
                lines.append(f"| {ir.name} | {ir.type} |")
            lines.append("")
        if result.run_history and result.run_history.by_pipeline:
            lines.extend(_run_history_markdown(result))
        written.append(write_markdown(out_dir / "pipelines.md", lines))

    if "html" in fmts:
        written.append(write_html(result, out_dir))

    return written


def _run_history_markdown(result: PipelinesAnalysis) -> list[str]:
    history = result.run_history
    assert history is not None
    lines = [
        "## Pipeline runtime statistics",
        "",
        f"- Window: {history.window_start.isoformat()} → {history.window_end.isoformat()}",
        f"- Runs collected: {history.fetched_run_count}"
        + (" (truncated)" if history.truncated else ""),
        f"- Activity runs collected: {history.fetched_activity_run_count}",
        "",
        "| Pipeline | Window | Runs | Succeeded | Failed | Success rate | Avg duration (ms) | Avg MB / run |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stats in history.by_pipeline:
        for w in stats.windows:
            sr = "—" if w.success_rate is None else f"{w.success_rate * 100:.1f}%"
            avg_dur = "—" if w.avg_duration_ms is None else f"{w.avg_duration_ms:.0f}"
            avg_mb = (
                "—" if w.avg_data_moved_mb_per_run is None
                else f"{w.avg_data_moved_mb_per_run:.2f}"
            )
            lines.append(
                f"| {stats.pipeline} | {w.window_days}d | {w.run_count} | "
                f"{w.succeeded} | {w.failed} | {sr} | {avg_dur} | {avg_mb} |"
            )
    lines.append("")
    return lines
