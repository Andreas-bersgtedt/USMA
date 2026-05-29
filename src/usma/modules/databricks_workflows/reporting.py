"""Report writers for the databricks_workflows module."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .models import DatabricksWorkflowsAnalysis


def write_reports(
    result: DatabricksWorkflowsAnalysis,
    out_dir: Path,
    formats: Iterable[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(
            write_json_model(result, out_dir / "databricks_workflows.json"),
        )

    if "csv" in fmts:
        for fname, rows in (
            ("databricks_workflows.csv", result.workflows),
            ("databricks_tasks.csv", result.tasks),
            ("databricks_job_clusters.csv", result.job_clusters),
            ("databricks_interactive_clusters.csv", result.interactive_clusters),
            ("databricks_sql_warehouses.csv", result.sql_warehouses),
            ("databricks_sql_warehouse_queries.csv", result.sql_warehouse_queries),
            ("databricks_sql_warehouse_stats.csv", result.sql_warehouse_stats),
            ("databricks_sql_warehouse_usage.csv", result.sql_warehouse_daily_usage),
            ("databricks_sql_warehouse_fabric_mappings.csv", result.sql_warehouse_fabric_mappings),
        ):
            p = write_csv_rows(out_dir / fname, rows)
            if p:
                written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer — Databricks workflows",
            "",
            f"- Workspace: `{result.workspace_name}`",
            f"- Workspace URL: `{result.workspace_url or '-'}`",
            f"- Generated: {result.generated_at.isoformat()}",
            f"- Workflows: {result.workflow_count}",
            f"- Tasks: {result.task_count}"
            + (
                f" (partial: {result.partial_task_count}, "
                f"unsupported: {result.unsupported_task_count})"
                if result.task_count
                else ""
            ),
            f"- Interactive clusters: {len(result.interactive_clusters)}",
            f"- SQL warehouses: {result.sql_warehouse_count}",
            f"- SQL queries (last {result.sql_warehouse_stats[0].lookback_days if result.sql_warehouse_stats else '-'} d): {result.sql_warehouse_query_count}",
            "",
        ]
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        if result.workflows:
            lines.append("## Workflows")
            lines.append("| Name | Tasks | Job clusters | Schedule | Serverless |")
            lines.append("|---|---:|---:|---|---|")
            for w in result.workflows:
                lines.append(
                    f"| {w.name} | {w.task_count} | {w.job_cluster_count} | "
                    f"{w.schedule_cron or '-'} | "
                    f"{'yes' if w.uses_serverless else 'no'} |"
                )
            lines.append("")
        if result.tasks:
            lines.append("## Task support")
            lines.append("| Workflow | Task | Type | Support |")
            lines.append("|---|---|---|---|")
            for t in result.tasks:
                lines.append(
                    f"| {t.job_name} | {t.task_key} | {t.task_type} | {t.support} |"
                )
            lines.append("")
        if result.sql_warehouses:
            lines.append("## SQL warehouses")
            lines.append("| Name | Type | Size | State | Auto-stop (min) | Photon | Clusters (min\u2013max) |")
            lines.append("|---|---|---|---|---:|---|---|")
            for w in result.sql_warehouses:
                clusters = f"{w.min_num_clusters or '-'}\u2013{w.max_num_clusters or '-'}"
                lines.append(
                    f"| {w.name} | {w.warehouse_type or '-'} | {w.cluster_size or '-'} | "
                    f"{w.state or '-'} | {w.auto_stop_mins if w.auto_stop_mins is not None else '-'} | "
                    f"{'yes' if w.enable_photon else 'no'} | {clusters} |"
                )
            lines.append("")
        if result.sql_warehouse_stats:
            lines.append("## SQL warehouse usage")
            lines.append(
                "| Warehouse | Queries | Succeeded | Failed | Avg dur (s) | p95 dur (s) | CPU sec (REST) | From jobs | Unique users |"
            )
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
            for s in result.sql_warehouse_stats:
                avg = f"{s.avg_duration_seconds:.1f}" if s.avg_duration_seconds is not None else "-"
                p95 = f"{s.p95_duration_seconds:.1f}" if s.p95_duration_seconds is not None else "-"
                cpu = (
                    f"{s.total_cpu_seconds:.1f} ({s.queries_with_cpu_metric}/{s.query_count})"
                    if s.total_cpu_seconds is not None
                    else "-"
                )
                lines.append(
                    f"| {s.warehouse_name or s.warehouse_id} | {s.query_count} | "
                    f"{s.succeeded_count} | {s.failed_count} | {avg} | {p95} | {cpu} | "
                    f"{s.queries_from_jobs} | {s.unique_users} |"
                )
            lines.append("")
        if result.sql_warehouse_daily_usage:
            lines.append("## SQL warehouse daily usage (Unity Catalog system tables)")
            lines.append(
                "| Date | Warehouse | Queries | Task seconds | DBU-hours | SKU |"
            )
            lines.append("|---|---|---:|---:|---:|---|")
            for u in result.sql_warehouse_daily_usage:
                tsec = f"{u.total_task_seconds:.1f}" if u.total_task_seconds is not None else "-"
                dbu = f"{u.dbu_hours:.3f}" if u.dbu_hours is not None else "-"
                lines.append(
                    f"| {u.usage_date} | {u.warehouse_name or u.warehouse_id} | "
                    f"{u.query_count} | {tsec} | {dbu} | {u.sku_name or '-'} |"
                )
            lines.append("")
        if result.sql_warehouse_fabric_mappings:
            lines.append("## SQL warehouse → Fabric mapping")
            lines.append(
                "| Warehouse | Source type | Fabric target | F-SKU | Support | "
                "Confidence | Evidence | CPU sec | Task sec | DBU-h | Avg CUs |"
            )
            lines.append("|---|---|---|---|---|---|---|---:|---:|---:|---:|")
            for m in result.sql_warehouse_fabric_mappings:
                cpu = f"{m.total_cpu_seconds:.1f}" if m.total_cpu_seconds is not None else "-"
                tsec = f"{m.total_task_seconds:.1f}" if m.total_task_seconds is not None else "-"
                dbu = f"{m.total_dbu_hours:.3f}" if m.total_dbu_hours is not None else "-"
                avg = f"{m.avg_concurrent_cus:.4f}" if m.avg_concurrent_cus is not None else "-"
                lines.append(
                    f"| {m.warehouse_name or m.warehouse_id} | "
                    f"{m.source_warehouse_type or '-'} | "
                    f"{m.target_fabric_artifact} | {m.recommended_sku or '-'} | "
                    f"{m.support} | {m.confidence} | {m.evidence_source} | "
                    f"{cpu} | {tsec} | {dbu} | {avg} |"
                )
            lines.append("")
        written.append(
            write_markdown(out_dir / "databricks_workflows.md", lines),
        )

    return written


__all__ = ["write_reports"]
