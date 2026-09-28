"""Aggregate Spark Livy job history into per-pool, per-window rollups.

Mirrors the design of `modules/pipelines/run_stats.py` but operates on
SparkRunRecord. Mapping rule: 1 vCore-second = 0.5 CU-second.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Sequence

from .models import (
    SparkDailyUsage,
    SparkPoolRunStats,
    SparkRunRecord,
    SparkRunWindowStats,
)
from .spark_history_client import VCORE_HOURS_TO_CU_HOURS

DEFAULT_WINDOWS: tuple[int, ...] = (7, 14, 28, 90)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _zero_window(days: int) -> SparkRunWindowStats:
    return SparkRunWindowStats(
        window_days=days,
        run_count=0,
        succeeded=0,
        failed=0,
        in_progress=0,
        total_duration_hours=0.0,
        total_vcore_hours=0.0,
        est_cu_hours_fabric_spark=0.0,
        avg_vcore_hours_per_run=None,
        avg_daily_vcore_hours=None,
        avg_daily_cu_hours=None,
        steady_state_cu=None,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _attempt_id(run: SparkRunRecord) -> str | None:
    for key in ("appAttemptId", "app_attempt_id", "attemptId", "attempt_id"):
        value = run.app_info.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _has_usage(run: SparkRunRecord) -> bool:
    return run.usage_basis == "fixed_shape_estimate" or (
        run.total_vcores is not None and run.vcore_hours is not None
    )


def _interval_end(run: SparkRunRecord, observation_end: datetime) -> datetime | None:
    if run.ended_at is not None:
        return _utc(run.ended_at)
    start = run.accounting_start_at or run.submitted_at
    if start is not None and run.duration_seconds is not None:
        return _utc(start) + timedelta(seconds=run.duration_seconds)
    if run.outcome == "in_progress":
        return _utc(observation_end)
    return None


def _identity(run: SparkRunRecord) -> tuple[object, ...]:
    attempt = _attempt_id(run)
    app_id = run.app_id.strip() if run.app_id else ""
    if app_id:
        return ("application", run.pool.casefold(), app_id, attempt)
    return (
        "livy",
        run.pool.casefold(),
        run.source_endpoint or "unknown",
        run.livy_id,
        _utc(run.submitted_at) if run.submitted_at is not None else None,
        run.kind,
    )


def _record_quality(run: SparkRunRecord) -> tuple[int, int, int, int]:
    return (
        int(_has_usage(run)),
        int(run.vcore_hours is not None),
        int(run.ended_at is not None),
        len(run.app_info),
    )


def deduplicate_runs(runs: Iterable[SparkRunRecord]) -> list[SparkRunRecord]:
    """Drop duplicate observations without merging endpoint-local Livy IDs."""
    unique: dict[tuple[object, ...], SparkRunRecord] = {}
    for run in runs:
        key = _identity(run)
        previous = unique.get(key)
        if previous is None:
            unique[key] = run
        elif _record_quality(run) > _record_quality(previous):
            unique[key] = run
    return list(unique.values())


def _interval_usage(
    run: SparkRunRecord,
    start: datetime,
    end: datetime,
) -> tuple[float | None, float | None]:
    start = _utc(start)
    end = _utc(end)
    run_start = run.accounting_start_at or run.submitted_at
    if run_start is None:
        return None, None
    run_start = _utc(run_start)
    run_end = _interval_end(run, end)
    if run_end is None:
        return None, None
    clipped_start = max(start, run_start)
    clipped_end = min(end, run_end)
    if clipped_end <= clipped_start:
        return 0.0, 0.0
    elapsed_seconds = (run_end - run_start).total_seconds()
    if elapsed_seconds <= 0:
        return None, None
    if not _has_usage(run):
        return None, None
    clipped_seconds = (clipped_end - clipped_start).total_seconds()
    if run.total_vcores is not None:
        vcore_hours = run.total_vcores * clipped_seconds / 3600.0
    elif run.vcore_hours is not None:
        vcore_hours = run.vcore_hours * clipped_seconds / elapsed_seconds
    else:
        return None, None
    cu_hours = (
        vcore_hours * VCORE_HOURS_TO_CU_HOURS
        if run.est_cu_hours_fabric_spark is None or run.vcore_hours is None
        else run.est_cu_hours_fabric_spark * clipped_seconds / elapsed_seconds
    )
    return vcore_hours, cu_hours


def _overlaps(run: SparkRunRecord, start: datetime, end: datetime) -> bool:
    interval_start = run.accounting_start_at or run.submitted_at
    if interval_start is None:
        return False
    interval_end = _interval_end(run, end)
    if interval_end is None:
        return _utc(interval_start) < _utc(end)
    return _utc(interval_start) < _utc(end) and _utc(interval_end) > _utc(start)


def _full_utc_days(start: datetime, end: datetime) -> list[date]:
    start = _utc(start)
    end = _utc(end)
    first = start.date()
    if start.time() != time.min:
        first += timedelta(days=1)
    end_exclusive = end.date()
    return [
        first + timedelta(days=offset)
        for offset in range(max(0, (end_exclusive - first).days))
    ]


def _accumulate(stats: SparkRunWindowStats, run: SparkRunRecord) -> None:
    stats.run_count += 1
    if run.outcome == "succeeded":
        stats.succeeded += 1
    elif run.outcome == "failed":
        stats.failed += 1
    else:
        stats.in_progress += 1
def _finalize(stats: SparkRunWindowStats, full_day_count: int) -> None:
    if stats.known_usage_run_count > 0 and stats.total_vcore_hours:
        stats.avg_vcore_hours_per_run = (
            stats.total_vcore_hours / stats.known_usage_run_count
        )
    if full_day_count > 0 and stats.unknown_usage_run_count == 0:
        stats.avg_daily_vcore_hours = stats.total_vcore_hours / full_day_count
        stats.avg_daily_cu_hours = stats.est_cu_hours_fabric_spark / full_day_count
        stats.steady_state_cu = stats.avg_daily_cu_hours / 24.0


def aggregate_runs(
    runs: Iterable[SparkRunRecord],
    *,
    windows: Sequence[int] = DEFAULT_WINDOWS,
    now: datetime | None = None,
    collection_complete: bool = True,
    groups: Iterable[tuple[str, str]] = (),
) -> list[SparkPoolRunStats]:
    """Group `runs` by (pool, kind) and roll up into windowed stats.

    Returns one `SparkPoolRunStats` per (pool, kind) seen, each containing one
    `SparkRunWindowStats` per entry in `windows`.
    """
    now = _utc(now or _now_utc())
    unique_runs = deduplicate_runs(runs)
    # (pool, kind) -> [SparkRunRecord]
    bucket: dict[tuple[str, str], list[SparkRunRecord]] = defaultdict(list)
    for r in unique_runs:
        bucket[(r.pool, r.kind)].append(r)
    for pool, kind in groups:
        bucket.setdefault((pool, kind), [])

    out: list[SparkPoolRunStats] = []
    for (pool, kind), records in sorted(bucket.items()):
        windowed: list[SparkRunWindowStats] = []
        for days in windows:
            cutoff = now - timedelta(days=days)
            stat = _zero_window(days)
            daily: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])
            for r in records:
                if not _overlaps(r, cutoff, now):
                    continue
                _accumulate(stat, r)
                usage_vcore = usage_cu = 0.0
                known = True
                run_start = _utc(r.accounting_start_at or r.submitted_at)
                run_end = _interval_end(r, now)
                if run_end is None:
                    stat.unknown_usage_run_count += 1
                    continue
                clipped_start = max(cutoff, run_start)
                clipped_end = min(now, run_end)
                if clipped_end > clipped_start:
                    stat.total_duration_hours += (
                        clipped_end - clipped_start
                    ).total_seconds() / 3600.0
                for day in _days_intersecting(clipped_start, clipped_end):
                    day_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
                    day_end = day_start + timedelta(days=1)
                    vh, ch = _interval_usage(r, day_start, day_end)
                    if vh is None or ch is None:
                        known = False
                        continue
                    daily[day][0] += vh
                    daily[day][1] += ch
                    usage_vcore += vh
                    usage_cu += ch
                if known and _has_usage(r):
                    stat.known_usage_run_count += 1
                    stat.total_vcore_hours += usage_vcore
                    stat.est_cu_hours_fabric_spark += usage_cu
                else:
                    stat.unknown_usage_run_count += 1
            _finalize(
                stat,
                len(_full_utc_days(cutoff, now)) if collection_complete else 0,
            )
            stat.peak_day_cu_hours = max((values[1] for values in daily.values()), default=0.0)
            windowed.append(stat)
        out.append(
            SparkPoolRunStats(
                pool=pool,
                kind=kind,  # type: ignore[arg-type]
                windows=windowed,
            )
        )
    return out


def _days_intersecting(start: datetime, end: datetime) -> list[date]:
    if end <= start:
        return []
    day = start.date()
    last = (end - timedelta(microseconds=1)).date()
    return [day + timedelta(days=i) for i in range((last - day).days + 1)]


def aggregate_daily_usage(
    runs: Iterable[SparkRunRecord],
    *,
    start: datetime,
    end: datetime,
) -> list[SparkDailyUsage]:
    """Return fixed-shape estimated usage clipped and split by UTC day."""
    start, end = _utc(start), _utc(end)
    daily: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])
    unique_runs = deduplicate_runs(runs)
    unknown_days: set[date] = set()
    all_days = _days_intersecting(start, end)
    for day in all_days:
        daily[day]
    for run in unique_runs:
        if not _overlaps(run, start, end):
            continue
        interval_start = max(start, _utc(run.accounting_start_at or run.submitted_at))
        effective_end = _interval_end(run, end)
        if effective_end is None:
            unknown_days.update(_days_intersecting(interval_start, end))
            continue
        interval_end = min(end, effective_end)
        if not _has_usage(run):
            unknown_days.update(_days_intersecting(interval_start, interval_end))
            continue
        for day in _days_intersecting(interval_start, interval_end):
            day_start = max(start, datetime.combine(day, time.min, tzinfo=timezone.utc))
            day_end = min(end, datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc))
            vh, ch = _interval_usage(run, day_start, day_end)
            if vh is not None and ch is not None:
                daily[day][0] += vh
                daily[day][1] += ch
    return [
        SparkDailyUsage(
            day=day,
            total_vcore_hours=values[0],
            est_cu_hours_fabric_spark=values[1],
        )
        for day, values in sorted(daily.items())
        if day not in unknown_days
    ]


__all__ = [
    "aggregate_runs",
    "aggregate_daily_usage",
    "deduplicate_runs",
    "DEFAULT_WINDOWS",
    "VCORE_HOURS_TO_CU_HOURS",
]
