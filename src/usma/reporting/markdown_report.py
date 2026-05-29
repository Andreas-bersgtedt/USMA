from __future__ import annotations

from pathlib import Path

from ..modules.dedicated_pools.models import WorkspaceAnalysis


def write_markdown(result: WorkspaceAnalysis, out_dir: Path) -> Path:
    lines: list[str] = []
    lines.append("# Unified Solution Migration Analyzer - Dedicated SQL Pools")
    lines.append("")
    lines.append(f"- **Workspace:** `{result.workspace_name}`")
    lines.append(f"- **Subscription:** `{result.subscription_id}`")
    lines.append(f"- **Resource group:** `{result.resource_group}`")
    lines.append(f"- **Generated:** {result.generated_at.isoformat()}")
    lines.append(f"- **Pools analyzed:** {len(result.pools)}")
    lines.append("")

    for pool in result.pools:
        inv = pool.inventory
        lines.append(f"## Pool: `{inv.name}`")
        lines.append("")
        lines.append(f"- Status: `{inv.status}` | SKU: `{inv.sku_name}` | DWU: `{inv.sku_capacity}` | Location: `{inv.location}`")
        lines.append(f"- Collation: `{inv.collation}` | MaxSizeBytes: `{inv.max_size_bytes}`")
        if pool.errors:
            lines.append("")
            lines.append("**Collection errors:**")
            for e in pool.errors:
                lines.append(f"- {e}")

        lines.append("")
        lines.append(f"### Schemas ({len(pool.schemas)})")
        if pool.schemas:
            lines.append("| Schema | Objects |")
            lines.append("|---|---:|")
            for s in pool.schemas:
                lines.append(f"| {s.schema_name} | {s.object_count} |")

        lines.append("")
        lines.append(f"### Tables ({len(pool.tables)})")
        if pool.tables:
            lines.append("| Schema | Table | Distribution | Dist Col | Partitioned | Rows | Reserved MB | Index Type |")
            lines.append("|---|---|---|---|---:|---:|---:|---|")
            for t in pool.tables:
                lines.append(
                    f"| {t.schema_name} | {t.table_name} | {t.distribution_policy or ''} | "
                    f"{t.distribution_column or ''} | {t.is_partitioned} | "
                    f"{t.row_count if t.row_count is not None else ''} | "
                    f"{t.reserved_space_mb if t.reserved_space_mb is not None else ''} | "
                    f"{t.index_type or ''} |"
                )

        lines.append("")
        lines.append("### Usage")
        if pool.usage:
            lines.append("| Metric | Value | Unit |")
            lines.append("|---|---|---|")
            for u in pool.usage:
                lines.append(f"| {u.metric} | {u.value} | {u.unit or ''} |")

        lines.append("")
        lines.append(f"### Workload groups ({len(pool.workload_groups)})")
        if pool.workload_groups:
            lines.append("| Name | Importance | Min % | Cap % | Min Grant % | Classifiers |")
            lines.append("|---|---|---:|---:|---:|---:|")
            for w in pool.workload_groups:
                lines.append(
                    f"| {w.name} | {w.importance or ''} | {w.min_resource_pct or ''} | "
                    f"{w.cap_resource_pct or ''} | {w.request_min_resource_grant_pct or ''} | "
                    f"{w.classifier_count} |"
                )

        lines.append("")
        lines.append(f"### Security principals ({len(pool.security)})")
        if pool.security:
            lines.append("| Name | Type | Roles |")
            lines.append("|---|---|---|")
            for sp in pool.security:
                lines.append(f"| {sp.name} | {sp.type} | {', '.join(sp.role_memberships)} |")
        lines.append("")

        # ----- SQL-plane inventory (stored procedures, functions, views) -----
        if pool.code_objects:
            sumr = pool.code_object_summary
            lines.append(f"### Code objects (SQL plane) ({len(pool.code_objects)})")
            lines.append("")
            if sumr:
                pct = "" if sumr.compatibility_pct is None else f" | Fabric-compatible: {sumr.compatibility_pct}%"
                by_type = ", ".join(f"{k}={v}" for k, v in sorted(sumr.by_type.items())) or "-"
                by_compat = ", ".join(
                    f"{k}={v}" for k, v in sorted(sumr.by_compatibility.items())
                ) or "-"
                lines.append(f"- **By type:** {by_type}")
                lines.append(f"- **By Fabric compatibility:** {by_compat}{pct}")
                lines.append("")
            # Top 20 objects ranked by gap_count desc, then by name.
            ranked = sorted(
                pool.code_objects,
                key=lambda o: (-(o.gap_count or 0), o.schema_name or "", o.object_name or ""),
            )[:20]
            lines.append(
                "| Schema | Object | Type | Compatibility | Lines | Params | Gaps |"
            )
            lines.append("|---|---|---|---|---:|---:|---:|")
            for o in ranked:
                lines.append(
                    f"| {o.schema_name} | {o.object_name} | {o.object_type} | "
                    f"{o.compatibility} | {o.line_count if o.line_count is not None else ''} | "
                    f"{o.parameter_count} | {o.gap_count} |"
                )
            lines.append("")

            # T-SQL surface rule rollup mirrors the HTML view -- one row per rule.
            if pool.tsql_surface_gaps:
                rule_agg: dict[str, dict] = {}
                for g in pool.tsql_surface_gaps:
                    rb = rule_agg.setdefault(g.rule_id, {
                        "label": g.label, "severity": g.severity,
                        "fabric_action": g.fabric_action,
                        "findings": 0, "objects": set(),
                    })
                    rb["findings"] += 1
                    rb["objects"].add(g.code_object_id)
                sev_order = {"blocker": 0, "warning": 1, "info": 2}
                lines.append("#### T-SQL surface gaps -- rule rollup")
                lines.append("")
                lines.append("| Rule | Severity | Findings | Objects | Fabric action |")
                lines.append("|---|---|---:|---:|---|")
                for rid, rb in sorted(
                    rule_agg.items(),
                    key=lambda kv: (sev_order.get(kv[1]["severity"], 3), -kv[1]["findings"]),
                ):
                    lines.append(
                        f"| {rb['label']} (`{rid}`) | {rb['severity']} | "
                        f"{rb['findings']} | {len(rb['objects'])} | "
                        f"{rb['fabric_action'] or ''} |"
                    )
                lines.append("")

    path = out_dir / "dedicated_pools.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
