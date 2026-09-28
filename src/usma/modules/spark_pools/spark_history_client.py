"""Synapse Spark Livy history client.

Wraps `azure-synapse-spark` (per-pool `SparkClient`) and yields normalized
`SparkRunRecord` instances for both interactive (Livy session) and scheduled
(Livy batch / Spark Job Definition / pipeline-triggered) Spark jobs.

vCore→CU mapping: 1 CU = 2 Spark vCores  ⇒  1 vCore-second = 0.5 CU-second
(see https://learn.microsoft.com/fabric/data-engineering/billing-spark).
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

from azure.synapse.spark import SparkClient

from ...auth import get_credential
from ...config import AzureConfig
from .models import SparkRunRecord

log = logging.getLogger(__name__)

_LIVY_API_VERSION = "2019-11-01-preview"
# The Synapse Livy endpoint hard-caps `size` at 20 per page (server returns
# HTTP 400 "Size cannot be more than 20" otherwise). Documented here:
# https://learn.microsoft.com/rest/api/synapse/data-plane/spark-batch/get-spark-batch-jobs
_MAX_PAGE_SIZE = 20
_HARD_LIMIT = 100_000

# Synapse Spark vCore → Fabric CU mapping (1 CU = 2 Spark vCores).
VCORE_HOURS_TO_CU_HOURS: float = 0.5


# Livy state / result classification, aligned with the Synapse Studio UI.
#
# Studio shows a Spark run as "Succeeded" whenever the user code completed
# successfully, regardless of how the underlying cluster was torn down
# afterwards. The Livy `state` field reflects cluster lifecycle
# (`success` / `dead` / `killed` / `shutting_down` / `stopped` /
# `error`), while `result` reflects the actual job outcome
# (`Succeeded` / `Failed` / `FailedTimeout` / `Cancelled` / `Uncertain`).
#
# Both Synapse pipelines and Spark Job Definitions routinely shut down a
# session/batch as `dead` or `killed` immediately after the notebook code
# returns successfully, and the platform sometimes records this teardown
# as `result="Cancelled"` ("session was reaped by the framework") even
# though the notebook itself succeeded. We therefore only count an
# **explicit** failure result (`Failed` / `FailedTimeout`) — or a real
# Spark `error` state, or a `Cancelled` result that interrupted a
# non-terminal run — as a failure. Everything else that has reached a
# terminal state is treated as succeeded, matching Studio.
_TERMINAL_CLEANUP_STATES = {"dead", "killed", "shutting_down", "stopped"}
_RUNNING_STATES = {"not_started", "starting", "idle", "busy", "recovering", "running"}

# Synapse pipeline-triggered notebook executions and Spark Job Definition
# runs *also* land on the Livy session API (Synapse Studio shows them as
# Type = "Spark session"). The authoritative discriminator from the
# pipeline's NotebookActivity is the Spark configuration injected into
# `livyInfo.jobCreationRequest.conf`:
#
#   spark.synapse.context.pipelinejobid   — parent pipeline run id
#   spark.synapse.context.activityrunid   — pipeline activity run id
#   spark.synapse.context.activityname    — pipeline activity name
#   spark.synapse.nbs.runid               — notebook run id
#
# These keys are *only* set by the Synapse pipeline framework (and Spark
# Job Definition runtime) — user-attached interactive notebooks in Studio
# do not have them. The previously-used auto-name pattern
# `<name>_<pool>_<unix-ts>` is unreliable because Studio assigns the same
# format to interactive sessions as well, causing false positives. Tags
# are kept as a defensive secondary signal but are typically empty `{}`
# in real telemetry.
_PIPELINE_CONF_KEYS = (
    "spark.synapse.context.pipelinejobid",
    "spark.synapse.context.activityrunid",
    "spark.synapse.context.activityname",
    "spark.synapse.nbs.runid",
)
_SCHEDULED_TAG_KEY_HINTS = ("pipeline", "trigger", "scheduler")
_SCHEDULED_TAG_VALUE_HINTS = ("sparknotebook", "sparkjobdefinition", "scheduled")


def _conf_from_raw(raw: Any) -> dict[str, Any] | None:
    """Extract `livy_info.job_creation_request.conf` from an SDK record.

    Returns the dict if present, else None. The Synapse Spark SDK exposes
    this as nested attributes (snake_case); the underlying Livy JSON uses
    camelCase, so we tolerate both shapes.
    """
    livy_info = getattr(raw, "livy_info", None)
    if livy_info is None:
        return None
    creation_req = (
        getattr(livy_info, "job_creation_request", None)
        or getattr(livy_info, "jobCreationRequest", None)
    )
    if creation_req is None:
        return None
    conf = getattr(creation_req, "configuration", None)
    if conf is None:
        conf = getattr(creation_req, "conf", None)
    if isinstance(conf, dict):
        return conf
    return None


def _classify_trigger(raw: Any, *, livy_kind: str) -> str:
    """Maps a raw Livy record to the user-facing trigger kind.

    - Livy batch endpoint  → always "scheduled" (programmatic submission;
      Synapse pipelines / Spark Job Definitions also surface batches here).
    - Livy session endpoint → "scheduled" iff the Spark conf carries one
      of the pipeline-injected `spark.synapse.context.*` keys (or tags
      explicitly mark the run as pipeline/trigger-driven). Otherwise
      "interactive" (user-attached notebook in Studio).
    """
    if livy_kind == "batch":
        return "scheduled"
    # Primary signal: pipeline conf keys are present iff the Synapse
    # pipeline framework / Spark Job Definition runtime submitted the run.
    conf = _conf_from_raw(raw)
    if conf:
        for k in _PIPELINE_CONF_KEYS:
            v = conf.get(k)
            if v is not None and str(v).strip():
                return "scheduled"
    # Defensive secondary: tags. Real Synapse telemetry shows tags = {}
    # for pipeline runs in 2026, but historical workspaces sometimes
    # populate them, so we still inspect.
    tags = getattr(raw, "tags", None)
    if isinstance(tags, dict):
        for k, v in tags.items():
            kl = str(k).lower()
            vl = str(v).lower() if v is not None else ""
            if any(h in kl for h in _SCHEDULED_TAG_KEY_HINTS):
                return "scheduled"
            if any(h in vl for h in _SCHEDULED_TAG_VALUE_HINTS):
                return "scheduled"
    return "interactive"


class SparkHistoryClient:
    """Fetches Livy batch + session history per Spark pool."""

    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        self._endpoint = f"https://{azure.workspace_name}.dev.azuresynapse.net"
        self._credential = get_credential(azure)
        self._collection_status: dict[tuple[str, str], bool] = {}

    def _client(self, pool_name: str) -> SparkClient:
        return SparkClient(
            credential=self._credential,
            endpoint=self._endpoint,
            spark_pool_name=pool_name,
            livy_api_version=_LIVY_API_VERSION,
        )

    def collection_status(
        self,
        pool_name: str,
        livy_kind: Literal["batch", "session"],
    ) -> bool | None:
        """Whether paging reached the end of the endpoint's available history."""
        return self._collection_status.get((pool_name, livy_kind))

    # ------------------------------------------------------------------ batches
    def iter_batch_jobs(
        self,
        pool_name: str,
        *,
        start: datetime,
        limit: int = 5000,
        page_size: int = _MAX_PAGE_SIZE,
    ) -> Iterator[SparkRunRecord]:
        """Yields normalized Livy batch records (always scheduled)."""
        yield from self._iter(
            pool_name,
            livy_kind="batch",
            list_fn=lambda c, off, sz: c.spark_batch.get_spark_batch_jobs(
                from_parameter=off, size=sz, detailed=True
            ),
            sessions_field="sessions",
            start=start,
            limit=limit,
            page_size=page_size,
        )

    # ----------------------------------------------------------------- sessions
    def iter_sessions(
        self,
        pool_name: str,
        *,
        start: datetime,
        limit: int = 5000,
        page_size: int = _MAX_PAGE_SIZE,
    ) -> Iterator[SparkRunRecord]:
        """Yields normalized Livy session records.

        Sessions are classified per-record as `scheduled` (pipeline /
        Spark Job Definition / trigger) or `interactive` (user-attached
        notebook in Studio) using `_classify_trigger`.
        """
        yield from self._iter(
            pool_name,
            livy_kind="session",
            list_fn=lambda c, off, sz: c.spark_session.get_spark_sessions(
                from_parameter=off, size=sz, detailed=True
            ),
            sessions_field="sessions",
            start=start,
            limit=limit,
            page_size=page_size,
        )

    # --------------------------------------------------------------- internals
    def _iter(
        self,
        pool_name: str,
        *,
        livy_kind: Literal["batch", "session"],
        list_fn,
        sessions_field: str,
        start: datetime,
        limit: int,
        page_size: int,
    ) -> Iterator[SparkRunRecord]:
        client = self._client(pool_name)
        complete = False
        try:
            offset = 0
            yielded = 0
            scanned = 0
            page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
            limit = min(limit, _HARD_LIMIT)
            server_total: int | None = None
            # Diagnostic: histogram of (state, result, trigger_kind, outcome)
            # tuples per (pool, livy_kind). Surfaces at INFO so a user can
            # confirm classification when Studio and the analyzer disagree.
            outcome_hist: Counter[tuple[str, str, str, str]] = Counter()
            while scanned < limit:
                resp = list_fn(client, offset, page_size)
                items = (
                    getattr(resp, sessions_field, None)
                    or getattr(resp, "sessions", None)
                    or []
                )
                # The Livy `total` field reports how many runs the server has
                # for this pool. Capture it on the first page so we can detect
                # under-collection at the end of the loop.
                if server_total is None:
                    for total_name in ("total", "total_sessions", "total_batches"):
                        candidate = getattr(resp, total_name, None)
                        if candidate is not None:
                            server_total = int(candidate)
                            break
                if not items:
                    complete = server_total is None or scanned >= server_total
                    break
                for raw in items:
                    scanned += 1
                    trigger_kind = _classify_trigger(raw, livy_kind=livy_kind)
                    rec = _normalize(
                        raw,
                        kind=trigger_kind,
                        pool=pool_name,
                        livy_kind=livy_kind,
                    )
                    if rec is None:
                        continue
                    outcome_hist[(
                        (rec.state or "").lower() or "∅",
                        (rec.result or "").lower() or "∅",
                        trigger_kind,
                        rec.outcome,
                    )] += 1
                    # Fetch overlap candidates irrespective of submit time. An
                    # older submission can have an execution interval inside
                    # the observation window; aggregation clips it precisely.
                    if _overlaps_window(rec, start):
                        yield rec
                        yielded += 1
                    if scanned >= limit:
                        break
                if scanned >= limit:
                    complete = server_total is not None and scanned >= server_total
                    break
                if len(items) < page_size:
                    complete = server_total is None or scanned >= server_total
                    break
                offset += len(items)
            log.info(
                "spark_history pool=%s livy=%s scanned=%d yielded=%d server_total=%s window_start=%s",
                pool_name, livy_kind, scanned, yielded, server_total, start.isoformat(),
            )
            if outcome_hist:
                top = ", ".join(
                    f"state={st}/result={rs}/trigger={tk}→{oc}:{n}"
                    for (st, rs, tk, oc), n in outcome_hist.most_common(10)
                )
                log.info(
                    "spark_history pool=%s livy=%s outcome_hist (top): %s",
                    pool_name, livy_kind, top,
                )
            if server_total is not None and scanned < server_total:
                log.warning(
                    "spark_history pool=%s livy=%s under-collected %d of %d runs (raise SMA_SPARK_RUN_LIMIT or check pagination)",
                    pool_name, livy_kind, scanned, server_total,
                )
        finally:
            self._collection_status[(pool_name, livy_kind)] = complete
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _overlaps_window(record: SparkRunRecord, start: datetime) -> bool:
    run_start = record.accounting_start_at or record.submitted_at
    if run_start is None:
        return False
    if record.ended_at is None:
        return True
    return _utc(record.ended_at) > _utc(start)


# ---------------------------------------------------------------- normalization

def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def vcore_shape_from_app_info(
    app_info: dict[str, Any] | None,
    creation_request: Any = None,
) -> tuple[int | None, int | None, int | None]:
    """Returns (driver_cores, executor_cores, num_executors).

    Prefers `app_info` (populated by Livy once the cluster is provisioned);
    falls back to `livy_info.job_creation_request` when present.
    """
    dc = ec = nx = None
    if app_info:
        dc = _as_int(_mapping_value(app_info, "driverCores", "driver_cores"))
        ec = _as_int(_mapping_value(app_info, "executorCores", "executor_cores"))
        nx = _as_int(_mapping_value(
            app_info, "numExecutors", "num_executors", "executorCount",
        ))
    if creation_request is not None:
        dc = dc if dc is not None else _as_int(_field(creation_request, "driver_cores"))
        ec = ec if ec is not None else _as_int(_field(creation_request, "executor_cores"))
        nx = nx if nx is not None else _as_int(_field(creation_request, "executor_count"))
    return dc, ec, nx


def _mapping_value(values: Any, *keys: str) -> Any:
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return None


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _first_datetime(*values: Any) -> datetime | None:
    value = next((value for value in values if isinstance(value, datetime)), None)
    return _utc(value) if value is not None else None


def compute_vcore_seconds(
    driver_cores: int | None,
    executor_cores: int | None,
    num_executors: int | None,
    duration_seconds: float | None,
) -> tuple[int | None, float | None]:
    """Returns (total_vcores, vcore_seconds). Returns (None, None) if shape unknown."""
    if (
        driver_cores is None
        or executor_cores is None
        or num_executors is None
        or duration_seconds is None
        or duration_seconds <= 0
    ):
        return None, None
    total_vcores = driver_cores + executor_cores * num_executors
    return total_vcores, float(total_vcores) * float(duration_seconds)


def _classify_state(
    state: str | None,
    result: str | None,
    *,
    livy_kind: str = "batch",
) -> str:
    """Map Livy `state` + `result` to succeeded / failed / in_progress.

    Aligned with the Synapse Studio Monitor view, which is the
    user-facing source of truth for Spark run outcomes:

    - Explicit success (`result=Succeeded` or `state=success`) → succeeded.
    - Explicit failure (`result=Failed` or `result=FailedTimeout`,
      or `state=error`) → failed.
    - `result=Cancelled` while the run was still non-terminal → failed
      (genuine user/admin cancel mid-execution).
    - Any other terminal cleanup state (`dead` / `killed` /
      `shutting_down` / `stopped`) — including when paired with
      `result=Cancelled` post-completion, which is how Synapse pipelines
      and Spark Job Definitions routinely reap sessions/batches after
      the notebook code returns successfully — → succeeded.
    - Otherwise → in_progress.

    `livy_kind` is accepted for backward compatibility but no longer
    changes the outcome: both batches and sessions follow the same
    Studio-aligned rules.
    """
    s = (state or "").lower()
    r = (result or "").lower()
    # Explicit success.
    if r == "succeeded" or s == "success":
        return "succeeded"
    # Explicit failure.
    if r in ("failed", "failedtimeout"):
        return "failed"
    if s == "error":
        return "failed"
    # Cancelled mid-run (no terminal cleanup state yet) is a real failure.
    if r == "cancelled" and s not in _TERMINAL_CLEANUP_STATES:
        return "failed"
    # Terminal cleanup states — including post-success Cancelled by the
    # pipeline / SJD framework — count as succeeded (matches Studio).
    if s in _TERMINAL_CLEANUP_STATES:
        return "succeeded"
    return "in_progress"


def _normalize(
    raw: Any,
    *,
    kind: str,
    pool: str,
    livy_kind: str | None = None,
) -> SparkRunRecord | None:
    """Convert SDK `SparkBatchJob` / `SparkSession` into a `SparkRunRecord`.

    `kind` is the user-facing trigger label (scheduled / interactive).
    `livy_kind` (batch / session) selects the failure-classification
    semantics. When not provided, we default to `session` for interactive
    trigger kinds and `batch` otherwise — the right behavior for legacy
    callers and unit tests that only know the trigger kind.
    """
    if livy_kind is None:
        livy_kind = "session" if kind == "interactive" else "batch"
    job_id = getattr(raw, "id", None)
    if job_id is None:
        return None
    scheduler = getattr(raw, "scheduler", None)
    plugin = getattr(raw, "plugin", None)
    livy_info = getattr(raw, "livy_info", None)
    submitted_at = _first_datetime(
        _field(scheduler, "submitted_at"),
        _field(scheduler, "scheduled_at"),
        _field(plugin, "submission_started_at"),
        _field(plugin, "preparation_started_at"),
        _field(livy_info, "created_at"),
        getattr(raw, "created_at", None),
    )
    accounting_start_at = _first_datetime(
        _field(plugin, "resource_acquisition_started_at"),
        _field(livy_info, "running_at"),
        _field(livy_info, "idle_at"),
        _field(livy_info, "busy_at"),
        _field(plugin, "monitoring_started_at"),
        _field(plugin, "submission_started_at"),
        _field(scheduler, "scheduled_at"),
        _field(scheduler, "submitted_at"),
        _field(plugin, "preparation_started_at"),
        _field(livy_info, "created_at"),
        getattr(raw, "created_at", None),
    )
    accounting_start_basis = next(
        (
            basis for value, basis in (
                (_field(plugin, "resource_acquisition_started_at"), "resource_acquisition_started_at"),
                (_field(livy_info, "running_at"), "livy_running_at"),
                (_field(livy_info, "idle_at"), "livy_idle_at"),
                (_field(livy_info, "busy_at"), "livy_busy_at"),
                (_field(plugin, "monitoring_started_at"), "monitoring_started_at"),
                (_field(plugin, "submission_started_at"), "submission_started_at"),
                (_field(scheduler, "scheduled_at"), "scheduled_at"),
                (_field(scheduler, "submitted_at"), "submitted_at_fallback"),
                (_field(plugin, "preparation_started_at"), "preparation_started_at_fallback"),
                (_field(livy_info, "created_at"), "livy_created_at_fallback"),
                (getattr(raw, "created_at", None), "created_at_fallback"),
            )
            if isinstance(value, datetime)
        ),
        None,
    )
    ended_at = _first_datetime(
        _field(scheduler, "ended_at"),
        _field(livy_info, "success_at"),
        _field(livy_info, "dead_at"),
        _field(livy_info, "shutting_down_at"),
        _field(livy_info, "terminated_at"),
        _field(livy_info, "error_at"),
        _field(plugin, "cleanup_started_at"),
        _field(livy_info, "deleted_at"),
    )
    duration_seconds: float | None = None
    if accounting_start_at and ended_at:
        duration_seconds = max(
            0.0,
            (_utc(ended_at) - _utc(accounting_start_at)).total_seconds(),
        )

    creation_req = getattr(livy_info, "job_creation_request", None) if livy_info else None
    app_info = getattr(raw, "app_info", None) or {}
    if not isinstance(app_info, dict):
        app_info = {}
    dc, ec, nx = vcore_shape_from_app_info(app_info, creation_req)
    shape_is_estimate = False
    if app_info and all(
        _as_int(_mapping_value(app_info, *keys)) is not None
        for keys in (
            ("driverCores", "driver_cores"),
            ("executorCores", "executor_cores"),
            ("numExecutors", "num_executors", "executorCount"),
        )
    ):
        shape_is_estimate = True
    elif creation_req is not None and any(
        _field(creation_req, field) is not None
        for field in ("driver_cores", "executor_cores", "executor_count")
    ):
        shape_is_estimate = True
    total_vcores, _ = compute_vcore_seconds(dc, ec, nx, 1.0)
    if not shape_is_estimate:
        total_vcores = None
    _, vcore_seconds = compute_vcore_seconds(
        dc, ec, nx, duration_seconds if shape_is_estimate else None,
    )
    vcore_hours = (vcore_seconds / 3600.0) if vcore_seconds is not None else None
    est_cu_hours = (
        vcore_hours * VCORE_HOURS_TO_CU_HOURS if vcore_hours is not None else None
    )

    state = getattr(raw, "state", None)
    if hasattr(state, "value"):
        state = state.value
    result = getattr(raw, "result", None)
    if hasattr(result, "value"):
        result = result.value
    outcome = _classify_state(
        str(state) if state else None,
        str(result) if result else None,
        livy_kind=livy_kind,
    )

    # Pull pipeline correlation IDs out of the Spark conf when present.
    # These are the authoritative join keys back to the Synapse pipeline /
    # activity run that triggered this Spark job.
    conf = _conf_from_raw(raw) or {}
    def _conf_str(key: str) -> str | None:
        v = conf.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None
    pipeline_job_id = _conf_str("spark.synapse.context.pipelinejobid")
    activity_run_id = _conf_str("spark.synapse.context.activityrunid")
    activity_name = _conf_str("spark.synapse.context.activityname")
    notebook_name = _conf_str("spark.synapse.context.notebookname")
    notebook_run_id = _conf_str("spark.synapse.nbs.runid")

    return SparkRunRecord(
        livy_id=int(job_id),
        kind=kind,  # type: ignore[arg-type]
        pool=pool,
        name=getattr(raw, "name", None),
        app_id=getattr(raw, "app_id", None),
        app_info=app_info,
        source_endpoint=livy_kind,  # type: ignore[arg-type]
        submitter_id=getattr(raw, "submitter_id", None),
        submitter_name=getattr(raw, "submitter_name", None),
        artifact_id=getattr(raw, "artifact_id", None),
        state=str(state) if state else None,
        result=str(result) if result else None,
        outcome=outcome,
        submitted_at=submitted_at,
        accounting_start_at=accounting_start_at,
        accounting_start_basis=accounting_start_basis,
        ended_at=ended_at,
        duration_seconds=duration_seconds,
        driver_cores=dc,
        executor_cores=ec,
        num_executors=nx,
        total_vcores=total_vcores,
        vcore_seconds=vcore_seconds,
        vcore_hours=vcore_hours,
        est_cu_hours_fabric_spark=est_cu_hours,
        usage_basis=(
            "fixed_shape_estimate"
            if shape_is_estimate and total_vcores is not None
            else "unknown"
        ),
        pipeline_job_id=pipeline_job_id,
        activity_run_id=activity_run_id,
        activity_name=activity_name,
        notebook_name=notebook_name,
        notebook_run_id=notebook_run_id,
    )


__all__ = [
    "SparkHistoryClient",
    "VCORE_HOURS_TO_CU_HOURS",
    "vcore_shape_from_app_info",
    "compute_vcore_seconds",
    "_overlaps_window",
    "_classify_trigger",
    "_classify_state",
    "_normalize",
]
