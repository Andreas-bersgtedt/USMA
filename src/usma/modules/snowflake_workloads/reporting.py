"""Report writers for the snowflake_workloads module."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .models import SnowflakeWorkloadsAnalysis


def write_reports(
    result: SnowflakeWorkloadsAnalysis,
    out_dir: Path,
    formats: Iterable[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(
            write_json_model(result, out_dir / "snowflake_workloads.json"),
        )

    if "csv" in fmts:
        for fname, rows in (
            ("snowflake_warehouses.csv", result.warehouses),
            ("snowflake_databases.csv", result.databases),
            ("snowflake_schemas.csv", result.schemas),
            ("snowflake_tables.csv", result.tables),
            ("snowflake_routines.csv", result.routines),
            ("snowflake_stages.csv", result.stages),
            ("snowflake_streams.csv", result.streams),
            ("snowflake_tasks.csv", result.tasks),
            ("snowflake_pipes.csv", result.pipes),
            ("snowflake_jobs.csv", result.jobs),
            ("snowflake_warehouse_window_stats.csv", result.warehouse_window_stats),
            ("snowflake_job_window_stats.csv", result.job_window_stats),
            ("snowflake_daily_stats.csv", result.daily_stats),
            ("snowflake_breakdowns.csv", result.breakdowns),
            ("snowflake_table_usage.csv", result.table_usage),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer — Snowflake workloads",
            "",
            f"- Account: `{result.account}`",
            f"- Platform: `{result.platform or '-'}`"
            + (f" (region `{result.region}`)" if result.region else ""),
            f"- Edition: `{result.edition or '-'}`",
            f"- Generated: {result.generated_at.isoformat()}",
            f"- Warehouses: {result.warehouse_count}",
            f"- Databases: {result.database_count}"
            + (f" (schemas: {result.schema_count})" if result.schema_count else ""),
            f"- Tables: {result.table_count}"
            + (
                f" (views: {result.view_count}, materialized: "
                f"{result.materialized_view_count}, external: "
                f"{result.external_table_count}, dynamic: "
                f"{result.dynamic_table_count}, iceberg: "
                f"{result.iceberg_table_count})"
                if any(
                    (
                        result.view_count, result.materialized_view_count,
                        result.external_table_count, result.dynamic_table_count,
                        result.iceberg_table_count,
                    )
                )
                else ""
            ),
            f"- Routines: {result.routine_count}",
            f"- Stages: {result.stage_count}, Streams: {result.stream_count}, "
            f"Tasks: {result.task_count}, Pipes: {result.pipe_count}",
            f"- Jobs (query-history window): {result.job_count}",
            f"- Unsupported objects: {result.unsupported_object_count}, "
            f"Partial: {result.partial_object_count}",
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
        if result.warehouses:
            lines.append("## Warehouses")
            lines.append("| Name | Size | Type | Clusters (min..max) | Scaling | Auto-suspend (s) |")
            lines.append("|---|---|---|---|---|---:|")
            for w in result.warehouses:
                lines.append(
                    f"| {w.name} | {w.size} | {w.type} | "
                    f"{w.min_cluster_count}..{w.max_cluster_count} | "
                    f"{w.scaling_policy} | "
                    f"{w.auto_suspend_seconds if w.auto_suspend_seconds is not None else '-'} |",
                )
            lines.append("")
        if result.databases:
            lines.append("## Databases")
            lines.append("| Database | Owner | Schemas | Transient | Share |")
            lines.append("|---|---|---:|:---:|:---:|")
            for d in result.databases:
                lines.append(
                    f"| {d.name} | {d.owner or '-'} | {d.schema_count} | "
                    f"{'yes' if d.is_transient else 'no'} | "
                    f"{'yes' if d.is_share else 'no'} |",
                )
            lines.append("")
        if result.code_object_summary is not None and result.code_object_summary.total:
            cs = result.code_object_summary
            lines.append("## Code-object compatibility")
            pct = (
                f"{cs.compatibility_pct:.1f}%"
                if cs.compatibility_pct is not None else "n/a"
            )
            lines.append(
                f"- Total: **{cs.total}** · Fabric-supported: **{pct}**",
            )
            if cs.by_support:
                parts = ", ".join(
                    f"{k}: {v}" for k, v in sorted(cs.by_support.items())
                )
                lines.append(f"- By support: {parts}")
            if cs.by_kind:
                parts = ", ".join(
                    f"{k}: {v}" for k, v in cs.by_kind.items()
                )
                lines.append(f"- By kind: {parts}")
            if cs.by_language:
                parts = ", ".join(
                    f"{k}: {v}" for k, v in cs.by_language.items()
                )
                lines.append(f"- Routine languages: {parts}")
            if cs.unsupported_object_names:
                lines.append(
                    f"- Unsupported ({len(cs.unsupported_object_names)}): "
                    + ", ".join(
                        f"`{n}`" for n in cs.unsupported_object_names[:10]
                    )
                    + (" ..." if len(cs.unsupported_object_names) > 10 else ""),
                )
            if cs.partial_object_names:
                lines.append(
                    f"- Partial ({len(cs.partial_object_names)}): "
                    + ", ".join(
                        f"`{n}`" for n in cs.partial_object_names[:10]
                    )
                    + (" ..." if len(cs.partial_object_names) > 10 else ""),
                )
            lines.append("")
        if result.table_usage:
            top = result.table_usage[:20]
            note = (
                "ACCESS_HISTORY"
                if top[0].source == "access_history"
                else "QUERY_HISTORY DB.SCHEMA fallback"
            )
            lines.append(f"## Top-active tables (source: {note})")
            lines.append(
                "| Object | Domain | Jobs | Bytes scanned | Rows produced | Last seen |"
            )
            lines.append("|---|---|---:|---:|---:|---|")
            for u in top:
                lines.append(
                    f"| `{u.full_name}` | {u.object_domain} | {u.usage_count} | "
                    f"{u.total_bytes_scanned:,} | {u.total_rows_produced:,} | "
                    f"{u.last_seen.isoformat() if u.last_seen else '-'} |",
                )
            if len(result.table_usage) > len(top):
                lines.append(
                    f"_{len(result.table_usage) - len(top)} more in "
                    "`snowflake_table_usage.csv`._",
                )
            lines.append("")
        if result.breakdowns:
            for dim, label in (
                ("query_type", "Query-type breakdown"),
                ("user", "Top users"),
                ("warehouse", "Per-warehouse activity"),
            ):
                rows = [b for b in result.breakdowns if b.dimension == dim][:15]
                if not rows:
                    continue
                lines.append(f"## {label}")
                lines.append(
                    "| Key | Jobs | OK | Failed | Bytes scanned | Avg duration (s) | Est. credits |"
                )
                lines.append("|---|---:|---:|---:|---:|---:|---:|")
                for r in rows:
                    avg = (
                        f"{r.avg_duration_seconds:.1f}"
                        if r.avg_duration_seconds is not None else "-"
                    )
                    lines.append(
                        f"| `{r.key}` | {r.job_count} | {r.succeeded_count} | "
                        f"{r.failed_count} | {r.total_bytes_scanned:,} | "
                        f"{avg} | {r.est_credits:.2f} |",
                    )
                lines.append("")
        if result.tables:
            top = result.tables[:50]
            lines.append("## Table support (first 50 shown)")
            lines.append("| Table | Kind | Support |")
            lines.append("|---|---|---|")
            for t in top:
                lines.append(f"| {t.full_name} | {t.kind} | {t.support} |")
            if len(result.tables) > len(top):
                lines.append(
                    f"_{len(result.tables) - len(top)} more table(s) in "
                    "`snowflake_tables.csv`._",
                )
            lines.append("")
        if result.routines:
            top = result.routines[:50]
            lines.append("## Routines (first 50 shown)")
            lines.append("| Routine | Kind | Language | Support |")
            lines.append("|---|---|---|---|")
            for r in top:
                lines.append(
                    f"| {r.full_name} | {r.routine_kind} | {r.language} | {r.support} |",
                )
            if len(result.routines) > len(top):
                lines.append(
                    f"_{len(result.routines) - len(top)} more routine(s) in "
                    "`snowflake_routines.csv`._",
                )
            lines.append("")
        for label, items, csv_name in (
            ("Stages", result.stages, "snowflake_stages.csv"),
            ("Streams", result.streams, "snowflake_streams.csv"),
            ("Tasks", result.tasks, "snowflake_tasks.csv"),
            ("Pipes", result.pipes, "snowflake_pipes.csv"),
        ):
            if not items:
                continue
            top = items[:25]
            lines.append(f"## {label}")
            lines.append("| Name | Support |")
            lines.append("|---|---|")
            for obj in top:
                lines.append(f"| {obj.full_name} | {obj.support} |")
            if len(items) > len(top):
                lines.append(
                    f"_{len(items) - len(top)} more in `{csv_name}`._",
                )
            lines.append("")
        written.append(
            write_markdown(out_dir / "snowflake_workloads.md", lines),
        )

    return written


__all__ = ["write_reports"]
