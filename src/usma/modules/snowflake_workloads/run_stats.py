"""Credit -> vCore-hour -> Fabric Warehouse CU conversion for Snowflake.

Phase 7 Slice 7-D. Mirrors :mod:`...bigquery_workloads.run_stats` and
:mod:`...databricks_workflows.run_stats` so the SPA Dashboard and Fabric
CU-projection pipeline receive the same shape of rolling-window stats
for Snowflake as for Databricks and BigQuery.

Conversion ratios
-----------------
Snowflake bills compute in **credits**. Each credit nominally pays for
one hour of an X-SMALL warehouse, with the credit rate doubling at each
size step (X-SMALL=1, SMALL=2, MEDIUM=4, LARGE=8, X-LARGE=16, ...).

The number of vCPUs Snowflake provisions per warehouse is not public for
every size, but published benchmarks + Snowflake's own architecture
documentation place a STANDARD X-SMALL at approximately one 8-vCPU node;
the credit cost scales with the underlying machine count so a 1-credit
hour buys roughly the same compute-class as one vCore-hour after the
node-shape normalisation. We treat that as the **load-class proxy**
here: ``1 credit ~ 1 vCore-hour``. This is a single module-level
constant so a future calibration (Snowpark-optimized warehouses, real
benchmarks against migrated workloads) can be applied in one place.

Fabric Warehouse bills 1 CU = 2 vCores, identical denominator to
Synapse / Databricks / BigQuery. We reuse
:data:`...spark_pools.spark_history_client.VCORE_HOURS_TO_CU_HOURS` so a
single edit changes all four sources.

Net result: ``1 Snowflake credit ~ 0.5 Fabric Warehouse CU-hour``.

The conversion is intentionally simplistic. Snowflake warehouses
autoscale, Snowpark-optimized sizes double the vCore-per-credit ratio,
and serverless functions (tasks, Snowpipe, materialized-view refresh)
bill credits without a stable size. Any single number is a lossy
approximation. Callers must surface :data:`CREDIT_TO_CU_CAVEAT` in the
analyzer's ``caveats`` list so downstream consumers can present the
assumption.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from ..spark_pools.spark_history_client import VCORE_HOURS_TO_CU_HOURS
from .models import (
    Routine,
    SnowflakeCodeObjectSummary,
    SnowflakeDailyJobStats,
    SnowflakeJob,
    SnowflakeJobBreakdown,
    SnowflakeJobWindowStats,
    SnowflakeWarehouseWindowStats,
    Table,
    Warehouse,
)


#: STANDARD warehouse size -> credits per running hour (Snowflake docs).
CREDITS_PER_HOUR_BY_SIZE: dict[str, float] = {
    "X-SMALL": 1.0,
    "SMALL": 2.0,
    "MEDIUM": 4.0,
    "LARGE": 8.0,
    "X-LARGE": 16.0,
    "2X-LARGE": 32.0,
    "3X-LARGE": 64.0,
    "4X-LARGE": 128.0,
    "5X-LARGE": 256.0,
    "6X-LARGE": 512.0,
}

#: STANDARD warehouse size -> vCore proxy. Same doubling progression as
#: the credit rate so 1 credit ~ 1 vCore-hour at any size.
VCORES_BY_SIZE: dict[str, int] = {
    "X-SMALL": 1,
    "SMALL": 2,
    "MEDIUM": 4,
    "LARGE": 8,
    "X-LARGE": 16,
    "2X-LARGE": 32,
    "3X-LARGE": 64,
    "4X-LARGE": 128,
    "5X-LARGE": 256,
    "6X-LARGE": 512,
}

#: Load-class proxy: 1 Snowflake credit ~ 1 vCore-hour.
CREDIT_TO_VCORE_HOURS: float = 1.0

#: Combined credit -> Fabric Warehouse CU-hour ratio (1.0 x 0.5 = 0.5).
CREDIT_TO_CU_HOURS: float = CREDIT_TO_VCORE_HOURS * VCORE_HOURS_TO_CU_HOURS

#: Standard rolling windows in days -- matches ``databricks_workflows``
#: and ``bigquery_workloads``.
DEFAULT_WINDOWS: tuple[int, ...] = (7, 14, 28, 90)

#: Caveat string the analyzer appends to ``SnowflakeWorkloadsAnalysis.caveats``
#: whenever any credit math was applied. Surfaces the assumption to every
#: downstream consumer (runbook, dashboard, exec summary).
CREDIT_TO_CU_CAVEAT: str = (
    "Snowflake credits converted to Fabric Warehouse CU-hours using a "
    f"size-class proxy (1 credit ~ {CREDIT_TO_VCORE_HOURS} vCore-hour, "
    f"1 CU = 2 vCores, net {CREDIT_TO_CU_HOURS} CU-hr per credit). "
    "Snowflake autoscaling and Snowpark-optimized warehouses violate "
    "the 1:1 credit:vCore-hour assumption; calibrate via "
    "SMA_SNOWFLAKE_CREDIT_TO_VCORE_RATIO once production telemetry "
    "from the migrated workload is available (planned)."
)


# --------------------------------------------------------------- per-object math


def credits_per_hour_for_size(size: str | None) -> float | None:
    """Return STANDARD credits-per-hour for the supplied warehouse size."""
    if not size:
        return None
    return CREDITS_PER_HOUR_BY_SIZE.get(size.upper())


def vcores_for_size(size: str | None) -> int | None:
    """Return proxy vCore count for the supplied warehouse size."""
    if not size:
        return None
    return VCORES_BY_SIZE.get(size.upper())


def populate_warehouse_size_metrics(wh: Warehouse) -> Warehouse:
    """Fill ``credits_per_hour`` + ``est_vcore_hours_per_hour`` in-place.

    No-op when ``size`` is unknown. Returns the same object so callers
    can chain.
    """
    cph = credits_per_hour_for_size(wh.size)
    if cph is None:
        return wh
    wh.credits_per_hour = cph
    vcores = vcores_for_size(wh.size)
    if vcores is not None:
        wh.est_vcore_hours_per_hour = float(vcores)
    return wh


def populate_job_credits(job: SnowflakeJob) -> SnowflakeJob:
    """Fill ``est_credits`` + ``est_vcore_hours`` + ``est_cu_hours_fabric_warehouse``.

    Prefers ``execution_ms`` (compute-only) over ``total_elapsed_ms``
    (includes queue + compile) so the credit estimate matches the time
    the warehouse was actually doing work. No-op when ``warehouse_size``
    or both time fields are missing.
    """
    vcores = vcores_for_size(job.warehouse_size)
    if vcores is None:
        return job
    ms = job.execution_ms if job.execution_ms is not None else job.total_elapsed_ms
    if ms is None or ms < 0:
        return job
    hours = float(ms) / 3_600_000.0
    vcore_hours = hours * float(vcores)
    job.est_vcore_hours = vcore_hours
    job.est_credits = vcore_hours * CREDIT_TO_VCORE_HOURS
    job.est_cu_hours_fabric_warehouse = vcore_hours * VCORE_HOURS_TO_CU_HOURS
    return job


# ----------------------------------------------------------- rolling aggregates


def _window_start(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def aggregate_jobs_by_window(
    jobs: Iterable[SnowflakeJob],
    *,
    now: datetime | None = None,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> list[SnowflakeJobWindowStats]:
    """Produce one :class:`SnowflakeJobWindowStats` per rolling window."""
    now = now or datetime.now(timezone.utc)
    job_list = list(jobs)
    out: list[SnowflakeJobWindowStats] = []
    for w in windows:
        cutoff = _window_start(now, w)
        in_window = [
            j for j in job_list
            if j.start_time is not None and j.start_time >= cutoff
        ]
        completed = [j for j in in_window if j.outcome in ("succeeded", "failed")]
        succeeded = [j for j in completed if j.outcome == "succeeded"]
        durations = [
            j.duration_seconds for j in completed if j.duration_seconds is not None
        ]
        success_rate = (len(succeeded) / len(completed)) if completed else None
        avg_dur = (sum(durations) / len(durations)) if durations else None
        total_bytes_scanned = int(sum(int(j.bytes_scanned or 0) for j in completed))
        total_credits_cs = float(sum(
            float(j.credits_used_cloud_services or 0.0) for j in completed
        ))
        total_cu = float(sum(
            float(j.est_cu_hours_fabric_warehouse or 0.0) for j in completed
        ))
        out.append(SnowflakeJobWindowStats(
            window_days=w,
            job_count=len(in_window),
            succeeded_count=len(succeeded),
            failed_count=len(completed) - len(succeeded),
            success_rate=success_rate,
            avg_duration_seconds=avg_dur,
            total_bytes_scanned=total_bytes_scanned,
            total_credits_cloud_services=total_credits_cs,
            est_cu_hours_fabric_warehouse=total_cu,
        ))
    return out


def aggregate_warehouse_metering_by_window(
    jobs: Iterable[SnowflakeJob],
    *,
    now: datetime | None = None,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> list[SnowflakeWarehouseWindowStats]:
    """Roll job-derived credits + CU per warehouse per rolling window.

    Slice 7-D ships the QUERY_HISTORY-derived proxy. A future slice can
    replace this with a ``WAREHOUSE_METERING_HISTORY``-backed implementation
    without changing the output shape. Jobs without a ``warehouse_name``
    or without populated ``est_credits`` (i.e. unknown warehouse size or
    missing execution_ms) are skipped. Output is sorted by
    ``(window_days asc, warehouse_name asc)``.
    """
    now = now or datetime.now(timezone.utc)
    bucket: dict[tuple[int, str], dict[str, float]] = defaultdict(lambda: {
        "total_credits": 0.0,
        "credits_used_compute": 0.0,
        "credits_used_cloud_services": 0.0,
        "total_vcore_hours": 0.0,
        "est_cu_hours_fabric_warehouse": 0.0,
    })
    job_list = list(jobs)
    for w in windows:
        cutoff = _window_start(now, w)
        for j in job_list:
            if j.start_time is None or j.start_time < cutoff:
                continue
            wh = j.warehouse_name
            if not wh:
                continue
            key = (w, wh)
            b = bucket[key]
            cs = float(j.credits_used_cloud_services or 0.0)
            compute = float(j.est_credits or 0.0)
            b["credits_used_compute"] += compute
            b["credits_used_cloud_services"] += cs
            b["total_credits"] += compute + cs
            b["total_vcore_hours"] += float(j.est_vcore_hours or 0.0)
            b["est_cu_hours_fabric_warehouse"] += float(
                j.est_cu_hours_fabric_warehouse or 0.0
            )
    out: list[SnowflakeWarehouseWindowStats] = []
    for (w, wh) in sorted(bucket.keys()):
        b = bucket[(w, wh)]
        out.append(SnowflakeWarehouseWindowStats(
            window_days=w,
            warehouse_name=wh,
            total_credits=b["total_credits"],
            credits_used_compute=b["credits_used_compute"],
            credits_used_cloud_services=b["credits_used_cloud_services"],
            total_vcore_hours=b["total_vcore_hours"],
            est_cu_hours_fabric_warehouse=b["est_cu_hours_fabric_warehouse"],
        ))
    return out


def _percentile(values: list[float], pct: float) -> float | None:
    """Tiny percentile helper to avoid pulling in numpy for one site.

    Linear interpolation between sorted samples, matching numpy's
    default. Returns ``None`` for an empty input.
    """
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def aggregate_jobs_by_day(
    jobs: Iterable[SnowflakeJob],
    *,
    now: datetime | None = None,
) -> list[SnowflakeDailyJobStats]:
    """Per-calendar-day (UTC) rollup, mirroring the BigQuery shape.

    Jobs without ``start_time`` are skipped. p95 duration is computed
    across all completed jobs (succeeded + failed) in the bucket.
    Sorted ascending by date so chart libs can consume directly.
    """
    _ = now
    bucket: dict[str, dict[str, float | int | list[float]]] = {}
    for j in jobs:
        if j.start_time is None:
            continue
        day = j.start_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
        b = bucket.setdefault(
            day,
            {
                "job_count": 0,
                "succeeded_count": 0,
                "failed_count": 0,
                "other_count": 0,
                "total_bytes_scanned": 0,
                "total_execution_ms": 0,
                "total_queued_ms": 0,
                "duration_sum": 0.0,
                "duration_n": 0,
                "durations": [],
                "est_credits": 0.0,
                "est_cu_hours": 0.0,
            },
        )
        b["job_count"] = int(b["job_count"]) + 1
        if j.outcome == "succeeded":
            b["succeeded_count"] = int(b["succeeded_count"]) + 1
        elif j.outcome == "failed":
            b["failed_count"] = int(b["failed_count"]) + 1
        else:
            b["other_count"] = int(b["other_count"]) + 1
        if j.bytes_scanned:
            b["total_bytes_scanned"] = int(b["total_bytes_scanned"]) + int(j.bytes_scanned)
        if j.execution_ms:
            b["total_execution_ms"] = int(b["total_execution_ms"]) + int(j.execution_ms)
        if j.queued_overload_ms:
            b["total_queued_ms"] = int(b["total_queued_ms"]) + int(j.queued_overload_ms)
        if j.duration_seconds is not None:
            b["duration_sum"] = float(b["duration_sum"]) + float(j.duration_seconds)
            b["duration_n"] = int(b["duration_n"]) + 1
            durations = b["durations"]
            assert isinstance(durations, list)
            durations.append(float(j.duration_seconds))
        if j.est_credits:
            b["est_credits"] = float(b["est_credits"]) + float(j.est_credits)
        if j.est_cu_hours_fabric_warehouse:
            b["est_cu_hours"] = (
                float(b["est_cu_hours"]) + float(j.est_cu_hours_fabric_warehouse)
            )

    out: list[SnowflakeDailyJobStats] = []
    for day in sorted(bucket.keys()):
        b = bucket[day]
        completed = int(b["succeeded_count"]) + int(b["failed_count"])
        success_rate = (int(b["succeeded_count"]) / completed) if completed else None
        avg_dur = (
            float(b["duration_sum"]) / int(b["duration_n"])
            if int(b["duration_n"]) > 0
            else None
        )
        durations = b["durations"]
        assert isinstance(durations, list)
        p95 = _percentile(durations, 0.95)
        out.append(
            SnowflakeDailyJobStats(
                date=day,
                job_count=int(b["job_count"]),
                succeeded_count=int(b["succeeded_count"]),
                failed_count=int(b["failed_count"]),
                other_count=int(b["other_count"]),
                total_bytes_scanned=int(b["total_bytes_scanned"]),
                total_execution_ms=int(b["total_execution_ms"]),
                total_queued_ms=int(b["total_queued_ms"]),
                avg_duration_seconds=avg_dur,
                p95_duration_seconds=p95,
                success_rate=success_rate,
                est_credits=float(b["est_credits"]),
                est_cu_hours_fabric_warehouse=float(b["est_cu_hours"]),
            )
        )
    return out


def aggregate_jobs_by_dimension(
    jobs: Iterable[SnowflakeJob],
    *,
    dimension: str,
    key: Callable[[SnowflakeJob], str | None],
) -> list[SnowflakeJobBreakdown]:
    """Group jobs by an arbitrary key extractor (query_type / user / warehouse / ...).

    Sorted by ``(est_credits desc, job_count desc, key asc)`` so the
    heaviest contributors lead.
    """
    bucket: dict[str, dict[str, float | int]] = {}
    for j in jobs:
        raw = key(j)
        k = "<unknown>" if raw in (None, "") else str(raw)
        b = bucket.setdefault(
            k,
            {
                "job_count": 0,
                "succeeded_count": 0,
                "failed_count": 0,
                "total_bytes_scanned": 0,
                "total_execution_ms": 0,
                "duration_sum": 0.0,
                "duration_n": 0,
                "est_credits": 0.0,
                "est_cu_hours": 0.0,
            },
        )
        b["job_count"] = int(b["job_count"]) + 1
        if j.outcome == "succeeded":
            b["succeeded_count"] = int(b["succeeded_count"]) + 1
        elif j.outcome == "failed":
            b["failed_count"] = int(b["failed_count"]) + 1
        if j.bytes_scanned:
            b["total_bytes_scanned"] = int(b["total_bytes_scanned"]) + int(j.bytes_scanned)
        if j.execution_ms:
            b["total_execution_ms"] = int(b["total_execution_ms"]) + int(j.execution_ms)
        if j.duration_seconds is not None:
            b["duration_sum"] = float(b["duration_sum"]) + float(j.duration_seconds)
            b["duration_n"] = int(b["duration_n"]) + 1
        if j.est_credits:
            b["est_credits"] = float(b["est_credits"]) + float(j.est_credits)
        if j.est_cu_hours_fabric_warehouse:
            b["est_cu_hours"] = (
                float(b["est_cu_hours"]) + float(j.est_cu_hours_fabric_warehouse)
            )

    rows: list[SnowflakeJobBreakdown] = []
    for k, b in bucket.items():
        avg_dur = (
            float(b["duration_sum"]) / int(b["duration_n"])
            if int(b["duration_n"]) > 0
            else None
        )
        rows.append(
            SnowflakeJobBreakdown(
                dimension=dimension,
                key=k,
                job_count=int(b["job_count"]),
                succeeded_count=int(b["succeeded_count"]),
                failed_count=int(b["failed_count"]),
                total_bytes_scanned=int(b["total_bytes_scanned"]),
                total_execution_ms=int(b["total_execution_ms"]),
                avg_duration_seconds=avg_dur,
                est_credits=float(b["est_credits"]),
                est_cu_hours_fabric_warehouse=float(b["est_cu_hours"]),
            )
        )
    return sorted(
        rows,
        key=lambda r: (-r.est_credits, -r.job_count, r.key),
    )


def summarize_code_objects(
    tables: Iterable[Table],
    routines: Iterable[Routine],
) -> SnowflakeCodeObjectSummary:
    """Build a :class:`SnowflakeCodeObjectSummary` from the catalog.

    Mirrors :func:`...dedicated_pools.tsql_surface_gap.summarize_code_objects`
    so the SPA can render the Synapse + Snowflake compatibility cards
    with the same grammar (total / by_kind / by_support /
    compatibility_pct + lists of partial / unsupported full_names).

    Snowflake-specific extension: a ``by_language`` map for routines so
    JavaScript / Java / Python / SQL split is visible at-a-glance.
    """
    by_kind: dict[str, int] = {}
    by_support: dict[str, int] = {}
    by_language: dict[str, int] = {}
    partial: list[str] = []
    unsupported: list[str] = []
    unknown: list[str] = []
    total = 0
    for obj in tables:
        total += 1
        kind = obj.kind or "UNKNOWN"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        sup = obj.support or "unknown"
        by_support[sup] = by_support.get(sup, 0) + 1
        if sup == "partial":
            partial.append(obj.full_name)
        elif sup == "unsupported":
            unsupported.append(obj.full_name)
        elif sup == "unknown":
            unknown.append(obj.full_name)
    for obj in routines:
        total += 1
        kind = obj.routine_kind or "UNKNOWN"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        sup = obj.support or "unknown"
        by_support[sup] = by_support.get(sup, 0) + 1
        lang = (obj.language or "UNKNOWN").upper()
        by_language[lang] = by_language.get(lang, 0) + 1
        if sup == "partial":
            partial.append(obj.full_name)
        elif sup == "unsupported":
            unsupported.append(obj.full_name)
        elif sup == "unknown":
            unknown.append(obj.full_name)
    pct: float | None = None
    if total:
        supported = by_support.get("supported", 0)
        pct = round(supported / total * 100, 1)
    return SnowflakeCodeObjectSummary(
        total=total,
        by_kind=dict(sorted(by_kind.items())),
        by_support={k: v for k, v in by_support.items() if v},
        by_language=dict(sorted(by_language.items())),
        compatibility_pct=pct,
        unsupported_object_names=sorted(unsupported),
        partial_object_names=sorted(partial),
        unknown_object_names=sorted(unknown),
    )


__all__ = [
    "CREDITS_PER_HOUR_BY_SIZE",
    "VCORES_BY_SIZE",
    "CREDIT_TO_VCORE_HOURS",
    "CREDIT_TO_CU_HOURS",
    "CREDIT_TO_CU_CAVEAT",
    "DEFAULT_WINDOWS",
    "VCORE_HOURS_TO_CU_HOURS",
    "credits_per_hour_for_size",
    "vcores_for_size",
    "populate_warehouse_size_metrics",
    "populate_job_credits",
    "aggregate_jobs_by_window",
    "aggregate_warehouse_metering_by_window",
    "aggregate_jobs_by_day",
    "aggregate_jobs_by_dimension",
    "summarize_code_objects",
]
