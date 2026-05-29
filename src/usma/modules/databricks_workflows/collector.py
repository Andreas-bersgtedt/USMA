"""Collector for Databricks workflows + clusters.

Wraps a ``databricks.sdk.WorkspaceClient`` (duck-typed) so unit tests
can inject any object exposing the same ``jobs.list`` / ``clusters.list``
surface. The collector returns flat lists of typed Pydantic models;
classification + roll-ups happen in :mod:`.analyzer`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

from .fabric_compat import classify_task
from .models import (
    InteractiveCluster,
    JobCluster,
    SqlWarehouse,
    SqlWarehouseDailyUsage,
    SqlWarehouseQuery,
    Workflow,
    WorkflowRun,
    WorkflowTask,
)
from .run_stats import (
    compute_vcore_seconds,
    resolve_worker_count,
    vcores_for_node_type,
)
from ..spark_pools.spark_history_client import VCORE_HOURS_TO_CU_HOURS


log = logging.getLogger(__name__)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Safe attribute lookup that also handles ``dict``-shaped SDK responses."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_value(obj: Any) -> str | None:
    """Return the canonical string for an SDK enum-or-string value."""
    if obj is None:
        return None
    val = _get(obj, "value", None)
    if isinstance(val, str):
        return val
    return str(obj) if obj is not None else None


class DatabricksWorkflowsCollector:
    """Enumerate workflows + clusters from a Databricks workspace."""

    def __init__(
        self,
        workspace_client: Any,
        *,
        collect_cluster_post_mortem: bool = True,
    ) -> None:
        self._ws = workspace_client
        self._node_type_vcpus: dict[str, int] | None = None
        # ``clusters.get`` cache for post-mortem cluster lookups (ephemeral
        # job clusters that have terminated — Databricks retains their
        # config for ~30 days). Maps cluster_id -> InteractiveCluster-shaped
        # record, or ``None`` to memoise a miss / API failure so we don't
        # retry on every run that references the same dead cluster.
        self._cluster_post_mortem: dict[str, InteractiveCluster | None] = {}
        self._collect_cluster_post_mortem = collect_cluster_post_mortem

    # ------------------------------------------------------------------ jobs

    def iter_workflows_with_tasks(self) -> Iterator[tuple[Workflow, list[WorkflowTask], list[JobCluster]]]:
        """Yield ``(workflow, tasks, job_clusters)`` for every job.

        Uses ``jobs.list(expand_tasks=True)`` so the per-task list and the
        ``job_clusters[]`` block are returned inline (Jobs API 2.2). For
        jobs where the listing still comes back stripped (older API
        versions, jobs with >100 tasks, dict-shaped fakes in tests),
        falls back to a per-job ``jobs.get(job_id)`` so cluster sizing
        can resolve ``job_cluster_key`` lookups downstream.
        """
        try:
            listing = self._ws.jobs.list(expand_tasks=True)
        except TypeError:
            # Older SDK / test double that doesn't accept the kwarg.
            listing = self._ws.jobs.list()
        for job in listing:
            try:
                yield self._convert_job_with_fallback(job)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to parse Databricks job: %s", exc)

    def _convert_job_with_fallback(
        self, job: Any,
    ) -> tuple[Workflow, list[WorkflowTask], list[JobCluster]]:
        """Convert ``job``; if tasks/job_clusters are empty, retry via
        ``jobs.get(job_id)`` which always returns the full settings."""
        wf, tasks, job_clusters = self._convert_job(job)
        if tasks or job_clusters:
            return wf, tasks, job_clusters
        # Empty — try the deep fetch. If the SDK / test double doesn't
        # expose ``get`` we silently keep the shallow record.
        get_fn = getattr(self._ws.jobs, "get", None)
        if get_fn is None or not wf.job_id:
            return wf, tasks, job_clusters
        try:
            full = get_fn(wf.job_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("jobs.get fallback failed for job %s: %s", wf.job_id, exc)
            return wf, tasks, job_clusters
        try:
            return self._convert_job(full)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to parse jobs.get response for job %s: %s", wf.job_id, exc)
            return wf, tasks, job_clusters

    def list_interactive_clusters(self) -> list[InteractiveCluster]:
        out: list[InteractiveCluster] = []
        for c in self._ws.clusters.list():
            try:
                out.append(_convert_cluster(c))
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to parse Databricks cluster: %s", exc)
        return out

    # ------------------------------------------------- current-user probe

    def current_user_access(self) -> tuple[bool, list[str]]:
        """Return ``(has_workspace_access, group_names)`` for the caller.

        Used by the analyzer to distinguish a permission-blocked empty
        result (SP can't see jobs/clusters) from a genuinely empty
        workspace (SP is admin and there really is nothing there).

        A caller has "workspace access" when:
          * they are a member of the ``admins`` group, OR
          * they carry the ``workspace-access`` entitlement.

        Returns ``(False, [])`` on any error so the analyzer falls back
        to the conservative entitlements message.
        """
        api = getattr(self._ws, "current_user", None)
        if api is None or not hasattr(api, "me"):
            return False, []
        try:
            me = api.me()
            groups_raw = _get(me, "groups", None) or []
            group_names: list[str] = []
            for g in groups_raw:
                name = _get(g, "display", None) or _get(g, "value", None)
                if name:
                    group_names.append(str(name))
            entitlements_raw = _get(me, "entitlements", None) or []
            entitlement_values = {
                _enum_value(_get(e, "value", e)) or "" for e in entitlements_raw
            }
            is_admin = "admins" in group_names
            has_workspace_access = is_admin or "workspace-access" in entitlement_values
            return has_workspace_access, group_names
        except Exception as exc:  # noqa: BLE001
            log.warning("current_user.me() probe failed: %s", exc)
            return False, []

    # ------------------------------------------------- node-type vCPU cache

    def node_type_vcpus(self) -> dict[str, int]:
        """Return (and cache) the workspace's ``node_type_id`` → ``num_cores`` map.

        Empty dict on any error — callers should treat missing entries as
        "unmapped" and record a caveat rather than failing the run.
        """
        if self._node_type_vcpus is not None:
            return self._node_type_vcpus
        out: dict[str, int] = {}
        try:
            resp = self._ws.clusters.list_node_types()
            items = _get(resp, "node_types", None) or []
            for nt in items:
                nid = _get(nt, "node_type_id", None)
                cores = _get(nt, "num_cores", None)
                if nid and cores is not None:
                    try:
                        out[str(nid)] = int(round(float(cores)))
                    except (TypeError, ValueError):
                        continue
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to enumerate node types: %s", exc)
        self._node_type_vcpus = out
        return out

    # ------------------------------------------------------------- job runs

    def iter_workflow_runs(
        self,
        *,
        job_ids: Iterable[int] | None = None,
        lookback_days: int = 90,
        job_index: dict[int, Workflow] | None = None,
        job_cluster_index: dict[tuple[int, str], JobCluster] | None = None,
        interactive_cluster_index: dict[str, InteractiveCluster] | None = None,
    ) -> Iterator[WorkflowRun]:
        """Yield :class:`WorkflowRun` records for each job over ``lookback_days``.

        ``job_ids`` defaults to every job seen via ``jobs.list()`` — when
        provided, only those jobs are queried (lets callers reuse the work
        already done in :meth:`iter_workflows_with_tasks`).

        ``job_cluster_index`` is ``{(job_id, job_cluster_key): JobCluster}``
        and ``interactive_cluster_index`` is ``{cluster_id: InteractiveCluster}``;
        both are used to resolve the cluster shape (node_type + worker count)
        for a run when the run payload doesn't carry the full ``cluster_spec``.
        """
        node_type_vcpus = self.node_type_vcpus()
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        start_time_from_ms = int(cutoff.timestamp() * 1000)
        job_ids_list = list(job_ids) if job_ids is not None else None
        if job_ids_list is None and job_index is not None:
            job_ids_list = list(job_index.keys())
        if job_ids_list is None:
            try:
                job_ids_list = [int(_get(j, "job_id", 0) or 0) for j in self._ws.jobs.list()]
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to enumerate jobs for run history: %s", exc)
                return

        for jid in job_ids_list:
            if not jid:
                continue
            try:
                runs_iter = self._ws.jobs.list_runs(
                    job_id=jid,
                    start_time_from=start_time_from_ms,
                    expand_tasks=True,
                    completed_only=False,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to list runs for job %s: %s", jid, exc)
                continue
            wf_name = None
            if job_index and jid in job_index:
                wf_name = job_index[jid].name
            for raw in runs_iter:
                try:
                    yield _convert_run(
                        raw,
                        job_id=jid,
                        job_name=wf_name,
                        node_type_vcpus=node_type_vcpus,
                        job_cluster_index=job_cluster_index or {},
                        interactive_cluster_index=interactive_cluster_index or {},
                        cluster_post_mortem=self._cluster_post_mortem_lookup,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("failed to parse run for job %s: %s", jid, exc)

    # --------------------------------------------- post-mortem cluster lookup

    def _cluster_post_mortem_lookup(self, cluster_id: str | None) -> InteractiveCluster | None:
        """Best-effort ``clusters.get`` for an ephemeral / terminated cluster.

        Databricks retains cluster config records for ~30 days after
        termination, so a ``clusters.get(cluster_id)`` call typically
        succeeds even for job-cluster runs whose cluster is long gone.
        Results (including misses) are memoised so a flapping ephemeral
        only costs one API call across all of its runs.

        Gated by ``collect_cluster_post_mortem`` — set False (or
        ``SMA_DATABRICKS_CLUSTER_POSTMORTEM=0``) on workspaces with
        thousands of dead clusters where the extra calls are too
        expensive.
        """
        if not cluster_id or not self._collect_cluster_post_mortem:
            return None
        if cluster_id in self._cluster_post_mortem:
            return self._cluster_post_mortem[cluster_id]
        get_fn = getattr(self._ws.clusters, "get", None)
        if get_fn is None:
            self._cluster_post_mortem[cluster_id] = None
            return None
        try:
            raw = get_fn(cluster_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("clusters.get post-mortem failed for %s: %s", cluster_id, exc)
            self._cluster_post_mortem[cluster_id] = None
            return None
        try:
            ic = _convert_cluster(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to parse clusters.get response for %s: %s", cluster_id, exc)
            self._cluster_post_mortem[cluster_id] = None
            return None
        self._cluster_post_mortem[cluster_id] = ic
        return ic

    # -------------------------------------------- interactive cluster usage

    def iter_cluster_events_runtime(
        self,
        cluster_id: str,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        """Return total RUNNING-time (seconds) for ``cluster_id`` in the window.

        Returns ``0.0`` and logs on any error (so a single cluster can't
        kill the whole run).
        """
        try:
            events = list(
                self._ws.clusters.events(
                    cluster_id,
                    start_time=int(window_start.timestamp() * 1000),
                    end_time=int(window_end.timestamp() * 1000),
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to fetch events for cluster %s: %s", cluster_id, exc)
            return 0.0
        return reconstruct_runtime_seconds(
            events, window_start=window_start, window_end=window_end,
        )

    # ----------------------------------------------------- SQL warehouses

    def list_sql_warehouses(self) -> list[SqlWarehouse]:
        """Enumerate SQL Warehouses (formerly "SQL endpoints").

        Returns an empty list (and logs at WARN) if the SDK surface is
        absent — older ``databricks-sdk`` releases exposed warehouses
        under ``ws.sql.warehouses`` instead of ``ws.warehouses``, and
        some on-prem-style stubs may have neither.
        """
        api = getattr(self._ws, "warehouses", None) or getattr(
            getattr(self._ws, "sql", None), "warehouses", None,
        )
        if api is None or not hasattr(api, "list"):
            log.info("workspace client has no warehouses.list; skipping SQL warehouses")
            return []
        out: list[SqlWarehouse] = []
        try:
            listing = api.list()
        except Exception as exc:  # noqa: BLE001
            log.warning("warehouses.list failed: %s", exc)
            return out
        for wh in listing:
            try:
                out.append(_convert_sql_warehouse(wh))
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to parse SQL warehouse: %s", exc)
        return out

    def iter_warehouse_queries(
        self,
        *,
        lookback_days: int = 30,
        max_queries: int | None = 5000,
        query_text_truncate: int = 500,
    ) -> Iterator[SqlWarehouseQuery]:
        """Yield :class:`SqlWarehouseQuery` records from ``query_history.list``.

        ``max_queries`` caps total emissions \u2014 query history on a busy
        workspace can run into the millions over 30 days, which would
        blow out the JSON report. The aggregate stats in the analyzer
        are computed from the same capped stream, so callers who care
        about precise totals on huge workspaces should raise the cap
        explicitly. Set to ``None`` to disable.

        Pagination note: ``QueryHistoryAPI.list`` returns a
        :class:`ListQueriesResponse` (NOT an iterator) in
        ``databricks-sdk`` >= 0.30. We walk ``next_page_token`` manually.
        Older SDKs that returned a bare iterator are also supported via
        the ``isinstance(..., (list, tuple)) or iter()`` fallback.
        """
        api = getattr(self._ws, "query_history", None) or getattr(
            getattr(self._ws, "sql", None), "query_history", None,
        )
        if api is None or not hasattr(api, "list"):
            log.info("workspace client has no query_history.list; skipping SQL queries")
            return
        cutoff_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp() * 1000,
        )
        # Build a typed filter when the SDK exposes one; otherwise rely
        # on the post-filter below to drop records older than the cutoff.
        filter_kwargs: dict[str, Any] = {}
        try:
            from databricks.sdk.service.sql import QueryFilter, TimeRange

            filter_kwargs["filter_by"] = QueryFilter(
                query_start_time_range=TimeRange(start_time_ms=cutoff_ms),
            )
        except Exception:  # noqa: BLE001 — fall back to unfiltered listing
            pass

        emitted = 0
        page_token: str | None = None
        while True:
            try:
                resp = api.list(
                    include_metrics=True,
                    **filter_kwargs,
                    **({"page_token": page_token} if page_token else {}),
                )
            except TypeError:
                # Older SDKs / test fakes that don't accept include_metrics:
                # retry without it. ``cpu_seconds`` will simply stay ``None``
                # on the resulting records.
                try:
                    resp = api.list(
                        **filter_kwargs,
                        **({"page_token": page_token} if page_token else {}),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("query_history.list failed: %s", exc)
                    return
            except Exception as exc:  # noqa: BLE001
                log.warning("query_history.list failed: %s", exc)
                return
            # SDK shape juggling. Three known cases:
            #   1. ``ListQueriesResponse`` with ``.res`` + ``.next_page_token``
            #      + ``.has_next_page`` (databricks-sdk >= ~0.30).
            #   2. Bare iterator/generator (older SDKs or test fakes).
            #   3. Plain list/tuple (test fakes that pre-materialise).
            page_items: Iterable[Any]
            next_token: str | None = None
            has_next: bool = False
            if hasattr(resp, "res") and not isinstance(resp, (list, tuple, str, bytes)):
                page_items = _get(resp, "res", None) or []
                next_token = _get(resp, "next_page_token", None)
                # Require a real string token; defensive against MagicMock
                # / SDK responses that return non-string sentinels.
                if not isinstance(next_token, str) or not next_token:
                    next_token = None
                has_next = bool(_get(resp, "has_next_page", False)) and bool(next_token)
            elif isinstance(resp, (list, tuple)):
                page_items = resp
            else:
                # Iterator / generator: drain and stop (no pagination).
                page_items = resp
            for raw in page_items:
                try:
                    rec = _convert_sql_query(
                        raw, query_text_truncate=query_text_truncate,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("failed to parse query_history record: %s", exc)
                    continue
                # Defensive post-filter when the SDK didn't honour the cutoff.
                if (
                    rec.start_time is not None
                    and rec.start_time.timestamp() * 1000 < cutoff_ms
                ):
                    continue
                yield rec
                emitted += 1
                if max_queries is not None and emitted >= max_queries:
                    log.info(
                        "query_history capped at max_queries=%s; "
                        "raise the cap to see more queries on this workspace.",
                        max_queries,
                    )
                    return
            if not has_next:
                return
            page_token = next_token

    # ---------------------------------------------- Unity Catalog system tables

    def collect_system_table_usage(
        self,
        *,
        execution_warehouse_id: str,
        warehouses: list[SqlWarehouse] | None = None,
        lookback_days: int = 30,
        wait_timeout_seconds: int = 50,
    ) -> list[SqlWarehouseDailyUsage]:
        """Query ``system.query.history`` + ``system.billing.usage`` for
        per-day CPU-seconds and DBU-hours, per SQL warehouse.

        Runs through ``statement_execution`` against
        ``execution_warehouse_id`` (which must be RUNNING-able and the
        caller must have ``CAN_USE`` on it). The two system tables are
        managed by Unity Catalog and require the account admin to enable
        the ``system.query`` and ``system.billing`` schemas and grant
        ``SELECT`` to the service principal.

        Returns one row per ``(warehouse_id, usage_date)``; warehouses
        absent from both system tables for the window simply don't
        appear in the output.
        """
        api = getattr(self._ws, "statement_execution", None)
        if api is None or not hasattr(api, "execute_statement"):
            log.info(
                "workspace client has no statement_execution.execute_statement; "
                "skipping system-table usage collection",
            )
            return []

        wh_name_by_id: dict[str, str] = {}
        if warehouses:
            for w in warehouses:
                if w.warehouse_id:
                    wh_name_by_id[w.warehouse_id] = w.name
        wh_ids = list(wh_name_by_id.keys())

        # Build an IN-list filter if we have a small set of warehouses;
        # otherwise leave it open and let the analyzer join afterwards.
        if wh_ids and len(wh_ids) <= 200:
            quoted = ",".join("'" + _sql_escape(wid) + "'" for wid in wh_ids)
            wh_filter_qh = f"AND warehouse_id IN ({quoted})"
            wh_filter_bill = (
                f"AND usage_metadata.warehouse_id IN ({quoted})"
            )
        else:
            wh_filter_qh = ""
            wh_filter_bill = ""

        qh_sql = f"""
            SELECT warehouse_id,
                   to_date(start_time) AS usage_date,
                   COUNT(*) AS query_count,
                   SUM(COALESCE(total_task_duration_ms, 0)) / 1000.0 AS total_task_seconds
            FROM system.query.history
            WHERE start_time >= current_timestamp() - INTERVAL {int(lookback_days)} DAYS
              AND warehouse_id IS NOT NULL
              {wh_filter_qh}
            GROUP BY warehouse_id, to_date(start_time)
            ORDER BY usage_date, warehouse_id
        """
        bill_sql = f"""
            SELECT usage_metadata.warehouse_id AS warehouse_id,
                   usage_date,
                   sku_name,
                   SUM(usage_quantity) AS dbu_hours
            FROM system.billing.usage
            WHERE usage_date >= current_date() - INTERVAL {int(lookback_days)} DAYS
              AND usage_metadata.warehouse_id IS NOT NULL
              AND lower(sku_name) LIKE '%sql%'
              {wh_filter_bill}
            GROUP BY usage_metadata.warehouse_id, usage_date, sku_name
            ORDER BY usage_date, warehouse_id
        """

        def _run(stmt: str) -> list[dict[str, Any]]:
            try:
                resp = api.execute_statement(
                    warehouse_id=execution_warehouse_id,
                    statement=stmt,
                    wait_timeout=f"{int(wait_timeout_seconds)}s",
                    on_wait_timeout="CANCEL",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("statement_execution failed: %s", exc)
                return []
            return _rows_from_statement_response(resp)

        qh_rows = _run(qh_sql)
        bill_rows = _run(bill_sql)

        merged: dict[tuple[str, str], SqlWarehouseDailyUsage] = {}
        for r in qh_rows:
            wid = str(r.get("warehouse_id") or "")
            day = str(r.get("usage_date") or "")
            if not wid or not day:
                continue
            key = (wid, day)
            merged[key] = SqlWarehouseDailyUsage(
                warehouse_id=wid,
                warehouse_name=wh_name_by_id.get(wid),
                usage_date=day,
                query_count=int(r.get("query_count") or 0),
                total_task_seconds=_to_float(r.get("total_task_seconds")),
            )
        for r in bill_rows:
            wid = str(r.get("warehouse_id") or "")
            day = str(r.get("usage_date") or "")
            if not wid or not day:
                continue
            key = (wid, day)
            dbu = _to_float(r.get("dbu_hours"))
            sku = r.get("sku_name")
            existing = merged.get(key)
            if existing is None:
                merged[key] = SqlWarehouseDailyUsage(
                    warehouse_id=wid,
                    warehouse_name=wh_name_by_id.get(wid),
                    usage_date=day,
                    dbu_hours=dbu,
                    sku_name=str(sku) if sku is not None else None,
                )
            else:
                # If multiple SKU rows roll up on the same day (e.g.
                # serverless + classic compute mix), sum DBU-hours and
                # join the SKU names.
                prev_dbu = existing.dbu_hours or 0.0
                existing.dbu_hours = prev_dbu + (dbu or 0.0)
                if sku is not None:
                    prev_sku = existing.sku_name
                    existing.sku_name = (
                        f"{prev_sku},{sku}" if prev_sku and str(sku) not in prev_sku.split(",")
                        else (prev_sku or str(sku))
                    )
        return sorted(
            merged.values(),
            key=lambda u: (u.usage_date, u.warehouse_id),
        )

    # ------------------------------------------------------------------ helpers

    def _convert_job(self, job: Any) -> tuple[Workflow, list[WorkflowTask], list[JobCluster]]:
        job_id = int(_get(job, "job_id", 0) or 0)
        settings = _get(job, "settings", None)
        name = _get(settings, "name", None) or _get(job, "name", None) or f"job-{job_id}"

        schedule = _get(settings, "schedule", None)
        tags_obj = _get(settings, "tags", None) or {}
        tags = {str(k): str(v) for k, v in (tags_obj.items() if hasattr(tags_obj, "items") else [])}

        tasks_in = list(_get(settings, "tasks", None) or [])
        job_clusters_in = list(_get(settings, "job_clusters", None) or [])
        format_val = _enum_value(_get(settings, "format", None))

        task_models: list[WorkflowTask] = []
        task_types: list[str] = []
        uses_serverless = False
        for raw_task in tasks_in:
            tm = _convert_task(raw_task, job_id=job_id, job_name=name)
            task_models.append(tm)
            if tm.task_type and tm.task_type not in task_types:
                task_types.append(tm.task_type)
            if tm.cluster_kind == "serverless":
                uses_serverless = True

        cluster_models: list[JobCluster] = []
        for raw_jc in job_clusters_in:
            cluster_models.append(_convert_job_cluster(raw_jc, job_id=job_id, job_name=name))

        wf = Workflow(
            job_id=job_id,
            name=name,
            creator_user_name=_get(job, "creator_user_name", None),
            run_as_user_name=_get(settings, "run_as_user_name", None),
            schedule_cron=_get(schedule, "quartz_cron_expression", None),
            schedule_timezone=_get(schedule, "timezone_id", None),
            schedule_pause_status=_enum_value(_get(schedule, "pause_status", None)),
            max_concurrent_runs=_get(settings, "max_concurrent_runs", None),
            task_count=len(task_models),
            task_types=task_types,
            job_cluster_count=len(cluster_models),
            uses_serverless=uses_serverless,
            tags=tags,
            format=format_val,
            has_continuous=_get(settings, "continuous", None) is not None,
        )
        return wf, task_models, cluster_models


def _convert_task(raw_task: Any, *, job_id: int, job_name: str) -> WorkflowTask:
    task_key = _get(raw_task, "task_key", None) or ""

    # The SDK exposes task-type fields as ``notebook_task`` / ``spark_python_task`` /
    # ``sql_task`` / ``pipeline_task`` / ``run_job_task`` / ... — exactly one is
    # populated per task. We detect which by walking the known field names.
    task_type, type_payload = _detect_task_type(raw_task)

    notebook_path = _get(type_payload, "notebook_path", None) if task_type == "notebook_task" else None
    python_file = _get(type_payload, "python_file", None) if task_type == "spark_python_task" else None
    sql_query_id = None
    sql_warehouse_id = None
    if task_type == "sql_task":
        sql_query = _get(type_payload, "query", None)
        sql_query_id = _get(sql_query, "query_id", None)
        # ``warehouse_id`` is the SQL Warehouse the task executes against.
        # Present on every sql_task in modern Jobs API payloads.
        wh = _get(type_payload, "warehouse_id", None)
        if wh:
            sql_warehouse_id = str(wh)
    dlt_pipeline_id = _get(type_payload, "pipeline_id", None) if task_type == "pipeline_task" else None

    depends_on_raw = _get(raw_task, "depends_on", None) or []
    depends_on = [
        _get(d, "task_key", None)
        for d in depends_on_raw
        if _get(d, "task_key", None) is not None
    ]

    cluster_kind, cluster_ref = _classify_cluster_ref(raw_task)
    support_label, note = classify_task(task_type)

    return WorkflowTask(
        job_id=job_id,
        job_name=job_name,
        task_key=task_key,
        task_type=task_type or "unknown",
        depends_on=[str(d) for d in depends_on],
        notebook_path=notebook_path,
        python_file=python_file,
        sql_query_id=sql_query_id,
        sql_warehouse_id=sql_warehouse_id,
        dlt_pipeline_id=dlt_pipeline_id,
        cluster_kind=cluster_kind,
        cluster_ref=cluster_ref,
        support=support_label,
        notes=[note] if note else [],
    )


_TASK_TYPE_FIELDS: tuple[str, ...] = (
    "notebook_task",
    "spark_python_task",
    "spark_jar_task",
    "spark_submit_task",
    "sql_task",
    "pipeline_task",
    "dbt_task",
    "run_job_task",
    "condition_task",
    "for_each_task",
)


def _detect_task_type(raw_task: Any) -> tuple[str | None, Any]:
    for field in _TASK_TYPE_FIELDS:
        payload = _get(raw_task, field, None)
        if payload is not None:
            return field, payload
    return None, None


def _classify_cluster_ref(raw_task: Any) -> tuple[str, str | None]:
    job_cluster_key = _get(raw_task, "job_cluster_key", None)
    if job_cluster_key:
        return "job_cluster", str(job_cluster_key)
    existing_cluster_id = _get(raw_task, "existing_cluster_id", None)
    if existing_cluster_id:
        return "existing_cluster", str(existing_cluster_id)
    # ``new_cluster`` block on the task itself is rare in modern multi-task
    # jobs but still legal; treat it like an inline job cluster.
    if _get(raw_task, "new_cluster", None) is not None:
        return "job_cluster", None
    # The Databricks SDK exposes serverless tasks via the absence of any
    # cluster field plus the ``environment_key`` hint.
    if _get(raw_task, "environment_key", None) is not None:
        return "serverless", str(_get(raw_task, "environment_key", None))
    return "unknown", None


def _convert_job_cluster(raw_jc: Any, *, job_id: int, job_name: str) -> JobCluster:
    spec = _get(raw_jc, "new_cluster", None)
    autoscale = _get(spec, "autoscale", None)
    return JobCluster(
        job_id=job_id,
        job_name=job_name,
        job_cluster_key=str(_get(raw_jc, "job_cluster_key", "") or ""),
        spark_version=_get(spec, "spark_version", None),
        node_type_id=_get(spec, "node_type_id", None),
        driver_node_type_id=_get(spec, "driver_node_type_id", None),
        num_workers=_get(spec, "num_workers", None),
        autoscale_min=_get(autoscale, "min_workers", None),
        autoscale_max=_get(autoscale, "max_workers", None),
        data_security_mode=_enum_value(_get(spec, "data_security_mode", None)),
        runtime_engine=_enum_value(_get(spec, "runtime_engine", None)),
    )


def _convert_cluster(c: Any) -> InteractiveCluster:
    autoscale = _get(c, "autoscale", None)
    return InteractiveCluster(
        cluster_id=str(_get(c, "cluster_id", "") or ""),
        cluster_name=str(_get(c, "cluster_name", "") or ""),
        state=_enum_value(_get(c, "state", None)),
        spark_version=_get(c, "spark_version", None),
        node_type_id=_get(c, "node_type_id", None),
        driver_node_type_id=_get(c, "driver_node_type_id", None),
        num_workers=_get(c, "num_workers", None),
        autoscale_min=_get(autoscale, "min_workers", None),
        autoscale_max=_get(autoscale, "max_workers", None),
        data_security_mode=_enum_value(_get(c, "data_security_mode", None)),
        runtime_engine=_enum_value(_get(c, "runtime_engine", None)),
        pinned=bool(_get(c, "pinned_by_user_name", None)),
        creator_user_name=_get(c, "creator_user_name", None),
    )


# ---------------------------------------------------------------- run records


def _outcome_from_state(life_cycle: str | None, result: str | None) -> str:
    lc = (life_cycle or "").upper()
    rs = (result or "").upper()
    if rs == "SUCCESS":
        return "succeeded"
    if rs in ("FAILED", "TIMEDOUT"):
        return "failed"
    if rs in ("CANCELED", "CANCELLED"):
        return "failed"  # treat as failed run for stats purposes
    if lc in ("TERMINATED", "INTERNAL_ERROR") and not rs:
        return "failed"
    if lc in ("PENDING", "RUNNING", "TERMINATING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY"):
        return "in_progress"
    return "unknown"


def _convert_run(
    raw: Any,
    *,
    job_id: int,
    job_name: str | None,
    node_type_vcpus: dict[str, int],
    job_cluster_index: dict[tuple[int, str], JobCluster],
    interactive_cluster_index: dict[str, InteractiveCluster],
    cluster_post_mortem: Any = None,
) -> WorkflowRun:
    run_id = int(_get(raw, "run_id", 0) or 0)
    state_obj = _get(raw, "state", None)
    life_cycle = _enum_value(_get(state_obj, "life_cycle_state", None))
    result_state = _enum_value(_get(state_obj, "result_state", None))
    outcome = _outcome_from_state(life_cycle, result_state)

    start_ms = _get(raw, "start_time", None)
    end_ms = _get(raw, "end_time", None)
    exec_ms = _get(raw, "execution_duration", None)
    start_dt = (
        datetime.fromtimestamp(int(start_ms) / 1000.0, tz=timezone.utc)
        if start_ms else None
    )
    end_dt = (
        datetime.fromtimestamp(int(end_ms) / 1000.0, tz=timezone.utc)
        if end_ms else None
    )
    duration_seconds: float | None = None
    if exec_ms is not None:
        try:
            duration_seconds = float(int(exec_ms)) / 1000.0
        except (TypeError, ValueError):
            duration_seconds = None

    # Resolve the cluster used. ``cluster_spec`` is the source of truth when
    # the SDK populates it (it carries the resolved ``new_cluster`` block);
    # otherwise we fall back to the cluster registry built from
    # ``iter_workflows_with_tasks`` + ``list_interactive_clusters``, and
    # finally to a post-mortem ``clusters.get`` for ephemeral job
    # clusters that have already terminated.
    cluster_kind = "unknown"
    cluster_id = None
    node_type_id = None
    driver_node_type_id = None
    num_workers_raw: int | None = None
    autoscale_min: int | None = None
    autoscale_max: int | None = None
    worker_count_source = "unknown"

    cluster_instance = _get(raw, "cluster_instance", None)
    if cluster_instance is not None:
        cluster_id = _get(cluster_instance, "cluster_id", None)

    cluster_spec = _get(raw, "cluster_spec", None)
    # Multi-task runs (Jobs API 2.2 with expand_tasks=True) put the
    # per-task ``cluster_spec`` on each entry of ``raw.tasks[]`` instead
    # of on the top-level run. Pick the first task that carries a usable
    # spec \u2014 sizing is dominated by the heaviest task in practice but
    # for the headline math any resolved task is far better than nothing.
    if cluster_spec is None:
        for t in (_get(raw, "tasks", None) or []):
            t_spec = _get(t, "cluster_spec", None)
            if t_spec is not None:
                cluster_spec = t_spec
                break
            # Some payloads inline ``new_cluster`` / ``job_cluster_key`` /
            # ``existing_cluster_id`` directly on the task without a
            # ``cluster_spec`` wrapper. Synthesize a dict so the lookup
            # logic below treats it uniformly.
            if (
                _get(t, "new_cluster", None) is not None
                or _get(t, "job_cluster_key", None) is not None
                or _get(t, "existing_cluster_id", None) is not None
            ):
                cluster_spec = {
                    "new_cluster": _get(t, "new_cluster", None),
                    "job_cluster_key": _get(t, "job_cluster_key", None),
                    "existing_cluster_id": _get(t, "existing_cluster_id", None),
                }
                break

    new_cluster_inline = _get(cluster_spec, "new_cluster", None) if cluster_spec else None
    job_cluster_key = _get(cluster_spec, "job_cluster_key", None) if cluster_spec else None
    existing_id = _get(cluster_spec, "existing_cluster_id", None) if cluster_spec else None

    if new_cluster_inline is not None:
        cluster_kind = "job_cluster"
        node_type_id = _get(new_cluster_inline, "node_type_id", None)
        driver_node_type_id = _get(new_cluster_inline, "driver_node_type_id", None)
        num_workers_raw = _get(new_cluster_inline, "num_workers", None)
        autoscale = _get(new_cluster_inline, "autoscale", None)
        autoscale_min = _get(autoscale, "min_workers", None)
        autoscale_max = _get(autoscale, "max_workers", None)
    elif job_cluster_key is not None:
        cluster_kind = "job_cluster"
        jc = job_cluster_index.get((job_id, str(job_cluster_key)))
        if jc is not None:
            node_type_id = jc.node_type_id
            driver_node_type_id = jc.driver_node_type_id
            num_workers_raw = jc.num_workers
            autoscale_min = jc.autoscale_min
            autoscale_max = jc.autoscale_max
    elif existing_id is not None:
        cluster_kind = "existing_cluster"
        cluster_id = cluster_id or str(existing_id)
        ic = interactive_cluster_index.get(str(existing_id))
        if ic is not None:
            node_type_id = ic.node_type_id
            driver_node_type_id = ic.driver_node_type_id
            num_workers_raw = ic.num_workers
            autoscale_min = ic.autoscale_min
            autoscale_max = ic.autoscale_max
    elif cluster_id is not None and str(cluster_id) in interactive_cluster_index:
        cluster_kind = "existing_cluster"
        ic = interactive_cluster_index[str(cluster_id)]
        node_type_id = ic.node_type_id
        driver_node_type_id = ic.driver_node_type_id
        num_workers_raw = ic.num_workers
        autoscale_min = ic.autoscale_min
        autoscale_max = ic.autoscale_max

    # Final fallback: ephemeral job-cluster runs whose ``cluster_spec``
    # was stripped from the listing AND whose cluster_id isn't in the
    # interactive (all-purpose) index. Databricks retains the config of
    # terminated clusters for ~30 days, so a ``clusters.get(cluster_id)``
    # post-mortem usually resolves the shape. Gated and memoised by the
    # collector \u2014 see ``DatabricksWorkflowsCollector.__init__``.
    if node_type_id is None and cluster_id is not None and cluster_post_mortem is not None:
        ic_pm = cluster_post_mortem(str(cluster_id))
        if ic_pm is not None:
            if cluster_kind == "unknown":
                cluster_kind = "job_cluster"
            node_type_id = ic_pm.node_type_id
            driver_node_type_id = ic_pm.driver_node_type_id
            if num_workers_raw is None:
                num_workers_raw = ic_pm.num_workers
            if autoscale_min is None:
                autoscale_min = ic_pm.autoscale_min
            if autoscale_max is None:
                autoscale_max = ic_pm.autoscale_max

    effective_workers = resolve_worker_count(
        num_workers=num_workers_raw,
        autoscale_min=autoscale_min,
        autoscale_max=autoscale_max,
        strategy="min",
    )
    if num_workers_raw is not None:
        worker_count_source = "static"
    elif autoscale_min is not None or autoscale_max is not None:
        worker_count_source = "autoscale_min"

    worker_vcores = vcores_for_node_type(node_type_id, node_type_vcpus)
    driver_vcores = vcores_for_node_type(driver_node_type_id, node_type_vcpus) or worker_vcores

    total_vcores, vcore_seconds = compute_vcore_seconds(
        driver_vcores=driver_vcores,
        worker_vcores=worker_vcores,
        worker_count=effective_workers,
        duration_seconds=duration_seconds,
    )
    vcore_hours = (vcore_seconds / 3600.0) if vcore_seconds is not None else None
    est_cu_hours = (vcore_hours * VCORE_HOURS_TO_CU_HOURS) if vcore_hours is not None else None

    return WorkflowRun(
        job_id=job_id,
        job_name=job_name or f"job-{job_id}",
        run_id=run_id,
        run_name=_get(raw, "run_name", None),
        run_type=_enum_value(_get(raw, "run_type", None)),
        trigger=_enum_value(_get(raw, "trigger", None)),
        state=life_cycle,
        result=result_state,
        outcome=outcome,  # type: ignore[arg-type]
        start_time=start_dt,
        end_time=end_dt,
        duration_seconds=duration_seconds,
        cluster_kind=cluster_kind,  # type: ignore[arg-type]
        cluster_id=str(cluster_id) if cluster_id is not None else None,
        node_type_id=node_type_id,
        driver_node_type_id=driver_node_type_id,
        num_workers=effective_workers,
        worker_count_source=worker_count_source,  # type: ignore[arg-type]
        driver_vcores=driver_vcores,
        worker_vcores=worker_vcores,
        total_vcores=total_vcores,
        vcore_seconds=vcore_seconds,
        vcore_hours=vcore_hours,
        est_cu_hours_fabric_spark=est_cu_hours,
        run_page_url=_get(raw, "run_page_url", None),
    )


# --------------------------------------------------------- cluster-event walk


def reconstruct_runtime_seconds(
    events: Iterable[Any],
    *,
    window_start: datetime,
    window_end: datetime,
) -> float:
    """Walk cluster events and sum total RUNNING-time clipped to the window.

    Pairs the latest ``RUNNING`` event with the next terminating event
    (``TERMINATING`` / ``TERMINATED`` / ``RESIZING`` / etc. that ends the
    running interval). Open intervals (no terminating event yet) are
    closed at ``window_end``.
    """
    start_ms_window = int(window_start.timestamp() * 1000)
    end_ms_window = int(window_end.timestamp() * 1000)

    # Events come back in descending order by default → re-sort ascending.
    ordered = sorted(
        events,
        key=lambda ev: int(_get(ev, "timestamp", 0) or 0),
    )
    total_seconds = 0.0
    open_start_ms: int | None = None
    for ev in ordered:
        ts_ms = int(_get(ev, "timestamp", 0) or 0)
        ev_type = _enum_value(_get(ev, "type", None)) or ""
        et = ev_type.upper()
        if et == "RUNNING":
            if open_start_ms is None:
                open_start_ms = ts_ms
        elif et in ("TERMINATING", "TERMINATED", "RESTARTING", "INIT_SCRIPTS_FINISHED", "EDITED"):
            # EDITED on a running cluster restarts it; close the interval too.
            if open_start_ms is not None:
                s = max(open_start_ms, start_ms_window)
                e = min(ts_ms, end_ms_window)
                if e > s:
                    total_seconds += (e - s) / 1000.0
                open_start_ms = None
    if open_start_ms is not None:
        s = max(open_start_ms, start_ms_window)
        e = end_ms_window
        if e > s:
            total_seconds += (e - s) / 1000.0
    return total_seconds


# ------------------------------------------------------------- SQL helpers


def _convert_sql_warehouse(wh: Any) -> SqlWarehouse:
    tags_obj = _get(wh, "tags", None)
    tags_pairs = _get(tags_obj, "custom_tags", None) if tags_obj is not None else None
    tags: dict[str, str] = {}
    if isinstance(tags_pairs, dict):
        tags = {str(k): str(v) for k, v in tags_pairs.items()}
    elif tags_pairs is not None:
        for kv in tags_pairs:
            k = _get(kv, "key", None)
            v = _get(kv, "value", None)
            if k is not None:
                tags[str(k)] = "" if v is None else str(v)
    elif isinstance(tags_obj, dict):  # plain dict shape
        tags = {str(k): str(v) for k, v in tags_obj.items()}

    serverless = _get(wh, "enable_serverless_compute", None)
    raw_type = _enum_value(_get(wh, "warehouse_type", None))
    warehouse_type = raw_type
    if warehouse_type is None and serverless is True:
        warehouse_type = "SERVERLESS"

    odbc = _get(wh, "odbc_params", None)
    jdbc_url = None
    if odbc is not None:
        host = _get(odbc, "hostname", None) or _get(odbc, "host", None)
        path = _get(odbc, "path", None)
        if host:
            jdbc_url = f"jdbc:databricks://{host}{path or ''}"

    return SqlWarehouse(
        warehouse_id=str(_get(wh, "id", "") or _get(wh, "warehouse_id", "") or ""),
        name=str(_get(wh, "name", "") or ""),
        warehouse_type=warehouse_type,
        cluster_size=_get(wh, "cluster_size", None),
        state=_enum_value(_get(wh, "state", None)),
        auto_stop_mins=_get(wh, "auto_stop_mins", None),
        enable_serverless_compute=serverless,
        enable_photon=_get(wh, "enable_photon", None),
        channel=_enum_value(_get(_get(wh, "channel", None), "name", None))
        or _enum_value(_get(wh, "channel", None)),
        min_num_clusters=_get(wh, "min_num_clusters", None),
        max_num_clusters=_get(wh, "max_num_clusters", None),
        num_clusters=_get(wh, "num_clusters", None),
        num_active_sessions=_get(wh, "num_active_sessions", None),
        creator_name=_get(wh, "creator_name", None),
        tags=tags,
        spot_instance_policy=_enum_value(_get(wh, "spot_instance_policy", None)),
        jdbc_url=jdbc_url,
    )


def _convert_sql_query(raw: Any, *, query_text_truncate: int = 500) -> SqlWarehouseQuery:
    start_ms = _get(raw, "query_start_time_ms", None) or _get(raw, "start_time", None)
    end_ms = _get(raw, "query_end_time_ms", None) or _get(raw, "end_time", None)
    dur_ms = (
        _get(raw, "duration", None)
        or _get(raw, "total_time_ms", None)
        or _get(raw, "execution_time_ms", None)
    )
    start_dt = (
        datetime.fromtimestamp(int(start_ms) / 1000.0, tz=timezone.utc)
        if start_ms else None
    )
    end_dt = (
        datetime.fromtimestamp(int(end_ms) / 1000.0, tz=timezone.utc)
        if end_ms else None
    )
    duration_seconds: float | None = None
    if dur_ms is not None:
        try:
            duration_seconds = float(int(dur_ms)) / 1000.0
        except (TypeError, ValueError):
            duration_seconds = None
    if duration_seconds is None and start_dt and end_dt:
        duration_seconds = (end_dt - start_dt).total_seconds()

    metrics = _get(raw, "metrics", None)
    rows = _get(metrics, "rows_produced_count", None) if metrics is not None else None
    if rows is None:
        rows = _get(raw, "rows_produced", None)
    bytes_read = _get(metrics, "read_bytes", None) if metrics is not None else None
    bytes_written = _get(metrics, "write_remote_bytes", None) if metrics is not None else None
    # ``task_total_time_ms`` = sum of CPU time across all executor tasks
    # for the query. Populated for CLASSIC / PRO warehouses when
    # ``include_metrics=True`` is passed to ``query_history.list``;
    # omitted by Databricks for SERVERLESS SQL warehouses, in which case
    # ``cpu_seconds`` stays ``None`` here (the analyzer aggregates only
    # non-null values).
    cpu_ms = (
        _get(metrics, "task_total_time_ms", None)
        if metrics is not None
        else None
    )
    if cpu_ms is None and metrics is not None:
        # Legacy SDK field name (pre-0.30).
        cpu_ms = _get(metrics, "total_task_duration_ms", None)
    cpu_seconds: float | None = None
    if cpu_ms is not None:
        try:
            cpu_seconds = float(int(cpu_ms)) / 1000.0
        except (TypeError, ValueError):
            cpu_seconds = None

    query_text = _get(raw, "query_text", None)
    if query_text and query_text_truncate and len(query_text) > query_text_truncate:
        query_text = query_text[:query_text_truncate] + "\u2026"

    channel = _get(raw, "channel_used", None)
    query_source = _enum_value(_get(raw, "query_source", None)) or _enum_value(channel)

    return SqlWarehouseQuery(
        query_id=str(_get(raw, "query_id", "") or ""),
        warehouse_id=(
            str(_get(raw, "warehouse_id", "") or "")
            or None
        ),
        status=_enum_value(_get(raw, "status", None)),
        statement_type=_enum_value(_get(raw, "statement_type", None)),
        user_name=_get(raw, "user_name", None) or _get(raw, "user_email", None),
        executed_as_user_name=_get(raw, "executed_as_user_name", None),
        query_source=query_source,
        query_text=query_text,
        start_time=start_dt,
        end_time=end_dt,
        duration_seconds=duration_seconds,
        cpu_seconds=cpu_seconds,
        rows_produced=int(rows) if rows is not None else None,
        bytes_read=int(bytes_read) if bytes_read is not None else None,
        bytes_written=int(bytes_written) if bytes_written is not None else None,
    )


def _sql_escape(value: str) -> str:
    """Escape a value for safe interpolation into a single-quoted SQL literal.

    The values we interpolate are SQL warehouse IDs returned by the
    Databricks SDK (hex strings), so this is mostly belt-and-braces —
    but doing it consistently keeps :func:`collect_system_table_usage`
    safe if the SDK ever returns a less constrained identifier.
    """
    return str(value).replace("'", "''")


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows_from_statement_response(resp: Any) -> list[dict[str, Any]]:
    """Convert a ``statement_execution.execute_statement`` response into
    a list of column-name -> value dicts.

    Handles the two shapes the Databricks SDK exposes:
      1. ``StatementResponse`` with ``result.data_array`` (inline result)
         and ``manifest.schema.columns`` for column names.
      2. Test fakes that just return ``{"columns": [...], "rows": [...]}``
         or ``[{...}, {...}]``.

    Failed / cancelled statements (``status.state != "SUCCEEDED"``)
    return an empty list and log a warning.
    """
    if resp is None:
        return []
    # Test-fake shapes first.
    if isinstance(resp, list):
        return [r for r in resp if isinstance(r, dict)]
    if isinstance(resp, dict) and "rows" in resp and "columns" in resp:
        cols = [str(c) for c in resp.get("columns") or []]
        return [dict(zip(cols, row)) for row in resp.get("rows") or []]

    status = _get(resp, "status", None)
    state = _enum_value(_get(status, "state", None))
    if state is not None and state != "SUCCEEDED":
        err = _get(status, "error", None)
        log.warning(
            "statement_execution did not succeed (state=%s): %s",
            state,
            _get(err, "message", err),
        )
        return []

    result = _get(resp, "result", None)
    data = _get(result, "data_array", None) or []
    manifest = _get(resp, "manifest", None)
    schema = _get(manifest, "schema", None)
    cols_raw = _get(schema, "columns", None) or []
    col_names: list[str] = []
    for c in cols_raw:
        name = _get(c, "name", None)
        if name is None:
            # Some SDK shapes use ``column_name``.
            name = _get(c, "column_name", None)
        col_names.append(str(name) if name is not None else "")
    out: list[dict[str, Any]] = []
    for row in data:
        if row is None:
            continue
        out.append({col_names[i]: row[i] for i in range(min(len(col_names), len(row)))})
    return out


__all__ = ["DatabricksWorkflowsCollector", "reconstruct_runtime_seconds"]
