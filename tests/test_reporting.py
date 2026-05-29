from datetime import datetime, timezone
from pathlib import Path

from usma.modules.dedicated_pools.models import (
    PoolAnalysis,
    PoolInventory,
    SchemaInfo,
    TableInfo,
    UsageStat,
    WorkspaceAnalysis,
)
from usma.reporting import write_reports


def _sample_workspace() -> WorkspaceAnalysis:
    inv = PoolInventory(name="dwsample", location="westeurope", sku_name="DW100c", sku_capacity=100, status="Online")
    pool = PoolAnalysis(
        inventory=inv,
        schemas=[SchemaInfo(schema_name="dbo", object_count=3)],
        tables=[TableInfo(schema_name="dbo", table_name="fact_sales", distribution_policy="HASH",
                          distribution_column="customer_id", row_count=1000, reserved_space_mb=12.5,
                          index_type="CCI")],
        usage=[UsageStat(metric="active_requests", value=2, unit="count")],
    )
    return WorkspaceAnalysis(
        workspace_name="ws-test", subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="rg-test", generated_at=datetime.now(timezone.utc), pools=[pool],
    )


def test_write_reports_creates_all_formats(tmp_path: Path) -> None:
    result = _sample_workspace()
    paths = write_reports(result, tmp_path, formats=["json", "csv", "markdown", "html"])

    names = {p.name for p in paths}
    assert "dedicated_pools.json" in names
    assert "dedicated_pools.md" in names
    assert "dedicated_pools.html" in names
    assert "pools_inventory.csv" in names
    assert "tables.csv" in names
    for p in paths:
        assert p.exists() and p.stat().st_size > 0
