"""Estate-wide aggregation of run artefacts.

Produces a single :class:`EstateReport` summarising every workspace
scanned by this server, by walking ``runs_dir/<id>/run.json`` plus the
relevant module artefacts (`fabric_mapping.json`, `cost.json`).

Designed to be cheap on repeat calls: results are memoised in-process
keyed by ``(run_id, run.json mtime)`` and rebuilt incrementally when a
new run lands or an existing run is mutated. There is no on-disk cache
\u2014 the index is small (~1 kB per run) so an in-memory dict is enough.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .schemas import (
    EstateHistoryPoint,
    EstateReport,
    EstateTopBlocker,
    EstateTotals,
    EstateWorkspace,
)
from .storage import FilesystemRunRepo

log = logging.getLogger(__name__)

# Per-workspace history cap. Older points are dropped once the count
# exceeds this; configurable via SMA_ESTATE_MAX_HISTORY for power users.
_DEFAULT_MAX_HISTORY = 50


def _max_history() -> int:
    raw = os.getenv("SMA_ESTATE_MAX_HISTORY")
    if not raw:
        return _DEFAULT_MAX_HISTORY
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return _DEFAULT_MAX_HISTORY


def workspace_key(
    *,
    tenant_id: str | None = None,  # noqa: ARG001 — kept for API compatibility
    subscription_id: str | None,
    resource_group: str | None = None,  # noqa: ARG001 — kept for API compatibility
    workspace_name: str | None,
) -> str:
    """Stable URL-safe key for a workspace.

    Identity is ``(subscription_id, workspace_name)`` — subscription ids
    are globally unique, so this stays correct across tenants while
    avoiding split rows when a workspace is rescanned after the
    tenant/RG fields were added to ``run.json``. The ``tenant_id`` and
    ``resource_group`` arguments are accepted but ignored.
    """
    return f"{subscription_id or '_'}|{workspace_name or '_'}"


class EstateIndex:
    """In-memory aggregator.

    Holds per-run extracts keyed by ``run_id`` so the next call only
    needs to re-read run folders whose ``run.json`` mtime advanced (or
    new folders that appeared).
    """

    def __init__(self, repo: FilesystemRunRepo) -> None:
        self.repo = repo
        self._lock = RLock()
        # run_id -> (mtime_ns, extract dict)
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def invalidate(self, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is None:
                self._cache.clear()
            else:
                self._cache.pop(run_id, None)

    def build(self) -> EstateReport:
        with self._lock:
            extracts = self._collect_extracts()

        groups: dict[str, list[dict[str, Any]]] = {}
        for ex in extracts:
            groups.setdefault(ex["workspace_key"], []).append(ex)

        cap = _max_history()
        workspaces: list[EstateWorkspace] = []
        latest_extracts: list[dict[str, Any]] = []
        for key, items in groups.items():
            items.sort(key=lambda r: r["finished_at_sort"], reverse=True)
            latest = next(
                (r for r in items if r["status"] == "ok"),
                items[0],
            )
            latest_extracts.append(latest)
            history_items = items[:cap]
            history = [
                EstateHistoryPoint(
                    run_id=r["id"],
                    finished_at=r["finished_at"],
                    status=r["status"],
                    readiness_score=r["readiness_score"],
                    blocker_count=r["blocker_count"],
                    warning_count=r["warning_count"],
                    actual_monthly_cost=r["actual_monthly_cost"],
                )
                for r in reversed(history_items)  # chronological for charting
            ]
            workspaces.append(EstateWorkspace(
                key=key,
                tenant_id=latest["tenant_id"],
                subscription_id=latest["subscription_id"],
                resource_group=latest["resource_group"],
                workspace_name=latest["workspace_name"] or "(unknown)",
                run_count=len(items),
                latest_run_id=latest["id"],
                latest_status=latest["status"],
                latest_finished_at=latest["finished_at"],
                modules_run=latest["modules_run"],
                source_type=latest.get("source_type"),
                cloud=latest.get("cloud"),
                readiness_score=latest["readiness_score"],
                readiness_bucket=latest["readiness_bucket"],
                blocker_count=latest["blocker_count"],
                warning_count=latest["warning_count"],
                info_count=latest["info_count"],
                tsql_compatibility_pct=latest["tsql_compatibility_pct"],
                projected_fabric_cu=latest["projected_fabric_cu"],
                recommended_fabric_sku=latest["recommended_fabric_sku"],
                actual_monthly_cost=latest["actual_monthly_cost"],
                actual_currency=latest["actual_currency"],
                fabric_estimated_monthly_cost=latest["fabric_estimated_monthly_cost"],
                fabric_estimated_monthly_cost_1y_ri=latest.get("fabric_estimated_monthly_cost_1y_ri"),
                fabric_estimated_monthly_cost_3y_ri=latest.get("fabric_estimated_monthly_cost_3y_ri"),
                fabric_pricing_source=latest.get("fabric_pricing_source"),
                fabric_cost_delta_abs=latest["fabric_cost_delta_abs"],
                fabric_cost_delta_pct=latest["fabric_cost_delta_pct"],
                effort_hours_p50=latest["effort_hours_p50"],
                effort_hours_p90=latest["effort_hours_p90"],
                effort_days_p50=latest["effort_days_p50"],
                effort_days_p90=latest["effort_days_p90"],
                history=history,
            ))

        workspaces.sort(key=lambda w: (w.workspace_name or "").lower())
        totals = _totals(workspaces)
        # Aggregate blockers from each workspace's *latest* extract only,
        # so the table can never disagree with the "Open blockers" total.
        top_blockers = _aggregate_top_blockers(latest_extracts)
        return EstateReport(
            generated_at=datetime.now(timezone.utc),
            totals=totals,
            workspaces=workspaces,
            top_blockers=top_blockers,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_extracts(self) -> list[dict[str, Any]]:
        runs_dir = self.repo.runs_dir
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in runs_dir.iterdir():
            if not entry.is_dir():
                continue
            run_id = entry.name
            try:
                self.repo._validate_id(run_id)  # noqa: SLF001 \u2014 internal check
            except ValueError:
                continue
            seen.add(run_id)
            run_json = entry / "run.json"
            if not run_json.exists():
                continue
            try:
                mtime_ns = run_json.stat().st_mtime_ns
            except OSError:
                continue
            cached = self._cache.get(run_id)
            if cached is not None and cached[0] == mtime_ns:
                out.append(cached[1])
                continue
            extract = _extract_run(entry)
            if extract is None:
                continue
            self._cache[run_id] = (mtime_ns, extract)
            out.append(extract)
        # Drop cache entries for deleted runs.
        for stale in [k for k in self._cache if k not in seen]:
            self._cache.pop(stale, None)
        return out


# ---------------------------------------------------------------------------
# Per-run extraction
# ---------------------------------------------------------------------------


def _extract_run(run_dir: Path) -> dict[str, Any] | None:
    run_json = run_dir / "run.json"
    try:
        meta = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("estate: skipping unreadable %s: %s", run_json, exc)
        return None

    started = _parse_dt(meta.get("started_at"))
    finished = _parse_dt(meta.get("finished_at")) or started
    if finished is None:
        # Cannot place this run on a timeline.
        return None

    fm = _read_optional_json(run_dir / "fabric_mapping.json")
    cost = _read_optional_json(run_dir / "cost.json")
    pipelines = _read_optional_json(run_dir / "pipelines.json")
    databricks = _read_optional_json(run_dir / "databricks_workflows.json")
    bigquery = _read_optional_json(run_dir / "bigquery_workloads.json")
    snowflake = _read_optional_json(run_dir / "snowflake_workloads.json")
    workspace_name = (
        meta.get("workspace_name")
        or (fm.get("workspace_name") if fm else None)
        or (cost.get("workspace_name") if cost else None)
    )
    # Older runs predate the workspace identity fields on ``run.json``;
    # fall back to cost.json (which has carried subscription_id /
    # resource_group since v1) so totals like "Subscriptions" stay
    # accurate for legacy data.
    subscription_id = meta.get("subscription_id") or (
        cost.get("subscription_id") if cost else None
    )
    resource_group = meta.get("resource_group") or (
        cost.get("resource_group") if cost else None
    )
    tenant_id = meta.get("tenant_id")

    readiness = (fm or {}).get("readiness") or {}
    capacity = (fm or {}).get("capacity_projection") or {}
    fabric_cmp = (cost or {}).get("fabric_comparison") or {}
    actual_monthly = None
    if cost:
        # Pick the latest month from monthly_totals (keys are YYYY-MM, so a
        # lexicographic max returns the most recent month). The cost
        # module's default window includes the current month-to-date, so
        # this value may be partial for the in-progress month; that is
        # acceptable because users typically want the most recent run-rate
        # rather than a smoothed multi-month average.
        monthly_totals = cost.get("monthly_totals") or {}
        if isinstance(monthly_totals, dict) and monthly_totals:
            try:
                latest_key = max(monthly_totals.keys())
                actual_monthly = float(monthly_totals[latest_key])
            except (TypeError, ValueError):
                actual_monthly = None
        if actual_monthly is None:
            actual_monthly = fabric_cmp.get("synapse_avg_monthly_cost")

    actual_currency = None
    if cost:
        for row in cost.get("rows") or []:
            cur = row.get("currency") if isinstance(row, dict) else None
            if cur:
                actual_currency = cur
                break

    modules_run: list[str] = [
        m.get("name") for m in meta.get("modules", []) if m.get("name")
    ]

    # v3 — pluck the primary source_type from run.json's scopes list. The
    # first scope wins; legacy runs without scopes[] yield ``None`` and
    # downstream UI falls back to generic "Scope" wording.
    scopes_list = meta.get("scopes") or []
    source_type: str | None = None
    databricks_platform: str | None = None
    scope_host_hint: str | None = None
    if isinstance(scopes_list, list) and scopes_list:
        first = scopes_list[0]
        if isinstance(first, dict):
            source_type = first.get("source_type")
            # Phase 4.7 — ScopeRecord.platform is the cloud discriminator
            # for Databricks ("azure" | "aws" | "gcp"); other source
            # types leave it null. Also check the nested extras dict
            # (ScopeRef wire shape), which is where the SPA-launched
            # run path persists the descriptor's platform.
            plat = first.get("platform")
            if isinstance(plat, str) and plat:
                databricks_platform = plat
            extras = first.get("extras")
            if (
                databricks_platform is None
                and isinstance(extras, dict)
            ):
                extras_plat = extras.get("platform")
                if isinstance(extras_plat, str) and extras_plat:
                    databricks_platform = extras_plat
            # Fallback for legacy Databricks runs (pre-5.1.2) whose
            # scopes[] entries don't carry platform/extras at all: the
            # scope id is the workspace host, so we can sniff the
            # hyperscaler from its suffix.
            candidate = first.get("id") or first.get("display_name")
            if isinstance(candidate, str) and candidate:
                scope_host_hint = candidate
    # Backfill for legacy runs whose run.json predates the scopes[]
    # array: fabric_mapping recommendations carry ``source_type`` since
    # Slice A, so the first one with a non-null value is a safe proxy
    # for the run's primary source platform.
    if not source_type and fm:
        for rec in fm.get("recommendations") or []:
            cand = rec.get("source_type") if isinstance(rec, dict) else None
            if cand:
                source_type = cand
                break
    cloud = _cloud_for(source_type, databricks_platform, scope_host_hint)

    blockers: list[dict[str, Any]] = []
    warnings_count = 0
    info_count = 0
    if fm:
        for r in fm.get("recommendations") or []:
            sev = r.get("severity")
            if sev == "blocker":
                blockers.append(r)
            elif sev == "warning":
                warnings_count += 1
            elif sev == "info":
                info_count += 1

    workspace_key_str = workspace_key(
        subscription_id=subscription_id,
        workspace_name=workspace_name,
    )

    # Steady-state CU contribution from pipeline integration activity
    # (DIU-hr + orchestration CU-hr). Mirrors Dashboard's calculation so the
    # estate-level "Estimated SKU needed" accounts for pipelines, not just
    # the dedicated-pool capacity projection.
    integration_daily_cu = _integration_daily_cu(pipelines)
    # Databricks workflow + interactive-cluster Spark CU contribution
    # (vCore-hr × 0.5 → Fabric Spark CU-hr, normalized to sustained CU/day).
    # Without this the Estate Overview's CU and recommended SKU columns
    # stay blank for Databricks-only scopes whose fabric_mapping run has
    # no dedicated-pool capacity projection.
    databricks_daily_cu = _databricks_daily_cu(databricks)
    # Databricks SQL warehouse (Fabric Warehouse) sustained-CU contribution,
    # derived from the per-warehouse Fabric mapping the analyzer emits.
    # Sum of ``avg_concurrent_cus * peak_to_avg_headroom`` across all
    # warehouses; matches the dimension of the other daily-CU components,
    # which already include their own headroom factor so that
    # ``_smallest_sku_covering`` can be applied to the combined total.
    databricks_sql_daily_cu = _databricks_sql_daily_cu(databricks)
    # BigQuery slot-hour contribution (slot-hr × 0.25 → Fabric Spark CU-hr,
    # normalized to sustained CU/day). Same motivation as the Databricks
    # branch above — BigQuery-only scopes need a non-blank CU column.
    bigquery_daily_cu = _bigquery_daily_cu(bigquery)
    # Snowflake warehouse credit-hour contribution (credit → vCore-hr →
    # Fabric Warehouse CU-hr, sustained CU/day). Same motivation as the
    # BigQuery branch above — Snowflake-only scopes need a non-blank CU
    # column on the Estate Overview row.
    snowflake_daily_cu = _snowflake_daily_cu(snowflake)

    dw_cu = capacity.get("estimated_cu")
    if (
        dw_cu is None
        and integration_daily_cu == 0.0
        and databricks_daily_cu == 0.0
        and databricks_sql_daily_cu == 0.0
        and bigquery_daily_cu == 0.0
        and snowflake_daily_cu == 0.0
    ):
        projected_fabric_cu: float | None = None
    else:
        projected_fabric_cu = (
            float(dw_cu or 0.0)
            + integration_daily_cu
            + databricks_daily_cu
            + databricks_sql_daily_cu
            + bigquery_daily_cu
            + snowflake_daily_cu
        )

    # If the fabric_mapping capacity projection didn't yield a SKU
    # (typical for Databricks-only scopes), derive one from the combined
    # daily CU so the workspace row has a usable headline.
    recommended_sku = capacity.get("recommended_sku")
    if not recommended_sku and projected_fabric_cu is not None and projected_fabric_cu > 0:
        from ..modules.fabric_mapping.cu_projection import _smallest_sku_covering

        recommended_sku = _smallest_sku_covering(projected_fabric_cu)

    # ------------------------------------------------------------------
    # Fabric price re-resolution.
    #
    # ``fabric_cmp`` is whatever the cost module persisted in cost.json
    # at scan time. For older runs that ran before the live-pricing
    # module landed (or before fabric_mapping.json existed in the same
    # run dir), the per-SKU monthly figure may be ``None``. The estate
    # rollup is the user-visible "is my migration cheaper?" headline, so
    # we re-resolve here from the *current* recommended SKU using the
    # live Azure Retail Prices API (cached 24h on disk). This costs at
    # most one HTTP call per region per render and gracefully degrades
    # to the static F2–F64 fallback when offline. F128+ stays ``None``
    # by design.
    # ------------------------------------------------------------------
    payg_monthly = fabric_cmp.get("fabric_estimated_monthly_cost")
    ri_1y_monthly = fabric_cmp.get("fabric_estimated_monthly_cost_1y_ri")
    ri_3y_monthly = fabric_cmp.get("fabric_estimated_monthly_cost_3y_ri")
    pricing_source = fabric_cmp.get("pricing_source")
    if recommended_sku and (
        payg_monthly is None or ri_1y_monthly is None or ri_3y_monthly is None
    ):
        try:
            from ..modules.cost.fabric_compare import (
                _FABRIC_STATIC_MONTHLY_PAYG_USD,
            )
            from ..modules.cost.fabric_pricing import estimate_monthly_costs

            live = estimate_monthly_costs(recommended_sku)
            sku_u = recommended_sku.upper()
            if payg_monthly is None:
                payg_monthly = live.get("payg_monthly")
                if payg_monthly is None and sku_u in _FABRIC_STATIC_MONTHLY_PAYG_USD:
                    payg_monthly = _FABRIC_STATIC_MONTHLY_PAYG_USD[sku_u]
                    pricing_source = pricing_source or "static"
                elif payg_monthly is not None:
                    pricing_source = live.get("source") or pricing_source
            if ri_1y_monthly is None:
                ri_1y_monthly = live.get("ri_1y_monthly")
            if ri_3y_monthly is None:
                ri_3y_monthly = live.get("ri_3y_monthly")
        except Exception as exc:  # noqa: BLE001 — fail-soft, keep stale value
            log.debug("estate: live fabric repricing failed: %s", exc)

    # Recompute delta if we just supplied a fresh PAYG number.
    fabric_delta_abs = fabric_cmp.get("delta_abs")
    fabric_delta_pct = fabric_cmp.get("delta_pct")
    if fabric_delta_abs is None and payg_monthly is not None and actual_monthly is not None:
        fabric_delta_abs = payg_monthly - actual_monthly
        if actual_monthly:
            fabric_delta_pct = fabric_delta_abs / actual_monthly

    return {
        "id": meta.get("id") or run_dir.name,
        "status": meta.get("status") or "ok",
        "finished_at": finished,
        # ``finished_at_sort`` is a comparable key with a stable tiebreak.
        "finished_at_sort": (finished, run_dir.name),
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "workspace_name": workspace_name,
        "workspace_key": workspace_key_str,
        "source_type": source_type,
        "cloud": cloud,
        "modules_run": modules_run,
        "readiness_score": readiness.get("score"),
        "readiness_bucket": readiness.get("bucket"),
        "blocker_count": len(blockers),
        "warning_count": warnings_count,
        "info_count": info_count,
        "tsql_compatibility_pct": readiness.get("tsql_compatibility_pct"),
        "projected_fabric_cu": projected_fabric_cu,
        "projected_fabric_cu_dw": capacity.get("estimated_cu"),
        "projected_fabric_cu_pipelines": integration_daily_cu or None,
        "recommended_fabric_sku": recommended_sku,
        "actual_monthly_cost": actual_monthly,
        "actual_currency": actual_currency,
        "fabric_estimated_monthly_cost": payg_monthly,
        "fabric_estimated_monthly_cost_1y_ri": ri_1y_monthly,
        "fabric_estimated_monthly_cost_3y_ri": ri_3y_monthly,
        "fabric_pricing_source": pricing_source,
        "fabric_cost_delta_abs": fabric_delta_abs,
        "fabric_cost_delta_pct": fabric_delta_pct,
        "effort_hours_p50": ((fm or {}).get("effort_summary") or {}).get("total_p50_hours"),
        "effort_hours_p90": ((fm or {}).get("effort_summary") or {}).get("total_p90_hours"),
        "effort_days_p50": ((fm or {}).get("effort_summary") or {}).get("total_p50_days"),
        "effort_days_p90": ((fm or {}).get("effort_summary") or {}).get("total_p90_days"),
        "blockers_raw": blockers,
    }


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("estate: skipping unreadable %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _integration_daily_cu(pipelines: dict[str, Any] | None) -> float:
    """Steady-state CU/day from pipeline integration activity.

    Mirrors ``Dashboard.tsx``: prefers the 7-day window per pipeline (falls
    back to the first available window), sums DIU + orchestration CU-hours
    across pipelines, then converts CU-hr/window into sustained CU
    (``total / window_days / 24``). Returns ``0.0`` when ``pipelines.json``
    is absent or has no run-history rollup.
    """
    if not pipelines:
        return 0.0
    hist = pipelines.get("run_history") or {}
    by_pipeline = hist.get("by_pipeline") or []
    if not isinstance(by_pipeline, list) or not by_pipeline:
        return 0.0
    total_cu_hr = 0.0
    window_days = 0
    for p in by_pipeline:
        if not isinstance(p, dict):
            continue
        windows = p.get("windows") or []
        if not isinstance(windows, list) or not windows:
            continue
        w = next(
            (x for x in windows if isinstance(x, dict) and x.get("window_days") == 7),
            windows[0] if isinstance(windows[0], dict) else None,
        )
        if not w:
            continue
        try:
            total_cu_hr += float(w.get("est_cu_hours_from_diu") or 0.0)
            total_cu_hr += float(w.get("est_cu_hours_from_vcore") or 0.0)
            total_cu_hr += float(w.get("est_cu_hours_from_orchestration") or 0.0)
        except (TypeError, ValueError):
            continue
        wd = w.get("window_days")
        if isinstance(wd, (int, float)) and wd > window_days:
            window_days = int(wd)
    if window_days <= 0:
        return 0.0
    return (total_cu_hr / window_days) / 24.0


def _databricks_daily_cu(databricks: dict[str, Any] | None) -> float:
    """Steady-state CU/day from Databricks workflow runs + interactive clusters.

    Sums ``est_cu_hours_fabric_spark`` from each workflow's and each
    interactive cluster's preferred rolling window (7d if present, else
    the largest available window), then converts CU-hr/window into
    sustained CU (``total / window_days / 24``). Returns ``0.0`` when
    ``databricks_workflows.json`` is absent or carries no resolvable
    cluster shape (e.g. all runs hit ``cluster_sizing_caveats``).
    """
    if not databricks:
        return 0.0
    total_cu_hr = 0.0
    window_days = 0
    for source_key in ("workflow_run_stats", "interactive_cluster_usage"):
        entries = databricks.get(source_key) or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            windows = entry.get("windows") or []
            if not isinstance(windows, list) or not windows:
                continue
            w = next(
                (
                    x for x in windows
                    if isinstance(x, dict) and x.get("window_days") == 7
                ),
                windows[0] if isinstance(windows[0], dict) else None,
            )
            if not w:
                continue
            try:
                total_cu_hr += float(w.get("est_cu_hours_fabric_spark") or 0.0)
            except (TypeError, ValueError):
                continue
            wd = w.get("window_days")
            if isinstance(wd, (int, float)) and wd > window_days:
                window_days = int(wd)
    if window_days <= 0:
        return 0.0
    return (total_cu_hr / window_days) / 24.0


def _cloud_for(
    source_type: str | None,
    databricks_platform: str | None,
    scope_host_hint: str | None = None,
) -> str | None:
    """Map a scope's source platform to a hyperscaler bucket.

    Returns one of ``"azure"`` / ``"aws"`` / ``"gcp"`` / ``"on_prem"``,
    or ``None`` for legacy runs whose source platform could not be
    determined. Synapse + ADF + Azure-Databricks all land under
    ``"azure"``; Databricks scopes with ``extras["platform"] in {"aws",
    "gcp"}`` get routed to the matching cloud; BigQuery is always
    ``"gcp"``; SAP BW is bucketed as ``"on_prem"`` so on-prem estates
    don't get mis-tagged as a cloud they don't live in.

    ``scope_host_hint`` (the scope id / display name) is used as a
    last-resort fallback for legacy Databricks runs that pre-date the
    Phase 4.7 platform discriminator and therefore have no
    ``extras["platform"]`` to read. The well-known workspace-host
    suffixes are:

    * ``*.azuredatabricks.net``        → ``azure``
    * ``*.cloud.databricks.com``       → ``aws``
    * ``*.gcp.databricks.com``         → ``gcp``
    """
    if not source_type:
        return None
    if source_type == "bigquery":
        return "gcp"
    if source_type == "snowflake":
        # Snowflake's ``extras['platform']`` rides in on the
        # ``databricks_platform`` parameter (it's misnamed — the loop
        # above hoists *any* scope's ``extras['platform']`` into it).
        if databricks_platform in {"aws", "azure", "gcp"}:
            return databricks_platform
        return "aws"  # most common Snowflake deployment
    if source_type == "sap_bw":
        return "on_prem"
    if source_type == "databricks":
        if databricks_platform in {"aws", "gcp", "azure"}:
            return databricks_platform
        host = (scope_host_hint or "").lower()
        if host:
            if ".azuredatabricks.net" in host:
                return "azure"
            if ".gcp.databricks.com" in host:
                return "gcp"
            if ".cloud.databricks.com" in host:
                return "aws"
        return "azure"
    # synapse_workspace, adf, and any future Azure-native sources.
    return "azure"


def _databricks_sql_daily_cu(databricks: dict[str, Any] | None) -> float:
    """Headroom-adjusted CU/day from Databricks SQL warehouses.

    Sums ``avg_concurrent_cus * peak_to_avg_headroom`` across every
    ``sql_warehouse_fabric_mappings`` entry. ``avg_concurrent_cus`` is
    already a sustained-CU figure (CPU-seconds / lookback-seconds); the
    headroom multiplier matches the dimension used by the other
    daily-CU contributors (dw / pipelines / Spark / BigQuery) so the
    combined total can be passed straight into ``_smallest_sku_covering``.

    Returns ``0.0`` when ``databricks_workflows.json`` is absent or has
    no mappings.
    """
    if not databricks:
        return 0.0
    mappings = databricks.get("sql_warehouse_fabric_mappings") or []
    if not isinstance(mappings, list):
        return 0.0
    total = 0.0
    for m in mappings:
        if not isinstance(m, dict):
            continue
        try:
            avg = m.get("avg_concurrent_cus")
            if avg is None:
                continue
            headroom = m.get("peak_to_avg_headroom")
            if headroom is None or headroom <= 0:
                headroom = 4.0
            total += float(avg) * float(headroom)
        except (TypeError, ValueError):
            continue
    return total


def _bigquery_daily_cu(bigquery: dict[str, Any] | None) -> float:
    """Steady-state CU/day from BigQuery slot-hour rollups.

    Unlike ``databricks_workflows.json``'s per-workflow ``windows`` array,
    ``bigquery_workloads.json`` carries a single flat ``job_window_stats``
    list with one row per rolling window (7 / 14 / 28 / 90 days). Prefers
    the 7-day window if present (matches the Dashboard tile selection),
    falls back to the largest available window. The slot-hour \u2192 CU-hour
    conversion already happened inside the BigQuery analyzer
    (``SLOT_HOURS_TO_CU_HOURS = 0.25``); this helper only normalizes the
    window total to sustained CU (``cu_hr / window_days / 24``). Returns
    ``0.0`` when the file is absent, lacks ``job_window_stats``, or every
    window has zero CU-hours.
    """
    if not bigquery:
        return 0.0
    stats = bigquery.get("job_window_stats") or []
    if not isinstance(stats, list) or not stats:
        return 0.0
    rows = [s for s in stats if isinstance(s, dict)]
    if not rows:
        return 0.0
    chosen = next(
        (s for s in rows if s.get("window_days") == 7),
        max(
            rows,
            key=lambda s: s.get("window_days") if isinstance(s.get("window_days"), (int, float)) else 0,
        ),
    )
    try:
        cu_hr = float(chosen.get("est_cu_hours_fabric_spark") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    wd = chosen.get("window_days")
    if not isinstance(wd, (int, float)) or wd <= 0:
        return 0.0
    return (cu_hr / float(wd)) / 24.0


def _snowflake_daily_cu(snowflake: dict[str, Any] | None) -> float:
    """Steady-state CU/day from Snowflake warehouse credit-hour rollups.

    Mirrors ``_bigquery_daily_cu``: prefers the 7-day window's
    ``warehouse_window_stats`` rows, sums ``est_cu_hours_fabric_warehouse``
    across all warehouses for that window, and normalizes to sustained CU
    (``cu_hr / window_days / 24``). The credit → vCore-hr → CU-hr math
    happened upstream in ``snowflake_workloads.run_stats`` (currently a
    size-class proxy via ``CREDIT_TO_VCORE_HOURS=1.0`` and
    ``VCORE_HOURS_TO_CU_HOURS=0.5``); this helper only does window
    normalization. Returns ``0.0`` when the artifact is absent, has no
    ``warehouse_window_stats``, or every row has zero CU-hours.
    """
    if not snowflake:
        return 0.0
    stats = snowflake.get("warehouse_window_stats") or []
    if not isinstance(stats, list) or not stats:
        return 0.0
    rows = [s for s in stats if isinstance(s, dict)]
    if not rows:
        return 0.0
    seven = [s for s in rows if s.get("window_days") == 7]
    if seven:
        chosen_window = 7
        chosen_rows = seven
    else:
        # Fall back to the largest available window; rows are per
        # (window, warehouse) so we pick the window with the most
        # CU-hours across warehouses to stay deterministic.
        windows = sorted({s.get("window_days") for s in rows
                          if isinstance(s.get("window_days"), (int, float))})
        if not windows:
            return 0.0
        chosen_window = int(max(windows))
        chosen_rows = [s for s in rows if s.get("window_days") == chosen_window]
    total_cu_hr = 0.0
    for row in chosen_rows:
        try:
            total_cu_hr += float(row.get("est_cu_hours_fabric_warehouse") or 0.0)
        except (TypeError, ValueError):
            continue
    if chosen_window <= 0 or total_cu_hr <= 0:
        return 0.0
    return (total_cu_hr / float(chosen_window)) / 24.0


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # fromisoformat handles "2026-05-08T12:00:00+00:00" and
            # "2026-05-08T12:00:00Z" in Python 3.11+.
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _totals(workspaces: Iterable[EstateWorkspace]) -> EstateTotals:
    ws_list = list(workspaces)
    runs = sum(w.run_count for w in ws_list)
    ready = sum(1 for w in ws_list if w.readiness_bucket == "ready")
    ready_with_effort = sum(
        1 for w in ws_list if w.readiness_bucket == "ready-with-effort"
    )
    blocked = sum(1 for w in ws_list if w.readiness_bucket == "blocked")
    blockers_total = sum(w.blocker_count for w in ws_list)
    tsql_vals = [w.tsql_compatibility_pct for w in ws_list if w.tsql_compatibility_pct is not None]
    cu_vals = [w.projected_fabric_cu for w in ws_list if w.projected_fabric_cu is not None]
    cost_vals = [w.actual_monthly_cost for w in ws_list if w.actual_monthly_cost is not None]
    fabric_cost_vals = [
        w.fabric_estimated_monthly_cost
        for w in ws_list
        if w.fabric_estimated_monthly_cost is not None
    ]
    fabric_1y_vals = [
        w.fabric_estimated_monthly_cost_1y_ri
        for w in ws_list
        if w.fabric_estimated_monthly_cost_1y_ri is not None
    ]
    fabric_3y_vals = [
        w.fabric_estimated_monthly_cost_3y_ri
        for w in ws_list
        if w.fabric_estimated_monthly_cost_3y_ri is not None
    ]
    effort_p50_vals = [w.effort_hours_p50 for w in ws_list if w.effort_hours_p50 is not None]
    effort_p90_vals = [w.effort_hours_p90 for w in ws_list if w.effort_hours_p90 is not None]
    effort_days_p50_vals = [w.effort_days_p50 for w in ws_list if w.effort_days_p50 is not None]
    effort_days_p90_vals = [w.effort_days_p90 for w in ws_list if w.effort_days_p90 is not None]
    subs = {(w.subscription_id or "(unknown)") for w in ws_list}
    tenants = {(w.tenant_id or "(unknown)") for w in ws_list}
    return EstateTotals(
        workspaces=len(ws_list),
        runs=runs,
        tenants=len(tenants),
        subscriptions=len(subs),
        ready=ready,
        ready_with_effort=ready_with_effort,
        blocked=blocked,
        blockers_total=blockers_total,
        tsql_compatibility_pct_avg=(
            sum(tsql_vals) / len(tsql_vals) if tsql_vals else None
        ),
        projected_fabric_cu_total=sum(cu_vals) if cu_vals else None,
        actual_monthly_cost_total=sum(cost_vals) if cost_vals else None,
        fabric_estimated_monthly_cost_total=(
            sum(fabric_cost_vals) if fabric_cost_vals else None
        ),
        fabric_estimated_monthly_cost_1y_ri_total=(
            sum(fabric_1y_vals) if fabric_1y_vals else None
        ),
        fabric_estimated_monthly_cost_3y_ri_total=(
            sum(fabric_3y_vals) if fabric_3y_vals else None
        ),
        effort_hours_p50_total=sum(effort_p50_vals) if effort_p50_vals else None,
        effort_hours_p90_total=sum(effort_p90_vals) if effort_p90_vals else None,
        effort_days_p50_total=sum(effort_days_p50_vals) if effort_days_p50_vals else None,
        effort_days_p90_total=sum(effort_days_p90_vals) if effort_days_p90_vals else None,
    )


def _aggregate_top_blockers(extracts: list[dict[str, Any]]) -> list[EstateTopBlocker]:
    """De-duplicate blockers by (area, title) and count how many workspaces hit them."""
    # Map (area, title) -> dict
    bucket: dict[tuple[str, str], dict[str, Any]] = {}
    for ex in extracts:
        ws_key = ex["workspace_key"]
        for r in ex.get("blockers_raw") or []:
            key = (r.get("area") or "", r.get("title") or "")
            entry = bucket.setdefault(
                key,
                {
                    "area": key[0],
                    "title": key[1],
                    "fabric_action": r.get("fabric_action"),
                    "effort": r.get("effort", "medium"),
                    "workspaces": set(),
                    "occurrences": 0,
                    "example_run_id": ex["id"],
                },
            )
            entry["workspaces"].add(ws_key)
            entry["occurrences"] += 1
    rows = [
        EstateTopBlocker(
            area=v["area"],
            title=v["title"],
            fabric_action=v["fabric_action"],
            effort=v["effort"],
            workspaces=len(v["workspaces"]),
            occurrences=v["occurrences"],
            example_run_id=v["example_run_id"],
        )
        for v in bucket.values()
    ]
    rows.sort(key=lambda r: (-r.workspaces, -r.occurrences, r.area, r.title))
    return rows[:20]
