"""Tests for Databricks workflow vCore-hours collection (Slice 4-F).

Mirrors the pattern used by the Synapse Spark vCore-hour pipeline:
math is pure, the collector is duck-typed, and the analyzer rolls up
per-job / per-cluster aggregates over 7/14/28/90-day windows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from usma.modules.databricks_workflows.collector import (
    DatabricksWorkflowsCollector,
    _convert_run,
    reconstruct_runtime_seconds,
)
from usma.modules.databricks_workflows.models import (
    InteractiveCluster,
    JobCluster,
    Workflow,
)
from usma.modules.databricks_workflows.run_stats import (
    DEFAULT_WINDOWS,
    VCORE_HOURS_TO_CU_HOURS,
    aggregate_runs_by_job,
    compute_vcore_seconds,
    resolve_worker_count,
    vcores_for_node_type,
)


# ----------------------------------------------------------- pure math


def test_resolve_worker_count_static_wins():
    assert resolve_worker_count(num_workers=4, autoscale_min=1, autoscale_max=8) == 4


def test_resolve_worker_count_autoscale_min_strategy():
    assert resolve_worker_count(
        num_workers=None, autoscale_min=2, autoscale_max=10, strategy="min"
    ) == 2


def test_resolve_worker_count_autoscale_avg_strategy():
    assert resolve_worker_count(
        num_workers=None, autoscale_min=2, autoscale_max=10, strategy="avg"
    ) == 6


def test_resolve_worker_count_autoscale_max_strategy():
    assert resolve_worker_count(
        num_workers=None, autoscale_min=2, autoscale_max=10, strategy="max"
    ) == 10


def test_resolve_worker_count_returns_none_when_no_signal():
    assert resolve_worker_count(
        num_workers=None, autoscale_min=None, autoscale_max=None
    ) is None


def test_compute_vcore_seconds_happy_path():
    # driver=4, worker=4 × 3 workers = 16 vCores × 3600 sec = 57600 vcore-sec
    total, vcs = compute_vcore_seconds(
        driver_vcores=4, worker_vcores=4, worker_count=3, duration_seconds=3600.0,
    )
    assert total == 16
    assert vcs == 16.0 * 3600.0


def test_compute_vcore_seconds_zero_workers_is_valid():
    # Single-node / driver-only cluster.
    total, vcs = compute_vcore_seconds(
        driver_vcores=8, worker_vcores=8, worker_count=0, duration_seconds=60.0,
    )
    assert total == 8
    assert vcs == 8.0 * 60.0


def test_compute_vcore_seconds_missing_input_returns_none():
    total, vcs = compute_vcore_seconds(
        driver_vcores=None, worker_vcores=4, worker_count=2, duration_seconds=60.0,
    )
    assert (total, vcs) == (None, None)


def test_compute_vcore_seconds_non_positive_duration_returns_none():
    total, vcs = compute_vcore_seconds(
        driver_vcores=4, worker_vcores=4, worker_count=2, duration_seconds=0.0,
    )
    assert (total, vcs) == (None, None)


def test_vcores_for_node_type_lookup():
    table = {"Standard_DS3_v2": 4, "Standard_E8s_v3": 8}
    assert vcores_for_node_type("Standard_DS3_v2", table) == 4
    assert vcores_for_node_type("Standard_E8s_v3", table) == 8
    assert vcores_for_node_type("Unknown_SKU", table) is None
    assert vcores_for_node_type(None, table) is None


# ---------------------------------------------------- _convert_run


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _fake_run(
    *,
    run_id: int,
    start: datetime,
    duration_seconds: float,
    life_cycle: str = "TERMINATED",
    result: str = "SUCCESS",
    node_type_id: str | None = "Standard_DS3_v2",
    num_workers: int | None = 3,
    autoscale: SimpleNamespace | None = None,
    job_cluster_key: str | None = None,
    existing_cluster_id: str | None = None,
) -> SimpleNamespace:
    state = SimpleNamespace(
        life_cycle_state=SimpleNamespace(value=life_cycle),
        result_state=SimpleNamespace(value=result) if result else None,
    )
    if job_cluster_key:
        cluster_spec = SimpleNamespace(
            new_cluster=None, job_cluster_key=job_cluster_key, existing_cluster_id=None,
        )
    elif existing_cluster_id:
        cluster_spec = SimpleNamespace(
            new_cluster=None, job_cluster_key=None, existing_cluster_id=existing_cluster_id,
        )
    else:
        new_cluster = SimpleNamespace(
            node_type_id=node_type_id,
            driver_node_type_id=node_type_id,
            num_workers=num_workers,
            autoscale=autoscale,
        )
        cluster_spec = SimpleNamespace(
            new_cluster=new_cluster, job_cluster_key=None, existing_cluster_id=None,
        )
    end = start + timedelta(seconds=duration_seconds)
    return SimpleNamespace(
        run_id=run_id,
        run_name=f"run-{run_id}",
        run_type=SimpleNamespace(value="JOB_RUN"),
        trigger=SimpleNamespace(value="PERIODIC"),
        state=state,
        start_time=_ms(start),
        end_time=_ms(end),
        execution_duration=int(duration_seconds * 1000),
        cluster_instance=SimpleNamespace(cluster_id="ephemeral-1"),
        cluster_spec=cluster_spec,
        run_page_url="https://example/runs/1",
    )


def test_convert_run_static_inline_cluster_computes_vcore_hours():
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    raw = _fake_run(run_id=1, start=start, duration_seconds=3600.0, num_workers=3)
    run = _convert_run(
        raw,
        job_id=42,
        job_name="my-job",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={},
        interactive_cluster_index={},
    )
    # driver(4) + 3 workers × 4 vCores = 16; × 1 hour = 16 vcore-hours.
    assert run.outcome == "succeeded"
    assert run.duration_seconds == 3600.0
    assert run.num_workers == 3
    assert run.worker_count_source == "static"
    assert run.total_vcores == 16
    assert run.vcore_hours == pytest.approx(16.0)
    assert run.est_cu_hours_fabric_spark == pytest.approx(16.0 * VCORE_HOURS_TO_CU_HOURS)
    assert run.cluster_kind == "job_cluster"


def test_convert_run_autoscale_uses_min_floor():
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    autoscale = SimpleNamespace(min_workers=2, max_workers=10)
    raw = _fake_run(
        run_id=2, start=start, duration_seconds=1800.0,
        num_workers=None, autoscale=autoscale,
    )
    run = _convert_run(
        raw,
        job_id=42,
        job_name="my-job",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={},
        interactive_cluster_index={},
    )
    # min_workers=2 used; driver(4) + 2×4 = 12 vCores × 0.5h = 6 vcore-hours.
    assert run.num_workers == 2
    assert run.worker_count_source == "autoscale_min"
    assert run.vcore_hours == pytest.approx(6.0)


def test_convert_run_resolves_job_cluster_from_index():
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    raw = _fake_run(
        run_id=3, start=start, duration_seconds=3600.0,
        job_cluster_key="shared", num_workers=None,
    )
    jc = JobCluster(
        job_id=42, job_name="my-job", job_cluster_key="shared",
        node_type_id="Standard_DS3_v2", driver_node_type_id="Standard_DS3_v2",
        num_workers=5,
    )
    run = _convert_run(
        raw,
        job_id=42,
        job_name="my-job",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={(42, "shared"): jc},
        interactive_cluster_index={},
    )
    # driver(4) + 5×4 = 24 vCores × 1h.
    assert run.cluster_kind == "job_cluster"
    assert run.num_workers == 5
    assert run.total_vcores == 24
    assert run.vcore_hours == pytest.approx(24.0)


def test_convert_run_resolves_existing_cluster_from_index():
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    raw = _fake_run(
        run_id=4, start=start, duration_seconds=3600.0,
        existing_cluster_id="c-1", num_workers=None,
    )
    ic = InteractiveCluster(
        cluster_id="c-1", cluster_name="shared",
        node_type_id="Standard_E8s_v3", driver_node_type_id="Standard_E8s_v3",
        num_workers=2,
    )
    run = _convert_run(
        raw,
        job_id=42,
        job_name="my-job",
        node_type_vcpus={"Standard_E8s_v3": 8},
        job_cluster_index={},
        interactive_cluster_index={"c-1": ic},
    )
    # driver(8) + 2×8 = 24 vCores × 1h.
    assert run.cluster_kind == "existing_cluster"
    # cluster_id reflects the actual cluster the run executed on
    # (cluster_instance.cluster_id wins when present); the lookup into the
    # interactive cluster index happens via existing_cluster_id from
    # cluster_spec.
    assert run.cluster_id == "ephemeral-1"
    assert run.vcore_hours == pytest.approx(24.0)


def test_convert_run_unmapped_node_type_yields_no_vcore_hours():
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    raw = _fake_run(
        run_id=5, start=start, duration_seconds=3600.0,
        node_type_id="Unknown_SKU", num_workers=3,
    )
    run = _convert_run(
        raw, job_id=42, job_name="my-job",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={}, interactive_cluster_index={},
    )
    assert run.vcore_hours is None
    assert run.duration_seconds == 3600.0
    assert run.node_type_id == "Unknown_SKU"


def test_convert_run_failed_outcome():
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    raw = _fake_run(
        run_id=6, start=start, duration_seconds=60.0,
        life_cycle="TERMINATED", result="FAILED",
    )
    run = _convert_run(
        raw, job_id=42, job_name="x",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={}, interactive_cluster_index={},
    )
    assert run.outcome == "failed"


def test_convert_run_in_progress_outcome():
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    raw = _fake_run(
        run_id=7, start=start, duration_seconds=0.0,
        life_cycle="RUNNING", result=None,
    )
    raw.execution_duration = None
    raw.end_time = None
    run = _convert_run(
        raw, job_id=42, job_name="x",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={}, interactive_cluster_index={},
    )
    assert run.outcome == "in_progress"
    assert run.vcore_hours is None


# ----------------------------------------------------- aggregate_runs_by_job


def _quick_run(job_id, start, duration_seconds, outcome="succeeded", vcore_hours=4.0):
    from usma.modules.databricks_workflows.models import (
        WorkflowRun,
    )
    return WorkflowRun(
        job_id=job_id, job_name=f"job-{job_id}", run_id=hash((job_id, start.isoformat())) & 0xFFFFFF,
        outcome=outcome, start_time=start,
        duration_seconds=duration_seconds, vcore_hours=vcore_hours,
    )


def test_aggregate_runs_by_job_windowing():
    now = datetime(2025, 6, 1, 12, tzinfo=timezone.utc)
    runs = [
        _quick_run(101, now - timedelta(days=1), 60.0, vcore_hours=2.0),
        _quick_run(101, now - timedelta(days=10), 120.0, vcore_hours=3.0),
        _quick_run(101, now - timedelta(days=40), 300.0, vcore_hours=5.0),  # outside 28d
        _quick_run(101, now - timedelta(days=120), 600.0, vcore_hours=8.0),  # outside 90d
        _quick_run(202, now - timedelta(days=2), 30.0, outcome="failed", vcore_hours=1.0),
        _quick_run(202, now - timedelta(days=3), 30.0, outcome="succeeded", vcore_hours=1.0),
    ]
    stats = aggregate_runs_by_job(runs, now=now)
    assert {s.job_id for s in stats} == {101, 202}
    s_101 = next(s for s in stats if s.job_id == 101)
    assert s_101.total_runs_observed == 4
    w_by_days = {w.window_days: w for w in s_101.windows}
    assert set(w_by_days) == set(DEFAULT_WINDOWS)
    # 7-day: only the 1-day run.
    assert w_by_days[7].run_count == 1
    assert w_by_days[7].total_vcore_hours == pytest.approx(2.0)
    # 14-day: 1-day + 10-day.
    assert w_by_days[14].run_count == 2
    assert w_by_days[14].total_vcore_hours == pytest.approx(5.0)
    # 28-day: same as 14 (40-day excluded).
    assert w_by_days[28].run_count == 2
    # 90-day: includes 40-day.
    assert w_by_days[90].run_count == 3
    assert w_by_days[90].total_vcore_hours == pytest.approx(10.0)
    # Fabric CU-hours = vcore × 0.5.
    assert w_by_days[90].est_cu_hours_fabric_spark == pytest.approx(5.0)

    s_202 = next(s for s in stats if s.job_id == 202)
    w7 = next(w for w in s_202.windows if w.window_days == 7)
    assert w7.completed_count == 2
    assert w7.succeeded_count == 1
    assert w7.failed_count == 1
    assert w7.success_rate == pytest.approx(0.5)


# ------------------------------------------------ reconstruct_runtime_seconds


def _event(ts: datetime, ev_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=int(ts.timestamp() * 1000),
        type=SimpleNamespace(value=ev_type),
    )


def test_reconstruct_runtime_pairs_running_and_terminated():
    base = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    events = [
        _event(base, "RUNNING"),
        _event(base + timedelta(hours=2), "TERMINATING"),
        _event(base + timedelta(hours=10), "RUNNING"),
        _event(base + timedelta(hours=11), "TERMINATED"),
    ]
    total = reconstruct_runtime_seconds(
        events,
        window_start=base - timedelta(days=1),
        window_end=base + timedelta(days=1),
    )
    assert total == pytest.approx((2 + 1) * 3600.0)


def test_reconstruct_runtime_clips_to_window():
    base = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    events = [
        _event(base, "RUNNING"),
        _event(base + timedelta(hours=5), "TERMINATED"),
    ]
    # Window only covers hours 2..4 of the running interval (2h overlap).
    total = reconstruct_runtime_seconds(
        events,
        window_start=base + timedelta(hours=2),
        window_end=base + timedelta(hours=4),
    )
    assert total == pytest.approx(2 * 3600.0)


def test_reconstruct_runtime_open_interval_closes_at_window_end():
    base = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    events = [_event(base, "RUNNING")]  # never terminated within window
    total = reconstruct_runtime_seconds(
        events,
        window_start=base - timedelta(hours=1),
        window_end=base + timedelta(hours=3),
    )
    assert total == pytest.approx(3 * 3600.0)


def test_reconstruct_runtime_empty_events():
    total = reconstruct_runtime_seconds(
        [],
        window_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    assert total == 0.0


# ---------------------------------------------------- collector node-type cache


def test_node_type_vcpus_caches_and_handles_errors():
    class FakeWS:
        def __init__(self) -> None:
            self.calls = 0

        @property
        def clusters(self) -> Any:
            outer = self

            class C:
                def list_node_types(self) -> Any:
                    outer.calls += 1
                    return SimpleNamespace(
                        node_types=[
                            SimpleNamespace(node_type_id="A", num_cores=4.0),
                            SimpleNamespace(node_type_id="B", num_cores=8.0),
                        ]
                    )

            return C()

    ws = FakeWS()
    collector = DatabricksWorkflowsCollector(ws)
    assert collector.node_type_vcpus() == {"A": 4, "B": 8}
    # Second call hits the cache.
    assert collector.node_type_vcpus() == {"A": 4, "B": 8}
    assert ws.calls == 1


def test_node_type_vcpus_returns_empty_on_error():
    class FakeWS:
        @property
        def clusters(self) -> Any:
            class C:
                def list_node_types(self) -> Any:
                    raise RuntimeError("boom")

            return C()

    collector = DatabricksWorkflowsCollector(FakeWS())
    assert collector.node_type_vcpus() == {}


# ---------------------------------------------------- analyzer integration


def test_analyzer_populates_run_history_and_caveats():
    """End-to-end: analyzer collects runs, rolls up stats, records caveats."""
    from unittest.mock import MagicMock

    from usma.modules.databricks_workflows.analyzer import (
        DatabricksWorkflowsAnalyzer,
    )

    now = datetime.now(timezone.utc)
    job_settings = SimpleNamespace(
        name="job-A", tasks=[], job_clusters=[
            SimpleNamespace(
                job_cluster_key="jc1",
                new_cluster=SimpleNamespace(
                    spark_version="14.x", node_type_id="Standard_DS3_v2",
                    driver_node_type_id="Standard_DS3_v2", num_workers=2,
                    autoscale=None, data_security_mode=None, runtime_engine=None,
                ),
            ),
        ],
        schedule=None, tags={}, max_concurrent_runs=1,
        format=SimpleNamespace(value="MULTI_TASK"), continuous=None,
    )
    job = SimpleNamespace(job_id=999, creator_user_name="x", settings=job_settings)
    run_recent = _fake_run(
        run_id=1, start=now - timedelta(hours=1), duration_seconds=600.0,
        job_cluster_key="jc1", num_workers=None,
    )
    run_unmapped = _fake_run(
        run_id=2, start=now - timedelta(days=2), duration_seconds=600.0,
        node_type_id="Mystery_SKU", num_workers=2,
    )
    ws = MagicMock(name="ws")
    ws.jobs.list.return_value = iter([job])
    ws.clusters.list.return_value = iter([])
    ws.clusters.list_node_types.return_value = SimpleNamespace(
        node_types=[SimpleNamespace(node_type_id="Standard_DS3_v2", num_cores=4.0)]
    )
    ws.jobs.list_runs.return_value = iter([run_recent, run_unmapped])

    cfg = SimpleNamespace(
        azure=SimpleNamespace(
            tenant_id="t", client_id="c", client_secret="s",
            subscription_id="s", resource_group="rg", workspace_name="ws",
        )
    )
    analyzer = DatabricksWorkflowsAnalyzer(
        cfg,
        collector=DatabricksWorkflowsCollector(ws),
        collect_interactive_usage=False,  # no clusters anyway
    )
    result = analyzer.run()

    assert len(result.workflow_runs) == 2
    assert len(result.workflow_run_stats) == 1
    s = result.workflow_run_stats[0]
    assert s.job_id == 999
    w90 = next(w for w in s.windows if w.window_days == 90)
    # Only run_recent (job_cluster jc1 with 2 workers, DS3 = 4 vCores) contributes:
    # driver(4) + 2×4 = 12 vCores × (600/3600)h = 2 vcore-hours.
    assert w90.total_vcore_hours == pytest.approx(2.0)
    # Mystery_SKU run produced a caveat.
    assert any("Mystery_SKU" in c for c in result.cluster_sizing_caveats)


# ---------------------------------------------------- Slice 4-F-2: shallow-list + post-mortem


def test_iter_workflows_falls_back_to_jobs_get_when_tasks_empty():
    """jobs.list() returns shallow BaseJob with empty tasks/job_clusters;
    collector must retry via jobs.get() to fetch the full settings."""
    shallow = SimpleNamespace(
        job_id=77,
        settings=SimpleNamespace(
            name="dummyNotebook_job", tasks=None, job_clusters=None,
        ),
        created_time=None,
        creator_user_name=None,
        run_as_user_name=None,
    )
    full_task = SimpleNamespace(
        task_key="t1", job_cluster_key="jc1",
        notebook_task=SimpleNamespace(notebook_path="/x"),
        existing_cluster_id=None, depends_on=None, max_retries=None,
    )
    full_jc = SimpleNamespace(
        job_cluster_key="jc1",
        new_cluster=SimpleNamespace(
            node_type_id="Standard_DS3_v2",
            driver_node_type_id="Standard_DS3_v2",
            num_workers=2,
            autoscale=None,
            spark_version="14.x",
            data_security_mode=None,
            runtime_engine=None,
        ),
    )
    full = SimpleNamespace(
        job_id=77,
        settings=SimpleNamespace(
            name="dummyNotebook_job", tasks=[full_task], job_clusters=[full_jc],
            schedule=None, continuous=None, max_concurrent_runs=None,
            timeout_seconds=None, tags=None,
        ),
        created_time=None,
        creator_user_name=None,
        run_as_user_name=None,
    )

    get_calls: list[int] = []

    class FakeJobs:
        def list(self, *args: Any, **kwargs: Any) -> Any:
            return iter([shallow])

        def get(self, job_id: int) -> Any:
            get_calls.append(job_id)
            return full

    class FakeWS:
        jobs = FakeJobs()

    collector = DatabricksWorkflowsCollector(FakeWS())
    results = list(collector.iter_workflows_with_tasks())
    assert len(results) == 1
    wf, tasks, job_clusters = results[0]
    assert wf.job_id == 77
    assert len(tasks) == 1
    assert tasks[0].cluster_ref == "jc1"
    assert len(job_clusters) == 1
    assert job_clusters[0].node_type_id == "Standard_DS3_v2"
    assert get_calls == [77], "jobs.get must be called exactly once for the empty job"


def test_iter_workflows_passes_expand_tasks_true():
    captured: dict[str, Any] = {}

    class FakeJobs:
        def list(self, *args: Any, **kwargs: Any) -> Any:
            captured["kwargs"] = kwargs
            return iter([])

    class FakeWS:
        jobs = FakeJobs()

    list(DatabricksWorkflowsCollector(FakeWS()).iter_workflows_with_tasks())
    assert captured["kwargs"].get("expand_tasks") is True


def test_list_runs_passes_expand_tasks_true():
    captured: dict[str, Any] = {}

    class FakeJobs:
        def list_runs(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return iter([])

    class FakeWS:
        jobs = FakeJobs()

    collector = DatabricksWorkflowsCollector(FakeWS())
    list(
        collector.iter_workflow_runs(
            lookback_days=7,
            job_index={42: Workflow(job_id=42, name="x")},
            job_cluster_index={},
            interactive_cluster_index={},
        )
    )
    assert captured.get("expand_tasks") is True


def test_convert_run_uses_cluster_post_mortem_when_shape_unresolved():
    """Run has cluster_instance but cluster_spec is None and no index hit \u2014
    the post-mortem lookup must populate node_type / workers."""
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    raw = SimpleNamespace(
        run_id=99,
        run_name="r",
        run_type=SimpleNamespace(value="JOB_RUN"),
        trigger=SimpleNamespace(value="PERIODIC"),
        state=SimpleNamespace(
            life_cycle_state=SimpleNamespace(value="TERMINATED"),
            result_state=SimpleNamespace(value="SUCCESS"),
        ),
        start_time=_ms(start),
        end_time=_ms(start + timedelta(seconds=3600)),
        execution_duration=3_600_000,
        cluster_instance=SimpleNamespace(cluster_id="ephemeral-x"),
        cluster_spec=None,
        run_page_url="u",
    )

    post_mortem_calls: list[str] = []

    def lookup(cid: str) -> InteractiveCluster:
        post_mortem_calls.append(cid)
        return InteractiveCluster(
            cluster_id=cid, cluster_name="pm",
            node_type_id="Standard_DS3_v2", driver_node_type_id="Standard_DS3_v2",
            num_workers=2,
        )

    run = _convert_run(
        raw,
        job_id=42,
        job_name="my-job",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={},
        interactive_cluster_index={},
        cluster_post_mortem=lookup,
    )
    assert post_mortem_calls == ["ephemeral-x"]
    # driver(4) + 2×4 = 12 vCores × 1h = 12 vcore-hours
    assert run.node_type_id == "Standard_DS3_v2"
    assert run.num_workers == 2
    assert run.vcore_hours == pytest.approx(12.0)


def test_cluster_post_mortem_caches_negative_lookups():
    """A clusters.get failure must be memoised so we don't retry."""
    calls: list[str] = []

    class FakeClusters:
        def get(self, cluster_id: str) -> Any:
            calls.append(cluster_id)
            raise RuntimeError("cluster gone")

    class FakeWS:
        clusters = FakeClusters()

    collector = DatabricksWorkflowsCollector(FakeWS())
    assert collector._cluster_post_mortem_lookup("dead-1") is None
    assert collector._cluster_post_mortem_lookup("dead-1") is None
    assert calls == ["dead-1"]


def test_cluster_post_mortem_disabled_skips_clusters_get():
    calls: list[str] = []

    class FakeClusters:
        def get(self, cluster_id: str) -> Any:
            calls.append(cluster_id)
            return None

    class FakeWS:
        clusters = FakeClusters()

    collector = DatabricksWorkflowsCollector(FakeWS(), collect_cluster_post_mortem=False)
    assert collector._cluster_post_mortem_lookup("any-id") is None
    assert calls == []


def test_convert_run_reads_cluster_spec_from_tasks_when_run_level_missing():
    """expand_tasks=True puts cluster_spec on per-task entries; _convert_run
    must look there when raw.cluster_spec is None."""
    start = datetime(2025, 1, 10, 12, tzinfo=timezone.utc)
    task_cluster = SimpleNamespace(
        new_cluster=SimpleNamespace(
            node_type_id="Standard_DS3_v2",
            driver_node_type_id="Standard_DS3_v2",
            num_workers=1,
            autoscale=None,
        ),
        job_cluster_key=None,
        existing_cluster_id=None,
    )
    raw = SimpleNamespace(
        run_id=101,
        run_name="r",
        run_type=SimpleNamespace(value="JOB_RUN"),
        trigger=SimpleNamespace(value="PERIODIC"),
        state=SimpleNamespace(
            life_cycle_state=SimpleNamespace(value="TERMINATED"),
            result_state=SimpleNamespace(value="SUCCESS"),
        ),
        start_time=_ms(start),
        end_time=_ms(start + timedelta(seconds=3600)),
        execution_duration=3_600_000,
        cluster_instance=SimpleNamespace(cluster_id="ephemeral-y"),
        cluster_spec=None,
        tasks=[SimpleNamespace(task_key="t1", cluster_spec=task_cluster)],
        run_page_url="u",
    )
    run = _convert_run(
        raw,
        job_id=42,
        job_name="my-job",
        node_type_vcpus={"Standard_DS3_v2": 4},
        job_cluster_index={},
        interactive_cluster_index={},
    )
    # driver(4) + 1×4 = 8 vCores × 1h = 8 vcore-hours
    assert run.node_type_id == "Standard_DS3_v2"
    assert run.vcore_hours == pytest.approx(8.0)


