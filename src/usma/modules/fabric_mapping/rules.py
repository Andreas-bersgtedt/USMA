"""Heuristic rules that translate Synapse findings into Fabric Warehouse actions.

Each rule reads a portion of the loaded module JSONs and yields Recommendation
objects. Keep rules small, named, and side-effect-free.
"""
from __future__ import annotations

from typing import Any

from . import tsql_surface
from .models import Recommendation

# Fabric Warehouse default collation. Anything else triggers a recommendation.
FABRIC_DEFAULT_COLLATION = "Latin1_General_100_BIN2_UTF8"

# A T-SQL surface finding on an object that consumed at least this much of the
# pool's observed elapsed time gets promoted to a per-object recommendation.
# Tuned conservatively so we only highlight things that genuinely move the
# needle; the rolled-up rec is still emitted alongside.
HOT_OBJECT_SHARE_PCT = 5.0


def _build_workload_map(top_consumed: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    """Return (lookup, total_elapsed_ms) where lookup maps lower-cased
    object name (qualified or 1-part) to its usage signal.

    The map keys both ``schema.name`` and bare ``name`` so callers can hit it
    with whichever form they have. ``total_elapsed_ms`` is the sum across all
    entries — used to compute the share percentage for an object.
    """
    lookup: dict[str, dict[str, Any]] = {}
    total = 0
    for obj in top_consumed or []:
        elapsed = int(obj.get("elapsed_time_ms") or 0)
        total += elapsed
        name = (obj.get("object_name") or "").strip()
        if not name:
            continue
        entry = {
            "object_name": name,
            "usage_count": int(obj.get("usage_count") or 0),
            "elapsed_time_ms": elapsed,
            "object_type": obj.get("object_type"),
            "match_kind": obj.get("match_kind"),
        }
        lookup[name.lower()] = entry
        if "." in name:
            bare = name.rsplit(".", 1)[-1]
            lookup.setdefault(bare.lower(), entry)
    return lookup, total


def _workload_for_object(
    schema_name: str | None,
    object_name: str | None,
    workload_map: dict[str, dict[str, Any]],
    total_elapsed_ms: int,
) -> tuple[dict[str, Any] | None, float]:
    """Look up ``schema.name`` in the workload map and return (entry, share_pct).

    ``share_pct`` is the fraction of the pool's captured elapsed-time that
    this object accounts for, 0..100. Returns ``(None, 0.0)`` if the object
    has no workload signal or the map is empty.
    """
    if not workload_map or not object_name:
        return None, 0.0
    qualified = f"{schema_name}.{object_name}" if schema_name else object_name
    entry = workload_map.get(qualified.lower()) or workload_map.get(object_name.lower())
    if entry is None or total_elapsed_ms <= 0:
        return entry, 0.0
    share = (entry.get("elapsed_time_ms", 0) / total_elapsed_ms) * 100.0
    return entry, share


def rules_for_dedicated_pools(payload: dict[str, Any]) -> list[Recommendation]:
    out: list[Recommendation] = []
    for pool in payload.get("pools", []):
        inv = pool.get("inventory", {}) or {}
        pool_name = inv.get("name", "?")

        # v2.11 — workload-cache signals are used by several rules below to
        # promote / demote recommendations on objects that are actually hot
        # in production vs. ones that nobody touches.
        workload_map, total_elapsed_ms = _build_workload_map(
            pool.get("top_consumed_objects") or []
        )

        # Paused pools
        if (inv.get("status") or "").lower() == "paused":
            out.append(Recommendation(
                id=f"dp.paused.{pool_name}",
                area="dedicated_pools.inventory",
                title=f"Pool '{pool_name}' is paused",
                severity="warning",
                effort="low",
                target=pool_name,
                detail="DMV-driven analysis was skipped because the pool is paused.",
                fabric_action="Resume the pool and re-run `sma analyze-dedicated-pools` for full analysis.",
                impact="high",
                impact_detail="Paused pools block all downstream sizing, cost and T-SQL surface analysis.",
            ))

        # Collation mismatch
        coll = (inv.get("collation") or "").strip()
        if coll and coll.lower() != FABRIC_DEFAULT_COLLATION.lower():
            out.append(Recommendation(
                id=f"dp.collation.{pool_name}",
                area="dedicated_pools.inventory",
                title=f"Non-default collation '{coll}' on pool '{pool_name}'",
                severity="warning",
                effort="medium",
                target=pool_name,
                detail=f"Fabric Warehouse default is `{FABRIC_DEFAULT_COLLATION}`. "
                       "Migrating data with mismatched collation can introduce sort/comparison "
                       "differences and may require COLLATE clauses on JOINs / WHEREs.",
                fabric_action="Plan column-level COLLATE conversions or pick a Fabric Warehouse with a matching collation.",
                impact="medium",
                impact_detail="Affects every cross-collation JOIN / WHERE post-migration.",
            ))

        # Tables: heuristics
        for t in pool.get("tables", []):
            schema_name = t.get("schema_name")
            table_name = t.get("table_name")
            tname = f"{pool_name}.{schema_name}.{table_name}"
            dist = (t.get("distribution_policy") or "").upper()
            rows = t.get("row_count")
            idx = (t.get("index_type") or "").upper()
            # v2.11 — heuristic #7: also consider physical size when judging
            # "large". Fabric's replicated-table guidance is size-based
            # (~2 GB), not row-based; a wide table with 30M rows can still
            # blow past that.
            data_mb = t.get("data_space_mb") or t.get("reserved_space_mb")
            wl_entry, wl_share = _workload_for_object(
                schema_name, table_name, workload_map, total_elapsed_ms
            )

            if dist == "REPLICATE" and (
                (rows and rows > 50_000_000) or (data_mb and data_mb > 2048)
            ):
                size_hint = (
                    f"~{rows:,} rows" if rows else "row count unknown"
                )
                if data_mb:
                    size_hint += f" / ~{data_mb:,.0f} MB"
                out.append(Recommendation(
                    id=f"dp.replicate_large.{tname}",
                    area="dedicated_pools.tables",
                    title="REPLICATE distribution on a large table",
                    severity="warning",
                    effort="medium",
                    target=tname,
                    detail=f"Table is REPLICATE with {size_hint}; replicated tables in Fabric "
                           "Warehouse should stay small (rule of thumb < 100M rows / < 2 GB).",
                    fabric_action="Re-evaluate as ROUND_ROBIN or HASH-distributed in Fabric Warehouse.",
                    impact=_impact_from_share(wl_share, default="medium"),
                    impact_detail=_share_detail(wl_entry, wl_share),
                ))

            if dist == "ROUND_ROBIN" and rows and rows > 100_000_000:
                out.append(Recommendation(
                    id=f"dp.rr_large.{tname}",
                    area="dedicated_pools.tables",
                    title="Large ROUND_ROBIN table",
                    severity="info",
                    effort="medium",
                    target=tname,
                    detail=f"~{rows:,} rows on ROUND_ROBIN may benefit from a hash key in Fabric.",
                    fabric_action="Pick a high-cardinality, frequently-joined column as the hash key.",
                    impact=_impact_from_share(wl_share, default="low"),
                    impact_detail=_share_detail(wl_entry, wl_share),
                ))

            if idx == "HEAP" and rows and rows > 1_000_000:
                # v2.11 — heuristic #7: bump severity when the heap is also
                # in the hot workload set; demote to "consider drop" when
                # nobody has touched it in the cache window.
                if wl_share >= HOT_OBJECT_SHARE_PCT:
                    sev: str = "warning"
                    detail_extra = (
                        f" This heap is also a hot object "
                        f"({wl_share:.1f}% of pool elapsed time) — fix urgent."
                    )
                    impact_lvl = "high"
                elif workload_map and wl_entry is None:
                    sev = "info"
                    detail_extra = (
                        " No workload activity observed in the 30-day cache "
                        "window — consider dropping before migration."
                    )
                    impact_lvl = "low"
                else:
                    sev = "info"
                    detail_extra = ""
                    impact_lvl = "medium"
                out.append(Recommendation(
                    id=f"dp.heap_large.{tname}",
                    area="dedicated_pools.tables",
                    title="Large heap table",
                    severity=sev,  # type: ignore[arg-type]
                    effort="low",
                    target=tname,
                    detail="Heaps are inefficient for analytical workloads." + detail_extra,
                    fabric_action="Migrate to clustered columnstore (default in Fabric Warehouse).",
                    impact=impact_lvl,  # type: ignore[arg-type]
                    impact_detail=_share_detail(wl_entry, wl_share),
                ))

        # Workload management settings have no direct equivalent in Fabric.
        if pool.get("workload_groups"):
            out.append(Recommendation(
                id=f"dp.wlm.{pool_name}",
                area="dedicated_pools.workload_management",
                title=f"Workload groups defined on '{pool_name}'",
                severity="info",
                effort="medium",
                target=pool_name,
                detail=f"{len(pool['workload_groups'])} workload group(s) configured; "
                       "Fabric uses capacity-level resource governance instead.",
                fabric_action="Map importance/quota intent to Fabric capacity sizing and workspace assignment.",
            ))

        # T-SQL surface scan over stored procedures / views / functions.
        out.extend(_tsql_recommendations(
            pool_name,
            pool.get("code_objects") or [],
            workload_map=workload_map,
            total_elapsed_ms=total_elapsed_ms,
        ))

        # v2 — column collations differing from database default.
        diff_colls = [c for c in (pool.get("column_collations") or []) if c.get("differs_from_db")]
        if diff_colls:
            out.append(Recommendation(
                id=f"dp.col_collation.{pool_name}",
                area="dedicated_pools.column_collations",
                title=f"{len(diff_colls)} column(s) with non-default collation on '{pool_name}'",
                severity="warning",
                effort="medium",
                target=pool_name,
                detail="Column-level collations differ from the database default; cross-collation joins"
                       " require explicit COLLATE clauses in Fabric Warehouse.",
                fabric_action="Standardize on the Fabric default collation or stamp explicit COLLATE on JOIN/WHERE.",
            ))

        # v2 — materialized views (Fabric Warehouse has no MV).
        mvs = pool.get("materialized_views") or []
        if mvs:
            out.append(Recommendation(
                id=f"dp.mvs.{pool_name}",
                area="dedicated_pools.materialized_views",
                title=f"{len(mvs)} materialized view(s) on '{pool_name}'",
                severity="warning",
                effort="high",
                target=pool_name,
                detail="Fabric Warehouse does not yet support materialized views; alternatives include"
                       " Lakehouse + scheduled notebook refresh, or a regular table refreshed via pipeline.",
                fabric_action="Recreate as Lakehouse Delta tables refreshed by a scheduled Fabric pipeline / notebook.",
            ))

        # v2 — stale statistics. v2.11: also flag stats whose modification
        # counter exceeds 10% of row count — those are stale regardless of
        # the wall-clock age and matter more for query planning.
        stats_all = pool.get("statistics") or []
        stats_aged = [s for s in stats_all if (s.get("days_since_update") or 0) > 14]
        stats_churned = [
            s for s in stats_all
            if (s.get("row_count") or 0) > 0
            and (s.get("modification_counter") or 0) / (s.get("row_count") or 1) > 0.10
        ]
        stats_problematic = {
            (s.get("schema_name"), s.get("object_name"), s.get("name")): s
            for s in stats_aged + stats_churned
        }
        if stats_problematic:
            n_aged = len(stats_aged)
            n_churned = len(stats_churned)
            out.append(Recommendation(
                id=f"dp.stats_stale.{pool_name}",
                area="dedicated_pools.statistics",
                title=f"{len(stats_problematic)} stale / high-churn statistic(s) on '{pool_name}'",
                severity="info",
                effort="low",
                target=pool_name,
                detail=(
                    f"Detected {n_aged} stat(s) >14 days old and {n_churned} stat(s) "
                    "with modification_counter > 10% of row count; both indicate stale "
                    "plans that will carry over into Fabric."
                ),
                fabric_action="Run UPDATE STATISTICS on the affected objects in Synapse before the cut-over snapshot.",
                impact="low" if n_churned == 0 else "medium",
                impact_detail=(
                    f"{n_churned} high-churn stat(s) affect plan quality for active workloads."
                    if n_churned else "Wall-clock age only; minimal impact if tables are static."
                ),
            ))

        # v2 — distribution candidates (advisor output). v2.11: cross-reference
        # with the workload cache so hot candidates get promoted to warning-
        # level recommendations with explicit workload evidence.
        cands = pool.get("distribution_candidates") or []
        if cands:
            top = cands[:5]
            sample = ", ".join(
                f"{c.get('schema_name')}.{c.get('table_name')}→{c.get('column_name')}"
                for c in top
            )
            hot_cands = []
            for c in cands:
                entry, share = _workload_for_object(
                    c.get("schema_name"), c.get("table_name"),
                    workload_map, total_elapsed_ms,
                )
                if share >= HOT_OBJECT_SHARE_PCT:
                    hot_cands.append((c, share, entry))
            out.append(Recommendation(
                id=f"dp.dist_advice.{pool_name}",
                area="dedicated_pools.distribution_advisor",
                title=f"Distribution-key candidates suggested for '{pool_name}'",
                severity="info",
                effort="medium",
                target=pool_name,
                detail=f"Top candidates (heuristic): {sample}",
                fabric_action="Review the candidates against query patterns; pick a high-cardinality, frequently-joined column.",
                impact="medium" if hot_cands else "low",
                impact_detail=(
                    f"{len(hot_cands)} candidate(s) are also hot workload objects."
                    if hot_cands else "No candidates intersect with the observed workload."
                ),
            ))
            for c, share, _entry in hot_cands[:5]:
                tname = f"{pool_name}.{c.get('schema_name')}.{c.get('table_name')}"
                out.append(Recommendation(
                    id=f"dp.dist_advice_hot.{tname}",
                    area="dedicated_pools.distribution_advisor",
                    title=(
                        f"Hot table {c.get('schema_name')}.{c.get('table_name')} "
                        f"({share:.1f}% pool time) needs hash key in Fabric"
                    ),
                    severity="warning",
                    effort="medium",
                    target=tname,
                    detail=(
                        f"Advisor suggests hashing on `{c.get('column_name')}`; this table accounts "
                        f"for {share:.1f}% of observed pool elapsed time, so a poor distribution choice "
                        "will dominate Fabric performance."
                    ),
                    fabric_action=(
                        f"Validate `{c.get('column_name')}` against actual query patterns before "
                        "creating the Fabric table; high cardinality + frequent JOIN key wins."
                    ),
                    impact="high",
                    impact_detail=f"{share:.1f}% of pool elapsed time.",
                ))

    return out


# --------------------------------------------------------------------------- #
# Helper formatters used by the dedicated-pools rules above.
# --------------------------------------------------------------------------- #

def _impact_from_share(share_pct: float, *, default: str) -> str:
    """Map workload-share to the business-impact axis. Caller supplies the
    fallback used when there is no workload signal at all."""
    if share_pct >= HOT_OBJECT_SHARE_PCT * 2:
        return "high"
    if share_pct >= HOT_OBJECT_SHARE_PCT:
        return "medium"
    if share_pct > 0:
        return "low"
    return default


def _share_detail(entry: dict[str, Any] | None, share_pct: float) -> str | None:
    if entry is None:
        return None
    if share_pct > 0:
        return (
            f"{share_pct:.1f}% of observed pool elapsed time, "
            f"{entry.get('usage_count', 0)} requests in 30-day cache."
        )
    return f"{entry.get('usage_count', 0)} request(s) observed in 30-day cache."


def _tsql_recommendations(
    pool_name: str,
    code_objects: list[dict[str, Any]],
    *,
    workload_map: dict[str, dict[str, Any]] | None = None,
    total_elapsed_ms: int = 0,
) -> list[Recommendation]:
    """Aggregate T-SQL findings into one recommendation per matched rule.

    When workload-cache data is supplied (v2.11+), additionally emit a
    per-object ``blocker``-severity recommendation for any finding whose
    object accounts for >= ``HOT_OBJECT_SHARE_PCT`` of the pool's elapsed
    time. The rolled-up rule recommendation is still emitted alongside
    so existing reports and tests don't regress.
    """
    if not code_objects:
        return []
    workload_map = workload_map or {}

    pairs: list[tuple[str, dict[str, Any], list[tsql_surface.TsqlFinding]]] = []
    for obj in code_objects:
        # Prefer the stable code_object_id (deterministic across runs); fall back to the
        # qualified name for older payloads that predate the v2 schema.
        cid = obj.get("code_object_id") or (
            f"{obj.get('schema_name')}.{obj.get('object_name')}.{obj.get('object_type')}"
        )
        pairs.append((cid, obj, tsql_surface.scan(obj.get("definition") or "")))

    grouped = tsql_surface.aggregate_by_rule(
        [(cid, findings) for cid, _obj, findings in pairs]
    )
    out: list[Recommendation] = []
    for rule_id, hits in grouped.items():
        # Severity is fixed per rule in tsql_surface._PATTERNS; pull it from the first finding.
        severity = "info"
        label = rule_id
        for _, _obj, findings in pairs:
            for f in findings:
                if f.rule_id == rule_id:
                    severity = f.severity
                    label = f.label
                    break
            if severity != "info":
                break
        sample = ", ".join(t for t, _ in hits[:5])
        more = "" if len(hits) <= 5 else f" (+{len(hits) - 5} more)"
        all_ids = sorted({t for t, _ in hits})

        # v2.11 — compute the aggregate workload share across the matched
        # objects so the rolled-up recommendation also carries an impact axis.
        agg_share = 0.0
        hot_objs: list[tuple[str, dict[str, Any], float]] = []
        for cid, obj, findings in pairs:
            if not any(f.rule_id == rule_id for f in findings):
                continue
            _entry, share = _workload_for_object(
                obj.get("schema_name"), obj.get("object_name"),
                workload_map, total_elapsed_ms,
            )
            agg_share += share
            if share >= HOT_OBJECT_SHARE_PCT:
                hot_objs.append((cid, obj, share))

        out.append(Recommendation(
            id=f"dp.tsql.{rule_id}.{pool_name}",
            area="dedicated_pools.tsql_surface",
            title=f"T-SQL surface: {label} ({len(hits)} object(s))",
            severity=severity,  # type: ignore[arg-type]
            effort="medium",
            target=pool_name,
            detail=f"Detected `{label}` in: {sample}{more}. "
                   f"Stable code_object_ids: {', '.join(all_ids[:10])}"
                   f"{'' if len(all_ids) <= 10 else f' (+{len(all_ids) - 10} more)'}.",
            fabric_action=tsql_surface.fabric_action_for(rule_id),
            impact=_impact_from_share(agg_share, default="unknown"),
            impact_detail=(
                f"Matched objects account for {agg_share:.1f}% of observed pool elapsed time."
                if agg_share > 0 else None
            ),
        ))

        # v2.11 — promote each hot object to its own blocker-severity rec.
        # Severity for the per-object rec is at least "warning"; bumped to
        # "blocker" for rules that are already warning/blocker in the
        # underlying pattern table.
        for cid, obj, share in hot_objs:
            per_sev = "blocker" if severity in ("warning", "blocker") else "warning"
            qual = f"{obj.get('schema_name')}.{obj.get('object_name')}"
            out.append(Recommendation(
                id=f"dp.tsql.{rule_id}.{pool_name}.hot.{cid}",
                area="dedicated_pools.tsql_surface",
                title=f"Hot {qual}: {label} ({share:.1f}% pool time)",
                severity=per_sev,  # type: ignore[arg-type]
                effort="medium",
                target=cid,
                detail=(
                    f"`{label}` detected in `{qual}` which accounts for {share:.1f}% of "
                    "observed pool elapsed time in the 30-day workload cache. Fix this object "
                    "before broader cutover — it will dominate the Fabric performance "
                    "baseline."
                ),
                fabric_action=tsql_surface.fabric_action_for(rule_id),
                impact="high",
                impact_detail=f"{share:.1f}% of pool elapsed time; code_object_id={cid}.",
            ))
    return out


def rules_for_serverless(payload: dict[str, Any]) -> list[Recommendation]:
    out: list[Recommendation] = []
    ext_tables = payload.get("external_tables") or []
    if ext_tables:
        out.append(Recommendation(
            id="sl.external_tables",
            area="serverless_pools.external_tables",
            title=f"{len(ext_tables)} external table(s) in serverless",
            severity="info",
            effort="medium",
            detail="Serverless external tables become Fabric shortcuts or OneLake-backed tables.",
            fabric_action="Recreate as OneLake shortcuts or COPY INTO Fabric Warehouse tables.",
        ))

    cost = payload.get("cost_estimate") or {}
    if cost and cost.get("estimated_cost_usd") is not None:
        out.append(Recommendation(
            id="sl.cost_baseline",
            area="serverless_pools.cost",
            title=f"Serverless data-processed baseline ~ {cost.get('total_data_processed_tb')} TB / "
                  f"USD {cost.get('estimated_cost_usd')} (last 30 d)",
            severity="info",
            effort="low",
            detail="Use this as a baseline when sizing Fabric capacity (Fabric is capacity-priced, not per-TB).",
            fabric_action="Translate observed TB processed into expected Fabric capacity SKU/sizing.",
        ))

    top = payload.get("top_queries") or []
    if top:
        biggest = top[0]
        out.append(Recommendation(
            id="sl.top_query",
            area="serverless_pools.queries",
            title=f"Most expensive serverless query: {biggest.get('data_processed_mb')} MB scanned",
            severity="info",
            effort="medium",
            detail=f"Top query (request_id={biggest.get('request_id')}) scanned "
                   f"{biggest.get('data_processed_mb')} MB; review for partition pruning / filtered shortcuts.",
            fabric_action="Add columnar shortcuts and verify partition predicates on the most expensive queries.",
        ))

    # v2 — per-storage-account attribution.
    sau = payload.get("storage_account_usage") or []
    if sau:
        head = sau[:3]
        sample = ", ".join(f"{a.get('storage_account')} ({a.get('data_processed_mb')} MB)" for a in head)
        out.append(Recommendation(
            id="sl.storage_attribution",
            area="serverless_pools.storage_attribution",
            title=f"Top storage account(s) by serverless scan: {sample}",
            severity="info",
            effort="low",
            detail="Storage attribution helps prioritize which datasets to migrate to OneLake first.",
            fabric_action="Migrate the heaviest accounts first via OneLake shortcuts, then optionally COPY INTO Fabric Warehouse.",
        ))

    # v2 — external table column projections.
    cols = payload.get("external_table_columns") or []
    if cols:
        out.append(Recommendation(
            id="sl.external_columns",
            area="serverless_pools.external_table_columns",
            title=f"{len(cols)} external-table column projection(s) captured",
            severity="info",
            effort="low",
            detail="Column projections inform Fabric Lakehouse / Warehouse schema design.",
            fabric_action="Use the captured projections to scaffold Fabric Lakehouse Delta schemas.",
        ))
    return out


def rules_for_spark(payload: dict[str, Any]) -> list[Recommendation]:
    out: list[Recommendation] = []
    pools = payload.get("pools") or []
    if pools:
        out.append(Recommendation(
            id="sp.spark_inventory",
            area="spark_pools",
            title=f"{len(pools)} Spark pool(s) detected",
            severity="info",
            effort="medium",
            detail="Synapse Spark pools migrate to Fabric Spark (Data Engineering / Data Science).",
            fabric_action="Map node sizes to Fabric Spark pool defaults; review notebooks for unsupported APIs.",
        ))

    notebooks = payload.get("notebooks") or []
    if notebooks:
        out.append(Recommendation(
            id="sp.notebooks",
            area="spark_pools.notebooks",
            title=f"{len(notebooks)} Synapse notebook(s) to migrate",
            severity="info",
            effort="high",
            detail="Notebooks need import + smoke-test in Fabric Notebooks; review %%-magics and mssparkutils calls.",
            fabric_action="Use the Fabric notebook import; replace `mssparkutils` with `notebookutils`.",
        ))

    sjds = payload.get("spark_job_definitions") or []
    if sjds:
        out.append(Recommendation(
            id="sp.spark_job_defs",
            area="spark_pools.spark_job_definitions",
            title=f"{len(sjds)} Spark job definition(s) to recreate",
            severity="warning",
            effort="medium",
            detail="Synapse Spark job definitions migrate to Fabric Spark job definitions but config keys may differ.",
            fabric_action="Recreate job definitions in Fabric; re-validate JAR / Python file paths and Spark conf.",
        ))

    # v2 — notebook lint findings.
    findings = payload.get("notebook_lint_findings") or []
    if findings:
        by_rule: dict[str, list[str]] = {}
        sev_by_rule: dict[str, str] = {}
        for f in findings:
            by_rule.setdefault(f.get("rule_id", "?"), []).append(f.get("notebook", "?"))
            sev_by_rule.setdefault(f.get("rule_id", "?"), f.get("severity", "info"))
        for rule_id, hits in by_rule.items():
            sev = sev_by_rule.get(rule_id, "info")
            sample = ", ".join(sorted(set(hits))[:5])
            out.append(Recommendation(
                id=f"sp.lint.{rule_id}",
                area="spark_pools.notebooks.lint",
                title=f"Notebook lint: {rule_id} ({len(hits)} hit(s))",
                severity=sev,  # type: ignore[arg-type]
                effort="medium",
                detail=f"Found in: {sample}",
                fabric_action="Refactor to Fabric notebookutils equivalents and remove Synapse-only magics.",
            ))

    # v2 — runtime upgrade hints.
    runtimes = payload.get("runtime_mappings") or []
    needs_upgrade = [r for r in runtimes if (r.get("status") or "") in ("upgrade", "deprecated")]
    if needs_upgrade:
        sample = ", ".join(f"{r.get('pool_name')} ({r.get('synapse_version')})" for r in needs_upgrade[:5])
        out.append(Recommendation(
            id="sp.runtime_upgrade",
            area="spark_pools.runtime_compat",
            title=f"{len(needs_upgrade)} pool(s) need a Fabric runtime upgrade",
            severity="warning",
            effort="medium",
            detail=f"Pools requiring upgrade: {sample}",
            fabric_action="Pin notebooks to a supported Fabric Spark runtime and validate library compatibility.",
        ))
    return out


def rules_for_pipelines(
    payload: dict[str, Any],
    *,
    source_type: str | None = None,
) -> list[Recommendation]:
    """Pipeline recommendations.

    ``source_type`` ("synapse_workspace" / "adf" / ...) is used to phrase the
    inventory recommendation correctly: Synapse pipelines migrate to Fabric
    Data pipelines, but so do ADF pipelines and the wording differs. When
    ``None`` (legacy callers) we fall back to the historical Synapse wording.
    """
    out: list[Recommendation] = []
    pipelines = payload.get("pipelines") or []
    activities = payload.get("activities") or []
    linked = payload.get("linked_services") or []

    if pipelines:
        if source_type == "adf":
            inv_title = f"{len(pipelines)} Azure Data Factory pipeline(s) to migrate"
            inv_detail = "Azure Data Factory pipelines migrate to Fabric Data Factory (Data pipelines)."
        else:
            inv_title = f"{len(pipelines)} Synapse pipeline(s) to migrate"
            inv_detail = "Synapse pipelines migrate to Fabric Data Factory (Data pipelines)."
        out.append(Recommendation(
            id="pl.pipelines_inventory",
            area="pipelines",
            title=inv_title,
            severity="info",
            effort="high",
            detail=inv_detail,
            fabric_action="Use the Fabric pipeline import tooling; review unsupported activities and linked services.",
        ))

    # Activity-level Fabric-unsupported detection.
    unsupported = [a for a in activities if (a.get("support") == "unsupported")]
    if unsupported:
        per_type: dict[str, list[str]] = {}
        for a in unsupported:
            per_type.setdefault(a["type"], []).append(f"{a['pipeline']}::{a['name']}")
        for atype, hits in per_type.items():
            sample = ", ".join(hits[:5])
            more = "" if len(hits) <= 5 else f" (+{len(hits) - 5} more)"
            out.append(Recommendation(
                id=f"pl.unsupported.{atype}",
                area="pipelines.activities",
                title=f"Fabric-unsupported activity: {atype} ({len(hits)} occurrence(s))",
                severity="warning",
                effort="high",
                target=atype,
                detail=f"Found in: {sample}{more}.",
                fabric_action=_action_for_activity(atype),
            ))

    partial = [a for a in activities if (a.get("support") == "partial")]
    if partial:
        out.append(Recommendation(
            id="pl.partial_activities",
            area="pipelines.activities",
            title=f"{len(partial)} partially-supported activity occurrence(s)",
            severity="info",
            effort="medium",
            detail="These activities exist in Fabric but configuration / auth options differ.",
            fabric_action="Re-validate parameters, authentication, and runtime behavior after import.",
        ))

    # Linked service compatibility.
    unsupported_ls = [ls for ls in linked if ls.get("fabric_supported") is False]
    if unsupported_ls:
        per_type = {}
        for ls in unsupported_ls:
            per_type.setdefault(ls.get("type", "?"), []).append(ls.get("name", "?"))
        for ls_type, hits in per_type.items():
            out.append(Recommendation(
                id=f"pl.unsupported_ls.{ls_type}",
                area="pipelines.linked_services",
                title=f"Fabric-unsupported linked service type: {ls_type} ({len(hits)})",
                severity="warning",
                effort="high",
                target=ls_type,
                detail=f"Linked services: {', '.join(hits[:5])}{'' if len(hits) <= 5 else ' (+more)'}",
                fabric_action="Replace with a supported Fabric connector or implement via Spark notebooks.",
            ))

    self_hosted = [ir for ir in (payload.get("integration_runtimes") or [])
                   if (ir.get("type") or "").lower().startswith("self")]
    if self_hosted:
        out.append(Recommendation(
            id="pl.self_hosted_ir",
            area="pipelines.integration_runtimes",
            title=f"{len(self_hosted)} self-hosted integration runtime(s)",
            severity="warning",
            effort="medium",
            detail="Self-hosted IRs need an equivalent gateway in Fabric (on-premises data gateway).",
            fabric_action="Provision an on-premises data gateway and re-point linked connections.",
        ))

    # Run-history derived signals.
    run_history = payload.get("run_history")
    if isinstance(run_history, dict):
        out.extend(_rules_for_run_history(run_history))

    # v2 — expression-language compatibility findings.
    expr = payload.get("expression_findings") or []
    if expr:
        by_rule: dict[str, list[str]] = {}
        sev_by_rule: dict[str, str] = {}
        for f in expr:
            by_rule.setdefault(f.get("rule_id", "?"), []).append(
                f"{f.get('pipeline')}::{f.get('activity')}"
            )
            sev_by_rule.setdefault(f.get("rule_id", "?"), f.get("severity", "info"))
        for rule_id, hits in by_rule.items():
            sample = ", ".join(sorted(set(hits))[:5])
            out.append(Recommendation(
                id=f"pl.expr.{rule_id}",
                area="pipelines.expressions",
                title=f"Expression compat: {rule_id} ({len(hits)} hit(s))",
                severity=sev_by_rule.get(rule_id, "info"),  # type: ignore[arg-type]
                effort="medium",
                detail=f"Found in: {sample}",
                fabric_action="Adjust expressions to Fabric Data Factory equivalents (system vars, secrets, getMetadata).",
            ))

    # v2 — trigger schedule mappings (informational).
    sched = payload.get("schedule_mappings") or []
    if sched:
        out.append(Recommendation(
            id="pl.schedule_mappings",
            area="pipelines.triggers",
            title=f"{len(sched)} trigger schedule(s) mapped to Fabric equivalents",
            severity="info",
            effort="low",
            detail="Trigger payloads were translated into Fabric scheduling primitives where possible.",
            fabric_action="Recreate the schedules in Fabric Data Factory using the captured cadence/start time.",
        ))
    return out


def _action_for_activity(activity_type: str) -> str:
    return _ACTIVITY_ACTIONS.get(activity_type, "No direct equivalent in Fabric — refactor or replace.")


def _rules_for_run_history(run_history: dict[str, Any]) -> list[Recommendation]:
    """Surface Fabric-relevant signals from observed pipeline run statistics.

    - Idle pipelines (no runs in the longest window): may not need migration now.
    - Low success rate (28d window) on terminal runs: investigate before cutover.
    - Heavy data movers (avg MB/run in 28d window) get a sizing hint.
    """
    out: list[Recommendation] = []
    by_pipeline = run_history.get("by_pipeline") or []
    if not by_pipeline:
        return out

    idle_30d: list[str] = []
    idle_7d: list[str] = []
    low_sr: list[tuple[str, float, int]] = []
    chronic: list[tuple[str, float, int]] = []
    heavy: list[tuple[str, float]] = []

    for stats in by_pipeline:
        pname = stats.get("pipeline") or "?"
        windows = {w.get("window_days"): w for w in (stats.get("windows") or [])}
        widest = max(windows) if windows else None
        wide_w = windows.get(widest) if widest is not None else None
        w28 = windows.get(28) or wide_w
        w7 = windows.get(7)
        if wide_w is not None and (wide_w.get("run_count") or 0) == 0:
            # v2.11 — heuristic #7: 30d-idle is a retire candidate; 7d-idle
            # without 30d-idle just means the pipeline is weekly/monthly.
            if widest >= 28:
                idle_30d.append(pname)
            else:
                idle_7d.append(pname)
            continue
        if w28 is not None:
            sr = w28.get("success_rate")
            terminal = (w28.get("succeeded") or 0) + (w28.get("failed") or 0)
            if sr is not None and terminal >= 5:
                if sr < 0.50:
                    # Chronic failure: this almost certainly broke and
                    # nobody's fixed it; treat as a blocker.
                    chronic.append((pname, sr, terminal))
                elif sr < 0.95:
                    low_sr.append((pname, sr, terminal))
            avg_mb = w28.get("avg_data_moved_mb_per_run")
            if avg_mb is not None and avg_mb >= 1024:  # >= 1 GB / run
                heavy.append((pname, avg_mb))
        # 7-day idle within an active 28-day pipeline is informational only
        # and not surfaced separately to avoid noise.
        _ = w7

    if idle_30d:
        sample = ", ".join(idle_30d[:5])
        more = "" if len(idle_30d) <= 5 else f" (+{len(idle_30d) - 5} more)"
        out.append(Recommendation(
            id="pl.runs.idle",
            area="pipelines.runs",
            title=f"{len(idle_30d)} pipeline(s) idle 30+ days — retire candidates",
            severity="info",
            effort="low",
            detail=f"No runs observed in the longest window: {sample}{more}.",
            fabric_action=(
                "Confirm with owners whether these pipelines are still in use; "
                "retire before migration to reduce scope."
            ),
            impact="medium",
            impact_detail="Cuts migration scope at zero risk — retire instead of port.",
        ))
    if idle_7d:
        sample = ", ".join(idle_7d[:5])
        out.append(Recommendation(
            id="pl.runs.idle_7d",
            area="pipelines.runs",
            title=f"{len(idle_7d)} pipeline(s) idle in the last 7 days only",
            severity="info",
            effort="low",
            detail=(
                f"No runs in the 7-day window for: {sample}. Could be weekly / "
                "monthly schedules — not necessarily retired."
            ),
            fabric_action="Check the schedule cadence before classifying as inactive.",
            impact="low",
            impact_detail="Likely weekly / monthly cadence; confirm before action.",
        ))

    for pname, sr, terminal in sorted(chronic, key=lambda x: x[1]):
        out.append(Recommendation(
            id=f"pl.runs.chronic_failure.{pname}",
            area="pipelines.runs",
            title=f"Pipeline {pname}: chronic failure {sr * 100:.1f}% success (28d, n={terminal})",
            severity="blocker",
            effort="high",
            target=pname,
            detail=(
                "Success rate below 50% over the last 28 days — this looks broken, "
                "not flaky."
            ),
            fabric_action=(
                "Stop migration of this pipeline until root cause is fixed; broken pipelines "
                "will be even harder to diagnose after cutover."
            ),
            impact="high",
            impact_detail=f"Only {sr * 100:.0f}% of {terminal} runs succeeded.",
        ))

    for pname, sr, terminal in sorted(low_sr, key=lambda x: x[1]):
        out.append(Recommendation(
            id=f"pl.runs.low_success.{pname}",
            area="pipelines.runs",
            title=f"Pipeline {pname}: {sr * 100:.1f}% success (28d, n={terminal})",
            severity="warning",
            effort="medium",
            target=pname,
            detail="Failure rate exceeds 5% over the last 28 days; flaky rather than broken.",
            fabric_action=(
                "Investigate root cause and stabilize before migration; failures will "
                "carry over and may be harder to diagnose post-cutover."
            ),
            impact="medium",
            impact_detail=f"{(1 - sr) * 100:.0f}% failure rate across {terminal} runs.",
        ))

    if heavy:
        # Single rollup recommendation; details list the offenders.
        items = "; ".join(f"{p} ({mb:,.0f} MB/run)" for p, mb in
                          sorted(heavy, key=lambda x: -x[1])[:5])
        more = "" if len(heavy) <= 5 else f" (+{len(heavy) - 5} more)"
        out.append(Recommendation(
            id="pl.runs.heavy_data_movement",
            area="pipelines.runs",
            title=f"{len(heavy)} pipeline(s) move >=1 GB / run on average",
            severity="info",
            effort="medium",
            detail=f"Top offenders: {items}{more}.",
            fabric_action=(
                "Factor sustained data movement into Fabric capacity sizing and "
                "consider staging copies into OneLake to reduce egress."
            ),
            impact="medium",
            impact_detail=f"{len(heavy)} pipeline(s) drive sustained data movement — sizes capacity.",
        ))
    return out


_ACTIVITY_ACTIONS: dict[str, str] = {
    "ExecuteDataFlow": "Rebuild the mapping data flow as a Fabric Dataflow Gen2 or Spark notebook.",
    "ExecuteWranglingDataflow": "Rebuild as a Fabric Dataflow Gen2 (Power Query online).",
    "ExecuteSSISPackage": "No SSIS-IR in Fabric — re-implement in Fabric Data Factory pipelines or Spark.",
    "Custom": "Custom .NET on Azure Batch is not supported — re-implement in Spark notebooks or Functions.",
    "HDInsightHive": "Re-implement using Fabric Spark.",
    "HDInsightPig": "Re-implement using Fabric Spark.",
    "HDInsightMapReduce": "Re-implement using Fabric Spark.",
    "HDInsightStreaming": "Re-implement using Fabric Eventstream / Spark Structured Streaming.",
    "HDInsightSpark": "Migrate to Fabric Spark notebooks / job definitions.",
    "AzureMLBatchExecution": "Move to Azure ML v2 (call from a Web/Webhook activity) or Fabric Data Science.",
    "AzureMLUpdateResource": "No Fabric equivalent — orchestrate AML v2 via REST.",
    "AzureMLExecutePipeline": "Move to Azure ML v2 pipelines invoked via REST.",
    "DataLakeAnalyticsU-SQL": "ADLA is retired — re-implement in Fabric Spark or Warehouse.",
}


def rules_for_monitoring(payload: dict[str, Any]) -> list[Recommendation]:
    """Translate observed DWU usage into Fabric capacity sizing hints."""
    out: list[Recommendation] = []
    series = payload.get("series") or []
    if not series:
        return out

    # Group DWU-percent series by pool and look at p95.
    by_pool: dict[str, dict[str, dict[str, Any]]] = {}
    for s in series:
        by_pool.setdefault(s["resource_name"], {})[s["metric_name"]] = s

    for pool, metrics in by_pool.items():
        used_pct = metrics.get("DWUUsedPercent") or {}
        p95 = used_pct.get("p95_value")
        avg = used_pct.get("avg_value")
        if p95 is None and avg is None:
            continue
        # v2.11 — heuristic #7: distinguish "bursty" (high p95 but low
        # average) from "sustained heavy" so we don't push customers into
        # oversized Fabric capacity. Bursty pools are great candidates for
        # autoscale / scheduled scale.
        bursty = (
            p95 is not None and avg is not None and p95 > 85 and avg < 40
        )
        if bursty:
            sev: str = "info"
            hint = (
                "Pool is bursty (high p95, low average); right-size Fabric capacity to the "
                "sustained load and use scheduled scale or autoscale to handle peaks."
            )
            impact_lvl = "medium"
            impact_detail = (
                f"p95={p95} % vs avg={avg} % — sustained load is far below peak."
            )
        elif p95 is not None and p95 < 30:
            sev, hint = (
                "info",
                "Pool is under-utilized; consider downsizing or migrating to a small Fabric capacity.",
            )
            impact_lvl = "low"
            impact_detail = f"p95={p95} % — sustained underuse, savings opportunity."
        elif p95 is not None and p95 > 85:
            sev, hint = (
                "warning",
                "Pool is heavily utilized; size Fabric capacity above current SKU and validate concurrency.",
            )
            impact_lvl = "high"
            impact_detail = f"p95={p95} % — sustained heavy load."
        else:
            sev, hint = (
                "info",
                "Pool utilization is moderate; pick Fabric capacity at or slightly above current SKU.",
            )
            impact_lvl = "low"
            impact_detail = f"p95={p95} %, avg={avg} % — moderate, no urgent change."
        out.append(Recommendation(
            id=f"mon.dwu.{pool}",
            area="monitoring.dwu",
            title=f"DWU usage on '{pool}': p95={p95}, avg={avg}" + (
                " (bursty)" if bursty else ""
            ),
            severity=sev,  # type: ignore[arg-type]
            effort="low",
            target=pool,
            detail=f"Last-window DWUUsedPercent p95={p95}, avg={avg}.",
            fabric_action=hint,
            impact=impact_lvl,  # type: ignore[arg-type]
            impact_detail=impact_detail,
        ))

        failed = metrics.get("ConnectionsBlockedByFirewall") or {}
        if (failed.get("max_value") or 0) > 0:
            out.append(Recommendation(
                id=f"mon.failed_conn.{pool}",
                area="monitoring.connections",
                title=f"Connections blocked by firewall on '{pool}'",
                severity="warning",
                effort="low",
                target=pool,
                detail=f"Max ConnectionsBlockedByFirewall in window: {failed.get('max_value')}.",
                fabric_action="Investigate firewall / auth issues before migration; replicate firewall rules in Fabric.",
            ))

    return out


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# v2.11 — storage module rules. The storage analyzer collects ADLS account
# inventory, capacity, and per-pool storage usage; until now none of that was
# surfaced as a Fabric mapping recommendation.
# --------------------------------------------------------------------------- #


# Default: warn when a dedicated pool is >80% full and block at >95%.
POOL_STORAGE_WARN_PCT = 80.0
POOL_STORAGE_BLOCK_PCT = 95.0


def rules_for_storage(payload: dict[str, Any]) -> list[Recommendation]:
    """Turn storage analyzer output into Fabric capacity / cleanup hints."""
    out: list[Recommendation] = []

    # Pool storage — headroom warnings.
    for pool_stg in payload.get("dedicated_pool_storage") or []:
        pname = pool_stg.get("pool_name") or "?"
        used_pct = pool_stg.get("used_pct_of_max")
        reserved_gb = pool_stg.get("reserved_space_gb")
        max_gb = pool_stg.get("max_size_gb")
        if used_pct is not None and used_pct >= POOL_STORAGE_BLOCK_PCT:
            out.append(Recommendation(
                id=f"st.pool_full.{pname}",
                area="storage.dedicated_pool",
                title=f"Pool '{pname}' is {used_pct:.0f}% of max storage",
                severity="blocker",
                effort="medium",
                target=pname,
                detail=(
                    f"Reserved {reserved_gb:.0f} GB of {max_gb:.0f} GB max — the pool is "
                    "effectively out of space and may fail loads."
                ),
                fabric_action=(
                    "Free space or raise pool max size before migration snapshots; size the "
                    "Fabric Warehouse with explicit headroom for growth."
                ),
                impact="high",
                impact_detail=f"{reserved_gb:.0f} GB / {max_gb:.0f} GB used.",
            ))
        elif used_pct is not None and used_pct >= POOL_STORAGE_WARN_PCT:
            out.append(Recommendation(
                id=f"st.pool_high.{pname}",
                area="storage.dedicated_pool",
                title=f"Pool '{pname}' is {used_pct:.0f}% of max storage",
                severity="warning",
                effort="low",
                target=pname,
                detail=(
                    f"Reserved {reserved_gb:.0f} GB of {max_gb:.0f} GB max; plan headroom "
                    "for the Fabric Warehouse."
                ),
                fabric_action="Size the Fabric Warehouse with at least 25% growth headroom.",
                impact="medium",
                impact_detail=f"{reserved_gb:.0f} GB / {max_gb:.0f} GB used.",
            ))
        if reserved_gb is not None and reserved_gb > 0:
            # Always emit an informational sizing baseline so the runbook
            # phase "Foundation" can reference an actual GB number.
            out.append(Recommendation(
                id=f"st.pool_size_baseline.{pname}",
                area="storage.dedicated_pool",
                title=f"Pool '{pname}' reserved storage baseline: {reserved_gb:.1f} GB",
                severity="info",
                effort="low",
                target=pname,
                detail=(
                    f"Reserved {reserved_gb:.1f} GB across {pool_stg.get('table_count', 0)} tables; "
                    "use this as the lower bound when sizing the Fabric Warehouse capacity."
                ),
                fabric_action="Map reserved GB to Fabric Warehouse storage estimates with headroom.",
                impact="low",
                impact_detail="Sizing baseline for capacity planning.",
            ))

    # Storage account inventory — surface the workspace-default account so the
    # runbook can call out OneLake shortcut targets explicitly.
    defaults = [a for a in (payload.get("accounts") or []) if a.get("is_workspace_default")]
    if defaults:
        sample = ", ".join(a.get("name", "?") for a in defaults)
        out.append(Recommendation(
            id="st.workspace_default",
            area="storage.accounts",
            title=f"Workspace default storage account(s): {sample}",
            severity="info",
            effort="low",
            detail=(
                "The Synapse workspace default storage account is the first candidate for "
                "a OneLake shortcut so existing pipelines keep working post-migration."
            ),
            fabric_action="Create OneLake shortcuts to the default container(s) before retiring the workspace.",
            impact="medium",
            impact_detail="Default account commonly stores hot Lakehouse data.",
        ))

    # ADLS account capacities — useful as a sizing baseline even when no pool
    # storage was captured.
    big_accts: list[tuple[str, float]] = []
    for cap in payload.get("capacities") or []:
        gb = cap.get("blob_capacity_gb") or cap.get("used_capacity_gb") or 0
        if gb and gb > 100:
            big_accts.append((cap.get("account_name", "?"), gb))
    if big_accts:
        big_accts.sort(key=lambda x: -x[1])
        sample = ", ".join(f"{n} ({g:.0f} GB)" for n, g in big_accts[:5])
        out.append(Recommendation(
            id="st.large_accounts",
            area="storage.accounts",
            title=f"{len(big_accts)} storage account(s) over 100 GB",
            severity="info",
            effort="low",
            detail=f"Largest: {sample}.",
            fabric_action="Plan OneLake shortcut order — hottest / largest accounts first.",
            impact="medium",
            impact_detail="Drives OneLake shortcut prioritisation.",
        ))

    return out
# ---------------------------------------------------------------------------
# Databricks workflows (Phase 4 Slice 4-C)
# ---------------------------------------------------------------------------

# Spark major-version strings considered Fabric-eligible for the
# db.cluster_runtime rule. Older runtimes get a warning recommendation.
DATABRICKS_MIN_LTS_SPARK = ('13.', '14.', '15.', '16.')


def rules_for_databricks_workflows(payload: dict, *, source_type: str | None = None) -> list:
    """Turn the databricks_workflows.json artefact into Fabric recommendations.

    Mirrors the shape of `rules_for_pipelines`: emits an inventory rec,
    per-task-type compat recs (unsupported / partial), a job-cluster
    Photon/runtime warning, and an interactive-cluster sprawl signal.
    `source_type` is unused today (Databricks workflows are always
    Databricks-sourced) but accepted for API symmetry with
    `rules_for_pipelines`.
    """
    del source_type  # accepted for API symmetry; not needed today

    out: list[Recommendation] = []
    workflows = payload.get('workflows') or []
    tasks = payload.get('tasks') or []
    job_clusters = payload.get('job_clusters') or []
    interactive = payload.get('interactive_clusters') or []

    if workflows:
        out.append(Recommendation(
            id='db.workflows_inventory',
            area='databricks_workflows',
            title=f'{len(workflows)} Databricks workflow(s) to migrate',
            severity='info',
            effort='high',
            detail='Databricks workflows (Jobs) migrate to Fabric Data Factory data pipelines plus Fabric notebooks / Spark job definitions for compute tasks.',
            fabric_action='Map each multi-task workflow to a Fabric data pipeline; port notebook + SQL + spark-python tasks to Fabric Lakehouse / Warehouse equivalents.',
        ))

    # Per-task-type support roll-up.
    unsupported = [t for t in tasks if (t.get('support') == 'unsupported')]
    partial = [t for t in tasks if (t.get('support') == 'partial')]
    unknown = [t for t in tasks if (t.get('support') == 'unknown')]

    if unsupported:
        per_type: dict[str, list[str]] = {}
        for t in unsupported:
            per_type.setdefault(str(t.get('task_type') or 'unknown'), []).append(
                f"{t.get('job_name')}::{t.get('task_key')}"
            )
        for ttype, hits in per_type.items():
            sample = ', '.join(hits[:5])
            more = '' if len(hits) <= 5 else f' (+{len(hits) - 5} more)'
            out.append(Recommendation(
                id=f'db.unsupported.{ttype}',
                area='databricks_workflows.tasks',
                title=f'Fabric-unsupported Databricks task type: {ttype} ({len(hits)})',
                severity='warning',
                effort='high',
                target=ttype,
                detail=f'Found in: {sample}{more}.',
                fabric_action='Redesign the task using a supported Fabric activity or convert to a Spark notebook.',
            ))

    if partial:
        per_type = {}
        for t in partial:
            per_type.setdefault(str(t.get('task_type') or 'unknown'), []).append(
                f"{t.get('job_name')}::{t.get('task_key')}"
            )
        for ttype, hits in per_type.items():
            out.append(Recommendation(
                id=f'db.partial.{ttype}',
                area='databricks_workflows.tasks',
                title=f'Partially supported Databricks task type: {ttype} ({len(hits)})',
                severity='info',
                effort='medium',
                target=ttype,
                detail=f'{len(hits)} occurrence(s) need redesign for Fabric.',
                fabric_action='Review configuration / auth / behavior differences after porting.',
            ))

    if unknown:
        out.append(Recommendation(
            id='db.unknown_tasks',
            area='databricks_workflows.tasks',
            title=f'{len(unknown)} task(s) with unrecognised type',
            severity='warning',
            effort='medium',
            detail='These task types fell outside the known classification table — manual review required.',
            fabric_action='Inspect the task definition in the Databricks UI and map to the closest Fabric activity.',
        ))

    # DLT pipeline tasks reference a separate artifact graph — flag them
    # explicitly so the runbook can call out the DLT-to-Fabric story.
    dlt_tasks = [t for t in tasks if t.get('task_type') == 'pipeline_task']
    if dlt_tasks:
        ids = sorted({t.get('dlt_pipeline_id') for t in dlt_tasks if t.get('dlt_pipeline_id')})
        sample = ', '.join(str(i) for i in ids[:5])
        more = '' if len(ids) <= 5 else f' (+{len(ids) - 5} more)'
        out.append(Recommendation(
            id='db.dlt_pipelines',
            area='databricks_workflows.tasks',
            title=f'{len(dlt_tasks)} Delta Live Tables pipeline task(s)',
            severity='warning',
            effort='high',
            detail=f'Distinct DLT pipeline IDs: {sample}{more}.',
            fabric_action='DLT expectations + auto-materialization need to be rebuilt as Fabric Lakehouse + Data pipeline patterns.',
        ))

    # Job-cluster runtime drift.
    outdated = [
        c for c in job_clusters
        if c.get('spark_version') and not any(
            str(c['spark_version']).startswith(prefix) for prefix in DATABRICKS_MIN_LTS_SPARK
        )
    ]
    if outdated:
        sample = ', '.join(
            f"{c.get('job_name')}::{c.get('job_cluster_key')} ({c.get('spark_version')})"
            for c in outdated[:5]
        )
        more = '' if len(outdated) <= 5 else f' (+{len(outdated) - 5} more)'
        out.append(Recommendation(
            id='db.cluster_runtime',
            area='databricks_workflows.clusters',
            title=f'{len(outdated)} job cluster(s) on a pre-13.x Databricks runtime',
            severity='warning',
            effort='medium',
            detail=f'Older runtimes complicate Fabric Spark compatibility. Examples: {sample}{more}.',
            fabric_action='Upgrade to DBR 13.x+ before porting, then map the workload onto Fabric Spark runtime 1.2 (Spark 3.4+).',
        ))

    # Interactive cluster sprawl is informational — Fabric has no
    # all-purpose-cluster equivalent so any reliance on them is a redesign.
    if interactive:
        out.append(Recommendation(
            id='db.interactive_clusters',
            area='databricks_workflows.clusters',
            title=f'{len(interactive)} interactive (all-purpose) cluster(s) in the workspace',
            severity='info',
            effort='medium',
            detail='Fabric has no all-purpose cluster equivalent; interactive workloads must move to Fabric notebooks or Spark job definitions.',
            fabric_action='Catalogue interactive cluster users and port their notebooks to Fabric notebooks.',
        ))

    # Per-warehouse Fabric mapping recommendations (Slice 4-W). One rec
    # per SQL warehouse, carrying the deterministic F-SKU target and the
    # CPU-seconds / DBU-hours evidence that fed it. The analyzer always
    # emits a mapping per warehouse, so we surface every row here.
    for m in (payload.get('sql_warehouse_fabric_mappings') or []):
        wh_id = str(m.get('warehouse_id') or '')
        if not wh_id:
            continue
        name = m.get('warehouse_name') or wh_id
        sku = m.get('recommended_sku')
        support = (m.get('support') or 'unknown').lower()
        evidence = m.get('evidence_source') or 'none'
        confidence = m.get('confidence') or 'low'
        target = m.get('target_fabric_artifact') or 'Fabric Warehouse'

        sev = 'info' if support == 'supported' else (
            'warning' if support in ('partial', 'unknown') else 'blocker'
        )
        title_sku = sku if sku else 'sizing TBD'
        title = f'SQL warehouse "{name}" → {target} ({title_sku})'

        evidence_parts: list[str] = []
        if m.get('total_cpu_seconds') is not None:
            evidence_parts.append(
                f"REST CPU {float(m['total_cpu_seconds']):.1f}s over "
                f"{m.get('lookback_days') or '?'}d "
                f"({m.get('queries_with_cpu_metric') or 0} queries)"
            )
        if m.get('total_task_seconds') is not None:
            evidence_parts.append(
                f"system.query.history task time "
                f"{float(m['total_task_seconds']):.1f}s"
            )
        if m.get('total_dbu_hours') is not None:
            evidence_parts.append(
                f"DBU-hours {float(m['total_dbu_hours']):.2f}"
            )
        if m.get('avg_concurrent_cus') is not None:
            evidence_parts.append(
                f"avg concurrent CUs {float(m['avg_concurrent_cus']):.4f} "
                f"× {float(m.get('peak_to_avg_headroom') or 0):.1f} headroom"
            )
        detail_parts: list[str] = []
        if evidence_parts:
            detail_parts.append('; '.join(evidence_parts))
        detail_parts.append(f"evidence={evidence}, confidence={confidence}")
        notes = m.get('notes') or []
        if notes:
            detail_parts.append(' '.join(str(n) for n in notes))
        detail = '. '.join(detail_parts)

        if sku:
            action = (
                f'Provision a {sku} Fabric capacity and migrate this warehouse '
                f'to {target}; validate concurrency with realistic BI load '
                f'before decommissioning the Databricks warehouse.'
            )
        else:
            action = (
                'Re-run the analyzer with query traffic in the lookback window '
                '(or set SMA_DATABRICKS_SYSTEM_TABLES=1 for Serverless) so an '
                'F-SKU recommendation can be derived from CPU-seconds / DBU-hours.'
            )

        out.append(Recommendation(
            id=f'db.sql_warehouse.{wh_id}',
            area='databricks_workflows.sql_warehouses',
            title=title,
            severity=sev,
            effort='medium',
            target=sku or wh_id,
            detail=detail,
            fabric_action=action,
        ))

    return out


# ---------------------------------------------------------------------------
# BigQuery workloads (Phase 5 Slice 5-C)
# ---------------------------------------------------------------------------

# BigQuery job types that map cleanly to a Fabric activity bucket. Anything
# outside this set is informational only (the catch-all "unknown" rec).
_BQ_JOB_BUCKETS: dict[str, str] = {
    'QUERY': 'Fabric Warehouse / Lakehouse SQL endpoint or Spark SQL',
    'LOAD': 'Fabric Data Pipeline Copy activity or Lakehouse shortcut',
    'EXTRACT': 'Fabric Data Pipeline Copy activity (destination = blob/ADLS)',
    'COPY': 'Fabric Data Pipeline Copy activity (table-to-table)',
}

# Job-type counts that warrant a per-bucket recommendation when present.
_BQ_INTERESTING_BUCKETS: tuple[str, ...] = (
    'QUERY', 'LOAD', 'EXTRACT', 'COPY',
)


def rules_for_bigquery_workloads(
    payload: dict, *, source_type: str | None = None,
) -> list:
    """Turn the bigquery_workloads.json artefact into Fabric recommendations.

    Mirrors the shape of `rules_for_databricks_workflows`: emits an inventory
    rec, per-job-type recs for the major buckets, table-support rollups
    (unsupported snapshots, partially-supported views/external), routine
    coverage signals, scheduled-query enumeration, and a slot-hour caveat
    when the analyzer reported one. `source_type` is accepted for API
    symmetry with `rules_for_pipelines` but is unused — BigQuery payloads
    are always BigQuery-sourced.
    """
    del source_type  # accepted for API symmetry; not needed today

    out: list[Recommendation] = []
    datasets = payload.get('datasets') or []
    tables = payload.get('tables') or []
    routines = payload.get('routines') or []
    scheduled = payload.get('scheduled_queries') or []
    jobs = payload.get('jobs') or []
    caveats = payload.get('caveats') or []

    # Inventory headline.
    if datasets:
        ds_count = len(datasets)
        tbl_count = payload.get('table_count') or sum(
            1 for t in tables if (t.get('table_type') or '').upper() == 'TABLE'
        )
        out.append(Recommendation(
            id='bq.inventory',
            area='bigquery_workloads',
            title=f'{ds_count} BigQuery dataset(s), {tbl_count} table(s) to migrate',
            severity='info',
            effort='high',
            detail=(
                'BigQuery datasets map to Fabric Lakehouse schemas (or Warehouse '
                'schemas for SQL-only workloads). Tables become OneLake-backed '
                'Delta tables; partitioning + clustering carry over as Spark '
                'partition columns / ZORDER hints.'
            ),
            fabric_action=(
                'Group datasets by team / sensitivity into one or more Fabric '
                'workspaces; pick Lakehouse vs Warehouse per dataset based on '
                'consumer pattern (notebook / Spark vs T-SQL).'
            ),
        ))

    # Table support rollups.
    unsupported = [t for t in tables if (t.get('support') == 'unsupported')]
    partial = [t for t in tables if (t.get('support') == 'partial')]
    if unsupported:
        by_kind: dict[str, int] = {}
        for t in unsupported:
            by_kind[str(t.get('table_type') or 'unknown')] = (
                by_kind.get(str(t.get('table_type') or 'unknown'), 0) + 1
            )
        for kind, count in sorted(by_kind.items()):
            out.append(Recommendation(
                id=f'bq.unsupported_table.{kind.lower()}',
                area='bigquery_workloads.tables',
                title=f'Fabric-unsupported BigQuery table kind: {kind} ({count})',
                severity='warning',
                effort='high',
                target=kind,
                detail=(
                    f'{count} {kind} object(s) have no direct Fabric equivalent.'
                ),
                fabric_action=(
                    'SNAPSHOT / CLONE — rebuild via Delta time-travel; AGGREGATE '
                    'functions — port to Spark UDAFs or Warehouse stored '
                    'procedures.'
                ),
            ))
    if partial:
        by_kind = {}
        for t in partial:
            by_kind[str(t.get('table_type') or 'unknown')] = (
                by_kind.get(str(t.get('table_type') or 'unknown'), 0) + 1
            )
        for kind, count in sorted(by_kind.items()):
            out.append(Recommendation(
                id=f'bq.partial_table.{kind.lower()}',
                area='bigquery_workloads.tables',
                title=f'Partially supported BigQuery table kind: {kind} ({count})',
                severity='info',
                effort='medium',
                target=kind,
                detail=(
                    f'{count} {kind} object(s) require redesign for Fabric '
                    '(VIEW → Fabric Warehouse view; MATERIALIZED_VIEW → Fabric '
                    'Lakehouse materialized view or scheduled Spark job; '
                    'EXTERNAL → OneLake shortcut + Delta partition discovery).'
                ),
                fabric_action='Review per-object before porting.',
            ))

    # Routine coverage.
    if routines:
        by_routine_kind: dict[str, int] = {}
        for r in routines:
            by_routine_kind[str(r.get('routine_type') or 'unknown')] = (
                by_routine_kind.get(str(r.get('routine_type') or 'unknown'), 0) + 1
            )
        out.append(Recommendation(
            id='bq.routines',
            area='bigquery_workloads.routines',
            title=(
                f'{len(routines)} BigQuery routine(s) to port '
                f'({", ".join(f"{k}={v}" for k, v in sorted(by_routine_kind.items()))})'
            ),
            severity='info',
            effort='medium',
            detail=(
                'PROCEDURE / SCALAR_FUNCTION / TABLE_VALUED_FUNCTION are partially '
                'supported via Fabric Warehouse T-SQL or Spark UDFs. JavaScript '
                'UDFs require redesign into Spark / Python UDFs. AGGREGATE_FUNCTION '
                'is unsupported and must be reimplemented as a Spark UDAF.'
            ),
            fabric_action='Inventory by language + signature; rewrite as Spark UDF / Warehouse procedure.',
        ))

    # Scheduled queries → Fabric pipelines.
    if scheduled:
        out.append(Recommendation(
            id='bq.scheduled_queries',
            area='bigquery_workloads.scheduled_queries',
            title=f'{len(scheduled)} scheduled BigQuery query/queries',
            severity='info',
            effort='medium',
            detail=(
                'BigQuery scheduled queries (via Data Transfer Service) map to '
                'Fabric Data Pipelines with a scheduled trigger. Cron-style '
                'schedules are preserved; "every N minutes" maps to a tumbling-'
                'window trigger.'
            ),
            fabric_action=(
                'For each scheduled query: create a Fabric Data Pipeline with a '
                'Script activity (Warehouse SQL) or Notebook activity (Spark SQL) '
                'and attach the same schedule.'
            ),
        ))

    # Job-type buckets (only when jobs were actually collected — audit-log
    # mining is opt-in / lookback-limited).
    if jobs:
        per_bucket: dict[str, int] = {}
        for j in jobs:
            jt = str(j.get('job_type') or 'UNKNOWN').upper()
            per_bucket[jt] = per_bucket.get(jt, 0) + 1
        for bucket in _BQ_INTERESTING_BUCKETS:
            count = per_bucket.get(bucket, 0)
            if count == 0:
                continue
            mapping = _BQ_JOB_BUCKETS[bucket]
            out.append(Recommendation(
                id=f'bq.jobs.{bucket.lower()}',
                area='bigquery_workloads.jobs',
                title=f'{count} {bucket} job(s) observed in the audit-log window',
                severity='info',
                effort='low',
                target=bucket,
                detail=(
                    'Observed via Cloud Logging audit-log mining over the '
                    'configured lookback window.'
                ),
                fabric_action=f'Replatform target: {mapping}.',
            ))
        # Failed-job rate signal.
        completed = [j for j in jobs if (j.get('outcome') in ('succeeded', 'failed'))]
        failed = [j for j in completed if j.get('outcome') == 'failed']
        if completed and len(failed) / len(completed) >= 0.10:
            pct = round(100 * len(failed) / len(completed), 1)
            out.append(Recommendation(
                id='bq.failed_jobs',
                area='bigquery_workloads.jobs',
                title=f'High BigQuery job failure rate observed ({pct}%)',
                severity='warning',
                effort='medium',
                detail=(
                    f'{len(failed)} of {len(completed)} completed jobs failed in '
                    'the audit-log window. Investigate before migration so the '
                    'same failure modes are not lifted-and-shifted into Fabric.'
                ),
                fabric_action='Review failed-job error_result samples; fix root cause before porting.',
            ))

    # Bubble up any analyzer-emitted caveats (e.g. slot-hour ratio).
    for c in caveats:
        if 'slot-hours' in str(c).lower() or 'slot' in str(c).lower():
            out.append(Recommendation(
                id='bq.slot_hour_caveat',
                area='bigquery_workloads.jobs',
                title='BigQuery slot-hour → Fabric CU conversion is approximate',
                severity='info',
                effort='low',
                detail=str(c),
                fabric_action='Calibrate the ratio once Fabric Spark telemetry from the migrated workload is available.',
            ))
            break

    return out


# --------------------------------------------------------------------- snowflake


def rules_for_snowflake_workloads(
    payload: dict, *, source_type: str | None = None,
) -> list:
    """Turn the snowflake_workloads.json artefact into Fabric recommendations.

    Mirrors :func:`rules_for_bigquery_workloads` / :func:`rules_for_databricks_workflows`:
    emits an inventory rec, per-object-kind compat rollups (unsupported +
    partial across tables, routines, stages, streams, tasks, pipes), a
    warehouse F-SKU recommendation derived from the 7-day window's CU
    rollup, and a credit-to-CU caveat passthrough when the analyzer
    reported one. ``source_type`` is accepted for API symmetry; Snowflake
    payloads are always Snowflake-sourced.
    """
    del source_type  # accepted for API symmetry; not needed today

    out: list[Recommendation] = []
    warehouses = payload.get('warehouses') or []
    databases = payload.get('databases') or []
    tables = payload.get('tables') or []
    routines = payload.get('routines') or []
    stages = payload.get('stages') or []
    streams = payload.get('streams') or []
    tasks = payload.get('tasks') or []
    pipes = payload.get('pipes') or []
    warehouse_window_stats = payload.get('warehouse_window_stats') or []
    caveats = payload.get('caveats') or []

    # Inventory headline.
    if databases or warehouses:
        wh_count = len(warehouses)
        db_count = len(databases)
        tbl_count = payload.get('table_count') or sum(
            1 for t in tables if (t.get('kind') or '').upper() == 'TABLE'
        )
        out.append(Recommendation(
            id='sf.inventory',
            area='snowflake_workloads',
            title=(
                f'{wh_count} Snowflake warehouse(s), {db_count} database(s), '
                f'{tbl_count} table(s) to migrate'
            ),
            severity='info',
            effort='high',
            detail=(
                'Snowflake databases map to Fabric Lakehouse / Warehouse '
                'schemas; permanent tables become OneLake-backed Delta '
                'tables. Clustering keys carry over as ZORDER hints; '
                'TRANSIENT / TEMPORARY tables require a manual decision on '
                'whether to materialise in Fabric or recompute on demand.'
            ),
            fabric_action=(
                'Group databases by team / sensitivity into one or more '
                'Fabric workspaces; pick Lakehouse vs Warehouse per database '
                'based on consumer pattern (notebook / Spark vs T-SQL).'
            ),
        ))

    # Table support rollups.
    unsupported_tables = [t for t in tables if t.get('support') == 'unsupported']
    partial_tables = [t for t in tables if t.get('support') == 'partial']
    if unsupported_tables:
        by_kind: dict[str, int] = {}
        for t in unsupported_tables:
            k = str(t.get('kind') or 'UNKNOWN').upper()
            by_kind[k] = by_kind.get(k, 0) + 1
        for kind, count in sorted(by_kind.items()):
            out.append(Recommendation(
                id=f'sf.unsupported_table.{kind.lower()}',
                area='snowflake_workloads.tables',
                title=f'Fabric-unsupported Snowflake table kind: {kind} ({count})',
                severity='warning',
                effort='high',
                target=kind,
                detail=(
                    f'{count} {kind} object(s) have no direct Fabric equivalent.'
                ),
                fabric_action=(
                    'Redesign before porting; see ADR-0007 for Snowflake-only '
                    'features that require alternate Fabric patterns.'
                ),
            ))
    if partial_tables:
        by_kind = {}
        for t in partial_tables:
            k = str(t.get('kind') or 'UNKNOWN').upper()
            by_kind[k] = by_kind.get(k, 0) + 1
        for kind, count in sorted(by_kind.items()):
            out.append(Recommendation(
                id=f'sf.partial_table.{kind.lower()}',
                area='snowflake_workloads.tables',
                title=f'Partially supported Snowflake table kind: {kind} ({count})',
                severity='info',
                effort='medium',
                target=kind,
                detail=(
                    f'{count} {kind} object(s) require redesign for Fabric '
                    '(VIEW -> Fabric Warehouse view; MATERIALIZED_VIEW -> '
                    'Fabric materialized view or scheduled Spark job; '
                    'DYNAMIC_TABLE -> scheduled refresh pipeline; '
                    'ICEBERG_TABLE -> OneLake shortcut with Iceberg-format '
                    'discovery; EXTERNAL_TABLE -> OneLake shortcut + '
                    'partition discovery).'
                ),
                fabric_action='Review per-object before porting.',
            ))

    # Routine coverage (FUNCTION / PROCEDURE x SQL/JS/Python/Java/Scala).
    if routines:
        by_routine_lang: dict[str, int] = {}
        unsupported_routines = 0
        partial_routines = 0
        for r in routines:
            kind = str(r.get('routine_kind') or 'UNKNOWN').upper()
            lang = str(r.get('language') or 'UNKNOWN').upper()
            key = f'{kind}/{lang}'
            by_routine_lang[key] = by_routine_lang.get(key, 0) + 1
            sup = r.get('support')
            if sup == 'unsupported':
                unsupported_routines += 1
            elif sup == 'partial':
                partial_routines += 1
        out.append(Recommendation(
            id='sf.routines',
            area='snowflake_workloads.routines',
            title=(
                f'{len(routines)} Snowflake routine(s) to port '
                f'({unsupported_routines} unsupported, {partial_routines} partial)'
            ),
            severity='warning' if unsupported_routines else 'info',
            effort='high' if unsupported_routines else 'medium',
            detail=(
                'JavaScript and Java UDFs / procedures have no direct Fabric '
                'equivalent and must be rewritten as Spark UDFs (Python / '
                'Scala) or T-SQL stored procedures. SQL, Python, and Scala '
                'routines are partially supported via Fabric Warehouse T-SQL '
                'or Spark notebooks. Per-language counts: '
                f'{", ".join(f"{k}={v}" for k, v in sorted(by_routine_lang.items()))}.'
            ),
            fabric_action=(
                'Inventory by language + signature; rewrite JavaScript / Java '
                'routines as Spark UDFs first (highest blast radius).'
            ),
        ))

    # Tasks (Snowflake DAG nodes) -> Fabric pipelines / scheduled notebooks.
    if tasks:
        out.append(Recommendation(
            id='sf.tasks',
            area='snowflake_workloads.tasks',
            title=f'{len(tasks)} Snowflake task(s) to replatform',
            severity='info',
            effort='medium',
            detail=(
                'Snowflake tasks (cron + dependency DAG) map to Fabric Data '
                'Pipelines with scheduled triggers; root tasks become the '
                'pipeline trigger, downstream tasks become Script / Notebook '
                'activities chained via "On success" dependencies. Conditional '
                'execution (WHEN system$stream_has_data) maps to a Fabric '
                'pipeline IfCondition activity over a Lakehouse change-data '
                'snapshot.'
            ),
            fabric_action=(
                'For each task DAG: build a single Fabric Data Pipeline with '
                'one activity per task, preserving dependency edges and the '
                'task warehouse via the chosen Fabric capacity binding.'
            ),
        ))

    # Streams (CDC cursors) -> Fabric Lakehouse change-data feed.
    if streams:
        out.append(Recommendation(
            id='sf.streams',
            area='snowflake_workloads.streams',
            title=f'{len(streams)} Snowflake stream(s) to replatform',
            severity='info',
            effort='medium',
            detail=(
                'Snowflake streams provide change-data capture over tables / '
                'views / external tables. Fabric Lakehouse Delta tables expose '
                'the equivalent via the Change Data Feed (`change_data_feed = '
                'true`) consumed by Spark `readChangeFeed`. APPEND_ONLY / '
                'INSERT_ONLY modes map directly; DEFAULT mode (which exposes '
                'both DML and DDL) requires careful schema-evolution handling '
                'in Fabric.'
            ),
            fabric_action=(
                'Enable Change Data Feed on the target Delta tables; rewrite '
                'consumer queries to use `readChangeFeed` instead of SELECT '
                'FROM stream.'
            ),
        ))

    # Snowpipe -> Fabric Data Pipeline event-triggered Copy activity.
    if pipes:
        out.append(Recommendation(
            id='sf.pipes',
            area='snowflake_workloads.pipes',
            title=f'{len(pipes)} Snowpipe(s) to replatform',
            severity='info',
            effort='medium',
            detail=(
                'Snowpipes auto-ingest from cloud-storage event notifications. '
                'In Fabric, the equivalent is a Data Pipeline with a Storage '
                'Event trigger and a Copy activity targeting OneLake. Cloud '
                'storage integration (S3 / Azure Blob / GCS) flips to the '
                'Fabric workspace identity + the source storage account.'
            ),
            fabric_action=(
                'Per-pipe: create a Fabric Data Pipeline with Storage Event '
                'trigger -> Copy activity -> Lakehouse / Warehouse target.'
            ),
        ))

    # Stages -> OneLake shortcuts.
    if stages:
        out.append(Recommendation(
            id='sf.stages',
            area='snowflake_workloads.stages',
            title=f'{len(stages)} Snowflake stage(s) to replatform',
            severity='info',
            effort='low',
            detail=(
                'External stages (S3 / Azure Blob / GCS) map cleanly to '
                'OneLake shortcuts; internal stages have no Fabric equivalent '
                'and must be replaced with the Lakehouse Files area or a '
                'pipeline-managed scratch container.'
            ),
            fabric_action=(
                'For external stages, create a OneLake shortcut to the same '
                'bucket/container. For internal stages, route uploads to the '
                'Lakehouse Files area or a Fabric-managed staging path.'
            ),
        ))

    # Warehouse F-SKU recommendation (from 7-day window's CU rollup).
    if warehouse_window_stats:
        # Pick the 7-day row if present; fall back to the largest window.
        seven_day = [w for w in warehouse_window_stats if int(w.get('window_days') or 0) == 7]
        rows = seven_day or sorted(
            warehouse_window_stats,
            key=lambda w: int(w.get('window_days') or 0),
            reverse=True,
        )[:1]
        if rows:
            window_days = int(rows[0].get('window_days') or 7)
            total_cu_hr = sum(
                float(w.get('est_cu_hours_fabric_warehouse') or 0.0)
                for w in rows
            )
            if total_cu_hr > 0 and window_days > 0:
                sustained_cu = total_cu_hr / window_days / 24.0
                # Lazy import to avoid a top-level cycle.
                from .cu_projection import _smallest_sku_covering  # type: ignore[attr-defined]
                sku = _smallest_sku_covering(sustained_cu)
                out.append(Recommendation(
                    id='sf.warehouse_sku',
                    area='snowflake_workloads.warehouses',
                    title=(
                        f'Recommended Fabric capacity for observed Snowflake '
                        f'compute: {sku} ({sustained_cu:.2f} CU sustained over '
                        f'last {window_days} day(s))'
                    ),
                    severity='info',
                    effort='low',
                    detail=(
                        f'{total_cu_hr:.1f} Fabric Warehouse CU-hours over the '
                        f'last {window_days}-day window across '
                        f'{len({w.get("warehouse_name") for w in rows})} '
                        'warehouse(s), derived from Snowflake QUERY_HISTORY '
                        'via the size-class proxy (see credit caveat).'
                    ),
                    fabric_action=(
                        f'Provision a Fabric capacity at {sku} or higher; '
                        'plan headroom per ADR-0006.'
                    ),
                ))

    # Credit -> CU caveat passthrough.
    for c in caveats:
        if 'credit' in str(c).lower():
            out.append(Recommendation(
                id='sf.credit_caveat',
                area='snowflake_workloads.jobs',
                title='Snowflake credit -> Fabric CU conversion is approximate',
                severity='info',
                effort='low',
                detail=str(c),
                fabric_action=(
                    'Calibrate the ratio once Fabric Warehouse telemetry from '
                    'the migrated workload is available.'
                ),
            ))
            break

    return out


