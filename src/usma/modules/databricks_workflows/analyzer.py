"""Orchestrator for the databricks_workflows module."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from ..spark_pools.spark_history_client import VCORE_HOURS_TO_CU_HOURS
from .collector import DatabricksWorkflowsCollector
from .fabric_compat import (
    DEFAULT_PEAK_TO_AVG_HEADROOM,
    classify_warehouse,
    recommend_fabric_sku,
)
from .models import (
    DatabricksWorkflowsAnalysis,
    InteractiveClusterUsage,
    SqlWarehouse,
    SqlWarehouseDailyUsage,
    SqlWarehouseFabricMapping,
    SqlWarehouseStats,
    WorkflowRunWindowStats,
)
from .run_stats import (
    DEFAULT_WINDOWS,
    aggregate_runs_by_job,
    resolve_worker_count,
    vcores_for_node_type,
)


log = logging.getLogger(__name__)


class DatabricksWorkflowsAnalyzer:
    """Enumerate workflows + clusters from a Databricks workspace."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
        collector: DatabricksWorkflowsCollector | None = None,
        workspace_url: str | None = None,
        workspace_id: str | None = None,
        run_history_lookback_days: int = 90,
        collect_run_history: bool = True,
        collect_interactive_usage: bool = True,
        collect_cluster_post_mortem: bool = True,
        collect_sql_warehouses: bool = True,
        sql_query_lookback_days: int = 30,
        sql_query_max: int | None = 5000,
        collect_system_tables: bool = False,
        system_tables_warehouse_id: str | None = None,
    ) -> None:
        self._cfg = cfg
        self._progress = progress or NullProgress()
        self._collector = collector  # required at run() time; checked there
        self._workspace_url = workspace_url
        self._workspace_id = workspace_id
        self._run_history_lookback_days = run_history_lookback_days
        self._collect_run_history = collect_run_history
        self._collect_interactive_usage = collect_interactive_usage
        self._collect_cluster_post_mortem = collect_cluster_post_mortem
        self._collect_sql_warehouses = collect_sql_warehouses
        self._sql_query_lookback_days = sql_query_lookback_days
        self._sql_query_max = sql_query_max
        self._collect_system_tables = collect_system_tables
        self._system_tables_warehouse_id = system_tables_warehouse_id

    # ------------------------------------------------------------------ factory

    @classmethod
    def for_descriptor(
        cls,
        cfg: AppConfig,
        descriptor: Any,
        creds: Any,
        *,
        progress: ProgressReporter | None = None,
    ) -> "DatabricksWorkflowsAnalyzer":
        """Build an analyzer wired to a real Databricks ``WorkspaceClient``.

        Raises ``ValueError`` when ``descriptor`` is not a Databricks
        source — this module is Databricks-only (it does **not** subsume
        Synapse pipelines or ADF — see ADR D5).
        """
        from ...sources import SourceType
        from ...sources.databricks import provider_for_descriptor
        from ...sources.databricks.provider import _make_workspace_client

        if getattr(descriptor, "type", None) is not SourceType.DATABRICKS:
            raise ValueError(
                "DatabricksWorkflowsAnalyzer.for_descriptor requires a "
                "Databricks SourceDescriptor"
            )
        # Dispatch Azure / AWS / (future) GCP by ``extras['platform']``
        # — see ADR-0005. Reuses the cloud-agnostic
        # ``_make_workspace_client`` helper so the collector below is
        # blissfully unaware of which cloud it's running against.
        bundle = provider_for_descriptor(descriptor).make_clients(descriptor, creds)
        workspace_client = _make_workspace_client(
            workspace_url=bundle.workspace_url,
            cred=bundle.credential,
            pat_token=bundle.pat_token,
            oauth_client_id=bundle.oauth_client_id,
            oauth_client_secret=bundle.oauth_client_secret,
        )
        # Honour SMA_DATABRICKS_CLUSTER_POSTMORTEM=0 to disable the
        # post-mortem ``clusters.get`` fallback on workspaces with
        # thousands of dead clusters where the extra calls are too
        # expensive. Default is on.
        import os as _os
        pm_env = _os.environ.get("SMA_DATABRICKS_CLUSTER_POSTMORTEM", "1")
        post_mortem = pm_env.strip().lower() not in ("0", "false", "no", "off")
        # SQL warehouse + query-history collection. Default ON; off via
        # ``SMA_DATABRICKS_SQL=0``. Lookback default 30 d (warehouses
        # typically run >10x the query volume of jobs); cap at 5000
        # records via ``SMA_DATABRICKS_SQL_MAX``.
        sql_env = _os.environ.get("SMA_DATABRICKS_SQL", "1")
        collect_sql = sql_env.strip().lower() not in ("0", "false", "no", "off")
        try:
            sql_lookback = int(
                _os.environ.get("SMA_DATABRICKS_SQL_LOOKBACK_DAYS", "30"),
            )
        except ValueError:
            sql_lookback = 30
        sql_max_raw = _os.environ.get("SMA_DATABRICKS_SQL_MAX", "5000").strip()
        sql_max: int | None
        if sql_max_raw.lower() in ("0", "none", ""):
            sql_max = None
        else:
            try:
                sql_max = int(sql_max_raw)
            except ValueError:
                sql_max = 5000
        # Unity Catalog system tables (``system.query.history`` +
        # ``system.billing.usage``) — the only place Databricks exposes
        # CPU-seconds and DBU-hours for **Serverless** SQL warehouses.
        # Off by default because it (a) requires UC, (b) requires SP
        # grants on the system schemas, and (c) runs two queries on a
        # warehouse, which warms compute. Set ``SMA_DATABRICKS_SYSTEM_TABLES=1``
        # to turn it on; optionally pin which warehouse runs the queries
        # via ``SMA_DATABRICKS_SYSTEM_TABLES_WAREHOUSE=<warehouse_id>``
        # (otherwise the first warehouse from ``warehouses.list`` is used).
        sys_env = _os.environ.get("SMA_DATABRICKS_SYSTEM_TABLES", "0")
        collect_sys = sys_env.strip().lower() in ("1", "true", "yes", "on")
        sys_wh = _os.environ.get(
            "SMA_DATABRICKS_SYSTEM_TABLES_WAREHOUSE", "",
        ).strip() or None
        return cls(
            cfg,
            progress=progress,
            collector=DatabricksWorkflowsCollector(
                workspace_client,
                collect_cluster_post_mortem=post_mortem,
            ),
            workspace_url=bundle.workspace_url,
            workspace_id=bundle.workspace_id,
            collect_cluster_post_mortem=post_mortem,
            collect_sql_warehouses=collect_sql,
            sql_query_lookback_days=sql_lookback,
            sql_query_max=sql_max,
            collect_system_tables=collect_sys,
            system_tables_warehouse_id=sys_wh,
        )

    # ------------------------------------------------------------------ run

    def run(self) -> DatabricksWorkflowsAnalysis:
        if self._collector is None:
            raise RuntimeError(
                "DatabricksWorkflowsAnalyzer requires a collector — call "
                "for_descriptor(cfg, descriptor, creds) or pass collector=...",
            )

        result = DatabricksWorkflowsAnalysis(
            workspace_name=self._cfg.azure.workspace_name,
            workspace_url=self._workspace_url,
            workspace_id=self._workspace_id,
            subscription_id=self._cfg.azure.subscription_id,
            resource_group=self._cfg.azure.resource_group,
            generated_at=datetime.now(timezone.utc),
        )

        # Sub-tasks: workflows + interactive clusters + (optional) run history
        # + (optional) interactive-cluster usage + (optional) SQL warehouses.
        total_steps = (
            2
            + (1 if self._collect_run_history else 0)
            + (1 if self._collect_interactive_usage else 0)
            + (1 if self._collect_sql_warehouses else 0)
        )
        self._progress.start(total_steps, label="enumerating Databricks workflows")

        try:
            for workflow, tasks, job_clusters in self._collector.iter_workflows_with_tasks():
                result.workflows.append(workflow)
                result.tasks.extend(tasks)
                result.job_clusters.extend(job_clusters)
        except Exception as exc:  # noqa: BLE001
            log.warning("workflow collection failed: %s", exc)
            result.errors.append(format_error("workflows", exc))
        self._progress.step(label="workflows")

        try:
            result.interactive_clusters = self._collector.list_interactive_clusters()
        except Exception as exc:  # noqa: BLE001
            log.warning("interactive cluster collection failed: %s", exc)
            result.errors.append(format_error("interactive_clusters", exc))
        self._progress.step(label="interactive_clusters")

        # ----------------------------------------------------- run history
        job_index = {wf.job_id: wf for wf in result.workflows}
        job_cluster_index = {
            (jc.job_id, jc.job_cluster_key): jc for jc in result.job_clusters
        }
        interactive_cluster_index = {
            ic.cluster_id: ic for ic in result.interactive_clusters
        }

        if self._collect_run_history and self._collector is not None:
            try:
                runs = list(
                    self._collector.iter_workflow_runs(
                        lookback_days=self._run_history_lookback_days,
                        job_index=job_index,
                        job_cluster_index=job_cluster_index,
                        interactive_cluster_index=interactive_cluster_index,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("workflow run history collection failed: %s", exc)
                result.errors.append(format_error("workflow_runs", exc))
                runs = []
            result.workflow_runs = runs
            result.workflow_run_stats = aggregate_runs_by_job(runs)

            # Record caveats for runs we couldn't size.
            unmapped_node_types: set[str] = set()
            unresolved_shape = 0
            for r in runs:
                if r.vcore_hours is None and r.duration_seconds and r.duration_seconds > 0:
                    if r.node_type_id and r.worker_vcores is None:
                        unmapped_node_types.add(r.node_type_id)
                    if r.node_type_id is None:
                        unresolved_shape += 1
            if unmapped_node_types:
                result.cluster_sizing_caveats.append(
                    "Unmapped node_type_id values (no vCPU resolved): "
                    + ", ".join(sorted(unmapped_node_types))
                )
            if unresolved_shape > 0 and not self._collect_cluster_post_mortem:
                result.cluster_sizing_caveats.append(
                    f"{unresolved_shape} run(s) had no resolvable cluster shape \u2014 "
                    "set SMA_DATABRICKS_CLUSTER_POSTMORTEM=1 to enable "
                    "clusters.get() post-mortem lookup for ephemeral job clusters."
                )
            self._progress.step(label="workflow_runs")

        # -------------------------------------------- interactive usage
        if self._collect_interactive_usage and self._collector is not None and result.interactive_clusters:
            node_type_vcpus = self._collector.node_type_vcpus()
            windows = DEFAULT_WINDOWS
            now = datetime.now(timezone.utc)
            usages: list[InteractiveClusterUsage] = []
            for ic in result.interactive_clusters:
                effective_workers = resolve_worker_count(
                    num_workers=ic.num_workers,
                    autoscale_min=ic.autoscale_min,
                    autoscale_max=ic.autoscale_max,
                    strategy="min",
                )
                worker_vcores = vcores_for_node_type(ic.node_type_id, node_type_vcpus)
                driver_vcores = (
                    vcores_for_node_type(ic.driver_node_type_id, node_type_vcpus)
                    or worker_vcores
                )
                total_vcores = (
                    (driver_vcores + worker_vcores * effective_workers)
                    if (driver_vcores is not None and worker_vcores is not None and effective_workers is not None)
                    else None
                )
                worker_count_source = (
                    "static" if ic.num_workers is not None
                    else "autoscale_min" if (ic.autoscale_min is not None or ic.autoscale_max is not None)
                    else "unknown"
                )
                window_stats: list[WorkflowRunWindowStats] = []
                for w in windows:
                    window_start = now - timedelta(days=w)
                    try:
                        runtime_seconds = self._collector.iter_cluster_events_runtime(
                            ic.cluster_id, window_start=window_start, window_end=now,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("cluster %s events failed: %s", ic.cluster_id, exc)
                        runtime_seconds = 0.0
                    if total_vcores is not None and runtime_seconds > 0:
                        vcore_hours = (total_vcores * runtime_seconds) / 3600.0
                    else:
                        vcore_hours = 0.0
                    window_stats.append(
                        WorkflowRunWindowStats(
                            window_days=w,
                            run_count=0,  # cluster events don't map cleanly to "runs"
                            completed_count=0,
                            succeeded_count=0,
                            failed_count=0,
                            success_rate=None,
                            avg_duration_seconds=None,
                            total_vcore_hours=vcore_hours,
                            avg_vcore_hours_per_run=None,
                            est_cu_hours_fabric_spark=vcore_hours * VCORE_HOURS_TO_CU_HOURS,
                        )
                    )
                usages.append(
                    InteractiveClusterUsage(
                        cluster_id=ic.cluster_id,
                        cluster_name=ic.cluster_name,
                        node_type_id=ic.node_type_id,
                        driver_node_type_id=ic.driver_node_type_id,
                        num_workers=effective_workers,
                        worker_count_source=worker_count_source,  # type: ignore[arg-type]
                        driver_vcores=driver_vcores,
                        worker_vcores=worker_vcores,
                        total_vcores=total_vcores,
                        windows=window_stats,
                    )
                )
            result.interactive_cluster_usage = usages
            self._progress.step(label="interactive_cluster_usage")

        # -------------------------------------------- SQL warehouses + queries
        if self._collect_sql_warehouses and self._collector is not None:
            try:
                result.sql_warehouses = self._collector.list_sql_warehouses()
            except Exception as exc:  # noqa: BLE001
                log.warning("SQL warehouse collection failed: %s", exc)
                result.errors.append(format_error("sql_warehouses", exc))

            try:
                queries = list(
                    self._collector.iter_warehouse_queries(
                        lookback_days=self._sql_query_lookback_days,
                        max_queries=self._sql_query_max,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("SQL query history collection failed: %s", exc)
                result.errors.append(format_error("sql_warehouse_queries", exc))
                queries = []
            result.sql_warehouse_queries = queries
            result.sql_warehouse_stats = _aggregate_warehouse_queries(
                queries,
                warehouses=result.sql_warehouses,
                lookback_days=self._sql_query_lookback_days,
            )
            self._progress.step(label="sql_warehouses")

            # Optional: Unity Catalog system-tables CPU + DBU rollup.
            # Skipped silently when no warehouses exist (nothing to query).
            if self._collect_system_tables and result.sql_warehouses:
                exec_wh = self._system_tables_warehouse_id or (
                    result.sql_warehouses[0].warehouse_id
                )
                try:
                    result.sql_warehouse_daily_usage = (
                        self._collector.collect_system_table_usage(
                            execution_warehouse_id=exec_wh,
                            warehouses=result.sql_warehouses,
                            lookback_days=self._sql_query_lookback_days,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "system-table usage collection failed: %s", exc,
                    )
                    result.errors.append(
                        format_error("sql_warehouse_system_tables", exc),
                    )

            # Deterministic per-warehouse Fabric mapping (F-SKU
            # recommendation backed by CPU-seconds / DBU-hours). Computed
            # last so it can consume both stats (REST) and daily_usage
            # (system tables) signals when present.
            if result.sql_warehouses:
                result.sql_warehouse_fabric_mappings = (
                    _build_sql_warehouse_fabric_mappings(
                        warehouses=result.sql_warehouses,
                        stats=result.sql_warehouse_stats,
                        daily_usage=result.sql_warehouse_daily_usage,
                        lookback_days=self._sql_query_lookback_days,
                    )
                )

        # Roll-ups.
        result.workflow_count = len(result.workflows)
        result.task_count = len(result.tasks)
        result.unsupported_task_count = sum(
            1 for t in result.tasks if t.support == "unsupported"
        )
        result.partial_task_count = sum(
            1 for t in result.tasks if t.support == "partial"
        )
        result.sql_warehouse_count = len(result.sql_warehouses)
        result.sql_warehouse_query_count = len(result.sql_warehouse_queries)

        # Helpful caveat when the SP can reach the SQL plane but sees
        # zero Workflows / clusters. On Databricks-on-AWS in particular
        # this almost always means the SP is an account admin (or has
        # SQL-only entitlements) but hasn't been granted "Can View"
        # on the Jobs / Clusters surface of this workspace. Catches the
        # silent-empty case before the user has to spelunk the report.
        if (
            result.sql_warehouse_count > 0
            and result.workflow_count == 0
            and len(result.interactive_clusters) == 0
            and len(result.job_clusters) == 0
        ):
            # Probe ``current_user.me()`` to decide between the two
            # very different shapes this empty result can have:
            #   (a) SP is workspace admin / has workspace-access  ->
            #       the workspace really is empty (fresh trial,
            #       SQL-only tenant, etc.); say so plainly.
            #   (b) SP is account-admin only / has no workspace
            #       entitlements -> jobs/clusters are filtered out
            #       and we need the legacy entitlements caveat.
            try:
                has_access, group_names = self._collector.current_user_access()
            except Exception as exc:  # noqa: BLE001
                log.warning("current_user.me() probe failed: %s", exc)
                has_access, group_names = False, []
            if has_access:
                role_hint = (
                    f"caller is a member of {group_names!r}"
                    if group_names
                    else "caller has workspace-access entitlement"
                )
                result.cluster_sizing_caveats.append(
                    "Workspace appears empty \u2014 the service principal "
                    f"can see the SQL warehouse and {role_hint}, but the "
                    "Jobs, Clusters, Pipelines and Query History surfaces "
                    "all returned zero records. Double-check you are "
                    "pointing at the right workspace; on a fresh / trial "
                    "Databricks account this is expected."
                )
            else:
                result.cluster_sizing_caveats.append(
                    "SP sees SQL warehouses but zero Workflows / clusters \u2014 "
                    "this usually means the service principal lacks workspace "
                    "entitlements for jobs/clusters. In the Databricks workspace, "
                    "go to Settings \u2192 Identity and access \u2192 Service principals "
                    "\u2192 (your SP) \u2192 Entitlements and grant 'Workspace access' "
                    "plus 'Allow cluster creation' (or assign the SP to a group "
                    "with Can View on the relevant jobs/clusters)."
                )

        return result


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile on a sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _aggregate_warehouse_queries(
    queries: list[Any],
    *,
    warehouses: list[Any],
    lookback_days: int,
) -> list[SqlWarehouseStats]:
    """Group queries by ``warehouse_id`` and compute per-warehouse stats.

    Warehouses with zero queries in the window are still emitted with
    ``query_count=0`` so reports show every warehouse � same convention
    as Workflows / WorkflowRunStats.
    """
    by_wh: dict[str | None, list[Any]] = {}
    for q in queries:
        by_wh.setdefault(q.warehouse_id, []).append(q)

    wh_name_by_id = {w.warehouse_id: w.name for w in warehouses}
    seen: set[str | None] = set()
    out: list[SqlWarehouseStats] = []

    def _build(wh_id: str | None, name: str | None, qs: list[Any]) -> SqlWarehouseStats:
        seen.add(wh_id)
        succeeded = sum(1 for q in qs if (q.status or "").upper() == "FINISHED")
        failed = sum(1 for q in qs if (q.status or "").upper() == "FAILED")
        canceled = sum(1 for q in qs if (q.status or "").upper() in ("CANCELED", "CANCELLED"))
        durations = sorted(
            float(q.duration_seconds) for q in qs
            if q.duration_seconds is not None and q.duration_seconds >= 0
        )
        total_dur = sum(durations)
        avg_dur = (total_dur / len(durations)) if durations else None
        rows_total = sum(int(q.rows_produced or 0) for q in qs)
        bytes_total = sum(int(q.bytes_read or 0) for q in qs)
        users = {q.user_name for q in qs if q.user_name}
        from_jobs = sum(
            1 for q in qs
            if (q.query_source or "").upper() == "JOB"
        )
        finished = succeeded + failed + canceled
        success_rate = (succeeded / finished) if finished else None
        # CPU-time rollup. Only count queries that actually carry the
        # ``cpu_seconds`` metric (Serverless SQL queries leave it None).
        cpu_vals = [float(q.cpu_seconds) for q in qs if q.cpu_seconds is not None]
        total_cpu = sum(cpu_vals) if cpu_vals else None
        return SqlWarehouseStats(
            warehouse_id=str(wh_id) if wh_id else "",
            warehouse_name=name,
            lookback_days=lookback_days,
            query_count=len(qs),
            succeeded_count=succeeded,
            failed_count=failed,
            canceled_count=canceled,
            success_rate=success_rate,
            total_duration_seconds=total_dur,
            avg_duration_seconds=avg_dur,
            p50_duration_seconds=_percentile(durations, 0.50),
            p95_duration_seconds=_percentile(durations, 0.95),
            total_rows_produced=rows_total,
            total_bytes_read=bytes_total,
            unique_users=len(users),
            queries_from_jobs=from_jobs,
            total_cpu_seconds=total_cpu,
            queries_with_cpu_metric=len(cpu_vals),
        )

    # Emit one row per known warehouse first (alphabetised by name) so
    # warehouses with zero recent queries still show up.
    for wh in sorted(warehouses, key=lambda w: (w.name or "").lower()):
        qs = by_wh.get(wh.warehouse_id, [])
        out.append(_build(wh.warehouse_id, wh.name, qs))

    # Then any orphaned warehouse_ids that appeared in query history
    # but not in warehouses.list() (deleted, or cross-workspace federation).
    for wh_id, qs in by_wh.items():
        if wh_id in seen:
            continue
        out.append(_build(wh_id, wh_name_by_id.get(wh_id or ""), qs))

    return out


def _build_sql_warehouse_fabric_mappings(
    *,
    warehouses: list[SqlWarehouse],
    stats: list[SqlWarehouseStats],
    daily_usage: list[SqlWarehouseDailyUsage],
    lookback_days: int,
    headroom: float = DEFAULT_PEAK_TO_AVG_HEADROOM,
) -> list[SqlWarehouseFabricMapping]:
    """Build a deterministic Fabric mapping for every SQL warehouse.

    For each warehouse: derive ``avg_concurrent_cus`` from REST
    ``total_cpu_seconds`` when available, else fall back to the system-tables
    ``total_task_seconds`` sum (Serverless path), then pick the smallest
    Fabric F-SKU that absorbs ``avg * headroom``. Surfaces every input
    that fed the decision so the Dashboard / runbook can show "why".
    """
    stats_by_id: dict[str, SqlWarehouseStats] = {
        s.warehouse_id: s for s in stats if s.warehouse_id
    }
    daily_by_id: dict[str, list[SqlWarehouseDailyUsage]] = {}
    for d in daily_usage:
        daily_by_id.setdefault(d.warehouse_id, []).append(d)

    window_seconds = float(max(lookback_days, 1) * 86400)
    out: list[SqlWarehouseFabricMapping] = []
    for wh in warehouses:
        support, target, classify_note = classify_warehouse(wh.warehouse_type)
        st = stats_by_id.get(wh.warehouse_id)
        daily = daily_by_id.get(wh.warehouse_id, [])

        total_cpu = st.total_cpu_seconds if st else None
        cpu_count = st.queries_with_cpu_metric if st else 0
        task_vals = [d.total_task_seconds for d in daily if d.total_task_seconds is not None]
        total_task = sum(task_vals) if task_vals else None
        dbu_vals = [d.dbu_hours for d in daily if d.dbu_hours is not None]
        total_dbu = sum(dbu_vals) if dbu_vals else None

        # Sizing prefers REST CPU; falls back to system-table task time
        # (Serverless). Both nominally measure executor task seconds.
        cpu_for_sizing = total_cpu if (total_cpu and total_cpu > 0) else total_task
        avg_cus: float | None = None
        if cpu_for_sizing and cpu_for_sizing > 0:
            avg_cus = float(cpu_for_sizing) / window_seconds

        sku = recommend_fabric_sku(avg_cus, headroom=headroom)

        has_rest = bool(total_cpu and total_cpu > 0)
        has_sys = bool((total_task and total_task > 0) or (total_dbu and total_dbu > 0))
        if has_rest and has_sys:
            evidence = "both"
            confidence = "high"
        elif has_rest:
            evidence = "rest_metrics"
            confidence = "medium"
        elif has_sys:
            evidence = "system_tables"
            confidence = "medium"
        else:
            evidence = "none"
            confidence = "low"

        notes: list[str] = [classify_note]
        if evidence == "none":
            notes.append(
                "No usage signal in the lookback window — sizing recommendation "
                "withheld until a run captures query metrics (REST "
                "include_metrics) or SMA_DATABRICKS_SYSTEM_TABLES=1 is set."
            )
        if total_cpu and total_task:
            ratio = (total_task / total_cpu) if total_cpu else 0.0
            if ratio > 2.0 or ratio < 0.5:
                notes.append(
                    f"REST CPU ({total_cpu:.0f}s) and system.query.history task "
                    f"time ({total_task:.0f}s) disagree by >2x — verify before "
                    "trusting the F-SKU recommendation."
                )
        if sku and sku.endswith("+"):
            notes.append(
                "Workload exceeds the largest standard F-SKU; split across "
                "multiple Fabric capacities or contact Microsoft for guidance."
            )

        out.append(
            SqlWarehouseFabricMapping(
                warehouse_id=wh.warehouse_id,
                warehouse_name=wh.name,
                source_warehouse_type=wh.warehouse_type,
                target_fabric_artifact=target,
                recommended_sku=sku,
                support=support,
                confidence=confidence,
                lookback_days=lookback_days,
                total_cpu_seconds=total_cpu,
                queries_with_cpu_metric=cpu_count,
                total_task_seconds=total_task,
                total_dbu_hours=total_dbu,
                avg_concurrent_cus=avg_cus,
                peak_to_avg_headroom=headroom,
                evidence_source=evidence,
                notes=notes,
            )
        )

    return out
