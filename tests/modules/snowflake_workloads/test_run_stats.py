"""Phase 7 Slice 7-D — tests for ``modules.snowflake_workloads.run_stats``."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from usma.modules.snowflake_workloads.models import (
    Routine,
    SnowflakeJob,
    SnowflakeJobWindowStats,
    SnowflakeWarehouseWindowStats,
    Table,
    Warehouse,
)
from usma.modules.snowflake_workloads.run_stats import (
    CREDIT_TO_CU_CAVEAT,
    CREDIT_TO_CU_HOURS,
    CREDIT_TO_VCORE_HOURS,
    DEFAULT_WINDOWS,
    VCORES_BY_SIZE,
    aggregate_jobs_by_day,
    aggregate_jobs_by_dimension,
    aggregate_jobs_by_window,
    aggregate_warehouse_metering_by_window,
    credits_per_hour_for_size,
    populate_job_credits,
    populate_warehouse_size_metrics,
    summarize_code_objects,
    vcores_for_size,
)


# ----------------------------------------------------------- constant sanity


def test_constants_doubling_progression() -> None:
    # X-SMALL -> 2X-LARGE doubles at every step.
    sizes = ["X-SMALL", "SMALL", "MEDIUM", "LARGE", "X-LARGE", "2X-LARGE"]
    creds = [VCORES_BY_SIZE[s] for s in sizes]
    for prev, curr in zip(creds, creds[1:]):
        assert curr == prev * 2


def test_credit_to_cu_constant() -> None:
    # 1 credit = 1 vCore-hour; 1 CU = 2 vCores; net 0.5 CU/credit.
    assert CREDIT_TO_VCORE_HOURS == 1.0
    assert CREDIT_TO_CU_HOURS == 0.5


def test_caveat_mentions_calibration_env_var() -> None:
    assert "credit" in CREDIT_TO_CU_CAVEAT.lower()
    assert "SMA_SNOWFLAKE_CREDIT_TO_VCORE_RATIO" in CREDIT_TO_CU_CAVEAT


# ----------------------------------------------------------- size lookups


def test_credits_per_hour_for_size_known() -> None:
    assert credits_per_hour_for_size("X-SMALL") == 1.0
    assert credits_per_hour_for_size("medium") == 4.0  # case-insensitive
    assert credits_per_hour_for_size("2X-LARGE") == 32.0


def test_credits_per_hour_for_size_unknown() -> None:
    assert credits_per_hour_for_size(None) is None
    assert credits_per_hour_for_size("") is None
    assert credits_per_hour_for_size("UNKNOWN") is None
    assert credits_per_hour_for_size("WHATEVER") is None


def test_vcores_for_size_known() -> None:
    assert vcores_for_size("X-SMALL") == 1
    assert vcores_for_size("Large") == 8


def test_vcores_for_size_unknown() -> None:
    assert vcores_for_size(None) is None
    assert vcores_for_size("UNKNOWN") is None


# ----------------------------------------------------------- populate_warehouse


def test_populate_warehouse_size_metrics_known() -> None:
    wh = Warehouse(name="W1", size="MEDIUM")
    populate_warehouse_size_metrics(wh)
    assert wh.credits_per_hour == 4.0
    assert wh.est_vcore_hours_per_hour == 4.0


def test_populate_warehouse_size_metrics_unknown_noop() -> None:
    wh = Warehouse(name="W1", size="UNKNOWN")
    populate_warehouse_size_metrics(wh)
    assert wh.credits_per_hour is None
    assert wh.est_vcore_hours_per_hour is None


# ----------------------------------------------------------- populate_job


def test_populate_job_credits_uses_execution_ms() -> None:
    # 1 hour of MEDIUM (4 vCores) = 4 vCore-hours = 4 credits = 2 CU-hours.
    job = SnowflakeJob(
        query_id="q1",
        warehouse_size="MEDIUM",
        execution_ms=3_600_000,
        total_elapsed_ms=4_000_000,  # higher; should be ignored.
    )
    populate_job_credits(job)
    assert job.est_vcore_hours == pytest.approx(4.0)
    assert job.est_credits == pytest.approx(4.0)
    assert job.est_cu_hours_fabric_warehouse == pytest.approx(2.0)


def test_populate_job_credits_falls_back_to_total_elapsed() -> None:
    job = SnowflakeJob(
        query_id="q2",
        warehouse_size="X-SMALL",
        execution_ms=None,
        total_elapsed_ms=1_800_000,  # 30 min on X-SMALL (1 vCore).
    )
    populate_job_credits(job)
    assert job.est_vcore_hours == pytest.approx(0.5)
    assert job.est_credits == pytest.approx(0.5)
    assert job.est_cu_hours_fabric_warehouse == pytest.approx(0.25)


def test_populate_job_credits_unknown_size_noop() -> None:
    job = SnowflakeJob(
        query_id="q3", warehouse_size=None, execution_ms=3_600_000,
    )
    populate_job_credits(job)
    assert job.est_credits is None


def test_populate_job_credits_missing_time_noop() -> None:
    job = SnowflakeJob(
        query_id="q4", warehouse_size="SMALL", execution_ms=None,
        total_elapsed_ms=None,
    )
    populate_job_credits(job)
    assert job.est_credits is None


# ----------------------------------------------------------- window aggregates


def _job(
    *,
    start_offset_days: float,
    warehouse: str = "WH1",
    size: str = "MEDIUM",
    execution_ms: int = 3_600_000,  # 1h
    outcome: str = "succeeded",
    credits_cs: float = 0.1,
    bytes_scanned: int = 1_000_000,
    now: datetime,
) -> SnowflakeJob:
    start = now - timedelta(days=start_offset_days)
    end = start + timedelta(seconds=execution_ms / 1000.0)
    job = SnowflakeJob(
        query_id=f"q-{start_offset_days}",
        warehouse_name=warehouse,
        warehouse_size=size,
        execution_ms=execution_ms,
        start_time=start,
        end_time=end,
        duration_seconds=(end - start).total_seconds(),
        outcome=outcome,
        credits_used_cloud_services=credits_cs,
        bytes_scanned=bytes_scanned,
    )
    populate_job_credits(job)
    return job


def test_aggregate_jobs_by_window_produces_one_row_per_window() -> None:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    jobs = [
        _job(start_offset_days=1, now=now),    # in every window
        _job(start_offset_days=10, now=now),   # 14/28/90 only
        _job(start_offset_days=50, now=now),   # 90 only
        _job(start_offset_days=200, now=now),  # outside every window
    ]
    rows = aggregate_jobs_by_window(jobs, now=now)
    assert [r.window_days for r in rows] == list(DEFAULT_WINDOWS)
    by_w = {r.window_days: r for r in rows}
    assert by_w[7].job_count == 1
    assert by_w[14].job_count == 2
    assert by_w[28].job_count == 2
    assert by_w[90].job_count == 3


def test_aggregate_jobs_by_window_sums_credits_and_cu() -> None:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # 2 succeeded MEDIUM jobs * 1h = 2 * (4 vCore-h * 0.5 CU/vCore-h) = 4 CU-h.
    jobs = [
        _job(start_offset_days=1, now=now),
        _job(start_offset_days=2, now=now, outcome="succeeded"),
    ]
    rows = aggregate_jobs_by_window(jobs, now=now)
    seven = next(r for r in rows if r.window_days == 7)
    assert seven.succeeded_count == 2
    assert seven.failed_count == 0
    assert seven.success_rate == pytest.approx(1.0)
    assert seven.est_cu_hours_fabric_warehouse == pytest.approx(4.0)
    assert seven.total_credits_cloud_services == pytest.approx(0.2)
    assert seven.total_bytes_scanned == 2_000_000


def test_aggregate_jobs_by_window_handles_empty() -> None:
    rows = aggregate_jobs_by_window([])
    assert all(r.job_count == 0 for r in rows)
    assert all(r.success_rate is None for r in rows)


# ----------------------------------------------------------- warehouse metering


def test_aggregate_warehouse_metering_groups_per_warehouse_per_window() -> None:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    jobs = [
        _job(start_offset_days=1, warehouse="WH_A", size="MEDIUM", now=now),
        _job(start_offset_days=2, warehouse="WH_A", size="MEDIUM", now=now),
        _job(start_offset_days=3, warehouse="WH_B", size="SMALL", now=now),
        _job(start_offset_days=50, warehouse="WH_A", size="LARGE", now=now),
    ]
    rows = aggregate_warehouse_metering_by_window(jobs, now=now)
    # Per window: 7-day has WH_A + WH_B; 90-day has both plus the extra WH_A entry.
    by_key = {(r.window_days, r.warehouse_name): r for r in rows}
    assert (7, "WH_A") in by_key and (7, "WH_B") in by_key
    seven_a = by_key[(7, "WH_A")]
    # 2 jobs * 1h * 4 vCores = 8 vCore-h = 8 credits = 4 CU-h.
    assert seven_a.total_vcore_hours == pytest.approx(8.0)
    assert seven_a.credits_used_compute == pytest.approx(8.0)
    assert seven_a.credits_used_cloud_services == pytest.approx(0.2)
    assert seven_a.total_credits == pytest.approx(8.2)
    assert seven_a.est_cu_hours_fabric_warehouse == pytest.approx(4.0)
    # 90-day window picks up the 50-day-old LARGE job too (1h * 8 vCores).
    ninety_a = by_key[(90, "WH_A")]
    assert ninety_a.total_vcore_hours == pytest.approx(8.0 + 8.0)


def test_aggregate_warehouse_metering_sorted_by_window_then_name() -> None:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    jobs = [
        _job(start_offset_days=1, warehouse="Z", now=now),
        _job(start_offset_days=1, warehouse="A", now=now),
    ]
    rows = aggregate_warehouse_metering_by_window(jobs, now=now)
    # Within window=7, A must come before Z.
    seven = [r for r in rows if r.window_days == 7]
    assert [r.warehouse_name for r in seven] == ["A", "Z"]


def test_aggregate_warehouse_metering_skips_unattributable() -> None:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # No warehouse_name -> skipped.
    j = SnowflakeJob(
        query_id="x", warehouse_name=None, warehouse_size="MEDIUM",
        execution_ms=3_600_000, start_time=now - timedelta(days=1),
        outcome="succeeded",
    )
    populate_job_credits(j)
    rows = aggregate_warehouse_metering_by_window([j], now=now)
    assert rows == []


# ----------------------------------------------------------- analyzer wiring


def test_analyzer_emits_window_stats_and_caveat() -> None:
    """Smoke test: analyzer.run() invokes run_stats end-to-end."""
    from datetime import datetime as dt
    from pathlib import Path
    from unittest.mock import MagicMock

    from usma.config import AppConfig, AzureConfig, SqlConfig
    from usma.modules.snowflake_workloads.analyzer import (
        SnowflakeWorkloadsAnalyzer,
    )

    collector = MagicMock()
    collector.iter_warehouses.return_value = [Warehouse(name="WH1", size="SMALL")]
    collector.iter_databases.return_value = []
    collector.iter_schemas.return_value = []
    collector.iter_jobs.return_value = [
        SnowflakeJob(
            query_id="q1", warehouse_name="WH1", warehouse_size="SMALL",
            execution_ms=3_600_000, outcome="succeeded",
            start_time=dt.now(timezone.utc) - timedelta(hours=1),
            end_time=dt.now(timezone.utc),
            duration_seconds=3600.0,
        ),
    ]
    cfg = AppConfig(
        azure=AzureConfig(
            tenant_id="t", client_id="c", client_secret="s",
            subscription_id="sub", resource_group="rg", workspace_name="ws",
        ),
        sql=SqlConfig(), output_dir=Path("./output"),
    )
    analyzer = SnowflakeWorkloadsAnalyzer(
        cfg, collector=collector, account="acct",
    )
    result = analyzer.run()
    assert result.warehouses[0].credits_per_hour == 2.0  # SMALL
    assert result.warehouses[0].est_vcore_hours_per_hour == 2.0
    assert any(r.window_days == 7 for r in result.job_window_stats)
    assert any(
        w.warehouse_name == "WH1" and w.window_days == 7
        for w in result.warehouse_window_stats
    )
    # CREDIT_TO_CU_CAVEAT should be appended exactly once.
    assert result.caveats.count(CREDIT_TO_CU_CAVEAT) == 1


# ---------------------------------------------------------------- new aggregators


def _mk(query_id: str, **overrides) -> SnowflakeJob:
    base = dict(
        query_id=query_id,
        warehouse_name="WH1",
        warehouse_size="SMALL",
        user_name="alice",
        query_type="SELECT",
        execution_status="SUCCESS",
        outcome="succeeded",
        start_time=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc),
        duration_seconds=30.0,
        execution_ms=30_000,
        bytes_scanned=1024,
    )
    base.update(overrides)
    return SnowflakeJob(**base)


def test_aggregate_jobs_by_day_groups_by_utc_date() -> None:
    jobs = [
        _mk("q1"),
        _mk("q2", start_time=datetime(2026, 5, 1, 23, 0, tzinfo=timezone.utc)),
        _mk(
            "q3",
            start_time=datetime(2026, 5, 2, 1, 0, tzinfo=timezone.utc),
            outcome="failed",
            execution_status="FAIL",
        ),
        _mk("q4", start_time=None),
    ]
    out = aggregate_jobs_by_day(jobs)
    assert [d.date for d in out] == ["2026-05-01", "2026-05-02"]
    d1 = out[0]
    assert d1.job_count == 2
    assert d1.succeeded_count == 2
    assert d1.failed_count == 0
    assert d1.success_rate == 1.0
    d2 = out[1]
    assert d2.failed_count == 1
    assert d2.success_rate == 0.0


def test_aggregate_jobs_by_day_p95() -> None:
    jobs = [
        _mk(f"q{i}", duration_seconds=float(i), execution_ms=i * 1000)
        for i in range(1, 11)
    ]
    out = aggregate_jobs_by_day(jobs)
    assert out[0].p95_duration_seconds == pytest.approx(9.55, abs=0.05)


def test_aggregate_jobs_by_dimension_sorts_by_credits_then_count() -> None:
    jobs = [
        _mk("q1", user_name="alice", est_credits=5.0),
        _mk("q2", user_name="bob", est_credits=10.0),
        _mk("q3", user_name="bob"),
    ]
    out = aggregate_jobs_by_dimension(
        jobs, dimension="user", key=lambda j: j.user_name,
    )
    assert [r.key for r in out] == ["bob", "alice"]
    assert out[0].job_count == 2
    assert out[0].est_credits == 10.0
    assert all(r.dimension == "user" for r in out)


def test_aggregate_jobs_by_dimension_unknown_bucket() -> None:
    out = aggregate_jobs_by_dimension(
        [_mk("q1", user_name=None), _mk("q2", user_name="")],
        dimension="user",
        key=lambda j: j.user_name,
    )
    assert len(out) == 1
    assert out[0].key == "<unknown>"
    assert out[0].job_count == 2


def test_summarize_code_objects_pct_and_lists() -> None:
    tables = [
        Table(
            database_name="D", schema_name="S", name="T1",
            full_name="D.S.T1", kind="TABLE", support="supported",
        ),
        Table(
            database_name="D", schema_name="S", name="V1",
            full_name="D.S.V1", kind="VIEW", support="partial",
        ),
        Table(
            database_name="D", schema_name="S", name="EXT",
            full_name="D.S.EXT", kind="EXTERNAL_TABLE", support="unsupported",
        ),
    ]
    routines = [
        Routine(
            database_name="D", schema_name="S", name="FN",
            full_name="D.S.FN", routine_kind="FUNCTION",
            language="JAVASCRIPT", support="unsupported",
        ),
        Routine(
            database_name="D", schema_name="S", name="SP",
            full_name="D.S.SP", routine_kind="PROCEDURE",
            language="SQL", support="partial",
        ),
    ]
    s = summarize_code_objects(tables, routines)
    assert s.total == 5
    assert s.compatibility_pct == 20.0
    assert s.by_kind == {
        "EXTERNAL_TABLE": 1, "FUNCTION": 1, "PROCEDURE": 1,
        "TABLE": 1, "VIEW": 1,
    }
    assert s.by_support["unsupported"] == 2
    assert s.by_support["partial"] == 2
    assert s.by_language == {"JAVASCRIPT": 1, "SQL": 1}
    assert "D.S.EXT" in s.unsupported_object_names
    assert "D.S.V1" in s.partial_object_names

