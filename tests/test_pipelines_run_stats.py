"""Tests for the pipelines run_stats aggregator (pure functions, no Azure)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from usma.modules.pipelines.run_stats import (
    DATA_MOVEMENT_ACTIVITY_TYPES,
    DEFAULT_DATAFLOW_CORES,
    DIU_TO_CU_HOURS,
    ORCHESTRATION_CU_HOURS_PER_ACTIVITY,
    VCORE_HOURS_TO_CU_HOURS,
    aggregate_runs,
    dataflow_cores_by_pipeline_activity,
    extract_data_bytes,
    extract_diu_hours,
    extract_vcore_hours,
    extract_vcore_hours_from_runtime,
    non_copy_activity_counts,
    pipelines_with_data_movement,
)

NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _run(pipeline: str, age_days: float, status: str = "Succeeded",
         duration_ms: float | None = 1000.0, run_id: str | None = None) -> dict:
    return {
        "run_id": run_id or f"{pipeline}-{age_days}",
        "pipeline_name": pipeline,
        "status": status,
        "run_end": NOW - timedelta(days=age_days),
        "run_start": NOW - timedelta(days=age_days, minutes=1),
        "duration_in_ms": duration_ms,
    }


def _copy_ar(read: int | None = None, written: int | None = None,
             diu_hours: float | None = None) -> dict:
    out: dict = {}
    if read is not None:
        out["dataRead"] = read
    if written is not None:
        out["dataWritten"] = written
    if diu_hours is not None:
        out["billingReference"] = {
            "activityType": "ExternalActivity",
            "billableDuration": [
                {"meterType": "AzureIR", "duration": diu_hours, "unit": "DIUHours"},
            ],
        }
    return {"activity_type": "Copy", "status": "Succeeded", "output": out}


def test_data_movement_types_complete() -> None:
    assert {"Copy", "ExecuteDataFlow", "Lookup"} <= DATA_MOVEMENT_ACTIVITY_TYPES


def test_pipelines_with_data_movement_detects_activities() -> None:
    activities = [
        {"pipeline": "p1", "type": "Copy"},
        {"pipeline": "p2", "type": "Wait"},
        {"pipeline": "p3", "type": "ExecuteDataFlow"},
    ]
    assert pipelines_with_data_movement(activities) == {"p1", "p3"}


def test_non_copy_activity_counts_excludes_copy_only() -> None:
    activities = [
        {"pipeline": "p1", "type": "Copy"},
        {"pipeline": "p1", "type": "Wait"},
        {"pipeline": "p1", "type": "Lookup"},      # Lookup is non-copy for billing
        {"pipeline": "p2", "type": "ExecuteDataFlow"},
        {"pipeline": "p3", "type": "Copy"},
        {"pipeline": "p3", "type": "Copy"},
        {"pipeline": "p4"},                          # missing type → counted as non-copy
        {"type": "IfCondition"},                     # missing pipeline → ignored
    ]
    counts = non_copy_activity_counts(activities)
    assert counts == {"p1": 2, "p2": 1, "p4": 1}


def test_extract_data_bytes_copy_uses_written_when_present() -> None:
    assert extract_data_bytes(_copy_ar(read=10, written=20)) == 20
    assert extract_data_bytes(_copy_ar(read=15)) == 15
    assert extract_data_bytes(_copy_ar()) is None


def test_extract_data_bytes_dataflow_metrics() -> None:
    ar = {
        "activity_type": "ExecuteDataFlow",
        "output": {"runStatus": {"metrics": {
            "sink1": {"bytes": 100},
            "sink2": {"bytes": 250},
        }}},
    }
    assert extract_data_bytes(ar) == 350


def test_extract_data_bytes_unknown_type_returns_none() -> None:
    assert extract_data_bytes({"activity_type": "Wait", "output": {}}) is None


def test_extract_diu_hours_sums_diuhours_entries() -> None:
    ar = {
        "activity_type": "Copy",
        "output": {
            "billingReference": {
                "billableDuration": [
                    {"meterType": "AzureIR", "duration": 0.5, "unit": "DIUHours"},
                    {"meterType": "AzureIR", "duration": 0.25, "unit": "DIUHours"},
                    {"meterType": "OtherIR", "duration": 99, "unit": "PipelineRuns"},
                ],
            },
        },
    }
    assert extract_diu_hours(ar) == 0.75


def test_extract_diu_hours_unit_is_case_insensitive() -> None:
    ar = {
        "activity_type": "Copy",
        "output": {
            "billingReference": {
                "billableDuration": [
                    {"duration": 0.1, "unit": "diuhours"},
                ],
            },
        },
    }
    assert extract_diu_hours(ar) == 0.1


def test_extract_diu_hours_accepts_dict_billable_duration() -> None:
    # Some payloads serialize a single billable-duration entry as a dict.
    ar = {
        "activity_type": "Copy",
        "output": {
            "billingReference": {
                "billableDuration": {"duration": 0.2, "unit": "DIUHours"},
            },
        },
    }
    assert extract_diu_hours(ar) == 0.2


def test_extract_diu_hours_returns_none_when_absent() -> None:
    assert extract_diu_hours({"activity_type": "Copy", "output": {}}) is None
    assert extract_diu_hours({"activity_type": "Copy", "output": {
        "billingReference": {"billableDuration": []}
    }}) is None
    assert extract_diu_hours({"activity_type": "Copy", "output": {
        "billingReference": {"billableDuration": [{"duration": 1.0, "unit": "PipelineRuns"}]}
    }}) is None


def test_aggregate_runs_empty_pipeline_emits_zero_windows() -> None:
    out = aggregate_runs(
        pipeline_names=["empty"],
        pipelines_with_data_movement=set(),
        runs=[],
        now=NOW,
    )
    assert len(out) == 1
    stats = out[0]
    assert stats.pipeline == "empty"
    assert stats.last_run_at is None
    assert [w.window_days for w in stats.windows] == [7, 14, 28, 90]
    for w in stats.windows:
        assert w.run_count == 0
        assert w.success_rate is None
        assert w.avg_duration_ms is None
        assert w.total_data_moved_mb is None  # no data movement


def test_aggregate_runs_age_buckets() -> None:
    runs = [
        _run("p", age_days=1),    # in all 4 windows
        _run("p", age_days=10),   # in 14/28/90
        _run("p", age_days=20),   # in 28/90
        _run("p", age_days=60),   # in 90 only
    ]
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=runs,
        now=NOW,
    )
    counts = {w.window_days: w.run_count for w in out[0].windows}
    assert counts == {7: 1, 14: 2, 28: 3, 90: 4}


def test_aggregate_runs_success_rate_and_failures() -> None:
    runs = [
        _run("p", 1, status="Succeeded"),
        _run("p", 2, status="Succeeded"),
        _run("p", 3, status="Failed"),
        _run("p", 4, status="InProgress"),
    ]
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=runs,
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert w7.run_count == 4
    assert w7.succeeded == 2
    assert w7.failed == 1
    assert w7.other == 1
    assert w7.success_rate is not None
    assert abs(w7.success_rate - (2 / 3)) < 1e-9


def test_aggregate_runs_data_movement_avg_and_total() -> None:
    runs = [
        _run("dm", 1, run_id="r1"),
        _run("dm", 2, run_id="r2"),
    ]
    activity_runs = {
        "r1": [_copy_ar(written=1024 * 1024, diu_hours=0.4)],          # 1 MB,  0.4 DIU-hr
        "r2": [_copy_ar(written=2 * 1024 * 1024, diu_hours=0.6)],      # 2 MB,  0.6 DIU-hr
    }
    out = aggregate_runs(
        pipeline_names=["dm"],
        pipelines_with_data_movement={"dm"},
        runs=runs,
        activity_runs_by_run_id=activity_runs,
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert out[0].has_data_movement is True
    assert w7.total_data_moved_mb == 3.0
    assert w7.avg_data_moved_mb_per_run == 1.5
    assert w7.total_diu_hours == pytest.approx(1.0)
    assert w7.avg_diu_hours_per_run == pytest.approx(0.5)
    assert w7.est_cu_hours_from_diu == pytest.approx(1.0 * DIU_TO_CU_HOURS)


def test_aggregate_runs_orchestration_cu_from_non_copy_counts() -> None:
    # 3 runs in the 7-day window of pipeline "p". Static definition has 4
    # non-copy activities, so we estimate 12 non-copy activity runs.
    runs = [_run("p", 1), _run("p", 2), _run("p", 3)]
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=runs,
        non_copy_activity_counts={"p": 4},
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert w7.est_non_copy_activity_runs == 12
    assert w7.est_cu_hours_from_orchestration == pytest.approx(
        12 * ORCHESTRATION_CU_HOURS_PER_ACTIVITY
    )


def test_aggregate_runs_orchestration_zero_when_no_runs_or_no_activities() -> None:
    # No runs in window → 0
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=[],
        non_copy_activity_counts={"p": 5},
        now=NOW,
    )
    for w in out[0].windows:
        assert w.est_non_copy_activity_runs == 0
        assert w.est_cu_hours_from_orchestration == 0.0

    # Pipeline not in non_copy_activity_counts → treated as 0
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=[_run("p", 1)],
        non_copy_activity_counts={},
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert w7.est_non_copy_activity_runs == 0
    assert w7.est_cu_hours_from_orchestration == 0.0


def test_aggregate_runs_orchestration_uses_observed_when_sampled() -> None:
    # 3 runs; we sampled activity-runs for r1 and r2 (5 + 7 non-copy
    # activities observed, including ForEach fan-out), but not r3 (so the
    # static fallback of 4 applies to that run only). Total = 5 + 7 + 4 = 16.
    runs = [_run("p", 1, run_id="r1"), _run("p", 2, run_id="r2"), _run("p", 3, run_id="r3")]
    activity_runs = {
        "r1": [
            {"activity_type": "Wait"},
            {"activity_type": "Lookup"},
            {"activity_type": "ForEach"},
            {"activity_type": "ExecutePipeline"},
            {"activity_type": "SetVariable"},
        ],
        "r2": [
            {"activity_type": "IfCondition"},
            {"activity_type": "Wait"},
            {"activity_type": "Wait"},
            {"activity_type": "Copy"},          # excluded \u2014 billed under DM
            {"activity_type": "ForEach"},
            {"activity_type": "Lookup"},
            {"activity_type": "SetVariable"},
            {"activity_type": "Wait"},
        ],
    }
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=runs,
        activity_runs_by_run_id=activity_runs,
        non_copy_activity_counts={"p": 4},
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    # r1 contributes 5, r2 contributes 7 (Copy excluded), r3 unsampled \u2192 4 static.
    assert w7.est_non_copy_activity_runs == 5 + 7 + 4
    assert w7.est_cu_hours_from_orchestration == pytest.approx(
        16 * ORCHESTRATION_CU_HOURS_PER_ACTIVITY
    )


def test_aggregate_runs_no_data_movement_pipeline_emits_none() -> None:
    runs = [_run("nodm", 1)]
    out = aggregate_runs(
        pipeline_names=["nodm"],
        pipelines_with_data_movement=set(),
        runs=runs,
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert out[0].has_data_movement is False
    assert w7.avg_data_moved_mb_per_run is None
    assert w7.total_data_moved_mb is None


def test_aggregate_runs_p95_duration() -> None:
    runs = [_run("p", 1, duration_ms=float(i * 100)) for i in range(1, 101)]
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=runs,
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    # nearest-rank p95 of 1..100 (scaled by 100) is the 95th-largest (i=95) → 9500
    assert w7.p95_duration_ms == 9500.0
    assert w7.avg_duration_ms is not None and abs(w7.avg_duration_ms - 5050.0) < 1e-6


def test_aggregate_runs_last_run_tracking() -> None:
    runs = [
        _run("p", 5, status="Failed", run_id="old"),
        _run("p", 1, status="Succeeded", run_id="new"),
    ]
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=runs,
        now=NOW,
    )
    assert out[0].last_run_status == "Succeeded"
    assert out[0].last_run_at == NOW - timedelta(days=1)


def test_aggregate_runs_iso_string_dates_are_parsed() -> None:
    iso_run = {
        "run_id": "x",
        "pipeline_name": "p",
        "status": "Succeeded",
        "run_end": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "duration_in_ms": 500,
    }
    out = aggregate_runs(
        pipeline_names=["p"],
        pipelines_with_data_movement=set(),
        runs=[iso_run],
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert w7.run_count == 1


# ---------------------------------------------------------------------------
# Mapping Data Flow vCore-second telemetry → Fabric CU projection
# ---------------------------------------------------------------------------


def _df_ar(*entries: dict) -> dict:
    """Build an ExecuteDataFlow activity-run dict with given billing entries."""
    return {
        "activity_type": "ExecuteDataFlow",
        "status": "Succeeded",
        "output": {
            "billingReference": {
                "activityType": "ExecuteDataFlow",
                "billableDuration": list(entries),
            },
        },
    }


def test_extract_vcore_hours_sums_corehour_entries() -> None:
    ar = _df_ar(
        {"meterType": "General", "duration": 0.5, "unit": "coreHour"},
        {"meterType": "MemoryOptimized", "duration": 0.25, "unit": "vCoreHour"},
        {"meterType": "AzureIR", "duration": 99, "unit": "DIUHours"},  # ignored
    )
    assert extract_vcore_hours(ar) == 0.75


def test_extract_vcore_hours_is_case_insensitive_and_accepts_dict() -> None:
    ar = _df_ar({"duration": 0.1, "unit": "COREHOURS"})
    assert extract_vcore_hours(ar) == 0.1
    ar_dict = {
        "activity_type": "ExecuteDataFlow",
        "output": {
            "billingReference": {
                "billableDuration": {"duration": 0.2, "unit": "vCoreHours"},
            },
        },
    }
    assert extract_vcore_hours(ar_dict) == 0.2


def test_extract_vcore_hours_returns_none_when_absent() -> None:
    assert extract_vcore_hours({"activity_type": "ExecuteDataFlow", "output": {}}) is None
    # Only DIU billing → no vCore entry.
    ar = _df_ar({"duration": 0.5, "unit": "DIUHours"})
    assert extract_vcore_hours(ar) is None


def test_extract_vcore_hours_unit_aliases_with_punctuation() -> None:
    """Real Synapse payloads have units like ``vCore-Hour`` / ``Core Hours``."""
    for unit in ("vCore-Hour", "Core Hours", "v core hour", "vCoreHours"):
        ar = _df_ar({"duration": 0.5, "unit": unit})
        assert extract_vcore_hours(ar) == 0.5, f"failed for unit={unit!r}"


def test_extract_vcore_hours_tolerates_json_string_payloads() -> None:
    """SDK sometimes leaves ``output`` / ``billingReference`` as JSON strings."""
    import json as _json
    billing = {
        "billableDuration": [
            {"meterType": "General", "duration": 0.5, "unit": "coreHour"},
        ],
    }
    # billingReference encoded as a string
    ar1 = {"activity_type": "ExecuteDataFlow", "output": {"billingReference": _json.dumps(billing)}}
    assert extract_vcore_hours(ar1) == 0.5
    # whole output encoded as a string
    ar2 = {"activity_type": "ExecuteDataFlow", "output": _json.dumps({"billingReference": billing})}
    assert extract_vcore_hours(ar2) == 0.5


def test_vcore_to_cu_constant_matches_user_requirement() -> None:
    # 1 vCore-second = 0.5 CU-second  ⇒  same ratio for hours.
    assert VCORE_HOURS_TO_CU_HOURS == 0.5


def test_aggregate_runs_dataflow_vcore_projects_to_fabric_cu() -> None:
    """ExecuteDataFlow runtime stats (cores × wall-clock) → Fabric CU projection.

    Anchors the user-facing example: 4 vCores × 36 s = 144 vCore-s = 0.04
    vCore-hr  ⇒  Fabric Spark CU = 0.04 × 0.5 = 0.02 CU-hr.
    """
    runs = [
        _run("df", 1, run_id="r1"),
        _run("df", 2, run_id="r2"),
    ]
    activity_runs = {
        # r1: a single ExecuteDataFlow, 36 s wall-clock.
        "r1": [_df_ar_runtime(seconds=36, activity_name="df1")],
        # r2: two ExecuteDataFlow activities in the same pipeline run.
        "r2": [
            _df_ar_runtime(seconds=18, activity_name="df1"),
            _df_ar_runtime(seconds=18, activity_name="df2"),
        ],
    }
    cores_map = {"df": {"df1": 4, "df2": 4}}
    out = aggregate_runs(
        pipeline_names=["df"],
        pipelines_with_data_movement={"df"},
        runs=runs,
        activity_runs_by_run_id=activity_runs,
        dataflow_cores_by_pipeline_activity=cores_map,
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    # r1: 4 × 36 / 3600 = 0.04 vCore-hr; r2: 4×(18+18)/3600 = 0.04 vCore-hr
    assert w7.total_vcore_hours == pytest.approx(0.08)
    assert w7.avg_vcore_hours_per_run == pytest.approx(0.04)
    # Fabric Spark CU = 0.08 × 0.5 = 0.04 CU-hr (matches user's 0.02/run example)
    assert w7.est_cu_hours_from_vcore == pytest.approx(0.04)


def test_aggregate_runs_dataflow_uses_default_cores_when_unmapped() -> None:
    """When activity not in cores map, fall back to DEFAULT_DATAFLOW_CORES (8)."""
    runs = [_run("df", 1, run_id="r1")]
    activity_runs = {
        "r1": [_df_ar_runtime(seconds=3600, activity_name="dfX")],
    }
    out = aggregate_runs(
        pipeline_names=["df"],
        pipelines_with_data_movement={"df"},
        runs=runs,
        activity_runs_by_run_id=activity_runs,
        dataflow_cores_by_pipeline_activity={},  # unmapped → default
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    # 8 cores × 1 hour = 8 vCore-hr
    assert w7.total_vcore_hours == pytest.approx(float(DEFAULT_DATAFLOW_CORES))


def test_aggregate_runs_dataflow_no_runtime_is_unknown_not_zero() -> None:
    """When a data-flow pipeline runs but no runtime is reported, surface None."""
    runs = [_run("df", 1, run_id="r1")]
    activity_runs = {
        # No executionDuration / duration_in_ms — runtime unknown.
        "r1": [{"activity_type": "ExecuteDataFlow", "output": {}}],
    }
    out = aggregate_runs(
        pipeline_names=["df"],
        pipelines_with_data_movement={"df"},
        runs=runs,
        activity_runs_by_run_id=activity_runs,
        dataflow_cores_by_pipeline_activity={"df": {"a": 8}},
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert w7.total_vcore_hours is None
    assert w7.est_cu_hours_from_vcore is None


def test_aggregate_runs_diu_and_vcore_tracked_independently() -> None:
    """A pipeline mixing Copy (DIU) and ExecuteDataFlow (vCore-runtime) reports both."""
    runs = [_run("mix", 1, run_id="r1")]
    activity_runs = {
        "r1": [
            _copy_ar(written=1024, diu_hours=0.2),
            _df_ar_runtime(seconds=3600, activity_name="df1"),  # 1 hr × 4 cores
        ],
    }
    out = aggregate_runs(
        pipeline_names=["mix"],
        pipelines_with_data_movement={"mix"},
        runs=runs,
        activity_runs_by_run_id=activity_runs,
        dataflow_cores_by_pipeline_activity={"mix": {"df1": 4}},
        now=NOW,
    )
    w7 = next(w for w in out[0].windows if w.window_days == 7)
    assert w7.total_diu_hours == pytest.approx(0.2)
    assert w7.est_cu_hours_from_diu == pytest.approx(0.2 * DIU_TO_CU_HOURS)
    # 4 cores × 1 hour = 4 vCore-hr ; 4 × 0.5 = 2 CU-hr
    assert w7.total_vcore_hours == pytest.approx(4.0)
    assert w7.est_cu_hours_from_vcore == pytest.approx(4.0 * VCORE_HOURS_TO_CU_HOURS)


# ---------------------------------------------------------------------------
# extract_vcore_hours_from_runtime + dataflow_cores_by_pipeline_activity
# ---------------------------------------------------------------------------


def _df_ar_runtime(*, seconds: float, activity_name: str = "df1") -> dict:
    """Build an ExecuteDataFlow activity-run dict with a known wall-clock runtime."""
    return {
        "activity_name": activity_name,
        "activity_type": "ExecuteDataFlow",
        "status": "Succeeded",
        "duration_in_ms": int(seconds * 1000),
        "output": {"executionDuration": seconds},
    }


def test_extract_vcore_hours_from_runtime_uses_execution_duration() -> None:
    ar = _df_ar_runtime(seconds=36)
    # 4 cores × 36 s = 144 vCore-s = 0.04 vCore-hr
    assert extract_vcore_hours_from_runtime(ar, cores=4) == pytest.approx(0.04)


def test_extract_vcore_hours_from_runtime_falls_back_to_duration_ms() -> None:
    ar = {
        "activity_type": "ExecuteDataFlow",
        "duration_in_ms": 36_000,
        "output": {},  # no executionDuration
    }
    assert extract_vcore_hours_from_runtime(ar, cores=4) == pytest.approx(0.04)


def test_extract_vcore_hours_from_runtime_returns_none_when_unknown() -> None:
    assert extract_vcore_hours_from_runtime(
        {"activity_type": "ExecuteDataFlow", "output": {}}, cores=4,
    ) is None
    # Wrong activity type
    assert extract_vcore_hours_from_runtime(
        {"activity_type": "Copy", "duration_in_ms": 1000, "output": {}}, cores=4,
    ) is None
    # Invalid cores
    assert extract_vcore_hours_from_runtime(_df_ar_runtime(seconds=10), cores=0) is None


def test_dataflow_cores_by_pipeline_activity_indexes_explicit_only() -> None:
    activities = [
        {"pipeline": "p1", "name": "df1", "type": "ExecuteDataFlow", "dataflow_cores": 16},
        {"pipeline": "p1", "name": "df2", "type": "ExecuteDataFlow", "dataflow_cores": None},
        {"pipeline": "p2", "name": "dfx", "type": "ExecuteDataFlow", "dataflow_cores": 8},
        {"pipeline": "p1", "name": "cp",  "type": "Copy"},  # ignored
    ]
    out = dataflow_cores_by_pipeline_activity(activities)
    assert out == {"p1": {"df1": 16}, "p2": {"dfx": 8}}
