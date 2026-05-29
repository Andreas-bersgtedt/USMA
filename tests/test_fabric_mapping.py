import json
from pathlib import Path

from usma.modules.fabric_mapping.analyzer import FabricMappingAnalyzer
from usma.modules.fabric_mapping.reporting import write_reports
from usma.config import AppConfig, AzureConfig, SqlConfig


def _cfg(out: Path) -> AppConfig:
    return AppConfig(
        azure=AzureConfig(tenant_id="t", client_id="c", client_secret="s",
                          subscription_id="sub", resource_group="rg", workspace_name="ws"),
        sql=SqlConfig(),
        output_dir=out,
    )


def test_fabric_mapping_picks_up_dedicated_pool_findings(tmp_path: Path) -> None:
    payload = {
        "workspace_name": "ws",
        "pools": [
            {
                "inventory": {"name": "dw1", "status": "Online", "collation": "SQL_Latin1_General_CP1_CI_AS"},
                "tables": [
                    {"schema_name": "dbo", "table_name": "big_replicate",
                     "distribution_policy": "REPLICATE", "row_count": 200_000_000, "index_type": "CCI"},
                    {"schema_name": "dbo", "table_name": "huge_heap",
                     "distribution_policy": "ROUND_ROBIN", "row_count": 5_000_000, "index_type": "HEAP"},
                ],
                "workload_groups": [{"name": "wg1"}],
                "code_objects": [
                    {"schema_name": "dbo", "object_name": "upsert", "object_type": "SQL_STORED_PROCEDURE",
                     "definition": "MERGE INTO dbo.t USING s ON 1=1 WHEN MATCHED THEN UPDATE SET a=1;"},
                ],
            },
            {"inventory": {"name": "dw2", "status": "Paused"}, "tables": [], "workload_groups": []},
        ],
    }
    (tmp_path / "dedicated_pools.json").write_text(json.dumps(payload), encoding="utf-8")

    cfg = _cfg(tmp_path)
    report = FabricMappingAnalyzer(cfg).run()

    titles = {r.title for r in report.recommendations}
    assert any("paused" in t.lower() for t in titles)
    assert any("REPLICATE" in t for t in titles)
    assert any("heap" in t.lower() for t in titles)
    assert any("Workload groups" in t for t in titles)
    # New v2 detections
    assert any("collation" in t.lower() for t in titles)
    assert any("MERGE" in t for t in titles)

    paths = write_reports(report, tmp_path, formats=["json", "csv", "markdown"])
    names = {p.name for p in paths}
    assert {"fabric_mapping.json", "fabric_recommendations.csv", "fabric_mapping.md"} <= names


def test_fabric_mapping_picks_up_pipelines_v2_findings(tmp_path: Path) -> None:
    payload = {
        "pipelines": [{"name": "p1"}],
        "activities": [
            {"pipeline": "p1", "name": "df", "type": "ExecuteDataFlow", "support": "unsupported"},
            {"pipeline": "p1", "name": "cp", "type": "Copy", "support": "partial"},
        ],
        "linked_services": [
            {"name": "ls_hdi", "type": "HDInsight", "fabric_supported": False},
            {"name": "ls_sql", "type": "AzureSqlDatabase", "fabric_supported": True},
        ],
        "integration_runtimes": [{"name": "ir1", "type": "SelfHosted"}],
    }
    (tmp_path / "pipelines.json").write_text(json.dumps(payload), encoding="utf-8")

    report = FabricMappingAnalyzer(_cfg(tmp_path)).run()
    titles = {r.title for r in report.recommendations}
    assert any("ExecuteDataFlow" in t for t in titles)
    assert any("HDInsight" in t for t in titles)
    assert any("self-hosted" in t.lower() for t in titles)


def test_fabric_mapping_picks_up_monitoring_findings(tmp_path: Path) -> None:
    payload = {
        "series": [
            {"resource_name": "dw1", "metric_name": "DWUUsedPercent",
             "p95_value": 92.0, "avg_value": 70.0, "max_value": 100.0},
            {"resource_name": "dw1", "metric_name": "ConnectionsBlockedByFirewall",
             "p95_value": 0, "avg_value": 0, "max_value": 4},
        ]
    }
    (tmp_path / "monitoring.json").write_text(json.dumps(payload), encoding="utf-8")

    report = FabricMappingAnalyzer(_cfg(tmp_path)).run()
    titles = {r.title for r in report.recommendations}
    assert any("DWU" in t for t in titles)
    assert any("blocked by firewall" in t.lower() for t in titles)
