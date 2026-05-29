"""Phase 7 Slice 7-D — tests for ``fabric_mapping.rules.rules_for_snowflake_workloads``."""
from __future__ import annotations

from usma.modules.fabric_mapping import rules


def _payload() -> dict:
    return {
        "warehouses": [
            {"name": "WH_A", "size": "MEDIUM"},
            {"name": "WH_B", "size": "SMALL"},
        ],
        "databases": [{"name": "ANALYTICS"}],
        "tables": [
            {"kind": "TABLE", "support": "supported"},
            {"kind": "MATERIALIZED_VIEW", "support": "partial"},
            {"kind": "DYNAMIC_TABLE", "support": "partial"},
            {"kind": "TEMPORARY", "support": "unsupported"},
        ],
        "routines": [
            {"routine_kind": "FUNCTION", "language": "JAVASCRIPT", "support": "unsupported"},
            {"routine_kind": "PROCEDURE", "language": "SQL", "support": "partial"},
        ],
        "stages": [{"name": "STG_EXT"}],
        "streams": [{"name": "STR1"}],
        "tasks": [{"name": "T_LOAD"}],
        "pipes": [{"name": "P_S3"}],
        "warehouse_window_stats": [
            {"window_days": 7, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": 168.0},
            {"window_days": 7, "warehouse_name": "WH_B", "est_cu_hours_fabric_warehouse": 168.0},
            {"window_days": 28, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": 500.0},
        ],
        "caveats": [
            "Snowflake credits converted to Fabric Warehouse CU-hours using a "
            "size-class proxy ...",
        ],
    }


def test_rules_empty_payload_returns_empty() -> None:
    assert rules.rules_for_snowflake_workloads({}) == []


def test_rules_inventory_headline() -> None:
    out = rules.rules_for_snowflake_workloads(_payload())
    inv = next(r for r in out if r.id == "sf.inventory")
    assert "2 Snowflake warehouse(s)" in inv.title
    assert "1 database(s)" in inv.title


def test_rules_unsupported_table_rollup() -> None:
    out = rules.rules_for_snowflake_workloads(_payload())
    rec = next(r for r in out if r.id == "sf.unsupported_table.temporary")
    assert rec.severity == "warning"
    assert "TEMPORARY" in rec.title


def test_rules_partial_table_rollup_groups_by_kind() -> None:
    out = rules.rules_for_snowflake_workloads(_payload())
    kinds = {r.target for r in out if r.id.startswith("sf.partial_table.")}
    assert kinds == {"MATERIALIZED_VIEW", "DYNAMIC_TABLE"}


def test_rules_routines_flag_unsupported_languages() -> None:
    out = rules.rules_for_snowflake_workloads(_payload())
    rec = next(r for r in out if r.id == "sf.routines")
    assert rec.severity == "warning"
    assert "1 unsupported" in rec.title
    assert "JAVASCRIPT" in rec.detail


def test_rules_object_recommendations_present() -> None:
    out = rules.rules_for_snowflake_workloads(_payload())
    ids = {r.id for r in out}
    assert {"sf.tasks", "sf.streams", "sf.pipes", "sf.stages"} <= ids


def test_rules_warehouse_sku_recommendation_uses_7day_window() -> None:
    out = rules.rules_for_snowflake_workloads(_payload())
    rec = next(r for r in out if r.id == "sf.warehouse_sku")
    # 168 + 168 = 336 CU-hours over 7 days = 2.0 sustained CU/day. Smallest SKU covering = F2.
    assert "F2" in rec.title
    assert "2.00 CU sustained" in rec.title


def test_rules_credit_caveat_passthrough() -> None:
    out = rules.rules_for_snowflake_workloads(_payload())
    rec = next(r for r in out if r.id == "sf.credit_caveat")
    assert "credit" in rec.detail.lower()


def test_rules_source_type_kwarg_accepted() -> None:
    out = rules.rules_for_snowflake_workloads(_payload(), source_type="snowflake")
    assert out  # accepts kwarg without raising
