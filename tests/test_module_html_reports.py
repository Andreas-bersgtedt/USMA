"""Smoke tests for the HTML reports added across all modules + the index page."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from usma.modules.fabric_mapping.html_report import write_html as fabric_html
from usma.modules.fabric_mapping.models import (
    CapacityProjection,
    FabricMappingReport,
    ModuleSummary,
    ReadinessSummary,
    Recommendation,
    RunbookStep,
)
from usma.modules.monitoring.html_report import write_html as monitoring_html
from usma.modules.monitoring.models import (
    DwuDayStat,
    LogAnalyticsResult,
    MetricSeries,
    MonitoringAnalysis,
)
from usma.modules.pipelines.html_report import write_html as pipelines_html
from usma.modules.pipelines.models import (
    Activity,
    Dataset,
    IntegrationRuntime,
    LinkedService,
    Pipeline,
    PipelinesAnalysis,
    ScheduleMapping,
    Trigger,
)
from usma.modules.spark_pools.html_report import write_html as spark_html
from usma.modules.spark_pools.models import (
    Notebook,
    NotebookLintFinding,
    RuntimeMappingResult,
    SparkAnalysis,
    SparkJobDefinition,
    SparkPool,
    WorkspacePackage,
)
from usma.reporting.index_report import write_index


def test_spark_html(tmp_path: Path) -> None:
    r = SparkAnalysis(
        workspace_name="ws", subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="rg", generated_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
        pools=[SparkPool(name="p1", spark_version="3.4", node_size="Small",
                         node_count=3, auto_scale_enabled=True,
                         min_node_count=2, max_node_count=10)],
        notebooks=[Notebook(name="nb1", language="Python",
                            attached_spark_pool="p1", cell_count=12,
                            source_size_chars=4096)],
        spark_job_definitions=[SparkJobDefinition(name="job1", language="Scala",
                                                  target_spark_pool="p1",
                                                  main_definition_file="main.jar",
                                                  class_name="com.x.Main")],
        notebook_lint_findings=[NotebookLintFinding(notebook="nb1", rule_id="mssparkutils",
                                                    label="mssparkutils API",
                                                    severity="warning", line=10,
                                                    snippet="mssparkutils.fs.ls('/')")],
        runtime_mappings=[RuntimeMappingResult(pool_name="p1", synapse_version="3.4",
                                               fabric_runtime="1.3", fabric_spark="3.5",
                                               status="upgrade", note="auto-upgrade")],
        libraries=[WorkspacePackage(scope="workspace", name="numpy",
                                    version="2.0.0", package_type="whl")],
    )
    out = spark_html(r, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "spark_pools.html"
    assert "p1" in text and "nb1" in text and "job1" in text
    assert "mssparkutils" in text
    assert "Runtime mapping" in text and "upgrade" in text
    assert "numpy" in text


def test_pipelines_html(tmp_path: Path) -> None:
    r = PipelinesAnalysis(
        workspace_name="ws", subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="rg",
        artifacts_endpoint="https://ws.dev.azuresynapse.net",
        generated_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
        pipelines=[Pipeline(name="pl1", activity_count=2,
                            activity_types=["Copy", "WebHook"],
                            unsupported_activity_count=1)],
        activities=[
            Activity(pipeline="pl1", name="copy", type="Copy", support="supported"),
            Activity(pipeline="pl1", name="hook", type="WebHook", support="unsupported",
                     notes=["Not supported in Fabric"]),
        ],
        linked_services=[LinkedService(name="ls_sql", type="AzureSqlDatabase",
                                       fabric_supported=True),
                         LinkedService(name="ls_old", type="HDInsight",
                                       fabric_supported=False)],
        datasets=[Dataset(name="ds1", type="Parquet", linked_service="ls_sql")],
        triggers=[Trigger(name="tr1", type="ScheduleTrigger",
                          runtime_state="Started", pipelines=["pl1"])],
        integration_runtimes=[IntegrationRuntime(name="ir-shir", type="SelfHosted",
                                                 description="on-prem")],
        schedule_mappings=[ScheduleMapping(trigger_name="tr1",
                                           trigger_type="ScheduleTrigger",
                                           fabric_kind="recurring", every_n=1,
                                           interval="Day",
                                           summary="every 1 Day")],
    )
    out = pipelines_html(r, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "pipelines.html"
    assert "pl1" in text and "ls_sql" in text and "ds1" in text and "tr1" in text
    assert "Fabric-unsupported activities" in text
    assert "Fabric-unsupported linked services" in text
    assert "ir-shir" in text and "SelfHosted" in text
    assert "Schedule mapping" in text and "recurring" in text


def test_monitoring_html(tmp_path: Path) -> None:
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    r = MonitoringAnalysis(
        workspace_name="ws", subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="rg", generated_at=now,
        window_start=datetime(2026, 4, 20, tzinfo=timezone.utc),
        window_end=now, interval="PT1H",
        series=[MetricSeries(
            resource_id="/subscriptions/.../pools/dw1",
            resource_kind="dedicated_pool", resource_name="dw1",
            metric_name="DWUUsedPercent", aggregation="average",
            interval="PT1H", points=[(now, 75.5)],
            min_value=10.0, max_value=99.0, avg_value=75.5, p95_value=90.0,
        )],
        dwu_days=[DwuDayStat(pool_name="dw1", day="2026-04-26",
                             active_hours=8, active_dwu_hours=2400,
                             peak_dwu=300, peak_pct=60)],
        log_analytics=[LogAnalyticsResult(query_name="failed_queries",
                                           rows=[{"login": "etl", "errors": 3}])],
    )
    out = monitoring_html(r, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "monitoring.html"
    assert "dw1" in text and "DWUUsedPercent" in text
    assert "75.50" in text or "75.5" in text
    assert "DWU days" in text and "60%" in text
    assert "failed_queries" in text


def test_fabric_html(tmp_path: Path) -> None:
    r = FabricMappingReport(
        workspace_name="ws",
        generated_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
        inputs=[ModuleSummary(module="dedicated_pools",
                              source_file="dedicated_pools.json",
                              counts={"pools": 1, "tables": 42})],
        recommendations=[
            Recommendation(id="r1", area="dedicated_pools.tables",
                           title="Drop heap fact tables",
                           severity="blocker", effort="medium",
                           target="dbo.fact_sales",
                           detail="Heap on a >10M row table",
                           fabric_action="Convert to clustered columnstore"),
            Recommendation(id="r2", area="serverless_pools",
                           title="UTF-8 collation recommended",
                           severity="info", effort="low",
                           detail="Consider UTF-8 catalog"),
        ],
        readiness=ReadinessSummary(score=72, bucket="ready-with-effort",
                                    counts={"blocker": 1, "warning": 0, "info": 1},
                                    top_blockers=[]),
        runbook=[RunbookStep(phase="prep", order=1, title="Inventory",
                             detail="Snapshot all metadata.",
                             severity="info", effort="low")],
        capacity_projection=CapacityProjection(
            peak_dwu=2000, peak_dwu_with_headroom=2400, estimated_cu=24,
            recommended_sku="F32", headroom_pct=20, notes=["20% buffer"]
        ),
    )
    out = fabric_html(r, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "fabric_mapping.html"
    assert "Readiness" in text or "ready-with-effort" in text
    assert "F32" in text and "Capacity projection" in text
    assert "Drop heap fact tables" in text and "blocker" in text
    assert "Runbook" in text and "Inventory" in text


def test_index_lists_available_and_missing(tmp_path: Path) -> None:
    # Pretend two modules already produced HTML.
    (tmp_path / "dedicated_pools.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "dedicated_pools.json").write_text("{}", encoding="utf-8")
    (tmp_path / "spark_pools.html").write_text("<html></html>", encoding="utf-8")

    out = write_index(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.name == "index.html"

    # Shows ready pills for the two present modules
    assert text.count('class="pill ok">ready') == 2
    # Shows missing pills for the remaining absent modules
    # (5 original: serverless, pipelines, monitoring, storage, fabric_mapping
    #  + 4 mid-term: governance, security, cost, fabric_validation
    #  + 1 mid-term: run_delta)
    assert text.count('class="pill warn">missing') == 10
    # Available count is reflected in the meta block
    assert "2 of 12" in text
    # Sibling JSON link for dedicated_pools is rendered
    assert 'href="dedicated_pools.json"' in text
    # Ensures hint command is present for missing modules
    assert "sma analyze-pipelines" in text
