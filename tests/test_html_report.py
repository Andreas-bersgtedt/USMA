from datetime import datetime, timezone
from pathlib import Path

from usma.modules.dedicated_pools.models import (
    CodeObject,
    ColumnCollation,
    ColumnStat,
    DistributionCandidate,
    MaterializedView,
    PoolAnalysis,
    PoolInventory,
    SchemaInfo,
    StatisticInfo,
    TableInfo,
    TsqlSurfaceGap,
    WorkspaceAnalysis,
)
from usma.reporting.html_report import write_html


def _v2_workspace() -> WorkspaceAnalysis:
    inv = PoolInventory(
        name="dwsample", location="westeurope", sku_name="DW100c",
        sku_capacity=100, status="Online",
        collation="Latin1_General_100_BIN2_UTF8",
    )
    pool = PoolAnalysis(
        inventory=inv,
        schemas=[SchemaInfo(schema_name="dbo", object_count=2)],
        tables=[
            TableInfo(schema_name="dbo", table_name="fact_sales",
                      distribution_policy="ROUND_ROBIN", row_count=10_000_000,
                      reserved_space_mb=2048.0, index_type="CCI"),
        ],
        column_collations=[
            ColumnCollation(schema_name="dbo", table_name="fact_sales",
                            column_name="customer_id", data_type="int",
                            collation_name=None, db_collation="Latin1_General_100_BIN2_UTF8"),
            ColumnCollation(schema_name="dbo", table_name="fact_sales",
                            column_name="region", data_type="nvarchar",
                            max_length=200, collation_name="SQL_Latin1_General_CP1_CI_AS",
                            db_collation="Latin1_General_100_BIN2_UTF8",
                            differs_from_db=True),
        ],
        column_stats=[
            ColumnStat(schema_name="dbo", table_name="fact_sales",
                       column_name="customer_id", data_type="int", is_nullable=False,
                       row_count=10_000_000, distinct_count=5_000_000,
                       null_count=0, max_frequency=50_000),
        ],
        statistics=[
            StatisticInfo(schema_name="dbo", table_name="fact_sales",
                          stat_name="_WA_Sys_customer_id", auto_created=True,
                          rows=10_000_000, modification_counter=1_000_000,
                          days_since_update=42),
        ],
        materialized_views=[
            MaterializedView(schema_name="dbo", view_name="mv_top_customers",
                             definition="SELECT TOP 100 * FROM dbo.fact_sales"),
        ],
        distribution_candidates=[
            DistributionCandidate(schema_name="dbo", table_name="fact_sales",
                                  column_name="customer_id", score=88,
                                  reasons=["selectivity=0.50", "filter usage hits=12"]),
        ],
        code_objects=[
            CodeObject(schema_name="dbo", object_name="usp_etl",
                       object_type="SQL_STORED_PROCEDURE",
                       definition="MERGE INTO dbo.fact_sales USING ...;",
                       code_object_id="dbo.usp_etl.sql_stored_procedure"),
        ],
        tsql_surface_gaps=[
            TsqlSurfaceGap(code_object_id="dbo.usp_etl.sql_stored_procedure",
                           schema_name="dbo", object_name="usp_etl",
                           object_type="SQL_STORED_PROCEDURE",
                           rule_id="merge", label="MERGE statement",
                           severity="warning", matches=1,
                           fabric_action="Rewrite MERGE as INSERT + UPDATE."),
        ],
    )
    return WorkspaceAnalysis(
        workspace_name="ws-test", subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="rg-test", generated_at=datetime.now(timezone.utc), pools=[pool],
    )


def test_html_report_renders_drilldown_sections(tmp_path: Path) -> None:
    path = write_html(_v2_workspace(), tmp_path)
    html = path.read_text(encoding="utf-8")

    # The per-table drill-down container must be present.
    assert "<details" in html
    # Column-level data surfaces.
    assert "customer_id" in html
    assert "region" in html
    # Differing collation gets a "differs" pill.
    assert "differs" in html
    # Distribution candidate score + reasons surface.
    assert "filter usage hits=12" in html
    # Stale stat pill.
    assert "stale" in html
    # T-SQL gap rule rollup with stable code_object_id.
    assert "dbo.usp_etl.sql_stored_procedure" in html
    assert "MERGE statement" in html
    # MV definition rendered in <pre>.
    assert "<pre>" in html and "mv_top_customers" in html
    # Filter input present for tables.
    assert 'class="filter"' in html
    assert "smaFilter" in html


def test_html_report_handles_minimal_pool(tmp_path: Path) -> None:
    # Pool with no v2 data — must still render without errors and skip empty sections.
    inv = PoolInventory(name="empty", location="eastus", status="Online")
    ws = WorkspaceAnalysis(
        workspace_name="w", subscription_id="s", resource_group="rg",
        generated_at=datetime.now(timezone.utc),
        pools=[PoolAnalysis(inventory=inv)],
    )
    path = write_html(ws, tmp_path)
    html = path.read_text(encoding="utf-8")
    assert "Pool: empty" in html
    # v2 sections are wrapped in {% if pool.X %} guards — must not appear.
    assert "Materialized views" not in html
    assert "T-SQL surface gaps" not in html
