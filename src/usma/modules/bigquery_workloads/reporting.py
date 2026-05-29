"""Report writers for the bigquery_workloads module."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .models import BigQueryWorkloadsAnalysis


def write_reports(
    result: BigQueryWorkloadsAnalysis,
    out_dir: Path,
    formats: Iterable[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(
            write_json_model(result, out_dir / "bigquery_workloads.json"),
        )

    if "csv" in fmts:
        for fname, rows in (
            ("bigquery_datasets.csv", result.datasets),
            ("bigquery_tables.csv", result.tables),
            ("bigquery_routines.csv", result.routines),
            ("bigquery_scheduled_queries.csv", result.scheduled_queries),
            ("bigquery_jobs.csv", result.jobs),
            ("bigquery_table_usage.csv", result.table_usage),
            ("bigquery_jobs_daily.csv", result.daily_stats),
            ("bigquery_jobs_breakdowns.csv", result.breakdowns),
            ("bigquery_query_features.csv", result.query_features),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer — BigQuery workloads",
            "",
            f"- Project: `{result.project_id}`"
            + (f" (number `{result.project_number}`)" if result.project_number else ""),
            f"- Location: `{result.location or '-'}`",
            f"- Generated: {result.generated_at.isoformat()}",
            f"- Datasets: {result.dataset_count}",
            f"- Tables: {result.table_count}"
            + (
                f" (views: {result.view_count}, materialized: "
                f"{result.materialized_view_count}, external: "
                f"{result.external_table_count})"
                if (result.view_count or result.materialized_view_count or result.external_table_count)
                else ""
            ),
            f"- Routines: {result.routine_count}",
            f"- Scheduled queries: {result.scheduled_query_count}",
            f"- Jobs (audit-log window): {result.job_count}",
            "",
        ]
        if result.caveats:
            lines.append("## Caveats")
            for c in result.caveats:
                lines.append(f"- {c}")
            lines.append("")
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        if result.datasets:
            lines.append("## Datasets")
            lines.append("| Dataset | Location | Tables |")
            lines.append("|---|---|---:|")
            for d in result.datasets:
                lines.append(
                    f"| {d.dataset_id} | {d.location or '-'} | {d.table_count} |",
                )
            lines.append("")
        if result.tables:
            lines.append("## Table support")
            lines.append("| Table | Type | Support |")
            lines.append("|---|---|---|")
            for t in result.tables:
                lines.append(
                    f"| {t.full_table_id} | {t.table_type} | {t.support} |",
                )
            lines.append("")
        if result.routines:
            lines.append("## Routines")
            lines.append("| Routine | Type | Language | Support |")
            lines.append("|---|---|---|---|")
            for r in result.routines:
                lines.append(
                    f"| {r.project_id}.{r.dataset_id}.{r.routine_id} | "
                    f"{r.routine_type} | {r.language} | {r.support} |",
                )
            lines.append("")
        if result.scheduled_queries:
            lines.append("## Scheduled queries")
            lines.append("| Display name | Schedule | State | Destination |")
            lines.append("|---|---|---|---|")
            for sq in result.scheduled_queries:
                lines.append(
                    f"| {sq.display_name} | {sq.schedule or '-'} | {sq.state} | "
                    f"{sq.destination_dataset_id or '-'} |",
                )
            lines.append("")
        if result.table_usage:
            top = result.table_usage[:20]
            lines.append("## Most-used tables (top 20 by usage count)")
            lines.append(
                "| Rank | Table | Jobs | Elapsed (s) | Slot-hours | "
                "Billed (GB) | Support | In catalog |",
            )
            lines.append("|---:|---|---:|---:|---:|---:|---|:---:|")
            for i, tu in enumerate(top, start=1):
                gb = tu.total_billed_bytes / (1024 ** 3) if tu.total_billed_bytes else 0.0
                lines.append(
                    f"| {i} | {tu.full_table_id} | {tu.usage_count} | "
                    f"{tu.total_elapsed_seconds:,.1f} | {tu.total_slot_hours:,.2f} | "
                    f"{gb:,.2f} | {tu.table_support} | "
                    f"{'yes' if tu.in_catalog else 'no'} |",
                )
            lines.append("")
            if len(result.table_usage) > len(top):
                lines.append(
                    f"_{len(result.table_usage) - len(top)} more table(s) in "
                    "`bigquery_table_usage.csv`._",
                )
                lines.append("")
        if result.daily_stats:
            recent = result.daily_stats[-30:]
            lines.append("## Daily job execution (last 30 days shown)")
            lines.append(
                "| Date | Jobs | OK | Failed | Slot-hours | Billed (GB) | "
                "Avg duration (s) | Success rate |",
            )
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
            for d in recent:
                gb = d.total_billed_bytes / (1024 ** 3) if d.total_billed_bytes else 0.0
                dur = f"{d.avg_duration_seconds:,.1f}" if d.avg_duration_seconds is not None else "-"
                sr = (
                    f"{d.success_rate:.0%}" if d.success_rate is not None else "-"
                )
                lines.append(
                    f"| {d.date} | {d.job_count} | {d.succeeded_count} | "
                    f"{d.failed_count} | {d.total_slot_hours:,.2f} | "
                    f"{gb:,.2f} | {dur} | {sr} |",
                )
            lines.append("")
            if len(result.daily_stats) > len(recent):
                lines.append(
                    f"_{len(result.daily_stats) - len(recent)} earlier day(s) "
                    "in `bigquery_jobs_daily.csv`._",
                )
                lines.append("")
        if result.breakdowns:
            # Group by dimension, render top-5 of each so the markdown
            # stays readable; full data lives in the CSV.
            from collections import defaultdict
            by_dim: dict[str, list] = defaultdict(list)
            for b in result.breakdowns:
                by_dim[b.dimension].append(b)
            for dim_label, header in (
                ("user", "Top 5 users by slot-hours"),
                ("statement_type", "Statement-type breakdown"),
                ("job_type", "Job-type breakdown"),
                ("reservation", "Reservation breakdown"),
                ("edition", "Edition breakdown"),
                ("priority", "Priority breakdown"),
            ):
                rows = by_dim.get(dim_label) or []
                if not rows:
                    continue
                shown = rows[:5]
                lines.append(f"## {header}")
                lines.append(
                    "| Key | Jobs | OK | Failed | Slot-hours | Billed (GB) | Avg duration (s) |",
                )
                lines.append("|---|---:|---:|---:|---:|---:|---:|")
                for b in shown:
                    gb = b.total_billed_bytes / (1024 ** 3) if b.total_billed_bytes else 0.0
                    dur = (
                        f"{b.avg_duration_seconds:,.1f}"
                        if b.avg_duration_seconds is not None else "-"
                    )
                    lines.append(
                        f"| {b.key} | {b.job_count} | {b.succeeded_count} | "
                        f"{b.failed_count} | {b.total_slot_hours:,.2f} | "
                        f"{gb:,.2f} | {dur} |",
                    )
                if len(rows) > len(shown):
                    lines.append(
                        f"_{len(rows) - len(shown)} more in "
                        "`bigquery_jobs_breakdowns.csv`._",
                    )
                lines.append("")
        if result.query_features:
            lines.append("## Query features detected (sqlglot)")
            lines.append(
                "_Per-job feature flags extracted from captured query text. "
                "Each row counts distinct jobs exhibiting the feature; "
                "see `bigquery_query_features.csv` for the full list._",
            )
            lines.append("")
            lines.append("| Feature | Jobs |")
            lines.append("|---|---:|")
            for qf in result.query_features[:25]:
                lines.append(f"| `{qf.feature}` | {qf.job_count} |")
            if len(result.query_features) > 25:
                lines.append(
                    f"_{len(result.query_features) - 25} more feature(s) in "
                    "`bigquery_query_features.csv`._",
                )
            lines.append("")
        written.append(
            write_markdown(out_dir / "bigquery_workloads.md", lines),
        )

    return written


__all__ = ["write_reports"]
