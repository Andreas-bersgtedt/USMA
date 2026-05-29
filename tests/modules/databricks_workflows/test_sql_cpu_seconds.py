"""Tests for SQL warehouse CPU-seconds capture and Unity Catalog
system-table usage rollup (added 2026-05).

Covers:

1. ``iter_warehouse_queries`` requests ``include_metrics=True`` and
   captures ``query_metrics.task_total_time_ms`` into
   ``SqlWarehouseQuery.cpu_seconds``; ``_aggregate_warehouse_queries``
   rolls those values into ``SqlWarehouseStats.total_cpu_seconds`` /
   ``queries_with_cpu_metric``. Serverless queries (no metric) leave
   both fields ``None`` / ``0``.

2. ``collect_system_table_usage`` runs the right SQL through
   ``statement_execution`` and merges ``system.query.history`` +
   ``system.billing.usage`` into one row per (warehouse, day).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from usma.modules.databricks_workflows.analyzer import (
    DatabricksWorkflowsAnalyzer,
    _aggregate_warehouse_queries,
)
from usma.modules.databricks_workflows.collector import (
    DatabricksWorkflowsCollector,
)
from usma.modules.databricks_workflows.models import (
    SqlWarehouse,
    SqlWarehouseQuery,
)
from usma.modules.databricks_workflows.reporting import write_reports


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
# 1. CPU-seconds capture from query_history
# ---------------------------------------------------------------------------


def _ws_with_queries(queries: list[SimpleNamespace]) -> MagicMock:
    ws = MagicMock(name="WorkspaceClient")
    ws.warehouses.list.return_value = iter([])
    ws.query_history.list.return_value = SimpleNamespace(
        res=queries, next_page_token=None, has_next_page=False,
    )
    return ws


def test_iter_warehouse_queries_passes_include_metrics_and_captures_cpu():
    classic_q = SimpleNamespace(
        query_id="q-classic",
        warehouse_id="wh-classic",
        status=SimpleNamespace(value="FINISHED"),
        statement_type=SimpleNamespace(value="SELECT"),
        user_name="bob@contoso",
        query_source=SimpleNamespace(value="JOB"),
        query_text="SELECT 1",
        query_start_time_ms=1700000000000,
        query_end_time_ms=1700000000500,
        duration=500,
        metrics=SimpleNamespace(
            rows_produced_count=1,
            read_bytes=0,
            write_remote_bytes=0,
            task_total_time_ms=12_500,  # 12.5 CPU-seconds
        ),
    )
    serverless_q = SimpleNamespace(
        query_id="q-srvless",
        warehouse_id="wh-srvless",
        status=SimpleNamespace(value="FINISHED"),
        statement_type=SimpleNamespace(value="SELECT"),
        user_name="bob@contoso",
        query_source=SimpleNamespace(value="JOB"),
        query_text="SELECT 2",
        query_start_time_ms=1700000001000,
        query_end_time_ms=1700000001750,
        duration=750,
        # Serverless: Databricks omits task_total_time_ms.
        metrics=SimpleNamespace(
            rows_produced_count=1,
            read_bytes=0,
            write_remote_bytes=0,
        ),
    )
    ws = _ws_with_queries([classic_q, serverless_q])

    collector = DatabricksWorkflowsCollector(ws)
    out = list(collector.iter_warehouse_queries(lookback_days=10_000))

    # include_metrics=True must be passed.
    assert ws.query_history.list.call_args.kwargs.get("include_metrics") is True
    by_id = {q.query_id: q for q in out}
    assert by_id["q-classic"].cpu_seconds == pytest.approx(12.5)
    assert by_id["q-srvless"].cpu_seconds is None


def test_iter_warehouse_queries_retries_without_include_metrics_on_typeerror():
    """Older SDKs / test fakes that reject ``include_metrics`` must not
    explode \u2014 the collector retries without it.
    """
    q = SimpleNamespace(
        query_id="q-old",
        warehouse_id="wh-old",
        status=SimpleNamespace(value="FINISHED"),
        statement_type=SimpleNamespace(value="SELECT"),
        user_name="bob",
        query_source=SimpleNamespace(value="JOB"),
        query_text="SELECT 1",
        query_start_time_ms=1700000000000,
        query_end_time_ms=1700000000500,
        duration=500,
        metrics=None,
    )
    page = SimpleNamespace(res=[q], next_page_token=None, has_next_page=False)
    ws = MagicMock(name="WorkspaceClient")

    def list_side_effect(*args, **kwargs):
        if "include_metrics" in kwargs:
            raise TypeError("unexpected keyword argument 'include_metrics'")
        return page

    ws.query_history.list.side_effect = list_side_effect

    collector = DatabricksWorkflowsCollector(ws)
    out = list(collector.iter_warehouse_queries(lookback_days=10_000))
    assert [q.query_id for q in out] == ["q-old"]
    assert out[0].cpu_seconds is None
    assert ws.query_history.list.call_count == 2


def test_aggregate_warehouse_stats_rolls_up_cpu_seconds():
    wh = SqlWarehouse(warehouse_id="wh-1", name="wh-1")
    queries = [
        SqlWarehouseQuery(query_id="a", warehouse_id="wh-1", status="FINISHED",
                          duration_seconds=1.0, cpu_seconds=10.0),
        SqlWarehouseQuery(query_id="b", warehouse_id="wh-1", status="FINISHED",
                          duration_seconds=2.0, cpu_seconds=20.5),
        SqlWarehouseQuery(query_id="c", warehouse_id="wh-1", status="FINISHED",
                          duration_seconds=3.0, cpu_seconds=None),
    ]
    [stats] = _aggregate_warehouse_queries(
        queries, warehouses=[wh], lookback_days=30,
    )
    assert stats.query_count == 3
    assert stats.queries_with_cpu_metric == 2
    assert stats.total_cpu_seconds == pytest.approx(30.5)


def test_aggregate_warehouse_stats_leaves_cpu_none_when_no_metric():
    wh = SqlWarehouse(warehouse_id="wh-srv", name="wh-srv")
    queries = [
        SqlWarehouseQuery(query_id="a", warehouse_id="wh-srv", status="FINISHED",
                          duration_seconds=1.0, cpu_seconds=None),
    ]
    [stats] = _aggregate_warehouse_queries(
        queries, warehouses=[wh], lookback_days=30,
    )
    assert stats.queries_with_cpu_metric == 0
    assert stats.total_cpu_seconds is None


# ---------------------------------------------------------------------------
# 2. Unity Catalog system-table usage collection
# ---------------------------------------------------------------------------


def _statement_response(columns: list[str], rows: list[list]) -> SimpleNamespace:
    cols = [SimpleNamespace(name=c) for c in columns]
    return SimpleNamespace(
        status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
        result=SimpleNamespace(data_array=rows),
        manifest=SimpleNamespace(schema=SimpleNamespace(columns=cols)),
    )


def test_collect_system_table_usage_merges_query_history_and_billing():
    ws = MagicMock(name="WorkspaceClient")
    qh_resp = _statement_response(
        ["warehouse_id", "usage_date", "query_count", "total_task_seconds"],
        [
            ["wh-srv", "2026-05-26", 100, 240.5],
            ["wh-srv", "2026-05-27", 50, 120.0],
        ],
    )
    bill_resp = _statement_response(
        ["warehouse_id", "usage_date", "sku_name", "dbu_hours"],
        [
            ["wh-srv", "2026-05-26", "ENTERPRISE_SQL_SERVERLESS_COMPUTE_US_EAST_1", 0.42],
            ["wh-srv", "2026-05-28", "ENTERPRISE_SQL_SERVERLESS_COMPUTE_US_EAST_1", 0.11],
        ],
    )
    ws.statement_execution.execute_statement.side_effect = [qh_resp, bill_resp]

    collector = DatabricksWorkflowsCollector(ws)
    wh = SqlWarehouse(
        warehouse_id="wh-srv",
        name="Serverless Starter",
        enable_serverless_compute=True,
    )
    out = collector.collect_system_table_usage(
        execution_warehouse_id="wh-srv",
        warehouses=[wh],
        lookback_days=30,
    )
    # Two days from query.history, one extra-only day from billing.usage.
    assert {(u.usage_date, u.warehouse_id) for u in out} == {
        ("2026-05-26", "wh-srv"),
        ("2026-05-27", "wh-srv"),
        ("2026-05-28", "wh-srv"),
    }
    by_day = {u.usage_date: u for u in out}
    assert by_day["2026-05-26"].total_task_seconds == pytest.approx(240.5)
    assert by_day["2026-05-26"].query_count == 100
    assert by_day["2026-05-26"].dbu_hours == pytest.approx(0.42)
    assert by_day["2026-05-26"].sku_name and "SERVERLESS" in by_day["2026-05-26"].sku_name
    # Day with billing-only data still emitted with task seconds = None.
    assert by_day["2026-05-28"].total_task_seconds is None
    assert by_day["2026-05-28"].dbu_hours == pytest.approx(0.11)
    # Warehouse name backfilled from the SqlWarehouse list.
    assert by_day["2026-05-26"].warehouse_name == "Serverless Starter"

    # Two statements executed against the chosen warehouse.
    assert ws.statement_execution.execute_statement.call_count == 2
    first_call = ws.statement_execution.execute_statement.call_args_list[0].kwargs
    assert first_call["warehouse_id"] == "wh-srv"
    assert "system.query.history" in first_call["statement"]
    second_call = ws.statement_execution.execute_statement.call_args_list[1].kwargs
    assert "system.billing.usage" in second_call["statement"]


def test_collect_system_table_usage_no_api_returns_empty():
    ws = MagicMock(name="WorkspaceClient", spec=[])  # nothing on the client
    collector = DatabricksWorkflowsCollector(ws)
    out = collector.collect_system_table_usage(
        execution_warehouse_id="wh-1",
        warehouses=None,
        lookback_days=30,
    )
    assert out == []


def test_collect_system_table_usage_handles_failed_statement():
    ws = MagicMock(name="WorkspaceClient")
    failed = SimpleNamespace(
        status=SimpleNamespace(
            state=SimpleNamespace(value="FAILED"),
            error=SimpleNamespace(message="PERMISSION_DENIED"),
        ),
        result=None,
        manifest=None,
    )
    ws.statement_execution.execute_statement.return_value = failed
    collector = DatabricksWorkflowsCollector(ws)
    out = collector.collect_system_table_usage(
        execution_warehouse_id="wh-1",
        warehouses=[],
        lookback_days=30,
    )
    assert out == []


def test_analyzer_runs_system_tables_when_enabled(cfg_stub, tmp_path):
    """Smoke test: with ``collect_system_tables=True`` the analyzer
    invokes ``collect_system_table_usage`` and writes the new CSV.
    """
    ws = MagicMock(name="WorkspaceClient")
    ws.jobs.list.return_value = iter([])
    ws.clusters.list.return_value = iter([])
    ws.query_history.list.return_value = SimpleNamespace(
        res=[], next_page_token=None, has_next_page=False,
    )
    wh = SimpleNamespace(
        id="wh-srv",
        name="Serverless Starter",
        warehouse_type=SimpleNamespace(value="SERVERLESS"),
        cluster_size="Small",
        state=SimpleNamespace(value="RUNNING"),
        auto_stop_mins=10,
        enable_serverless_compute=True,
        enable_photon=True,
        channel=SimpleNamespace(name=SimpleNamespace(value="CHANNEL_NAME_CURRENT")),
        min_num_clusters=1,
        max_num_clusters=1,
        num_clusters=1,
        num_active_sessions=0,
        creator_name="alice@contoso",
        tags=None,
        spot_instance_policy=SimpleNamespace(value="COST_OPTIMIZED"),
        odbc_params=None,
    )
    ws.warehouses.list.return_value = iter([wh])

    qh_resp = _statement_response(
        ["warehouse_id", "usage_date", "query_count", "total_task_seconds"],
        [["wh-srv", "2026-05-27", 239, 999.826]],
    )
    bill_resp = _statement_response(
        ["warehouse_id", "usage_date", "sku_name", "dbu_hours"],
        [["wh-srv", "2026-05-27",
          "ENTERPRISE_SQL_SERVERLESS_COMPUTE_US_EAST_1", 0.07]],
    )
    ws.statement_execution.execute_statement.side_effect = [qh_resp, bill_resp]
    ws.current_user.me.return_value = SimpleNamespace(
        user_name="sp", groups=[], emails=[], roles=[],
    )

    analyzer = DatabricksWorkflowsAnalyzer(
        cfg_stub,
        collector=DatabricksWorkflowsCollector(ws),
        sql_query_lookback_days=30,
        collect_system_tables=True,
    )
    result = analyzer.run()
    assert len(result.sql_warehouse_daily_usage) == 1
    u = result.sql_warehouse_daily_usage[0]
    assert u.warehouse_id == "wh-srv"
    assert u.total_task_seconds == pytest.approx(999.826)
    assert u.dbu_hours == pytest.approx(0.07)

    written = write_reports(result, tmp_path, formats=["csv", "markdown"])
    names = {p.name for p in written}
    assert "databricks_sql_warehouse_usage.csv" in names
    md = (tmp_path / "databricks_workflows.md").read_text(encoding="utf-8")
    assert "SQL warehouse daily usage" in md
