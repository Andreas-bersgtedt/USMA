from datetime import datetime, timezone
from pathlib import Path

from usma.modules.serverless_pools.html_report import write_html
from usma.modules.serverless_pools.models import (
    ExternalDataSource,
    ExternalTable,
    ExternalTableColumn,
    ServerlessAnalysis,
    ServerlessCostEstimate,
    ServerlessDailyUsage,
    ServerlessDatabase,
    ServerlessTopQuery,
    ServerlessUsageStat,
    StorageAccountUsage,
)


def _result() -> ServerlessAnalysis:
    return ServerlessAnalysis(
        workspace_name="ws-demo",
        subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="rg-demo",
        endpoint_fqdn="ws-demo-ondemand.sql.azuresynapse.net",
        generated_at=datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc),
        databases=[
            ServerlessDatabase(name="lake_db",
                               collation="Latin1_General_100_BIN2_UTF8",
                               create_date=datetime(2025, 1, 1, tzinfo=timezone.utc)),
            ServerlessDatabase(name="legacy_db",
                               collation="SQL_Latin1_General_CP1_CI_AS"),
        ],
        external_data_sources=[
            ExternalDataSource(database="lake_db", name="adls_root",
                               type="HADOOP",
                               location="abfss://data@account.dfs.core.windows.net/"),
        ],
        external_tables=[
            ExternalTable(database="lake_db", schema_name="dbo",
                          table_name="sales", data_source="adls_root",
                          file_format="parquet",
                          location="/curated/sales/"),
        ],
        external_table_columns=[
            ExternalTableColumn(database="lake_db", schema_name="dbo",
                                table_name="sales", column_name="order_id",
                                data_type="bigint", is_nullable=False, column_id=1),
            ExternalTableColumn(database="lake_db", schema_name="dbo",
                                table_name="sales", column_name="amount",
                                data_type="decimal", precision=18, scale=2,
                                is_nullable=True, column_id=2),
        ],
        usage=[
            ServerlessUsageStat(metric="active_requests", value=3, unit="count",
                                captured_at=datetime(2026, 4, 27, tzinfo=timezone.utc)),
        ],
        top_queries=[
            ServerlessTopQuery(request_id="r1", login_name="svc-etl",
                               start_time=datetime(2026, 4, 26, 8, tzinfo=timezone.utc),
                               status="Succeeded", duration_seconds=12,
                               data_processed_mb=2048,
                               command_text="SELECT TOP 100 * FROM dbo.sales"),
            ServerlessTopQuery(request_id="r2", login_name="ad-hoc",
                               status="Failed", error_code="42S02",
                               duration_seconds=1, data_processed_mb=0,
                               command_text="SELECT * FROM nope"),
        ],
        daily_usage=[
            ServerlessDailyUsage(day="2026-04-26", request_count=42,
                                 data_processed_mb=10240),
        ],
        cost_estimate=ServerlessCostEstimate(
            window_days=30, total_data_processed_tb=0.5,
            list_price_usd_per_tb=5.0, estimated_cost_usd=2.5,
            notes="List price approximation; check Azure pricing.",
        ),
        storage_account_usage=[
            StorageAccountUsage(storage_account="account",
                                query_count=42, data_processed_mb=10240,
                                estimated_cost_usd=0.05),
        ],
        errors=[],
    )


def test_serverless_html_drilldown(tmp_path: Path) -> None:
    out = write_html(_result(), tmp_path)
    assert out.name == "serverless_pools.html"
    text = out.read_text(encoding="utf-8")

    # Header / metadata
    assert "Unified Solution Migration Analyzer" in text
    assert "ws-demo-ondemand.sql.azuresynapse.net" in text

    # Database drill-down: external data sources, tables, and per-table columns drawer.
    assert "lake_db" in text
    assert "adls_root" in text
    assert "abfss://data@account.dfs.core.windows.net/" in text
    assert "dbo.sales" in text or "dbo</td>" in text or ">sales<" in text  # table name rendered
    assert "<code>order_id</code>" in text
    assert "<code>amount</code>" in text

    # Collation pill: UTF-8 vs non-UTF8
    assert "UTF-8" in text
    assert "non-UTF8" in text

    # Cost / storage / daily / top queries / usage sections
    assert "Cost estimate" in text
    assert "Storage account usage" in text
    assert "Top queries" in text
    assert "<pre>SELECT TOP 100 * FROM dbo.sales</pre>" in text
    assert "Daily data processed" in text

    # Failed query rendered with err pill
    assert "pill err" in text and "Failed" in text


def test_serverless_html_minimal(tmp_path: Path) -> None:
    """Render with no databases / no queries — should still produce a valid page."""
    minimal = ServerlessAnalysis(
        workspace_name="empty",
        subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="rg",
        endpoint_fqdn="empty-ondemand.sql.azuresynapse.net",
        generated_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
        errors=["databases failed: timeout"],
    )
    out = write_html(minimal, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "No user databases found" in text
    assert "Collection errors" in text
    assert "databases failed: timeout" in text
