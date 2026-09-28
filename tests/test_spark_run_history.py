"""Tests for Synapse Spark Livy job history collection & aggregation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from usma.modules.spark_pools.spark_history_client import (
    SparkHistoryClient,
    VCORE_HOURS_TO_CU_HOURS,
    _classify_trigger,
    _normalize,
    compute_vcore_seconds,
    vcore_shape_from_app_info,
)
from usma.modules.spark_pools.spark_run_stats import (
    aggregate_runs,
    aggregate_daily_usage,
    deduplicate_runs,
)
from usma.modules.spark_pools.models import SparkAnalysis, SparkRunRecord
from usma.modules.fabric_mapping.cu_projection import project_capacity


# ----------------------------------------------------------- shape extraction


def test_vcore_shape_prefers_app_info():
    dc, ec, nx = vcore_shape_from_app_info(
        {"driverCores": "4", "executorCores": "8", "numExecutors": "3"},
        SimpleNamespace(driver_cores=99, executor_cores=99, executor_count=99),
    )
    assert (dc, ec, nx) == (4, 8, 3)


def test_vcore_shape_falls_back_to_creation_request():
    dc, ec, nx = vcore_shape_from_app_info(
        None,
        SimpleNamespace(driver_cores=4, executor_cores=8, executor_count=2),
    )
    assert (dc, ec, nx) == (4, 8, 2)


def test_vcore_shape_returns_none_when_unknown():
    dc, ec, nx = vcore_shape_from_app_info(None, None)
    assert (dc, ec, nx) == (None, None, None)


def test_vcore_shape_uses_documented_sdk_request_fields():
    dc, ec, nx = vcore_shape_from_app_info(
        None,
        SimpleNamespace(driver_cores=8, executor_cores=4, executor_count=6),
    )
    assert (dc, ec, nx) == (8, 4, 6)


# -------------------------------------------------------------- vcore-second


def test_compute_vcore_seconds_basic():
    # driver 4 + 3 executors x 8 cores = 28 vcores; over 360s -> 10080 vcore-s
    total, vcs = compute_vcore_seconds(4, 8, 3, 360.0)
    assert total == 28
    assert vcs == 10080.0
    # 10080 / 3600 = 2.8 vcore-h; CU-h = 2.8 * 0.5 = 1.4
    vcore_hours = vcs / 3600.0
    assert pytest.approx(vcore_hours, rel=1e-6) == 2.8
    assert pytest.approx(vcore_hours * VCORE_HOURS_TO_CU_HOURS, rel=1e-6) == 1.4


def test_compute_vcore_seconds_missing_shape_returns_none():
    assert compute_vcore_seconds(None, 8, 3, 360.0) == (None, None)
    assert compute_vcore_seconds(4, 8, 3, 0) == (None, None)
    assert compute_vcore_seconds(4, 8, 3, None) == (None, None)


# ------------------------------------------------------------- normalization


def _make_sdk_run(
    *,
    job_id: int,
    submitted_at: datetime,
    ended_at: datetime,
    state: str = "success",
    result: str = "Succeeded",
    driver_cores: int = 4,
    executor_cores: int = 8,
    num_executors: int = 3,
    submitter_id: str = "user@contoso.com",
    name: str = "job-x",
):
    """Mirror the relevant attribute surface of azure-synapse-spark models."""
    return SimpleNamespace(
        id=job_id,
        name=name,
        app_id=f"application_{job_id}",
        submitter_id=submitter_id,
        submitter_name=None,
        artifact_id=None,
        state=state,
        result=result,
        scheduler=SimpleNamespace(submitted_at=submitted_at, ended_at=ended_at),
        livy_info=SimpleNamespace(
            job_creation_request=SimpleNamespace(
                driver_cores=driver_cores,
                executor_cores=executor_cores,
                executor_count=num_executors,
            )
        ),
        app_info={
            "driverCores": str(driver_cores),
            "executorCores": str(executor_cores),
            "numExecutors": str(num_executors),
        },
    )


def test_normalize_scheduled_batch_run():
    now = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    raw = _make_sdk_run(
        job_id=42,
        submitted_at=now - timedelta(minutes=6),
        ended_at=now,
    )
    rec = _normalize(raw, kind="scheduled", pool="poolA")
    assert isinstance(rec, SparkRunRecord)
    assert rec.kind == "scheduled"
    assert rec.pool == "poolA"
    assert rec.livy_id == 42
    assert rec.outcome == "succeeded"
    # 6 minutes = 360s; 28 vcores; 2.8 vcore-h; 1.4 CU-h
    assert rec.total_vcores == 28
    assert pytest.approx(rec.vcore_hours, rel=1e-6) == 2.8
    assert pytest.approx(rec.est_cu_hours_fabric_spark, rel=1e-6) == 1.4


def test_normalize_failed_interactive_session():
    now = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    raw = _make_sdk_run(
        job_id=7,
        submitted_at=now - timedelta(seconds=60),
        ended_at=now,
        state="dead",
        result="Failed",
    )
    rec = _normalize(raw, kind="interactive", pool="poolB")
    assert rec.outcome == "failed"
    assert rec.kind == "interactive"


def test_normalize_stopped_session_counts_as_succeeded():
    """Synapse Studio shows notebook sessions that shut down cleanly as
    "Stopped". Livy reports state=dead/shutting_down with result=Uncertain
    (or None). These must NOT be counted as failures — they're successful
    runs whose underlying compute should still contribute to CU sizing."""
    now = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    for state in ("dead", "shutting_down", "stopped", "killed"):
        raw = _make_sdk_run(
            job_id=9,
            submitted_at=now - timedelta(seconds=60),
            ended_at=now,
            state=state,
            result="Uncertain",
        )
        rec = _normalize(raw, kind="interactive", pool="poolB")
        assert rec.outcome == "succeeded", f"state={state} should map to succeeded"


def test_normalize_pipeline_cancelled_after_success_counts_as_succeeded():
    """Synapse pipeline and Spark Job Definition framework routinely reap
    notebook sessions/batches after the user code returned successfully,
    and Livy records this teardown as state=dead/killed + result=Cancelled.
    Studio shows these as Succeeded, so the analyzer must too."""
    now = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    for state in ("dead", "killed", "shutting_down", "stopped"):
        for trigger_kind in ("scheduled", "interactive"):
            raw = _make_sdk_run(
                job_id=11,
                submitted_at=now - timedelta(seconds=60),
                ended_at=now,
                state=state,
                result="Cancelled",
            )
            rec = _normalize(raw, kind=trigger_kind, pool="poolB")
            assert rec.outcome == "succeeded", (
                f"state={state} + result=Cancelled (post-success cleanup, "
                f"trigger={trigger_kind}) must map to succeeded"
            )


def test_normalize_mid_run_cancel_is_failure():
    """A genuine user/admin cancel mid-execution (Cancelled result while
    the session is still in a non-terminal running state) is a failure."""
    now = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    raw = _make_sdk_run(
        job_id=12,
        submitted_at=now - timedelta(seconds=60),
        ended_at=now,
        state="running",
        result="Cancelled",
    )
    rec = _normalize(raw, kind="scheduled", pool="poolB")
    assert rec.outcome == "failed"


def test_normalize_explicit_failed_is_failure():
    """Explicit result=Failed (real Spark application failure) is always a failure
    regardless of trigger kind or Livy endpoint."""
    now = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    raw = _make_sdk_run(
        job_id=13,
        submitted_at=now - timedelta(seconds=60),
        ended_at=now,
        state="dead",
        result="Failed",
    )
    rec = _normalize(raw, kind="scheduled", pool="poolB")
    assert rec.outcome == "failed"


def test_normalize_falls_back_to_plugin_timestamps():
    """Stopped sessions often drop the `scheduler` block — make sure we
    still recover submitted_at from the plugin lifecycle timestamps so the
    run is bucketed into the windowed aggregation."""
    now = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    raw = SimpleNamespace(
        id=11,
        name="job",
        app_id="app",
        submitter_id="u",
        submitter_name=None,
        artifact_id=None,
        state="dead",
        result="Uncertain",
        scheduler=None,
        plugin=SimpleNamespace(
            preparation_started_at=now - timedelta(minutes=5),
            submission_started_at=None,
            monitoring_started_at=None,
            cleanup_started_at=now,
        ),
        livy_info=SimpleNamespace(
            job_creation_request=SimpleNamespace(
                driver_cores=4, executor_cores=8, executor_count=3,
            ),
            created_at=None,
            deleted_at=None,
        ),
        app_info={"driverCores": "4", "executorCores": "8", "numExecutors": "3"},
    )
    rec = _normalize(raw, kind="interactive", pool="poolB")
    assert rec is not None
    assert rec.submitted_at == now - timedelta(minutes=5)
    assert rec.duration_seconds == pytest.approx(300.0)
    assert rec.outcome == "succeeded"


def test_normalize_starts_usage_at_resource_acquisition_not_submission():
    submitted = datetime(2026, 2, 1, 10, tzinfo=timezone.utc)
    allocated = submitted + timedelta(hours=1)
    ended = allocated + timedelta(hours=2)
    raw = _make_sdk_run(
        job_id=43,
        submitted_at=submitted,
        ended_at=ended,
    )
    raw.plugin = SimpleNamespace(resource_acquisition_started_at=allocated)
    rec = _normalize(raw, kind="scheduled", pool="poolA")
    assert rec is not None
    assert rec.submitted_at == submitted
    assert rec.accounting_start_at == allocated
    assert rec.duration_seconds == pytest.approx(7200)
    assert rec.vcore_hours == pytest.approx(56)
    assert rec.usage_basis == "fixed_shape_estimate"


def test_normalize_does_not_use_monitoring_started_as_end_time():
    started = datetime(2026, 2, 1, 10, tzinfo=timezone.utc)
    raw = _make_sdk_run(
        job_id=44,
        submitted_at=started,
        ended_at=started + timedelta(hours=1),
    )
    raw.scheduler.ended_at = None
    raw.plugin = SimpleNamespace(monitoring_started_at=started + timedelta(minutes=5))
    rec = _normalize(raw, kind="scheduled", pool="poolA")
    assert rec is not None
    assert rec.ended_at is None
    assert rec.duration_seconds is None
    assert rec.usage_basis == "fixed_shape_estimate"


def test_normalize_skips_run_without_id():
    raw = SimpleNamespace(id=None)
    assert _normalize(raw, kind="scheduled", pool="x") is None


def test_normalize_uses_configuration_alias_and_preserves_app_info():
    raw = _make_sdk_run(
        job_id=45,
        submitted_at=datetime(2026, 2, 1, 10, tzinfo=timezone.utc),
        ended_at=datetime(2026, 2, 1, 11, tzinfo=timezone.utc),
    )
    raw.app_info = {
        "driverCores": "4",
        "executorCores": "8",
        "numExecutors": "3",
        "attemptId": "attempt_2",
    }
    raw.livy_info.job_creation_request = SimpleNamespace(
        driver_cores=None,
        executor_cores=None,
        executor_count=None,
        configuration={"spark.synapse.context.pipelinejobid": "pipeline-1"},
    )
    rec = _normalize(raw, kind="scheduled", pool="poolA", livy_kind="session")
    assert rec is not None
    assert rec.pipeline_job_id == "pipeline-1"
    assert rec.app_info["attemptId"] == "attempt_2"


def test_normalize_uses_documented_livy_lifecycle_timestamps():
    submitted = datetime(2026, 2, 1, 10, tzinfo=timezone.utc)
    allocated = submitted + timedelta(minutes=20)
    ended = allocated + timedelta(hours=1)
    raw = _make_sdk_run(
        job_id=46,
        submitted_at=submitted,
        ended_at=ended,
    )
    raw.scheduler.ended_at = None
    raw.plugin = None
    raw.livy_info = SimpleNamespace(
        running_at=allocated,
        success_at=ended,
        job_creation_request=SimpleNamespace(
            driver_cores=4, executor_cores=8, executor_count=3,
        ),
    )
    rec = _normalize(raw, kind="scheduled", pool="poolA", livy_kind="batch")
    assert rec is not None
    assert rec.accounting_start_at == allocated
    assert rec.accounting_start_basis == "livy_running_at"
    assert rec.ended_at == ended
    assert rec.duration_seconds == pytest.approx(3600)


# --------------------------------------------------------- trigger classification


def _session_raw_with_conf(conf: dict | None, *, name: str = "Notebook 1_sparkpool001_1778572324"):
    """Helper that builds a minimal SDK-shaped raw record with the
    `livy_info.job_creation_request.conf` nested structure populated."""
    creation_req = SimpleNamespace(conf=conf) if conf is not None else SimpleNamespace(conf=None)
    livy_info = SimpleNamespace(job_creation_request=creation_req)
    return SimpleNamespace(name=name, tags={}, livy_info=livy_info)


def test_classify_trigger_batch_endpoint_is_always_scheduled():
    raw = SimpleNamespace(name="anything", tags=None, livy_info=None)
    assert _classify_trigger(raw, livy_kind="batch") == "scheduled"


def test_classify_trigger_pipeline_session_by_spark_conf():
    """The authoritative Synapse signal: the pipeline framework injects
    `spark.synapse.context.pipelinejobid` (and friends) into the Spark
    conf. These keys are *only* set for pipeline / Spark Job Definition
    runs, never for user-attached interactive notebooks."""
    for key in (
        "spark.synapse.context.pipelinejobid",
        "spark.synapse.context.activityrunid",
        "spark.synapse.context.activityname",
        "spark.synapse.nbs.runid",
    ):
        raw = _session_raw_with_conf({key: "some-value"})
        assert _classify_trigger(raw, livy_kind="session") == "scheduled", key


def test_classify_trigger_interactive_session_matches_auto_name_pattern_without_conf():
    """Regression: Synapse Studio assigns the *same* auto-name pattern
    `<NotebookName>_<pool>_<unix-ts>` to user-attached interactive
    notebooks as to pipeline-triggered runs. Without pipeline conf keys
    or scheduler tags, those sessions are interactive ad-hoc work."""
    raw = _session_raw_with_conf({"spark.executor.memory": "28g"})
    assert _classify_trigger(raw, livy_kind="session") == "interactive"
    raw2 = _session_raw_with_conf(None, name="some-other-session")
    assert _classify_trigger(raw2, livy_kind="session") == "interactive"


def test_classify_trigger_blank_conf_value_is_not_pipeline():
    """Defensive: an empty / whitespace conf value should not flip the
    classification — only a real value indicates a pipeline run."""
    raw = _session_raw_with_conf({"spark.synapse.context.pipelinejobid": "  "})
    assert _classify_trigger(raw, livy_kind="session") == "interactive"


def test_classify_trigger_pipeline_session_by_tags():
    raw = SimpleNamespace(
        name="some-name",
        tags={"PipelineRunId": "abc-123"},
        livy_info=None,
    )
    assert _classify_trigger(raw, livy_kind="session") == "scheduled"
    raw2 = SimpleNamespace(name="some-name", tags={"JobType": "SparkNotebook"}, livy_info=None)
    assert _classify_trigger(raw2, livy_kind="session") == "scheduled"


def test_normalize_extracts_pipeline_correlation_ids_from_conf():
    """The user's real pipeline-triggered notebook payload carries the
    pipeline / activity / notebook IDs in the Spark conf. We surface
    those on the SparkRunRecord so downstream tooling can join Spark
    runs to the parent pipeline activity."""
    now = datetime(2026, 5, 12, 9, 14, tzinfo=timezone.utc)
    creation_req = SimpleNamespace(
        driver_cores=4,
        executor_cores=4,
        executor_count=2,
        conf={
            "spark.synapse.context.pipelinejobid": "00fb2700-aa32-44ad-bee3-0acc821a8a9e",
            "spark.synapse.context.activityrunid": "427b17ca-b6b8-4be4-be34-0589320c0608",
            "spark.synapse.context.activityname": "Notebook1",
            "spark.synapse.context.notebookname": "Notebook 1",
            "spark.synapse.nbs.runid": "427b17ca-b6b8-4be4-be34-0589320c0608",
        },
    )
    livy_info = SimpleNamespace(
        job_creation_request=creation_req,
        created_at=now - timedelta(seconds=180),
        deleted_at=now,
    )
    raw = SimpleNamespace(
        id=52,
        name="Notebook 1_sparkpool001_1778577132",
        app_id="application_1778576030615_0002",
        submitter_id="2864980c-2195-47d6-bc42-d8b4ff8e3e11",
        submitter_name=None,
        artifact_id="Livy",
        state="killed",
        result="cancelled",
        scheduler=SimpleNamespace(
            submitted_at=now - timedelta(seconds=180),
            ended_at=now,
        ),
        plugin=None,
        livy_info=livy_info,
        app_info={"driverCores": "4", "executorCores": "4", "numExecutors": "2"},
        tags={},
    )
    rec = _normalize(raw, kind="scheduled", pool="sparkpool001", livy_kind="session")
    assert rec is not None
    assert rec.pipeline_job_id == "00fb2700-aa32-44ad-bee3-0acc821a8a9e"
    assert rec.activity_run_id == "427b17ca-b6b8-4be4-be34-0589320c0608"
    assert rec.activity_name == "Notebook1"
    assert rec.notebook_name == "Notebook 1"
    assert rec.notebook_run_id == "427b17ca-b6b8-4be4-be34-0589320c0608"
    # And — given the conf — `_classify_trigger` would mark this as scheduled.
    assert _classify_trigger(raw, livy_kind="session") == "scheduled"
    # Post-success Cancelled cleanup → succeeded outcome.
    assert rec.outcome == "succeeded"


# -------------------------------------------------------------- aggregation


def _rec(
    *,
    pool: str,
    kind: str,
    submitted_at: datetime,
    duration_s: float = 360.0,
    outcome: str = "succeeded",
    vcores: int = 28,
) -> SparkRunRecord:
    vcs = vcores * duration_s
    vch = vcs / 3600.0
    return SparkRunRecord(
        livy_id=1,
        kind=kind,  # type: ignore[arg-type]
        pool=pool,
        outcome=outcome,  # type: ignore[arg-type]
        submitted_at=submitted_at,
        duration_seconds=duration_s,
        total_vcores=vcores,
        vcore_seconds=vcs,
        vcore_hours=vch,
        est_cu_hours_fabric_spark=vch * VCORE_HOURS_TO_CU_HOURS,
    )


def test_aggregate_runs_per_pool_per_kind_windowed():
    now = datetime(2025, 1, 30, 12, 0, tzinfo=timezone.utc)
    runs = [
        # poolA scheduled, all within 7d
        _rec(pool="A", kind="scheduled", submitted_at=now - timedelta(days=1)),
        _rec(pool="A", kind="scheduled", submitted_at=now - timedelta(days=3)),
        # poolA scheduled older than 7d but within 28d
        _rec(pool="A", kind="scheduled", submitted_at=now - timedelta(days=10)),
        # poolA interactive
        _rec(pool="A", kind="interactive", submitted_at=now - timedelta(days=2),
             outcome="failed"),
        # poolB scheduled outside 7d window
        _rec(pool="B", kind="scheduled", submitted_at=now - timedelta(days=15)),
    ]
    stats = aggregate_runs(runs, windows=(7, 28, 90), now=now)
    # Expect (A,interactive), (A,scheduled), (B,scheduled)
    keys = [(s.pool, s.kind) for s in stats]
    assert keys == [("A", "interactive"), ("A", "scheduled"), ("B", "scheduled")]

    a_sched = next(s for s in stats if s.pool == "A" and s.kind == "scheduled")
    w7 = a_sched.windows[0]
    assert w7.window_days == 7
    assert w7.run_count == 2
    assert w7.succeeded == 2
    # Each run: 2.8 vcore-h x 1.4 CU-h
    assert pytest.approx(w7.total_vcore_hours, rel=1e-6) == 5.6
    assert pytest.approx(w7.est_cu_hours_fabric_spark, rel=1e-6) == 2.8

    w28 = a_sched.windows[1]
    assert w28.window_days == 28
    assert w28.run_count == 3
    assert pytest.approx(w28.est_cu_hours_fabric_spark, rel=1e-6) == 4.2

    a_int = next(s for s in stats if s.pool == "A" and s.kind == "interactive")
    assert a_int.windows[0].run_count == 1
    assert a_int.windows[0].failed == 1
    assert a_int.windows[0].succeeded == 0


def test_aggregate_runs_empty_returns_empty_list():
    assert aggregate_runs([]) == []


def test_complete_empty_collection_reports_covered_zero_usage():
    now = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)
    stats = aggregate_runs(
        [],
        windows=(3,),
        now=now,
        collection_complete=True,
        groups=(("poolA", "scheduled"), ("poolA", "interactive")),
    )
    assert [(item.pool, item.kind) for item in stats] == [
        ("poolA", "interactive"),
        ("poolA", "scheduled"),
    ]
    for pool_stats in stats:
        window = pool_stats.windows[0]
        assert window.run_count == 0
        assert window.known_usage_run_count == 0
        assert window.unknown_usage_run_count == 0
        assert window.avg_daily_vcore_hours == 0
        assert window.avg_daily_cu_hours == 0
        assert window.steady_state_cu == 0


def test_incomplete_empty_collection_does_not_claim_zero_usage():
    now = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)
    stats = aggregate_runs(
        [],
        windows=(3,),
        now=now,
        collection_complete=False,
        groups=(("poolA", "scheduled"),),
    )
    window = stats[0].windows[0]
    assert window.run_count == 0
    assert window.avg_daily_vcore_hours is None
    assert window.avg_daily_cu_hours is None
    assert window.steady_state_cu is None


def test_aggregate_clips_pre_window_runs_and_splits_usage_across_utc_days():
    end = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)
    crossing = SparkRunRecord(
        livy_id=1,
        kind="scheduled",
        pool="A",
        app_id="app-cross",
        submitted_at=end - timedelta(days=7, hours=1),
        accounting_start_at=end - timedelta(days=7, hours=1),
        ended_at=end - timedelta(days=7) + timedelta(hours=1),
        duration_seconds=7200,
        total_vcores=32,
        vcore_hours=64,
        est_cu_hours_fabric_spark=32,
        usage_basis="fixed_shape_estimate",
    )
    stats = aggregate_runs([crossing], windows=(7,), now=end)
    window = stats[0].windows[0]
    assert window.run_count == 1
    assert window.total_vcore_hours == pytest.approx(32)
    assert window.avg_daily_vcore_hours == pytest.approx(32 / 7)
    assert window.avg_daily_cu_hours == pytest.approx(16 / 7)
    assert window.steady_state_cu == pytest.approx(16 / (7 * 24))
    daily = aggregate_daily_usage(
        [crossing],
        start=end - timedelta(days=7),
        end=end,
    )
    assert len(daily) == 7
    assert daily[0].day.isoformat() == (end - timedelta(days=7)).date().isoformat()
    assert sum(row.total_vcore_hours for row in daily) == pytest.approx(32)


def test_deduplicate_uses_application_attempt_and_endpoint_scoped_livy_fallback():
    submitted = datetime(2026, 4, 1, tzinfo=timezone.utc)
    first = _rec(pool="A", kind="scheduled", submitted_at=submitted)
    first = first.model_copy(update={
        "app_id": "application-1",
        "app_info": {"attemptId": "attempt-1"},
        "source_endpoint": "session",
    })
    duplicate = first.model_copy(update={"source_endpoint": "batch"})
    retry = first.model_copy(update={
        "app_info": {"attemptId": "attempt-2"},
        "livy_id": 2,
    })
    same_numeric_id = first.model_copy(update={
        "app_id": None,
        "app_info": {},
        "source_endpoint": "batch",
        "livy_id": 7,
    })
    other_endpoint = same_numeric_id.model_copy(update={
        "source_endpoint": "session",
    })
    unique = deduplicate_runs([first, duplicate, retry, same_numeric_id, other_endpoint])
    assert len(unique) == 4
    assert {run.app_info.get("attemptId") for run in unique if run.app_id} == {
        "attempt-1", "attempt-2",
    }


def test_distinct_application_attempts_are_additive_and_missing_app_ids_keep_endpoint_scope():
    submitted = datetime(2026, 4, 1, tzinfo=timezone.utc)
    attempt_one = _rec(
        pool="A", kind="scheduled", submitted_at=submitted, duration_s=3600,
        vcores=32,
    ).model_copy(update={
        "app_id": "application-1",
        "app_info": {"attemptId": "attempt-1"},
        "source_endpoint": "session",
    })
    attempt_two = attempt_one.model_copy(update={
        "app_info": {"attemptId": "attempt-2"},
        "livy_id": 2,
    })
    no_app_batch = attempt_one.model_copy(update={
        "app_id": None,
        "app_info": {},
        "source_endpoint": "batch",
        "livy_id": 9,
    })
    no_app_session = no_app_batch.model_copy(update={"source_endpoint": "session"})
    records = deduplicate_runs([
        attempt_one, attempt_two, no_app_batch, no_app_session,
    ])
    assert len(records) == 4
    stats = aggregate_runs(
        records,
        windows=(7,),
        now=submitted + timedelta(days=1),
    )
    scheduled = next(item for item in stats if item.kind == "scheduled").windows[0]
    assert scheduled.run_count == 4
    assert scheduled.known_usage_run_count == 4
    assert scheduled.total_vcore_hours == pytest.approx(128)
    assert scheduled.est_cu_hours_fabric_spark == pytest.approx(64)


def test_history_paging_does_not_stop_before_old_overlapping_runs():
    start = datetime(2026, 4, 10, tzinfo=timezone.utc)
    old_finished = _make_sdk_run(
        job_id=1,
        submitted_at=start - timedelta(days=5),
        ended_at=start - timedelta(days=4),
    )
    long_running = _make_sdk_run(
        job_id=2,
        submitted_at=start - timedelta(days=8),
        ended_at=start + timedelta(hours=1),
    )
    client = SparkHistoryClient.__new__(SparkHistoryClient)
    client._collection_status = {}
    client._client = lambda pool_name: SimpleNamespace(close=lambda: None)
    pages = [
        SimpleNamespace(sessions=[old_finished], total=2),
        SimpleNamespace(sessions=[long_running], total=2),
        SimpleNamespace(sessions=[], total=2),
    ]
    iterator = client._iter(
        "poolA",
        livy_kind="session",
        list_fn=lambda _client, _offset, _size: pages.pop(0),
        sessions_field="sessions",
        start=start,
        limit=10,
        page_size=1,
    )
    runs = list(iterator)
    assert [run.livy_id for run in runs] == [2]
    assert client.collection_status("poolA", "session") is True


def test_history_paging_marks_limit_truncation_incomplete():
    start = datetime(2026, 4, 10, tzinfo=timezone.utc)
    record = _make_sdk_run(
        job_id=1,
        submitted_at=start,
        ended_at=start + timedelta(minutes=1),
    )
    second_record = _make_sdk_run(
        job_id=2,
        submitted_at=start + timedelta(minutes=2),
        ended_at=start + timedelta(minutes=3),
    )
    client = SparkHistoryClient.__new__(SparkHistoryClient)
    client._collection_status = {}
    client._client = lambda pool_name: SimpleNamespace(close=lambda: None)
    pages = [SimpleNamespace(sessions=[record, second_record], total=3)]
    runs = list(client._iter(
        "poolA",
        livy_kind="session",
        list_fn=lambda _client, _offset, _size: pages[0],
        sessions_field="sessions",
        start=start,
        limit=1,
        page_size=2,
    ))
    assert len(runs) == 1
    assert client.collection_status("poolA", "session") is False


def test_empty_history_is_complete_when_server_reports_zero():
    client = SparkHistoryClient.__new__(SparkHistoryClient)
    client._collection_status = {}
    client._client = lambda _pool: SimpleNamespace(close=lambda: None)
    runs = list(client._iter(
        "poolA",
        livy_kind="session",
        list_fn=lambda _client, _offset, _size: SimpleNamespace(sessions=[], total=0),
        sessions_field="sessions",
        start=datetime(2026, 4, 10, tzinfo=timezone.utc),
        limit=10,
        page_size=20,
    ))
    assert runs == []
    assert client.collection_status("poolA", "session") is True


def test_empty_page_with_nonzero_server_total_is_incomplete():
    client = SparkHistoryClient.__new__(SparkHistoryClient)
    client._collection_status = {}
    client._client = lambda _pool: SimpleNamespace(close=lambda: None)
    runs = list(client._iter(
        "poolA",
        livy_kind="session",
        list_fn=lambda _client, _offset, _size: SimpleNamespace(sessions=[], total=5),
        sessions_field="sessions",
        start=datetime(2026, 4, 10, tzinfo=timezone.utc),
        limit=10,
        page_size=20,
    ))
    assert runs == []
    assert client.collection_status("poolA", "session") is False


def test_paging_exception_is_not_reported_as_complete():
    client = SparkHistoryClient.__new__(SparkHistoryClient)
    client._collection_status = {}
    client._client = lambda _pool: SimpleNamespace(close=lambda: None)

    def fail(_client, _offset, _size):
        raise RuntimeError("paging failed")

    with pytest.raises(RuntimeError, match="paging failed"):
        list(client._iter(
            "poolA",
            livy_kind="batch",
            list_fn=fail,
            sessions_field="sessions",
            start=datetime(2026, 4, 10, tzinfo=timezone.utc),
            limit=10,
            page_size=20,
        ))
    assert client.collection_status("poolA", "batch") is False


def test_terminal_run_missing_end_across_window_is_unknown_not_excluded():
    now = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)
    submitted = now - timedelta(days=8)
    terminal_without_end = SparkRunRecord(
        livy_id=4,
        kind="scheduled",
        pool="A",
        app_id="application-missing-end",
        submitted_at=submitted,
        accounting_start_at=submitted,
        outcome="succeeded",
        total_vcores=32,
        usage_basis="fixed_shape_estimate",
    )
    stats = aggregate_runs(
        [terminal_without_end],
        windows=(7,),
        now=now,
    )[0].windows[0]
    assert stats.run_count == 1
    assert stats.known_usage_run_count == 0
    assert stats.unknown_usage_run_count == 1
    assert stats.total_vcore_hours == 0
    assert stats.avg_daily_vcore_hours is None


def test_aggregate_unknown_shape_is_reported_not_counted_as_zero_usage():
    now = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)
    unknown = SparkRunRecord(
        livy_id=3,
        kind="scheduled",
        pool="A",
        submitted_at=now - timedelta(days=1),
        ended_at=now - timedelta(days=1) + timedelta(hours=1),
    )
    stats = aggregate_runs([unknown], windows=(7,), now=now)[0].windows[0]
    assert stats.run_count == 1
    assert stats.known_usage_run_count == 0
    assert stats.unknown_usage_run_count == 1
    assert stats.avg_daily_vcore_hours is None


def test_collected_runs_aggregate_and_serialize_accounting_contract():
    observation_end = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)
    observation_start = observation_end - timedelta(days=3)
    allocated = observation_end - timedelta(hours=1)
    ended = observation_end + timedelta(hours=1)

    def sdk_record(
        *,
        livy_id: int,
        app_id: str,
        driver_cores: int,
        executor_cores: int,
        num_executors: int,
        attempt_id: str,
    ) -> SimpleNamespace:
        app_info = {
            "driverCores": str(driver_cores),
            "executorCores": str(executor_cores),
            "numExecutors": str(num_executors),
            "attemptId": attempt_id,
        }
        return SimpleNamespace(
            id=livy_id,
            app_id=app_id,
            app_info=app_info,
            name="job",
            state="success",
            result="Succeeded",
            scheduler=SimpleNamespace(
                submitted_at=allocated - timedelta(minutes=10),
                scheduled_at=None,
                ended_at=ended,
            ),
            plugin=SimpleNamespace(resource_acquisition_started_at=allocated),
            livy_info=SimpleNamespace(
                running_at=None,
                success_at=None,
                job_creation_request=SimpleNamespace(
                    driver_cores=driver_cores,
                    executor_cores=executor_cores,
                    executor_count=num_executors,
                    configuration={},
                ),
            ),
            tags={},
        )

    client = SparkHistoryClient.__new__(SparkHistoryClient)
    client._collection_status = {}
    client._client = lambda _pool: SimpleNamespace(close=lambda: None)
    sdk_records = [
        sdk_record(
            livy_id=7,
            app_id="application-7",
            driver_cores=8,
            executor_cores=8,
            num_executors=3,
            attempt_id="attempt-1",
        ),
        sdk_record(
            livy_id=8,
            app_id="application-8",
            driver_cores=4,
            executor_cores=4,
            num_executors=3,
            attempt_id="attempt-1",
        ),
    ]

    def collect(endpoint: str) -> list[SparkRunRecord]:
        response = SimpleNamespace(sessions=sdk_records, total=len(sdk_records))
        return list(client._iter(
            "poolA",
            livy_kind=endpoint,  # type: ignore[arg-type]
            list_fn=lambda _client, _offset, _size: response,
            sessions_field="sessions",
            start=observation_start,
            limit=10,
            page_size=20,
        ))

    collected = collect("batch") + collect("session")
    unique_runs = deduplicate_runs(collected)
    stats = aggregate_runs(
        unique_runs,
        windows=(3,),
        now=observation_end,
        collection_complete=True,
    )
    daily = aggregate_daily_usage(
        unique_runs,
        start=observation_start,
        end=observation_end,
    )
    analysis = SparkAnalysis(
        workspace_name="workspace",
        subscription_id="subscription",
        resource_group="group",
        generated_at=observation_end,
        spark_runs=unique_runs,
        run_stats=stats,
        accounting_basis=(
            "fixed_shape_estimate_from_livy_history; "
            "no executor allocation timeline is exposed by the current API"
        ),
        observation_start=observation_start,
        observation_end=observation_end,
        collection_complete=all(
            client.collection_status("poolA", endpoint) is True
            for endpoint in ("batch", "session")
        ),
        accounting_warnings=["Fixed-shape estimate; not measured allocation."],
        daily_usage=daily,
    )

    payload = analysis.to_dict()
    assert len(payload["spark_runs"]) == 2
    assert payload["spark_runs"][0]["source_endpoint"] == "batch"
    assert payload["spark_runs"][0]["app_info"]["attemptId"] == "attempt-1"
    assert payload["observation_start"] == "2026-04-07T00:00:00Z"
    assert payload["observation_end"] == "2026-04-10T00:00:00Z"
    assert payload["collection_complete"] is True
    assert payload["accounting_basis"].startswith("fixed_shape_estimate")
    assert payload["accounting_warnings"] == [
        "Fixed-shape estimate; not measured allocation."
    ]
    assert [row["day"] for row in payload["daily_usage"]] == [
        "2026-04-07", "2026-04-08", "2026-04-09",
    ]
    assert [row["total_vcore_hours"] for row in payload["daily_usage"]] == [
        0.0, 0.0, pytest.approx(48),
    ]
    assert [row["est_cu_hours_fabric_spark"] for row in payload["daily_usage"]] == [
        0.0, 0.0, pytest.approx(24),
    ]
    window = payload["run_stats"][0]["windows"][0]
    assert window["window_days"] == 3
    assert window["run_count"] == 2
    assert window["known_usage_run_count"] == 2
    assert window["unknown_usage_run_count"] == 0
    assert window["total_vcore_hours"] == pytest.approx(48)
    assert window["est_cu_hours_fabric_spark"] == pytest.approx(24)
    assert window["avg_daily_vcore_hours"] == pytest.approx(48 / 3)
    assert window["avg_daily_cu_hours"] == pytest.approx(24 / 3)
    assert window["steady_state_cu"] == pytest.approx(24 / (3 * 24))

    projection = project_capacity([], spark_payload=payload, headroom_pct=0)
    assert projection is not None
    assert projection.spark_window_days == 3
    assert projection.spark_daily_cu_hours == pytest.approx(24 / 3)
    assert projection.spark_steady_state_cu == pytest.approx(24 / 3 / 24)
    assert projection.spark_peak_day_cu_hours == pytest.approx(24)

    incomplete_payload = dict(payload)
    incomplete_payload["collection_complete"] = False
    assert project_capacity(
        [],
        spark_payload=incomplete_payload,
        headroom_pct=0,
    ) is None


def test_cu_hours_mapping_constant_is_one_half():
    """Sanity-check the rate matches Fabric docs: 1 CU = 2 Spark vCores."""
    assert VCORE_HOURS_TO_CU_HOURS == 0.5
