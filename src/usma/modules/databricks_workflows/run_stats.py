"""vCore-hour math + rolling-window aggregation for Databricks runs.

Mirrors :mod:`usma.modules.spark_pools.spark_run_stats`
so the SPA Dashboard and Fabric CU-projection pipeline get fed the same
shape of data for Databricks as for Synapse Spark pools.

Conversion ratio
----------------
Fabric Spark bills 1 CU = 2 Spark vCores, which is the same denominator
Synapse Spark uses. We deliberately re-use
:data:`...spark_pools.spark_history_client.VCORE_HOURS_TO_CU_HOURS` so a
single edit changes both sources.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

from ..spark_pools.spark_history_client import VCORE_HOURS_TO_CU_HOURS
from .models import (
    AutoscaleWorkerStrategy,
    WorkflowRun,
    WorkflowRunStats,
    WorkflowRunWindowStats,
)


DEFAULT_WINDOWS: tuple[int, ...] = (7, 14, 28, 90)


# ---------------------------------------------------------------- vcore math


def resolve_worker_count(
    *,
    num_workers: int | None,
    autoscale_min: int | None,
    autoscale_max: int | None,
    strategy: AutoscaleWorkerStrategy = "min",
) -> int | None:
    """Return the effective worker count to use for vCore math.

    Static clusters (``num_workers`` set, no autoscale) are returned
    as-is. Autoscale clusters use ``strategy``:

    - ``"min"`` (default, matches the conservative path the analyzer ships):
      use ``autoscale_min`` — the floor the cluster is guaranteed to provision.
      This is closest to Synapse Spark's static ``num_executors`` semantic.
    - ``"avg"``: average of ``autoscale_min`` and ``autoscale_max``.
    - ``"max"``: ``autoscale_max`` (upper bound — capacity-planning view).
    """
    if num_workers is not None and num_workers >= 0:
        return num_workers
    lo, hi = autoscale_min, autoscale_max
    if lo is None and hi is None:
        return None
    if strategy == "min":
        return lo if lo is not None else hi
    if strategy == "max":
        return hi if hi is not None else lo
    # avg
    if lo is None:
        return hi
    if hi is None:
        return lo
    return int(round((lo + hi) / 2))


def compute_vcore_seconds(
    *,
    driver_vcores: int | None,
    worker_vcores: int | None,
    worker_count: int | None,
    duration_seconds: float | None,
) -> tuple[int | None, float | None]:
    """Return ``(total_vcores, vcore_seconds)`` or ``(None, None)``.

    Driver always contributes once. Workers contribute ``worker_count``
    times each. Returns ``(None, None)`` whenever any input is missing or
    the duration is non-positive — keeps the Dashboard out of trouble for
    runs that are in-flight or for cluster specs we couldn't resolve.
    """
    if (
        driver_vcores is None
        or worker_vcores is None
        or worker_count is None
        or duration_seconds is None
        or duration_seconds <= 0
        or worker_count < 0
    ):
        return None, None
    total_vcores = int(driver_vcores) + int(worker_vcores) * int(worker_count)
    return total_vcores, float(total_vcores) * float(duration_seconds)


def vcores_for_node_type(
    node_type_id: str | None,
    node_type_vcpus: Mapping[str, int],
) -> int | None:
    """Look up vCPU count for a Databricks ``node_type_id``."""
    if not node_type_id:
        return None
    v = node_type_vcpus.get(node_type_id)
    return int(v) if v is not None else None


# --------------------------------------------------------- rolling aggregates


def _window_start(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _aggregate_window(runs: list[WorkflowRun], *, window_days: int, now: datetime) -> WorkflowRunWindowStats:
    cutoff = _window_start(now, window_days)
    in_window = [
        r for r in runs
        if r.start_time is not None and r.start_time >= cutoff
    ]
    completed = [r for r in in_window if r.outcome in ("succeeded", "failed")]
    succeeded = [r for r in completed if r.outcome == "succeeded"]
    durations = [r.duration_seconds for r in completed if r.duration_seconds is not None]
    vcore_hours = [r.vcore_hours for r in completed if r.vcore_hours is not None]
    total_vcore_hours = float(sum(vcore_hours))
    avg_vcore_hours_per_run = (
        total_vcore_hours / len(vcore_hours) if vcore_hours else None
    )
    success_rate = (len(succeeded) / len(completed)) if completed else None
    avg_duration_seconds = (sum(durations) / len(durations)) if durations else None
    return WorkflowRunWindowStats(
        window_days=window_days,
        run_count=len(in_window),
        completed_count=len(completed),
        succeeded_count=len(succeeded),
        failed_count=len(completed) - len(succeeded),
        success_rate=success_rate,
        avg_duration_seconds=avg_duration_seconds,
        total_vcore_hours=total_vcore_hours,
        avg_vcore_hours_per_run=avg_vcore_hours_per_run,
        est_cu_hours_fabric_spark=total_vcore_hours * VCORE_HOURS_TO_CU_HOURS,
    )


def aggregate_runs_by_job(
    runs: Iterable[WorkflowRun],
    *,
    now: datetime | None = None,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> list[WorkflowRunStats]:
    """Group ``runs`` by ``job_id`` and produce per-job rolling stats."""
    now = now or datetime.now(timezone.utc)
    by_job: dict[int, list[WorkflowRun]] = {}
    job_names: dict[int, str] = {}
    for r in runs:
        by_job.setdefault(r.job_id, []).append(r)
        if r.job_name:
            job_names.setdefault(r.job_id, r.job_name)

    out: list[WorkflowRunStats] = []
    for job_id, job_runs in by_job.items():
        windows_stats = [
            _aggregate_window(job_runs, window_days=w, now=now) for w in windows
        ]
        out.append(
            WorkflowRunStats(
                job_id=job_id,
                job_name=job_names.get(job_id, f"job-{job_id}"),
                total_runs_observed=len(job_runs),
                windows=windows_stats,
            )
        )
    # Stable ordering: highest total vcore-hours in the longest window first.
    longest = max(windows)

    def _key(s: WorkflowRunStats) -> float:
        for w in s.windows:
            if w.window_days == longest:
                return -w.total_vcore_hours
        return 0.0

    out.sort(key=_key)
    return out


__all__ = [
    "DEFAULT_WINDOWS",
    "VCORE_HOURS_TO_CU_HOURS",
    "aggregate_runs_by_job",
    "compute_vcore_seconds",
    "resolve_worker_count",
    "vcores_for_node_type",
]
