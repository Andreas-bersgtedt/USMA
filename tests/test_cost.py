"""Unit tests for the cost module's aggregation + Fabric comparison helpers."""
from __future__ import annotations

import pytest

from usma.modules.cost.cost_client import (
    _classify_resource_id,
    _coerce_month,
    aggregate_rows,
    average_monthly_cost,
    filter_rows_for_scope,
    latest_monthly_cost,
)
from usma.modules.cost.fabric_compare import (
    compare_to_fabric,
    estimate_fabric_monthly_cost,
)
from usma.modules.cost.models import MonthlyCostRow


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force USMA_OFFLINE so the Azure Retail Prices API is never hit in tests."""
    monkeypatch.setenv("USMA_OFFLINE", "1")


def _row(month: str, kind: str, cost: float) -> MonthlyCostRow:
    return MonthlyCostRow(
        month=month, resource_kind=kind, resource_name=None,
        sku=None, cost=cost, currency="USD",
        usage_quantity=0.0, usage_unit=None,
    )


def test_aggregate_rows_sums_per_month_and_kind() -> None:
    rows = [
        _row("2026-01", "dedicated_pool", 1000.0),
        _row("2026-01", "storage", 250.0),
        _row("2026-02", "dedicated_pool", 1100.0),
    ]
    monthly, by_kind = aggregate_rows(rows)
    assert monthly == {"2026-01": 1250.0, "2026-02": 1100.0}
    assert by_kind == {"dedicated_pool": 2100.0, "storage": 250.0}


def test_average_monthly_cost_handles_empty() -> None:
    assert average_monthly_cost({}) == 0.0
    assert average_monthly_cost({"2026-01": 100.0, "2026-02": 200.0}) == 150.0


def test_latest_monthly_cost_picks_max_yyyy_mm_key() -> None:
    assert latest_monthly_cost({}) == 0.0
    # Latest month wins regardless of dict insertion order.
    assert latest_monthly_cost(
        {"2026-03": 300.0, "2026-01": 100.0, "2026-02": 200.0}
    ) == 300.0
    assert latest_monthly_cost({"2026-05": 1246.0}) == 1246.0


def test_filter_rows_for_scope_narrows_to_adf_factory() -> None:
    sub = "/subscriptions/sub-1/resourceGroups/rg-1"
    syn_ws_id = f"{sub}/providers/Microsoft.Synapse/workspaces/ws1"
    adf_id = f"{sub}/providers/Microsoft.DataFactory/factories/fact1"
    rows = [
        MonthlyCostRow(
            month="2026-05", resource_kind="dedicated_pool",
            resource_name="dp1",
            resource_id=f"{syn_ws_id}/sqlPools/dp1",
            cost=500.0,
        ),
        MonthlyCostRow(
            month="2026-05", resource_kind="synapse_workspace",
            resource_name="ws1",
            resource_id=syn_ws_id,
            cost=100.0,
        ),
        MonthlyCostRow(
            month="2026-05", resource_kind="adf_factory",
            resource_name="fact1",
            resource_id=adf_id,
            cost=42.0,
        ),
        MonthlyCostRow(
            month="2026-05", resource_kind="adf_factory",
            resource_name="fact1",
            resource_id=f"{adf_id}/integrationRuntimes/AutoResolveIntegrationRuntime",
            cost=8.0,
        ),
        MonthlyCostRow(
            month="2026-05", resource_kind="other",
            resource_name="legacy",
            resource_id=None,  # legacy / hand-built row — keep
            cost=1.0,
        ),
    ]

    adf_only = filter_rows_for_scope(rows, adf_id)
    assert [r.cost for r in adf_only] == [42.0, 8.0, 1.0]

    syn_only = filter_rows_for_scope(rows, syn_ws_id)
    # Synapse workspace prefix should pull in its child SQL pool too.
    assert sorted(r.cost for r in syn_only) == [1.0, 100.0, 500.0]

    # No scope id -> passthrough.
    assert filter_rows_for_scope(rows, None) == rows
    assert filter_rows_for_scope(rows, "") == rows


def test_coerce_month_handles_int_and_iso() -> None:
    assert _coerce_month(20260101) == "2026-01"
    assert _coerce_month("2026-02-15") == "2026-02"
    assert _coerce_month(None) == ""


def test_classify_resource_id_dedicated_pool() -> None:
    arm = ("/subscriptions/x/resourceGroups/rg/providers/Microsoft.Synapse/"
           "workspaces/ws/sqlPools/p1")
    kind, name = _classify_resource_id(arm)
    assert kind == "dedicated_pool"
    assert name == "p1"


def test_classify_resource_id_spark_pool() -> None:
    arm = ("/subscriptions/x/resourceGroups/rg/providers/Microsoft.Synapse/"
           "workspaces/ws/bigDataPools/sp1")
    kind, name = _classify_resource_id(arm)
    assert kind == "spark_pool"
    assert name == "sp1"


def test_estimate_fabric_monthly_cost_known_sku() -> None:
    assert estimate_fabric_monthly_cost("F8") == 1051.20
    assert estimate_fabric_monthly_cost("f64") == 8409.60
    assert estimate_fabric_monthly_cost(None) is None
    assert estimate_fabric_monthly_cost("F999") is None


def test_compare_to_fabric_computes_delta() -> None:
    cmp = compare_to_fabric(2000.0, {"recommended_sku": "F8"})
    assert cmp is not None
    assert cmp.fabric_estimated_monthly_cost == 1051.20
    assert cmp.delta_abs == pytest.approx(-948.80, rel=0, abs=0.01)
    assert cmp.delta_pct is not None and abs(cmp.delta_pct + 0.474) < 0.01
    assert cmp.pricing_source == "static"


def test_compare_to_fabric_handles_missing_projection() -> None:
    assert compare_to_fabric(1000.0, None) is None


def test_compare_to_fabric_unknown_sku_yields_no_delta() -> None:
    cmp = compare_to_fabric(1000.0, {"recommended_sku": "F999"})
    assert cmp is not None
    assert cmp.fabric_estimated_monthly_cost is None
    assert cmp.delta_abs is None


def test_compare_to_fabric_large_sku_has_no_static_fallback() -> None:
    """Per design: never fabricate F128+ prices when the API is offline."""
    cmp = compare_to_fabric(50000.0, {"recommended_sku": "F128"})
    assert cmp is not None
    assert cmp.fabric_capacity_sku == "F128"
    assert cmp.fabric_estimated_monthly_cost is None
    assert cmp.fabric_estimated_monthly_cost_1y_ri is None
    assert cmp.fabric_estimated_monthly_cost_3y_ri is None
    assert cmp.delta_abs is None
    assert cmp.notes is not None and "F128" in cmp.notes
