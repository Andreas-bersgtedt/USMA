"""Aggregate Spark Livy job history into per-pool, per-window rollups.

Mirrors the design of `modules/pipelines/run_stats.py` but operates on
SparkRunRecord. Mapping rule: 1 vCore-second = 0.5 CU-second.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from .models import (
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
    )


def _accumulate(stats: SparkRunWindowStats, run: SparkRunRecord) -> None:
    stats.run_count += 1
    if run.outcome == "succeeded":
        stats.succeeded += 1
    elif run.outcome == "failed":
        stats.failed += 1
    else:
        stats.in_progress += 1
    if run.duration_seconds:
        stats.total_duration_hours += run.duration_seconds / 3600.0
    if run.vcore_hours:
        stats.total_vcore_hours += run.vcore_hours
    if run.est_cu_hours_fabric_spark:
        stats.est_cu_hours_fabric_spark += run.est_cu_hours_fabric_spark


def _finalize(stats: SparkRunWindowStats) -> None:
    if stats.run_count > 0 and stats.total_vcore_hours:
        stats.avg_vcore_hours_per_run = stats.total_vcore_hours / stats.run_count


def aggregate_runs(
    runs: Iterable[SparkRunRecord],
    *,
    windows: Sequence[int] = DEFAULT_WINDOWS,
    now: datetime | None = None,
) -> list[SparkPoolRunStats]:
    """Group `runs` by (pool, kind) and roll up into windowed stats.

    Returns one `SparkPoolRunStats` per (pool, kind) seen, each containing one
    `SparkRunWindowStats` per entry in `windows`.
    """
    now = now or _now_utc()
    # (pool, kind) -> [SparkRunRecord]
    bucket: dict[tuple[str, str], list[SparkRunRecord]] = defaultdict(list)
    for r in runs:
        bucket[(r.pool, r.kind)].append(r)

    out: list[SparkPoolRunStats] = []
    for (pool, kind), records in sorted(bucket.items()):
        windowed: list[SparkRunWindowStats] = []
        for days in windows:
            cutoff = now - timedelta(days=days)
            stat = _zero_window(days)
            # Per-UTC-day bucket of CU-hours for the peak-day metric.
            # Bucket key is the date the job was submitted (UTC).
            daily_cu: dict[object, float] = {}
            for r in records:
                if r.submitted_at is None or r.submitted_at < cutoff:
                    continue
                _accumulate(stat, r)
                if r.est_cu_hours_fabric_spark:
                    day = r.submitted_at.astimezone(timezone.utc).date()
                    daily_cu[day] = daily_cu.get(day, 0.0) + r.est_cu_hours_fabric_spark
            _finalize(stat)
            stat.peak_day_cu_hours = max(daily_cu.values()) if daily_cu else 0.0
            windowed.append(stat)
        out.append(
            SparkPoolRunStats(
                pool=pool,
                kind=kind,  # type: ignore[arg-type]
                windows=windowed,
            )
        )
    return out


__all__ = ["aggregate_runs", "DEFAULT_WINDOWS", "VCORE_HOURS_TO_CU_HOURS"]
