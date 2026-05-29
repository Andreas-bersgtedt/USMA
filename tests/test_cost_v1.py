"""Tests for the cost module v1: rules engine, by-resource aggregation, HTML."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from usma.modules.cost import rules as _rules
from usma.modules.cost.cost_client import aggregate_by_resource_name
from usma.modules.cost.html_report import write_html
from usma.modules.cost.models import (
    CostAnalysis,
    FabricCostComparison,
    MonthlyCostRow,
)


def _row(month: str, kind: str, cost: float, name: str | None = None) -> MonthlyCostRow:
    return MonthlyCostRow(
        month=month, resource_kind=kind, resource_name=name,
        sku=None, cost=cost, currency="USD", usage_quantity=0.0, usage_unit=None,
    )


def _result(**overrides) -> CostAnalysis:
    base = CostAnalysis(
        workspace_name="ws",
        subscription_id="sub-1",
        resource_group="rg",
        generated_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_aggregate_by_resource_name_skips_blank() -> None:
    rows = [
        _row("2026-01", "dedicated_pool", 100.0, "p1"),
        _row("2026-02", "dedicated_pool", 50.0, "p1"),
        _row("2026-01", "storage", 25.0, None),
    ]
    by_name = aggregate_by_resource_name(rows)
    assert by_name == {"p1": 150.0}


def test_no_data_finding_when_no_rows() -> None:
    findings = _rules.evaluate(_result())
    assert any(f.rule_id == "cost.no_data" and f.severity == "info" for f in findings)


def test_no_data_finding_branches_on_collection_status() -> None:
    findings = _rules.evaluate(_result(collection_status="sdk_missing"))
    assert any(f.rule_id == "cost.sdk_missing" and f.severity == "medium" for f in findings)
    findings = _rules.evaluate(_result(collection_status="live_disabled"))
    assert any(f.rule_id == "cost.live_disabled" and f.severity == "info" for f in findings)
    findings = _rules.evaluate(_result(collection_status="error"))
    assert any(f.rule_id == "cost.collection_error" and f.severity == "medium" for f in findings)


def test_fabric_savings_finding_when_fabric_cheaper() -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 2000.0, "p1")],
        monthly_totals={"2026-01": 2000.0, "2026-02": 2000.0},
        fabric_comparison=FabricCostComparison(
            synapse_avg_monthly_cost=2000.0,
            fabric_capacity_sku="F8",
            fabric_estimated_monthly_cost=1052.0,
            delta_abs=-948.0,
            delta_pct=-0.474,
        ),
    )
    findings = _rules.evaluate(r)
    matches = [f for f in findings if f.rule_id == "cost.fabric.savings"]
    assert len(matches) == 1
    assert matches[0].severity == "info"


def test_fabric_increase_finding_when_fabric_more_expensive() -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 500.0, "p1")],
        monthly_totals={"2026-01": 500.0, "2026-02": 500.0},
        fabric_comparison=FabricCostComparison(
            synapse_avg_monthly_cost=500.0,
            fabric_capacity_sku="F8",
            fabric_estimated_monthly_cost=1052.0,
            delta_abs=552.0,
            delta_pct=1.104,
        ),
    )
    findings = _rules.evaluate(r)
    matches = [f for f in findings if f.rule_id == "cost.fabric.increase"]
    assert len(matches) == 1
    assert matches[0].severity == "medium"


def test_fabric_no_finding_when_within_threshold() -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 1000.0, "p1")],
        monthly_totals={"2026-01": 1000.0, "2026-02": 1000.0},
        fabric_comparison=FabricCostComparison(
            synapse_avg_monthly_cost=1000.0,
            fabric_capacity_sku="F8",
            fabric_estimated_monthly_cost=1050.0,
            delta_abs=50.0,
            delta_pct=0.05,
        ),
    )
    findings = _rules.evaluate(r)
    assert not any(f.rule_id.startswith("cost.fabric.") for f in findings)


def test_concentration_finding_for_dedicated_pool() -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 800.0, "p1"),
              _row("2026-01", "storage", 100.0, "sa1")],
        by_resource_kind={"dedicated_pool": 800.0, "storage": 100.0},
    )
    findings = _rules.evaluate(r)
    matches = [f for f in findings
               if f.rule_id == "cost.concentration.dedicated_pool"]
    assert len(matches) == 1


def test_concentration_silent_when_below_threshold() -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 600.0, "p1"),
              _row("2026-01", "storage", 400.0, "sa1")],
        by_resource_kind={"dedicated_pool": 600.0, "storage": 400.0},
    )
    findings = _rules.evaluate(r)
    assert not any("concentration" in f.rule_id for f in findings)


def test_month_over_month_spike_finding() -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 1000.0, "p1"),
              _row("2026-02", "dedicated_pool", 1500.0, "p1")],
        monthly_totals={"2026-01": 1000.0, "2026-02": 1500.0},
    )
    findings = _rules.evaluate(r)
    matches = [f for f in findings if f.rule_id == "cost.month_over_month.spike"]
    assert len(matches) == 1
    assert matches[0].severity == "medium"
    assert "2026-01" in matches[0].title
    assert "2026-02" in matches[0].title


def test_month_over_month_silent_when_flat() -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 1000.0, "p1"),
              _row("2026-02", "dedicated_pool", 1050.0, "p1")],
        monthly_totals={"2026-01": 1000.0, "2026-02": 1050.0},
    )
    findings = _rules.evaluate(r)
    assert not any("spike" in f.rule_id for f in findings)


def test_cost_html_round_trip(tmp_path: Path) -> None:
    r = _result(
        rows=[_row("2026-01", "dedicated_pool", 1000.0, "p1"),
              _row("2026-02", "dedicated_pool", 1500.0, "p1"),
              _row("2026-01", "storage", 100.0, "sa1")],
        monthly_totals={"2026-01": 1100.0, "2026-02": 1500.0},
        by_resource_kind={"dedicated_pool": 2500.0, "storage": 100.0},
        by_resource_name={"p1": 2500.0, "sa1": 100.0},
        fabric_comparison=FabricCostComparison(
            synapse_avg_monthly_cost=1300.0,
            fabric_capacity_sku="F8",
            fabric_estimated_monthly_cost=1052.0,
            delta_abs=-248.0,
            delta_pct=-0.19,
        ),
    )
    r.findings = _rules.evaluate(r)
    out = write_html(r, tmp_path / "cost.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Cost" in text
    assert "p1" in text
    assert "F8" in text
    assert "cost.month_over_month.spike" in text


def test_collection_error_finding_is_snowflake_specific() -> None:
    r = _result(collection_status="error", source_type="snowflake", errors=["monthly_breakdown: ProgrammingError: SQL compilation error: Schema 'SNOWFLAKE.ACCOUNT_USAGE' does not exist or not authorized."])
    findings = _rules.evaluate(r)
    match = next(f for f in findings if f.rule_id == "cost.collection_error")
    assert "Snowflake ACCOUNT_USAGE" in match.title
    assert "IMPORTED PRIVILEGES" in (match.detail or "")
    assert "Underlying error" in (match.detail or "")


def test_collection_error_finding_flags_role_mismatch(monkeypatch) -> None:
    """When the underlying error embeds active_role=PUBLIC but
    SNOWFLAKE_ROLE=USMA_RO, the rule must surface the OAuth
    refresh-token-binding hazard (re-sign-in required)."""
    monkeypatch.setenv("SNOWFLAKE_ROLE", "USMA_RO")
    r = _result(
        collection_status="error",
        source_type="snowflake",
        errors=[
            "monthly_breakdown: 002003 (42S02): SQL compilation error: "
            "Object 'SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY' "
            "does not exist or not authorized. "
            "[configured_role=USMA_RO; active_role=PUBLIC; "
            "available_roles=[\"PUBLIC\"]]"
        ],
    )
    findings = _rules.evaluate(r)
    match = next(f for f in findings if f.rule_id == "cost.collection_error")
    detail = match.detail or ""
    assert "ROLE MISMATCH" in detail
    assert "USMA_RO" in detail
    assert "PUBLIC" in detail
    assert "Sign in to Snowflake" in detail


def test_snowflake_credits_only_finding() -> None:
    """When the cost client degrades to credits-only (no $ rows),
    rules must emit an info finding so $0 totals don't look like a
    silent failure."""
    credits_row = MonthlyCostRow(
        month="2026-04", resource_kind="snowflake_warehouse",
        resource_name="WH_A", sku="credits",
        cost=0.0, currency="USD",
        usage_quantity=50.0, usage_unit="credits",
    )
    findings = _rules.evaluate(_result(
        source_type="snowflake",
        collection_status="ok",
        rows=[credits_row],
    ))
    match = next(
        (f for f in findings if f.rule_id == "cost.snowflake_credits_only"),
        None,
    )
    assert match is not None
    assert "USAGE_IN_CURRENCY_DAILY" in (match.detail or "")


def test_collection_error_finding_active_role_matches_config(monkeypatch) -> None:
    """When the active role matches the configured role, the rule
    should pivot the message from 'role mismatch' to 'grant missing'."""
    monkeypatch.setenv("SNOWFLAKE_ROLE", "USMA_RO")
    r = _result(
        collection_status="error",
        source_type="snowflake",
        errors=[
            "monthly_breakdown: ProgrammingError: not authorized "
            "[configured_role=USMA_RO; active_role=USMA_RO]"
        ],
    )
    findings = _rules.evaluate(r)
    match = next(f for f in findings if f.rule_id == "cost.collection_error")
    detail = match.detail or ""
    assert "USMA_RO" in detail
    assert "IMPORTED PRIVILEGES" in detail
    assert "ROLE MISMATCH" not in detail


def test_no_data_finding_is_snowflake_specific() -> None:
    findings = _rules.evaluate(_result(source_type="snowflake"))
    match = next(f for f in findings if f.rule_id == "cost.no_data")
    assert "SNOWFLAKE.ACCOUNT_USAGE" in (match.detail or "")


def test_missing_config_finding_is_snowflake_specific() -> None:
    findings = _rules.evaluate(_result(collection_status="missing_config", source_type="snowflake"))
    match = next(f for f in findings if f.rule_id == "cost.missing_config")
    assert "SNOWFLAKE_ACCOUNT" in (match.detail or "")

