"""Tests for ``modules.databricks_workflows`` (Phase 4 Slice 4-B)."""
from __future__ import annotations

import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from usma.modules.databricks_workflows.analyzer import (
    DatabricksWorkflowsAnalyzer,
)
from usma.modules.databricks_workflows.collector import (
    DatabricksWorkflowsCollector,
)
from usma.modules.databricks_workflows.fabric_compat import (
    classify_task,
)
from usma.modules.databricks_workflows.models import (
    DatabricksWorkflowsAnalysis,
)
from usma.modules.databricks_workflows.reporting import (
    write_reports,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fake_job(
    job_id: int,
    name: str,
    *,
    tasks: list[SimpleNamespace] | None = None,
    job_clusters: list[SimpleNamespace] | None = None,
    schedule: SimpleNamespace | None = None,
    tags: dict[str, str] | None = None,
    max_concurrent_runs: int | None = 1,
) -> SimpleNamespace:
    settings = SimpleNamespace(
        name=name,
        tasks=tasks or [],
        job_clusters=job_clusters or [],
        schedule=schedule,
        tags=tags or {},
        max_concurrent_runs=max_concurrent_runs,
        format=SimpleNamespace(value="MULTI_TASK"),
        run_as_user_name="svc-databricks@contoso",
        continuous=None,
    )
    return SimpleNamespace(
        job_id=job_id,
        creator_user_name="alice@contoso",
        settings=settings,
    )


def _notebook_task(key: str, *, cluster_key: str | None = "shared-cluster") -> SimpleNamespace:
    return SimpleNamespace(
        task_key=key,
        depends_on=[],
        job_cluster_key=cluster_key,
        notebook_task=SimpleNamespace(notebook_path=f"/Users/alice/{key}"),
    )


def _sql_task(key: str, *, warehouse_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        task_key=key,
        depends_on=[],
        existing_cluster_id="cluster-abc",
        sql_task=SimpleNamespace(
            query=SimpleNamespace(query_id="q-1"),
            warehouse_id=warehouse_id,
        ),
    )


def _custom_task(key: str, payload_field: str, payload: SimpleNamespace) -> SimpleNamespace:
    """Build a task whose only populated type field is ``payload_field``."""
    kwargs = {"task_key": key, "depends_on": [], payload_field: payload}
    return SimpleNamespace(**kwargs)


def _job_cluster(key: str, *, num_workers: int = 2) -> SimpleNamespace:
    spec = SimpleNamespace(
        spark_version="13.3.x-scala2.12",
        node_type_id="Standard_DS3_v2",
        driver_node_type_id="Standard_DS3_v2",
        num_workers=num_workers,
        autoscale=None,
        data_security_mode=SimpleNamespace(value="SINGLE_USER"),
        runtime_engine=SimpleNamespace(value="PHOTON"),
    )
    return SimpleNamespace(job_cluster_key=key, new_cluster=spec)


def _fake_cluster(cluster_id: str, name: str, *, state: str = "RUNNING") -> SimpleNamespace:
    return SimpleNamespace(
        cluster_id=cluster_id,
        cluster_name=name,
        state=SimpleNamespace(value=state),
        spark_version="13.3.x-scala2.12",
        node_type_id="Standard_DS3_v2",
        driver_node_type_id="Standard_DS3_v2",
        num_workers=4,
        autoscale=None,
        data_security_mode=SimpleNamespace(value="USER_ISOLATION"),
        runtime_engine=SimpleNamespace(value="STANDARD"),
        pinned_by_user_name=None,
        creator_user_name="alice@contoso",
    )


@pytest.fixture
def fake_workspace_client():
    ws = MagicMock(name="WorkspaceClient")
    schedule = SimpleNamespace(
        quartz_cron_expression="0 0 * * * ?",
        timezone_id="UTC",
        pause_status=SimpleNamespace(value="UNPAUSED"),
    )
    job_a = _fake_job(
        101,
        "etl-daily",
        tasks=[
            _notebook_task("bronze"),
            _sql_task("silver_query", warehouse_id="wh-prod"),
            _custom_task(
                "dlt-step",
                "pipeline_task",
                SimpleNamespace(pipeline_id="dlt-pipe-1"),
            ),
        ],
        job_clusters=[_job_cluster("shared-cluster", num_workers=4)],
        schedule=schedule,
        tags={"env": "prod"},
    )
    job_b = _fake_job(
        202,
        "adhoc-experiment",
        tasks=[
            _custom_task("unknown-step", "weird_task", SimpleNamespace()),
        ],
    )
    ws.jobs.list.return_value = iter([job_a, job_b])
    ws.clusters.list.return_value = iter(
        [_fake_cluster("c-1", "shared-allpurpose")]
    )
    # ----- SQL warehouses + query history -----
    wh_prod = SimpleNamespace(
        id="wh-prod",
        name="prod-warehouse",
        warehouse_type=SimpleNamespace(value="PRO"),
        cluster_size="Medium",
        state=SimpleNamespace(value="RUNNING"),
        auto_stop_mins=10,
        enable_serverless_compute=False,
        enable_photon=True,
        channel=SimpleNamespace(name=SimpleNamespace(value="CHANNEL_NAME_CURRENT")),
        min_num_clusters=1,
        max_num_clusters=4,
        num_clusters=2,
        num_active_sessions=3,
        creator_name="alice@contoso",
        tags=SimpleNamespace(custom_tags={"env": "prod"}),
        spot_instance_policy=SimpleNamespace(value="COST_OPTIMIZED"),
        odbc_params=SimpleNamespace(hostname="adb-1.cloud.databricks.com", path="/sql/1.0/warehouses/wh-prod"),
    )
    ws.warehouses.list.return_value = iter([wh_prod])
    # Two queries against wh-prod (one succeeded fast, one failed slow);
    # a third with no warehouse_id should still aggregate as orphan.
    q1 = SimpleNamespace(
        query_id="q-aaa",
        warehouse_id="wh-prod",
        status=SimpleNamespace(value="FINISHED"),
        statement_type=SimpleNamespace(value="SELECT"),
        user_name="bob@contoso",
        executed_as_user_name=None,
        query_source=SimpleNamespace(value="JOB"),
        query_text="SELECT * FROM gold.sales",
        query_start_time_ms=1700000000000,
        query_end_time_ms=1700000001500,
        duration=1500,
        metrics=SimpleNamespace(rows_produced_count=10, read_bytes=2048, write_remote_bytes=0),
    )
    q2 = SimpleNamespace(
        query_id="q-bbb",
        warehouse_id="wh-prod",
        status=SimpleNamespace(value="FAILED"),
        statement_type=SimpleNamespace(value="INSERT"),
        user_name="alice@contoso",
        query_source=SimpleNamespace(value="DASHBOARD"),
        query_text="INSERT INTO bronze.events SELECT ...",
        query_start_time_ms=1700000010000,
        query_end_time_ms=1700000020000,
        duration=10000,
        metrics=SimpleNamespace(rows_produced_count=0, read_bytes=0, write_remote_bytes=0),
    )
    ws.query_history.list.return_value = iter([q1, q2])
    return ws


@pytest.fixture
def cfg_stub():
    azure = SimpleNamespace(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        subscription_id="sub-x",
        resource_group="rg-1",
        workspace_name="dbx-prod",
    )
    return SimpleNamespace(azure=azure)


# ---------------------------------------------------------------------------
# classify_task
# ---------------------------------------------------------------------------


def test_classify_task_known_types():
    assert classify_task("notebook_task")[0] == "supported"
    assert classify_task("sql_task")[0] == "supported"
    assert classify_task("pipeline_task")[0] == "partial"
    assert classify_task("dbt_task")[0] == "partial"


def test_classify_task_unknown_types():
    assert classify_task("weird_task")[0] == "unknown"
    assert classify_task(None)[0] == "unknown"


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def test_collector_walks_jobs_and_tasks(fake_workspace_client):
    collector = DatabricksWorkflowsCollector(fake_workspace_client)
    workflows = list(collector.iter_workflows_with_tasks())
    assert len(workflows) == 2

    daily, adhoc = workflows[0], workflows[1]
    wf_daily, tasks_daily, clusters_daily = daily
    assert wf_daily.name == "etl-daily"
    assert wf_daily.task_count == 3
    assert wf_daily.job_cluster_count == 1
    assert wf_daily.schedule_cron == "0 0 * * * ?"
    assert wf_daily.schedule_pause_status == "UNPAUSED"
    assert wf_daily.tags == {"env": "prod"}
    assert wf_daily.format == "MULTI_TASK"
    assert {t.task_type for t in tasks_daily} == {"notebook_task", "sql_task", "pipeline_task"}
    assert clusters_daily[0].job_cluster_key == "shared-cluster"
    assert clusters_daily[0].runtime_engine == "PHOTON"

    wf_adhoc, tasks_adhoc, clusters_adhoc = adhoc
    assert wf_adhoc.task_count == 1
    assert tasks_adhoc[0].task_type == "unknown"
    assert tasks_adhoc[0].support == "unknown"
    assert clusters_adhoc == []


def test_collector_cluster_classification(fake_workspace_client):
    collector = DatabricksWorkflowsCollector(fake_workspace_client)
    _, tasks, _ = next(collector.iter_workflows_with_tasks())
    notebook, sql, _dlt = tasks
    assert notebook.cluster_kind == "job_cluster"
    assert notebook.cluster_ref == "shared-cluster"
    assert sql.cluster_kind == "existing_cluster"
    assert sql.cluster_ref == "cluster-abc"


def test_collector_lists_interactive_clusters(fake_workspace_client):
    collector = DatabricksWorkflowsCollector(fake_workspace_client)
    clusters = collector.list_interactive_clusters()
    assert len(clusters) == 1
    assert clusters[0].cluster_id == "c-1"
    assert clusters[0].state == "RUNNING"
    assert clusters[0].data_security_mode == "USER_ISOLATION"


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


def test_analyzer_run_aggregates(fake_workspace_client, cfg_stub):
    analyzer = DatabricksWorkflowsAnalyzer(
        cfg_stub,
        collector=DatabricksWorkflowsCollector(fake_workspace_client),
        workspace_url="adb-1.0.azuredatabricks.net",
        workspace_id="1234567890",
    )
    result = analyzer.run()
    assert isinstance(result, DatabricksWorkflowsAnalysis)
    assert result.workflow_count == 2
    assert result.task_count == 4
    assert result.partial_task_count == 1  # the DLT pipeline_task
    assert result.unsupported_task_count == 0
    assert result.workspace_url == "adb-1.0.azuredatabricks.net"
    assert result.workspace_id == "1234567890"
    assert len(result.interactive_clusters) == 1


def test_analyzer_collects_sql_warehouses(fake_workspace_client, cfg_stub):
    analyzer = DatabricksWorkflowsAnalyzer(
        cfg_stub,
        collector=DatabricksWorkflowsCollector(fake_workspace_client),
        # Short, recent lookback so the post-filter doesn't drop our
        # synthetic 2023 timestamps. The collector falls back to an
        # unfiltered listing when the SDK ``QueryFilter`` import fails
        # (it does here), and then post-filters by ``start_time``.
        sql_query_lookback_days=10_000,
    )
    result = analyzer.run()
    assert result.sql_warehouse_count == 1
    wh = result.sql_warehouses[0]
    assert wh.warehouse_id == "wh-prod"
    assert wh.warehouse_type == "PRO"
    assert wh.enable_photon is True
    assert wh.tags == {"env": "prod"}

    # The sql_task on job 101 should now carry warehouse_id.
    sql_tasks = [t for t in result.tasks if t.task_type == "sql_task"]
    assert sql_tasks and sql_tasks[0].sql_warehouse_id == "wh-prod"

    # Query-history aggregate.
    assert result.sql_warehouse_query_count == 2
    stats = {s.warehouse_id: s for s in result.sql_warehouse_stats}
    assert "wh-prod" in stats
    s = stats["wh-prod"]
    assert s.query_count == 2
    assert s.succeeded_count == 1
    assert s.failed_count == 1
    assert s.queries_from_jobs == 1
    assert s.unique_users == 2


def test_analyzer_requires_collector(cfg_stub):
    analyzer = DatabricksWorkflowsAnalyzer(cfg_stub)
    with pytest.raises(RuntimeError, match="requires a collector"):
        analyzer.run()


def test_iter_warehouse_queries_paginates_list_queries_response(fake_workspace_client):
    """databricks-sdk >= ~0.30 returns ``ListQueriesResponse`` (NOT an
    iterator) from ``query_history.list``. The collector must walk
    ``next_page_token`` / ``has_next_page`` instead of iterating the
    response directly.
    """
    ws = fake_workspace_client
    q1 = SimpleNamespace(
        query_id="q-p1",
        warehouse_id="wh-prod",
        status=SimpleNamespace(value="FINISHED"),
        statement_type=SimpleNamespace(value="SELECT"),
        user_name="bob@contoso",
        query_source=SimpleNamespace(value="JOB"),
        query_text="SELECT 1",
        query_start_time_ms=1700000000000,
        query_end_time_ms=1700000000500,
        duration=500,
        metrics=None,
    )
    q2 = SimpleNamespace(
        query_id="q-p2",
        warehouse_id="wh-prod",
        status=SimpleNamespace(value="FINISHED"),
        statement_type=SimpleNamespace(value="SELECT"),
        user_name="bob@contoso",
        query_source=SimpleNamespace(value="JOB"),
        query_text="SELECT 2",
        query_start_time_ms=1700000001000,
        query_end_time_ms=1700000001500,
        duration=500,
        metrics=None,
    )
    page1 = SimpleNamespace(res=[q1], next_page_token="tok-2", has_next_page=True)
    page2 = SimpleNamespace(res=[q2], next_page_token=None, has_next_page=False)
    ws.query_history.list.side_effect = [page1, page2]

    collector = DatabricksWorkflowsCollector(ws)
    queries = list(collector.iter_warehouse_queries(lookback_days=10_000))
    assert [q.query_id for q in queries] == ["q-p1", "q-p2"]
    # The collector should have made exactly two paginated calls.
    assert ws.query_history.list.call_count == 2
    second_call_kwargs = ws.query_history.list.call_args_list[1].kwargs
    assert second_call_kwargs.get("page_token") == "tok-2"


def test_analyzer_emits_sp_entitlements_caveat_when_only_warehouses(
    fake_workspace_client, cfg_stub,
):
    """When the SP sees SQL warehouses but zero Workflows / clusters
    *and* lacks workspace access, the analyzer should add a caveat
    pointing at workspace entitlements. Mirrors the real-world
    Databricks-on-AWS run from Phase 4.7.9.
    """
    ws = fake_workspace_client
    ws.jobs.list.return_value = iter([])
    ws.clusters.list.return_value = iter([])
    ws.query_history.list.return_value = iter([])  # no queries either
    # SP without workspace access: not in ``admins``, no
    # ``workspace-access`` entitlement.
    ws.current_user.me.return_value = SimpleNamespace(
        user_name="acct-admin-sp",
        groups=[SimpleNamespace(display="account-admins")],
        entitlements=[],
    )

    analyzer = DatabricksWorkflowsAnalyzer(
        cfg_stub,
        collector=DatabricksWorkflowsCollector(ws),
        sql_query_lookback_days=10_000,
    )
    result = analyzer.run()
    assert result.workflow_count == 0
    assert result.sql_warehouse_count == 1
    assert any(
        "entitlement" in c.lower() for c in result.cluster_sizing_caveats
    ), result.cluster_sizing_caveats


def test_analyzer_emits_empty_workspace_caveat_when_admin_sees_nothing(
    fake_workspace_client, cfg_stub,
):
    """When the SP IS workspace admin but jobs/clusters/queries are all
    zero, the caveat should say the workspace appears empty rather than
    blaming entitlements. Mirrors the fresh-trial Databricks-on-AWS run
    where ``sma-analyzer`` was already in the ``admins`` group.
    """
    ws = fake_workspace_client
    ws.jobs.list.return_value = iter([])
    ws.clusters.list.return_value = iter([])
    ws.query_history.list.return_value = iter([])
    ws.current_user.me.return_value = SimpleNamespace(
        user_name="sma-analyzer",
        groups=[
            SimpleNamespace(display="users"),
            SimpleNamespace(display="admins"),
        ],
        entitlements=[],
    )

    analyzer = DatabricksWorkflowsAnalyzer(
        cfg_stub,
        collector=DatabricksWorkflowsCollector(ws),
        sql_query_lookback_days=10_000,
    )
    result = analyzer.run()
    assert result.workflow_count == 0
    assert result.sql_warehouse_count == 1
    caveats = " ".join(result.cluster_sizing_caveats).lower()
    assert "workspace appears empty" in caveats, result.cluster_sizing_caveats
    assert "admins" in caveats, result.cluster_sizing_caveats
    # The entitlements message must NOT also be emitted.
    assert not any(
        "lacks workspace" in c.lower() for c in result.cluster_sizing_caveats
    ), result.cluster_sizing_caveats


def test_analyzer_for_descriptor_rejects_wrong_source(cfg_stub):
    from usma.sources import SourceDescriptor, SourceType

    desc = SourceDescriptor(
        type=SourceType.ADF, id="x", display_name="x",
        subscription_id="s", resource_group="rg",
    )
    with pytest.raises(ValueError, match="Databricks SourceDescriptor"):
        DatabricksWorkflowsAnalyzer.for_descriptor(cfg_stub, desc, creds=None)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_reporting_writes_json_csv_markdown(
    fake_workspace_client, cfg_stub, tmp_path: Path,
):
    analyzer = DatabricksWorkflowsAnalyzer(
        cfg_stub,
        collector=DatabricksWorkflowsCollector(fake_workspace_client),
        workspace_url="adb-1.0.azuredatabricks.net",
    )
    result = analyzer.run()
    out = write_reports(result, tmp_path, formats=["json", "csv", "markdown"])
    names = {p.name for p in out}
    assert "databricks_workflows.json" in names
    assert "databricks_workflows.md" in names
    assert "databricks_workflows.csv" in names
    assert "databricks_tasks.csv" in names
    assert "databricks_job_clusters.csv" in names
    assert "databricks_interactive_clusters.csv" in names

    md = (tmp_path / "databricks_workflows.md").read_text(encoding="utf-8")
    assert "etl-daily" in md
    assert "Task support" in md


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_databricks_workflows_in_module_registry():
    from usma.modules import MODULE_REGISTRY
    from usma.modules.spec import MODULE_SPECS
    from usma.sources import SourceType

    assert "databricks_workflows" in MODULE_REGISTRY
    spec = MODULE_SPECS["databricks_workflows"]
    assert spec.supports == frozenset({SourceType.DATABRICKS})
    assert callable(spec.factory)
