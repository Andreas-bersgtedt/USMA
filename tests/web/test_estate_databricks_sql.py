"""Tests for the Databricks SQL warehouse CU rollup in the Estate Overview.

Mirrors :func:`tests.web.test_estate_bigquery` but covers the new
``_databricks_sql_daily_cu`` helper added alongside the per-warehouse
Fabric F-SKU mapping.
"""
from __future__ import annotations

import pytest

from usma.web.estate import _databricks_sql_daily_cu


def test_returns_zero_when_payload_missing() -> None:
    assert _databricks_sql_daily_cu(None) == 0.0
    assert _databricks_sql_daily_cu({}) == 0.0
    assert _databricks_sql_daily_cu({"sql_warehouse_fabric_mappings": []}) == 0.0


def test_returns_zero_when_mappings_lack_signal() -> None:
    payload = {
        "sql_warehouse_fabric_mappings": [
            {
                "warehouse_id": "w1",
                "avg_concurrent_cus": None,
                "peak_to_avg_headroom": 4.0,
            },
            {
                "warehouse_id": "w2",
                # missing avg_concurrent_cus entirely
                "peak_to_avg_headroom": 4.0,
            },
        ]
    }
    assert _databricks_sql_daily_cu(payload) == 0.0


def test_sums_avg_cus_times_headroom_across_warehouses() -> None:
    payload = {
        "sql_warehouse_fabric_mappings": [
            {
                "warehouse_id": "w1",
                "avg_concurrent_cus": 0.05,
                "peak_to_avg_headroom": 4.0,
            },
            {
                "warehouse_id": "w2",
                "avg_concurrent_cus": 0.10,
                "peak_to_avg_headroom": 4.0,
            },
        ]
    }
    # (0.05 + 0.10) * 4 = 0.6
    assert _databricks_sql_daily_cu(payload) == pytest.approx(0.6)


def test_falls_back_to_default_headroom_when_missing_or_zero() -> None:
    payload = {
        "sql_warehouse_fabric_mappings": [
            {"warehouse_id": "w1", "avg_concurrent_cus": 0.25},  # no headroom -> 4
            {
                "warehouse_id": "w2",
                "avg_concurrent_cus": 0.25,
                "peak_to_avg_headroom": 0,  # zero -> 4
            },
        ]
    }
    # 0.25 * 4 + 0.25 * 4 = 2.0
    assert _databricks_sql_daily_cu(payload) == pytest.approx(2.0)


def test_respects_custom_headroom_when_provided() -> None:
    payload = {
        "sql_warehouse_fabric_mappings": [
            {
                "warehouse_id": "w1",
                "avg_concurrent_cus": 0.5,
                "peak_to_avg_headroom": 2.0,
            },
        ]
    }
    assert _databricks_sql_daily_cu(payload) == pytest.approx(1.0)


def test_ignores_non_dict_entries_and_malformed_numbers() -> None:
    payload = {
        "sql_warehouse_fabric_mappings": [
            None,
            "not-a-dict",
            {
                "warehouse_id": "w1",
                "avg_concurrent_cus": "not-a-number",
                "peak_to_avg_headroom": 4.0,
            },
            {
                "warehouse_id": "w2",
                "avg_concurrent_cus": 0.1,
                "peak_to_avg_headroom": 4.0,
            },
        ]
    }
    assert _databricks_sql_daily_cu(payload) == pytest.approx(0.4)
