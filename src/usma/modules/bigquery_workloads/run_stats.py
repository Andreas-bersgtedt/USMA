"""Slot-hour → vCore-hour → Fabric Spark CU conversion for BigQuery jobs.

Mirrors :mod:`usma.modules.databricks_workflows.run_stats`
so the SPA Dashboard and Fabric CU-projection pipeline receive the same
shape of rolling-window stats for BigQuery as for Databricks workflows.

Conversion ratios
-----------------
BigQuery bills compute in **slot-hours**. A slot is documented by Google
as "a virtual CPU used by BigQuery to execute SQL queries". Empirical
benchmarks (Google's `BigQuery resource model` whitepaper, 2023; and
third-party slot-vs-vCPU measurements) place 1 slot ≈ 0.5 vCore for
on-demand pricing. We treat that as the **conservative default** here;
the constant is a single module-level value so a future calibration
(e.g. autoscaling reservation accounts) can be applied in one place.

Fabric Spark bills 1 CU = 2 Spark vCores, which is identical to the
Synapse / Databricks denominator. We reuse the
:data:`...spark_pools.spark_history_client.VCORE_HOURS_TO_CU_HOURS`
constant so a single edit changes all three sources.

Net result: ``1 BigQuery slot-hour ≈ 0.25 Fabric Spark CU-hour``.

The conversion is intentionally simplistic — BigQuery slots are highly
elastic and any single number is a lossy approximation. Callers must
surface the
:data:`SLOT_TO_CU_CAVEAT` string in the analyzer's ``caveats`` list so
downstream consumers can present the assumption.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from ..spark_pools.spark_history_client import VCORE_HOURS_TO_CU_HOURS
from .models import (
    BigQueryJob,
    BigQueryJobWindowStats,
    DailyJobStats,
    JobBreakdown,
    Table,
    TableUsage,
)


#: Conservative default: 1 BigQuery slot ≈ 0.5 vCore.
SLOT_HOURS_TO_VCORE_HOURS: float = 0.5

#: Combined slot-hour → Fabric Spark CU-hour ratio (0.5 × 0.5 = 0.25).
SLOT_HOURS_TO_CU_HOURS: float = SLOT_HOURS_TO_VCORE_HOURS * VCORE_HOURS_TO_CU_HOURS

#: Standard rolling windows in days — matches ``databricks_workflows``.
DEFAULT_WINDOWS: tuple[int, ...] = (7, 14, 28, 90)

#: Caveat string the analyzer appends to ``BigQueryWorkloadsAnalysis.caveats``
#: whenever any slot-hour math was applied. Surfaces the assumption to
#: every downstream consumer (runbook, dashboard, exec summary).
SLOT_TO_CU_CAVEAT: str = (
    "BigQuery slot-hours converted to Fabric Spark CU-hours using a fixed "
    f"ratio of {SLOT_HOURS_TO_CU_HOURS:.2f} CU-hr per slot-hr "
    f"(1 slot ≈ {SLOT_HOURS_TO_VCORE_HOURS} vCore, 1 CU = 2 vCores). "
    "BigQuery slot elasticity makes any single ratio a lossy approximation; "
    "calibrate via SMA_BQ_SLOT_TO_VCORE_RATIO once production telemetry is "
    "available (planned)."
)


# ----------------------------------------------------------------- per-job math


def slot_hours_for_ms(total_slot_ms: int | None) -> float | None:
    """Convert ``total_slot_ms`` to slot-hours (``ms / 3,600,000``)."""
    if total_slot_ms is None or total_slot_ms < 0:
        return None
    return float(total_slot_ms) / 3_600_000.0


def cu_hours_for_slot_hours(slot_hours: float | None) -> float | None:
    """Convert slot-hours to Fabric Spark CU-hours."""
    if slot_hours is None or slot_hours < 0:
        return None
    return slot_hours * SLOT_HOURS_TO_CU_HOURS


def populate_slot_hours(job: BigQueryJob) -> BigQueryJob:
    """Fill ``slot_hours`` + ``est_cu_hours_fabric_spark`` on ``job``.

    No-op when ``total_slot_ms`` is missing. Returns the same object
    so callers can chain.
    """
    sh = slot_hours_for_ms(job.total_slot_ms)
    if sh is None:
        return job
    job.slot_hours = sh
    job.est_cu_hours_fabric_spark = cu_hours_for_slot_hours(sh)
    return job


# ----------------------------------------------------------- rolling aggregates


def _window_start(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def aggregate_jobs_by_window(
    jobs: Iterable[BigQueryJob],
    *,
    now: datetime | None = None,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> list[BigQueryJobWindowStats]:
    """Produce one :class:`BigQueryJobWindowStats` per rolling window.

    Mirrors :func:`...databricks_workflows.run_stats.aggregate_runs_by_job`
    but rolled up across **all** jobs rather than per-job, because BigQuery
    jobs are submitted by users / scheduled-queries rather than belonging
    to a stable workflow identity.
    """
    now = now or datetime.now(timezone.utc)
    job_list = list(jobs)
    out: list[BigQueryJobWindowStats] = []
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
        slot_hours_vals = [
            j.slot_hours for j in completed if j.slot_hours is not None
        ]
        total_slot_hours = float(sum(slot_hours_vals))
        avg_slot_hours_per_job = (
            total_slot_hours / len(slot_hours_vals) if slot_hours_vals else None
        )
        success_rate = (len(succeeded) / len(completed)) if completed else None
        avg_duration_seconds = (sum(durations) / len(durations)) if durations else None
        total_billed_bytes = sum(
            (j.total_billed_bytes or 0) for j in completed
        )
        out.append(
            BigQueryJobWindowStats(
                window_days=w,
                job_count=len(in_window),
                completed_count=len(completed),
                succeeded_count=len(succeeded),
                failed_count=len(completed) - len(succeeded),
                success_rate=success_rate,
                avg_duration_seconds=avg_duration_seconds,
                total_slot_hours=total_slot_hours,
                total_billed_bytes=total_billed_bytes,
                avg_slot_hours_per_job=avg_slot_hours_per_job,
                est_cu_hours_fabric_spark=total_slot_hours * SLOT_HOURS_TO_CU_HOURS,
            )
        )
    return out


def aggregate_jobs_by_day(
    jobs: Iterable[BigQueryJob],
    *,
    now: datetime | None = None,
) -> list[DailyJobStats]:
    """Per-calendar-day (UTC) rollup of every job's outcome and resource use.

    Mirrors the dedicated-pool / pipelines ``daily_status`` artefact so
    the SPA Dashboard can render a single timeseries chart shape for
    every source. Sorted ascending by date so charting libraries can
    consume the list directly.

    Jobs without a ``start_time`` are skipped (cancelled-before-start
    edge case). Slot-hours / billed-bytes / duration sums include the
    job whether it succeeded or failed — the success/fail counts let
    the consumer split the stacked bars.
    """
    now = now or datetime.now(timezone.utc)
    bucket: dict[str, dict[str, float | int]] = {}
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
                "cancelled_count": 0,
                "other_count": 0,
                "total_slot_ms": 0,
                "total_billed_bytes": 0,
                "duration_sum": 0.0,
                "duration_n": 0,
            },
        )
        b["job_count"] = int(b["job_count"]) + 1
        outcome = j.outcome or "unknown"
        if outcome == "succeeded":
            b["succeeded_count"] = int(b["succeeded_count"]) + 1
        elif outcome == "failed":
            b["failed_count"] = int(b["failed_count"]) + 1
        elif outcome == "cancelled":
            b["cancelled_count"] = int(b["cancelled_count"]) + 1
        else:
            b["other_count"] = int(b["other_count"]) + 1
        if j.total_slot_ms:
            b["total_slot_ms"] = int(b["total_slot_ms"]) + int(j.total_slot_ms)
        if j.total_billed_bytes:
            b["total_billed_bytes"] = int(b["total_billed_bytes"]) + int(j.total_billed_bytes)
        if j.duration_seconds is not None:
            b["duration_sum"] = float(b["duration_sum"]) + float(j.duration_seconds)
            b["duration_n"] = int(b["duration_n"]) + 1

    out: list[DailyJobStats] = []
    for day in sorted(bucket.keys()):
        b = bucket[day]
        slot_ms = int(b["total_slot_ms"])
        slot_hours = float(slot_ms) / 3_600_000.0
        completed = int(b["succeeded_count"]) + int(b["failed_count"])
        success_rate = (
            (int(b["succeeded_count"]) / completed) if completed else None
        )
        avg_dur = (
            (float(b["duration_sum"]) / int(b["duration_n"]))
            if int(b["duration_n"]) > 0
            else None
        )
        out.append(
            DailyJobStats(
                date=day,
                job_count=int(b["job_count"]),
                succeeded_count=int(b["succeeded_count"]),
                failed_count=int(b["failed_count"]),
                cancelled_count=int(b["cancelled_count"]),
                other_count=int(b["other_count"]),
                total_slot_ms=slot_ms,
                total_slot_hours=slot_hours,
                total_billed_bytes=int(b["total_billed_bytes"]),
                avg_duration_seconds=avg_dur,
                success_rate=success_rate,
                est_cu_hours_fabric_spark=slot_hours * SLOT_HOURS_TO_CU_HOURS,
            )
        )
    return out


def aggregate_jobs_by_dimension(
    jobs: Iterable[BigQueryJob],
    *,
    dimension: str,
    key: Callable[[BigQueryJob], str | None],
) -> list[JobBreakdown]:
    """Group jobs by an arbitrary key extractor (user, statement_type, etc.).

    ``dimension`` is the label stamped on every emitted row so a single
    flat list can hold multiple breakdowns (see analyzer wiring). ``key``
    extracts the bucket label from each job; ``None`` collapses into the
    sentinel string ``"<unknown>"`` so missing data is still visible in
    the report rather than silently dropped.

    Output is sorted by ``slot_hours desc, job_count desc, key asc``
    so the heaviest contributors lead.
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
                "total_slot_ms": 0,
                "total_billed_bytes": 0,
                "duration_sum": 0.0,
                "duration_n": 0,
            },
        )
        b["job_count"] = int(b["job_count"]) + 1
        if j.outcome == "succeeded":
            b["succeeded_count"] = int(b["succeeded_count"]) + 1
        elif j.outcome == "failed":
            b["failed_count"] = int(b["failed_count"]) + 1
        if j.total_slot_ms:
            b["total_slot_ms"] = int(b["total_slot_ms"]) + int(j.total_slot_ms)
        if j.total_billed_bytes:
            b["total_billed_bytes"] = int(b["total_billed_bytes"]) + int(j.total_billed_bytes)
        if j.duration_seconds is not None:
            b["duration_sum"] = float(b["duration_sum"]) + float(j.duration_seconds)
            b["duration_n"] = int(b["duration_n"]) + 1

    rows: list[JobBreakdown] = []
    for k, b in bucket.items():
        slot_ms = int(b["total_slot_ms"])
        slot_hours = float(slot_ms) / 3_600_000.0
        avg_dur = (
            (float(b["duration_sum"]) / int(b["duration_n"]))
            if int(b["duration_n"]) > 0
            else None
        )
        rows.append(
            JobBreakdown(
                dimension=dimension,
                key=k,
                job_count=int(b["job_count"]),
                succeeded_count=int(b["succeeded_count"]),
                failed_count=int(b["failed_count"]),
                total_slot_ms=slot_ms,
                total_slot_hours=slot_hours,
                total_billed_bytes=int(b["total_billed_bytes"]),
                avg_duration_seconds=avg_dur,
                est_cu_hours_fabric_spark=slot_hours * SLOT_HOURS_TO_CU_HOURS,
            )
        )
    return sorted(
        rows,
        key=lambda r: (-r.total_slot_hours, -r.job_count, r.key),
    )


def aggregate_table_usage(
    jobs: Iterable[BigQueryJob],
    *,
    catalog: Iterable[Table] | None = None,
) -> list[TableUsage]:
    """Roll up ``BigQueryJob.referenced_tables`` into per-table usage stats.

    Mirrors the Synapse SQL Surface ranking: one row per fully-qualified
    ``project.dataset.table`` id with ``usage_count`` (distinct jobs that
    referenced it), ``total_elapsed_seconds``, ``total_slot_hours`` and
    ``total_billed_bytes``. A job that references *n* tables increments
    each of those *n* rows — slot-hours / bytes are **not** apportioned
    because BigQuery doesn't expose a per-table cost split.

    When ``catalog`` is provided, every referenced id is reconciled
    against it: ``in_catalog=False`` flags references the catalog
    enumeration didn't see (dropped datasets, cross-project reads, typos
    in stale audit-log entries), and ``table_support`` mirrors the
    catalog's ``Table.support`` so the surface naturally sorts blockers
    to the top of the ranking.

    Output is sorted by ``usage_count desc, total_slot_hours desc,
    total_elapsed_seconds desc, full_table_id asc`` so callers can
    ``[:N]`` directly for "top N most-used tables".
    """
    catalog_index: dict[str, Table] = {}
    if catalog is not None:
        for t in catalog:
            if t.full_table_id:
                catalog_index[t.full_table_id] = t

    by_id: dict[str, TableUsage] = {}
    for j in jobs:
        # De-dupe within a single job so a self-join doesn't count twice.
        for fqn in {fq for fq in (j.referenced_tables or []) if fq}:
            usage = by_id.get(fqn)
            if usage is None:
                parts = fqn.split(".")
                project_id = parts[0] if len(parts) >= 3 else ""
                dataset_id = parts[1] if len(parts) >= 3 else ""
                table_id = parts[-1] if parts else fqn
                cat = catalog_index.get(fqn)
                usage = TableUsage(
                    full_table_id=fqn,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    table_id=table_id,
                    in_catalog=cat is not None,
                    table_support=(cat.support if cat is not None else "unknown"),
                )
                by_id[fqn] = usage
            usage.usage_count += 1
            if j.duration_seconds is not None:
                usage.total_elapsed_seconds += float(j.duration_seconds)
            if j.total_slot_ms is not None:
                usage.total_slot_ms += int(j.total_slot_ms)
            if j.total_billed_bytes is not None:
                usage.total_billed_bytes += int(j.total_billed_bytes)

    for usage in by_id.values():
        usage.total_slot_hours = float(usage.total_slot_ms) / 3_600_000.0
        usage.est_cu_hours_fabric_spark = (
            usage.total_slot_hours * SLOT_HOURS_TO_CU_HOURS
        )

    return sorted(
        by_id.values(),
        key=lambda u: (
            -u.usage_count,
            -u.total_slot_hours,
            -u.total_elapsed_seconds,
            u.full_table_id,
        ),
    )


__all__ = [
    "DEFAULT_WINDOWS",
    "SLOT_HOURS_TO_CU_HOURS",
    "SLOT_HOURS_TO_VCORE_HOURS",
    "SLOT_TO_CU_CAVEAT",
    "VCORE_HOURS_TO_CU_HOURS",
    "aggregate_jobs_by_day",
    "aggregate_jobs_by_dimension",
    "aggregate_jobs_by_window",
    "aggregate_table_usage",
    "cu_hours_for_slot_hours",
    "populate_slot_hours",
    "slot_hours_for_ms",
]
