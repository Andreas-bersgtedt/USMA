"""Tests for ``bigquery_workloads.run_stats`` (Phase 5 Slice 5-C)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from usma.modules.bigquery_workloads.models import BigQueryJob
from usma.modules.bigquery_workloads.run_stats import (
    DEFAULT_WINDOWS,
    SLOT_HOURS_TO_CU_HOURS,
    SLOT_HOURS_TO_VCORE_HOURS,
    SLOT_TO_CU_CAVEAT,
    aggregate_jobs_by_day,
    aggregate_jobs_by_dimension,
    aggregate_jobs_by_window,
    aggregate_table_usage,
    cu_hours_for_slot_hours,
    populate_slot_hours,
    slot_hours_for_ms,
)


PROJECT = "demo-project-123"


def _job(
    *,
    job_id: str,
    start_offset_days: float,
    total_slot_ms: int | None,
    outcome: str = "succeeded",
    duration_seconds: float | None = 10.0,
    total_billed_bytes: int | None = 1024,
    now: datetime,
) -> BigQueryJob:
    start = now - timedelta(days=start_offset_days)
    return BigQueryJob(
        job_id=job_id,
        project_id=PROJECT,
        location="us",
        job_type="QUERY",
        outcome=outcome,  # type: ignore[arg-type]
        start_time=start,
        end_time=start + timedelta(seconds=duration_seconds or 0),
        duration_seconds=duration_seconds,
        total_slot_ms=total_slot_ms,
        total_billed_bytes=total_billed_bytes,
    )


# --------------------------------------------------------------- conversions


def test_constants_are_consistent():
    # 0.5 vCore per slot × 0.5 CU per vCore-hr = 0.25 CU-hr per slot-hr.
    assert SLOT_HOURS_TO_VCORE_HOURS == 0.5
    assert SLOT_HOURS_TO_CU_HOURS == 0.25


def test_slot_hours_for_ms():
    # 1 hour of slot work = 3,600,000 ms = 1.0 slot-hr.
    assert slot_hours_for_ms(3_600_000) == 1.0
    assert slot_hours_for_ms(1_800_000) == 0.5
    assert slot_hours_for_ms(0) == 0.0
    assert slot_hours_for_ms(None) is None
    assert slot_hours_for_ms(-1) is None


def test_cu_hours_for_slot_hours():
    assert cu_hours_for_slot_hours(4.0) == 1.0  # 4 slot-hr × 0.25 = 1 CU-hr
    assert cu_hours_for_slot_hours(0.0) == 0.0
    assert cu_hours_for_slot_hours(None) is None
    assert cu_hours_for_slot_hours(-1.0) is None


def test_populate_slot_hours_happy_path():
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    j = _job(job_id="j1", start_offset_days=1, total_slot_ms=3_600_000, now=now)
    populate_slot_hours(j)
    assert j.slot_hours == 1.0
    assert j.est_cu_hours_fabric_spark == 0.25


def test_populate_slot_hours_noop_when_missing():
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    j = _job(job_id="j2", start_offset_days=1, total_slot_ms=None, now=now)
    populate_slot_hours(j)
    assert j.slot_hours is None
    assert j.est_cu_hours_fabric_spark is None


# ---------------------------------------------------------------- aggregation


def test_aggregate_jobs_by_window_filters_and_sums():
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    jobs = [
        # within 7 days
        _job(job_id="j1", start_offset_days=1, total_slot_ms=3_600_000, now=now),
        _job(job_id="j2", start_offset_days=3, total_slot_ms=7_200_000, now=now),
        # within 14 days but not 7
        _job(job_id="j3", start_offset_days=10, total_slot_ms=1_800_000, now=now),
        # within 28 but not 14
        _job(job_id="j4", start_offset_days=20, total_slot_ms=900_000, outcome="failed", now=now),
        # within 90 but not 28
        _job(job_id="j5", start_offset_days=60, total_slot_ms=10_800_000, now=now),
        # outside 90
        _job(job_id="old", start_offset_days=120, total_slot_ms=1_000_000, now=now),
    ]
    for j in jobs:
        populate_slot_hours(j)

    windows = aggregate_jobs_by_window(jobs, now=now)
    assert [w.window_days for w in windows] == list(DEFAULT_WINDOWS)
    by_w = {w.window_days: w for w in windows}

    # 7-day window: j1 + j2 = 1 + 2 = 3 slot-hr → 0.75 CU-hr
    w7 = by_w[7]
    assert w7.job_count == 2
    assert w7.completed_count == 2
    assert w7.succeeded_count == 2
    assert w7.failed_count == 0
    assert w7.success_rate == 1.0
    assert w7.total_slot_hours == 3.0
    assert w7.est_cu_hours_fabric_spark == 3.0 * SLOT_HOURS_TO_CU_HOURS
    assert w7.avg_slot_hours_per_job == 1.5
    assert w7.total_billed_bytes == 2048

    # 14-day window: + j3 = 3.5 slot-hr
    w14 = by_w[14]
    assert w14.job_count == 3
    assert w14.total_slot_hours == 3.5

    # 28-day window: + j4 (failed) → 3.5 + 0.25 = 3.75 slot-hr
    w28 = by_w[28]
    assert w28.job_count == 4
    assert w28.failed_count == 1
    assert w28.succeeded_count == 3
    assert w28.success_rate == 0.75
    assert abs(w28.total_slot_hours - 3.75) < 1e-9

    # 90-day window: + j5 = 3.75 + 3 = 6.75 slot-hr (j5 has 10.8M ms = 3 slot-hr)
    w90 = by_w[90]
    assert w90.job_count == 5
    assert abs(w90.total_slot_hours - 6.75) < 1e-9
    assert w90.est_cu_hours_fabric_spark == 6.75 * SLOT_HOURS_TO_CU_HOURS


def test_aggregate_empty_returns_zero_filled_windows():
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    windows = aggregate_jobs_by_window([], now=now)
    assert len(windows) == len(DEFAULT_WINDOWS)
    assert all(w.job_count == 0 for w in windows)
    assert all(w.total_slot_hours == 0.0 for w in windows)
    assert all(w.success_rate is None for w in windows)


def test_caveat_string_documents_ratio():
    assert "0.25" in SLOT_TO_CU_CAVEAT
    assert "slot" in SLOT_TO_CU_CAVEAT.lower()


# --------------------------------------------------------- table usage rollup


def _job_with_refs(
    job_id: str,
    refs: list[str],
    *,
    slot_ms: int | None = 3_600_000,
    duration_s: float | None = 10.0,
    billed_bytes: int | None = 1_000_000,
) -> BigQueryJob:
    return BigQueryJob(
        job_id=job_id,
        project_id=PROJECT,
        location="us",
        job_type="QUERY",
        outcome="succeeded",
        duration_seconds=duration_s,
        total_slot_ms=slot_ms,
        total_billed_bytes=billed_bytes,
        referenced_tables=refs,
    )


def test_aggregate_table_usage_counts_and_sorts():
    """Hot table appears first; each job increments every referenced table once."""
    from usma.modules.bigquery_workloads.models import Table

    jobs = [
        _job_with_refs("j1", [f"{PROJECT}.sales.orders"]),
        _job_with_refs(
            "j2",
            [f"{PROJECT}.sales.orders", f"{PROJECT}.sales.customers"],
            slot_ms=1_800_000,
            duration_s=5.0,
            billed_bytes=500_000,
        ),
        _job_with_refs(
            "j3",
            [f"{PROJECT}.sales.orders"],
            slot_ms=7_200_000,
            duration_s=20.0,
            billed_bytes=2_000_000,
        ),
    ]
    catalog = [
        Table(
            project_id=PROJECT,
            dataset_id="sales",
            table_id="orders",
            full_table_id=f"{PROJECT}.sales.orders",
            table_type="TABLE",
            support="supported",
        ),
    ]

    usage = aggregate_table_usage(jobs, catalog=catalog)

    assert [u.full_table_id for u in usage] == [
        f"{PROJECT}.sales.orders",       # 3 jobs
        f"{PROJECT}.sales.customers",    # 1 job
    ]
    orders = usage[0]
    assert orders.usage_count == 3
    # 3.6M + 1.8M + 7.2M ms = 12.6M ms = 3.5 slot-hours
    assert orders.total_slot_hours == 3.5
    assert orders.total_elapsed_seconds == 35.0
    assert orders.total_billed_bytes == 3_500_000
    assert orders.in_catalog is True
    assert orders.table_support == "supported"
    # Off-catalog table flagged
    customers = usage[1]
    assert customers.in_catalog is False
    assert customers.table_support == "unknown"


def test_aggregate_table_usage_dedupes_within_job():
    """A self-join referencing the same table twice still counts once per job."""
    jobs = [
        _job_with_refs(
            "j1",
            [f"{PROJECT}.sales.orders", f"{PROJECT}.sales.orders"],
        ),
    ]
    usage = aggregate_table_usage(jobs)
    assert len(usage) == 1
    assert usage[0].usage_count == 1


def test_aggregate_table_usage_handles_missing_metrics():
    """Jobs with null slot_ms / duration / bytes contribute usage_count only."""
    jobs = [
        _job_with_refs(
            "j1",
            [f"{PROJECT}.sales.orders"],
            slot_ms=None,
            duration_s=None,
            billed_bytes=None,
        ),
    ]
    usage = aggregate_table_usage(jobs)
    assert usage[0].usage_count == 1
    assert usage[0].total_slot_hours == 0.0
    assert usage[0].total_elapsed_seconds == 0.0
    assert usage[0].total_billed_bytes == 0


def test_aggregate_table_usage_empty_input():
    assert aggregate_table_usage([]) == []


# ---------------------------------------------------- aggregate_jobs_by_day


def test_aggregate_jobs_by_day_buckets_by_utc_date():
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    jobs = [
        _job(job_id="a", start_offset_days=0, total_slot_ms=3_600_000, now=now),  # today
        _job(job_id="b", start_offset_days=0, total_slot_ms=1_800_000, outcome="failed", now=now),
        _job(job_id="c", start_offset_days=1, total_slot_ms=900_000, now=now),  # yesterday
    ]
    daily = aggregate_jobs_by_day(jobs)
    assert len(daily) == 2
    # Ascending by date.
    assert daily[0].date < daily[1].date
    by_date = {d.date: d for d in daily}
    today = by_date["2026-05-22"]
    assert today.job_count == 2
    assert today.succeeded_count == 1
    assert today.failed_count == 1
    assert today.total_slot_hours == pytest.approx(1.5)
    assert today.success_rate == 0.5
    assert today.est_cu_hours_fabric_spark == pytest.approx(0.375)


def test_aggregate_jobs_by_day_skips_jobs_without_start_time():
    j = BigQueryJob(
        job_id="x", project_id=PROJECT, location="us", job_type="QUERY",
        outcome="cancelled", start_time=None, end_time=None,
    )
    assert aggregate_jobs_by_day([j]) == []


def test_aggregate_jobs_by_day_empty_returns_empty():
    assert aggregate_jobs_by_day([]) == []


# ----------------------------------------------- aggregate_jobs_by_dimension


def test_aggregate_jobs_by_dimension_groups_and_sorts_desc():
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    j1 = _job(job_id="a", start_offset_days=0, total_slot_ms=3_600_000, now=now)
    j1.user_email = "alice@x"
    j2 = _job(job_id="b", start_offset_days=0, total_slot_ms=1_800_000, now=now)
    j2.user_email = "alice@x"
    j3 = _job(job_id="c", start_offset_days=0, total_slot_ms=600_000, now=now)
    j3.user_email = "bob@x"
    rows = aggregate_jobs_by_dimension([j1, j2, j3], dimension="user", key=lambda j: j.user_email)
    assert [r.key for r in rows] == ["alice@x", "bob@x"]
    assert rows[0].dimension == "user"
    assert rows[0].job_count == 2
    assert rows[0].total_slot_hours == pytest.approx(1.5)


def test_aggregate_jobs_by_dimension_null_key_becomes_unknown():
    now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    j = _job(job_id="a", start_offset_days=0, total_slot_ms=0, now=now)
    j.user_email = None
    rows = aggregate_jobs_by_dimension([j], dimension="user", key=lambda j: j.user_email)
    assert len(rows) == 1
    assert rows[0].key == "<unknown>"


def test_aggregate_jobs_by_dimension_empty_returns_empty():
    assert aggregate_jobs_by_dimension([], dimension="user", key=lambda j: j.user_email) == []
