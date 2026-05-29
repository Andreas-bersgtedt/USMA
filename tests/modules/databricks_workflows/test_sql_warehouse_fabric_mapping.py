"""Tests for the per-warehouse Fabric F-SKU mapping (added 2026-05).

Covers:

1. ``classify_warehouse`` returns the expected (support, target, note)
   tuple for each Databricks warehouse type and ``"unknown"`` for
   unrecognised values.
2. ``recommend_fabric_sku`` picks the smallest standard F-SKU that
   covers ``avg_concurrent_cus * headroom`` and returns ``None`` when
   the sizing input is missing / zero.
3. ``_build_sql_warehouse_fabric_mappings`` derives REST-evidence
   mappings, falls back to system-table task seconds on Serverless,
   marks ``confidence='high'`` when both signals are present, and
   withholds the SKU recommendation when neither signal is present.
4. ``rules_for_databricks_workflows`` emits one Recommendation per
   warehouse mapping with the F-SKU surfaced in ``target`` and the
   evidence in ``detail``.
"""
from __future__ import annotations

import pytest

from usma.modules.databricks_workflows.analyzer import (
    _build_sql_warehouse_fabric_mappings,
)
from usma.modules.databricks_workflows.fabric_compat import (
    DEFAULT_PEAK_TO_AVG_HEADROOM,
    FABRIC_SKUS,
    classify_warehouse,
    recommend_fabric_sku,
)
from usma.modules.databricks_workflows.models import (
    SqlWarehouse,
    SqlWarehouseDailyUsage,
    SqlWarehouseStats,
)
from usma.modules.fabric_mapping.rules import rules_for_databricks_workflows


# -------------------------------------------------- classify_warehouse


@pytest.mark.parametrize(
    ("wh_type", "expected_support"),
    [
        ("SERVERLESS", "supported"),
        ("serverless", "supported"),
        ("PRO", "supported"),
        ("CLASSIC", "partial"),
        ("WEIRD_FUTURE_SKU", "unknown"),
        (None, "unknown"),
    ],
)
def test_classify_warehouse_support_label(wh_type, expected_support):
    support, target, note = classify_warehouse(wh_type)
    assert support == expected_support
    assert target == "Fabric Warehouse"
    assert isinstance(note, str) and note


# -------------------------------------------------- recommend_fabric_sku


def test_recommend_fabric_sku_picks_smallest_covering_sku():
    # 0.4 CU avg * 4x headroom = 1.6 -> F2
    assert recommend_fabric_sku(0.4) == "F2"
    # 0.6 CU avg * 4x = 2.4 -> F4
    assert recommend_fabric_sku(0.6) == "F4"
    # 1.5 CU avg * 4x = 6 -> F8
    assert recommend_fabric_sku(1.5) == "F8"


def test_recommend_fabric_sku_honours_custom_headroom():
    # 1.0 CU avg * 1x headroom = 1 -> F2 (smallest)
    assert recommend_fabric_sku(1.0, headroom=1.0) == "F2"
    # 1.0 CU avg * 8x headroom = 8 -> F8
    assert recommend_fabric_sku(1.0, headroom=8.0) == "F8"


def test_recommend_fabric_sku_returns_none_without_signal():
    assert recommend_fabric_sku(None) is None
    assert recommend_fabric_sku(0.0) is None
    assert recommend_fabric_sku(-1.0) is None


def test_recommend_fabric_sku_overflow_marker():
    # 10000 CU avg * 4x = 40000 -> exceeds F2048; returns "F2048+"
    assert recommend_fabric_sku(10_000.0) == f"F{FABRIC_SKUS[-1]}+"


# -------------------------------------------------- mapping builder


def _wh(wh_id: str, name: str, wh_type: str) -> SqlWarehouse:
    return SqlWarehouse(warehouse_id=wh_id, name=name, warehouse_type=wh_type)


def test_mapping_builder_rest_only_evidence():
    wh = _wh("w1", "pro-wh", "PRO")
    # 1 day * 86400 s = 86400 s. cpu=4320 -> avg_cus = 0.05 -> *4 = 0.2 -> F2.
    st = SqlWarehouseStats(
        warehouse_id="w1",
        warehouse_name="pro-wh",
        lookback_days=1,
        query_count=10,
        total_cpu_seconds=4320.0,
        queries_with_cpu_metric=10,
    )
    mappings = _build_sql_warehouse_fabric_mappings(
        warehouses=[wh],
        stats=[st],
        daily_usage=[],
        lookback_days=1,
    )
    assert len(mappings) == 1
    m = mappings[0]
    assert m.warehouse_id == "w1"
    assert m.source_warehouse_type == "PRO"
    assert m.target_fabric_artifact == "Fabric Warehouse"
    assert m.support == "supported"
    assert m.recommended_sku == "F2"
    assert m.evidence_source == "rest_metrics"
    assert m.confidence == "medium"
    assert m.total_cpu_seconds == 4320.0
    assert m.total_task_seconds is None
    assert m.total_dbu_hours is None
    assert m.avg_concurrent_cus == pytest.approx(0.05, rel=1e-6)
    assert m.peak_to_avg_headroom == DEFAULT_PEAK_TO_AVG_HEADROOM


def test_mapping_builder_serverless_falls_back_to_system_tables():
    wh = _wh("w2", "srv-wh", "SERVERLESS")
    # Serverless: REST cpu is None. System table task_seconds drives sizing.
    st = SqlWarehouseStats(
        warehouse_id="w2",
        warehouse_name="srv-wh",
        lookback_days=2,
        query_count=5,
        total_cpu_seconds=None,
        queries_with_cpu_metric=0,
    )
    daily = [
        SqlWarehouseDailyUsage(
            warehouse_id="w2",
            warehouse_name="srv-wh",
            usage_date="2026-05-26",
            query_count=3,
            total_task_seconds=8640.0,
            dbu_hours=1.5,
            sku_name="PREMIUM_SQL_PRO_COMPUTE",
        ),
        SqlWarehouseDailyUsage(
            warehouse_id="w2",
            warehouse_name="srv-wh",
            usage_date="2026-05-27",
            query_count=2,
            total_task_seconds=8640.0,
            dbu_hours=1.5,
            sku_name="PREMIUM_SQL_PRO_COMPUTE",
        ),
    ]
    mappings = _build_sql_warehouse_fabric_mappings(
        warehouses=[wh],
        stats=[st],
        daily_usage=daily,
        lookback_days=2,
    )
    m = mappings[0]
    assert m.evidence_source == "system_tables"
    assert m.confidence == "medium"
    assert m.total_cpu_seconds is None
    assert m.total_task_seconds == pytest.approx(17280.0)
    assert m.total_dbu_hours == pytest.approx(3.0)
    # avg = 17280 / (2*86400) = 0.1 ; *4 = 0.4 -> F2
    assert m.avg_concurrent_cus == pytest.approx(0.1)
    assert m.recommended_sku == "F2"


def test_mapping_builder_both_signals_yield_high_confidence():
    wh = _wh("w3", "pro-wh", "PRO")
    st = SqlWarehouseStats(
        warehouse_id="w3",
        warehouse_name="pro-wh",
        lookback_days=1,
        query_count=10,
        total_cpu_seconds=4320.0,
        queries_with_cpu_metric=10,
    )
    daily = [
        SqlWarehouseDailyUsage(
            warehouse_id="w3",
            warehouse_name="pro-wh",
            usage_date="2026-05-27",
            query_count=10,
            total_task_seconds=4400.0,
            dbu_hours=2.0,
            sku_name="PREMIUM_SQL_PRO_COMPUTE",
        ),
    ]
    mappings = _build_sql_warehouse_fabric_mappings(
        warehouses=[wh],
        stats=[st],
        daily_usage=daily,
        lookback_days=1,
    )
    m = mappings[0]
    assert m.evidence_source == "both"
    assert m.confidence == "high"
    # REST cpu wins for sizing.
    assert m.avg_concurrent_cus == pytest.approx(4320.0 / 86400.0)
    assert m.total_dbu_hours == pytest.approx(2.0)


def test_mapping_builder_no_signal_withholds_sku():
    wh = _wh("w4", "idle-wh", "PRO")
    st = SqlWarehouseStats(
        warehouse_id="w4",
        warehouse_name="idle-wh",
        lookback_days=30,
        query_count=0,
        total_cpu_seconds=None,
    )
    mappings = _build_sql_warehouse_fabric_mappings(
        warehouses=[wh],
        stats=[st],
        daily_usage=[],
        lookback_days=30,
    )
    m = mappings[0]
    assert m.recommended_sku is None
    assert m.evidence_source == "none"
    assert m.confidence == "low"
    assert any("No usage signal" in n for n in m.notes)


def test_mapping_builder_flags_signal_disagreement():
    wh = _wh("w5", "skew-wh", "PRO")
    st = SqlWarehouseStats(
        warehouse_id="w5",
        warehouse_name="skew-wh",
        lookback_days=1,
        query_count=10,
        total_cpu_seconds=100.0,
        queries_with_cpu_metric=10,
    )
    daily = [
        SqlWarehouseDailyUsage(
            warehouse_id="w5",
            warehouse_name="skew-wh",
            usage_date="2026-05-27",
            query_count=10,
            total_task_seconds=500.0,  # 5x the REST value
            dbu_hours=1.0,
        ),
    ]
    mappings = _build_sql_warehouse_fabric_mappings(
        warehouses=[wh],
        stats=[st],
        daily_usage=daily,
        lookback_days=1,
    )
    m = mappings[0]
    assert any("disagree" in n for n in m.notes)


# -------------------------------------------------- rules emitter


def test_rules_for_databricks_workflows_emits_per_warehouse_rec():
    payload = {
        "workflows": [],
        "tasks": [],
        "job_clusters": [],
        "interactive_clusters": [],
        "sql_warehouse_fabric_mappings": [
            {
                "warehouse_id": "w1",
                "warehouse_name": "pro-wh",
                "source_warehouse_type": "PRO",
                "target_fabric_artifact": "Fabric Warehouse",
                "recommended_sku": "F4",
                "support": "supported",
                "confidence": "medium",
                "lookback_days": 30,
                "total_cpu_seconds": 12345.6,
                "queries_with_cpu_metric": 200,
                "total_task_seconds": None,
                "total_dbu_hours": None,
                "avg_concurrent_cus": 0.00476,
                "peak_to_avg_headroom": 4.0,
                "evidence_source": "rest_metrics",
                "notes": [
                    "Pro SQL warehouse — Fabric Warehouse covers the equivalent T-SQL surface.",
                ],
            },
        ],
    }
    recs = rules_for_databricks_workflows(payload)
    matches = [r for r in recs if r.id == "db.sql_warehouse.w1"]
    assert len(matches) == 1
    rec = matches[0]
    assert rec.area == "databricks_workflows.sql_warehouses"
    assert rec.target == "F4"
    assert "F4" in rec.title
    assert "pro-wh" in rec.title
    assert "REST CPU" in rec.detail
    assert "rest_metrics" in rec.detail
    assert rec.fabric_action and "F4" in rec.fabric_action


def test_rules_for_databricks_workflows_handles_missing_sku():
    payload = {
        "sql_warehouse_fabric_mappings": [
            {
                "warehouse_id": "w9",
                "warehouse_name": "idle",
                "source_warehouse_type": "PRO",
                "target_fabric_artifact": "Fabric Warehouse",
                "recommended_sku": None,
                "support": "supported",
                "confidence": "low",
                "evidence_source": "none",
                "notes": ["No usage signal in the lookback window — ..."],
            },
        ],
    }
    recs = rules_for_databricks_workflows(payload)
    rec = next(r for r in recs if r.id == "db.sql_warehouse.w9")
    assert rec.target == "w9"
    assert "sizing TBD" in rec.title
    assert rec.fabric_action and "SMA_DATABRICKS_SYSTEM_TABLES" in rec.fabric_action
