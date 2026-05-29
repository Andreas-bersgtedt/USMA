"""Slice 7-F — Snowflake CU rollup in the Estate Overview aggregator."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from usma.web.estate import (
    EstateIndex,
    _cloud_for,
    _snowflake_daily_cu,
)
from usma.web.storage import FilesystemRunRepo


# ---------------------------------------------------------------------------
# Direct unit tests for the helper.
# ---------------------------------------------------------------------------


def test_snowflake_daily_cu_returns_zero_when_no_payload() -> None:
    assert _snowflake_daily_cu(None) == 0.0
    assert _snowflake_daily_cu({}) == 0.0


def test_snowflake_daily_cu_returns_zero_when_no_window_stats() -> None:
    assert _snowflake_daily_cu({"warehouse_window_stats": []}) == 0.0
    assert _snowflake_daily_cu({"warehouse_window_stats": "not-a-list"}) == 0.0


def test_snowflake_daily_cu_prefers_seven_day_window_and_sums_warehouses() -> None:
    # Two warehouses in the 7-day window: 84 + 84 = 168 CU-hr → 1.0 CU/day.
    payload = {
        "warehouse_window_stats": [
            {"window_days": 28, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": 500.0},
            {"window_days": 7, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": 84.0},
            {"window_days": 7, "warehouse_name": "WH_B", "est_cu_hours_fabric_warehouse": 84.0},
        ]
    }
    assert _snowflake_daily_cu(payload) == 1.0


def test_snowflake_daily_cu_falls_back_to_largest_window() -> None:
    payload = {
        "warehouse_window_stats": [
            {"window_days": 28, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": 336.0},
            {"window_days": 28, "warehouse_name": "WH_B", "est_cu_hours_fabric_warehouse": 336.0},
            {"window_days": 14, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": 100.0},
        ]
    }
    # 672 CU-hr / 28 days / 24 hr = exactly 1.0 sustained CU/day.
    assert _snowflake_daily_cu(payload) == 1.0


def test_snowflake_daily_cu_swallows_bad_numbers() -> None:
    payload = {
        "warehouse_window_stats": [
            {"window_days": 7, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": "nope"},
            {"window_days": 7, "warehouse_name": "WH_B", "est_cu_hours_fabric_warehouse": 168.0},
        ]
    }
    # The string value is swallowed; WH_B alone → 168/7/24 = 1.0.
    assert _snowflake_daily_cu(payload) == 1.0


def test_snowflake_daily_cu_returns_zero_when_all_rows_zero() -> None:
    payload = {
        "warehouse_window_stats": [
            {"window_days": 7, "warehouse_name": "WH_A", "est_cu_hours_fabric_warehouse": 0.0},
        ]
    }
    assert _snowflake_daily_cu(payload) == 0.0


# ---------------------------------------------------------------------------
# _cloud_for — Snowflake routes via extras["platform"] (defaults to aws).
# ---------------------------------------------------------------------------


def test_cloud_for_snowflake_routes_by_platform_hint() -> None:
    assert _cloud_for("snowflake", "azure", None) == "azure"
    assert _cloud_for("snowflake", "aws", None) == "aws"
    assert _cloud_for("snowflake", "gcp", None) == "gcp"


def test_cloud_for_snowflake_defaults_to_aws_when_platform_missing() -> None:
    assert _cloud_for("snowflake", None, None) == "aws"


# ---------------------------------------------------------------------------
# End-to-end: a Snowflake-only run lights up projected_fabric_cu +
# recommended_fabric_sku in the Estate Overview.
# ---------------------------------------------------------------------------


def test_estate_overview_uses_snowflake_cu_for_snowflake_only_run(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = runs_dir / "20260528T120000Z-d0000001"
    run_dir.mkdir()

    started = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    finished = started + timedelta(minutes=4)

    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "id": run_dir.name,
                "status": "ok",
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "config_hash": "h",
                "modules": [
                    {"name": "snowflake_workloads", "state": "ok"},
                    {"name": "fabric_mapping", "state": "ok"},
                ],
                "tenant_id": "tenant-1",
                "subscription_id": "acme-prod",
                "resource_group": None,
                "workspace_name": "acme-prod",
                "scopes": [
                    {
                        "source_type": "snowflake",
                        "id": "acme-prod",
                        "display_name": "acme-prod",
                        "extras": {"platform": "azure"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    (run_dir / "fabric_mapping.json").write_text(
        json.dumps(
            {
                "workspace_name": "acme-prod",
                "generated_at": finished.isoformat(),
                "inputs": [],
                "recommendations": [],
                "readiness": {
                    "score": 70.0,
                    "bucket": "ready-with-effort",
                    "counts": {"blocker": 0, "warning": 0, "info": 0},
                    "top_blockers": [],
                    "tsql_compatibility_pct": None,
                    "tsql_objects_total": 0,
                    "tsql_objects_incompatible": 0,
                    "tsql_objects_needs_review": 0,
                },
                "runbook": [],
                "capacity_projection": {
                    "peak_dwu": None,
                    "peak_dwu_with_headroom": None,
                    "estimated_cu": None,
                    "recommended_sku": None,
                    "headroom_pct": 0,
                    "notes": [],
                },
            }
        ),
        encoding="utf-8",
    )

    # Snowflake analyzer output with a 7-day window summing to 1344 CU-hr
    # across two warehouses → 8 sustained CU/day.
    (run_dir / "snowflake_workloads.json").write_text(
        json.dumps(
            {
                "account": "acme-prod",
                "platform": "azure",
                "region": "AZURE_WESTEUROPE",
                "edition": "ENTERPRISE",
                "generated_at": finished.isoformat(),
                "warehouses": [],
                "databases": [],
                "schemas": [],
                "tables": [],
                "routines": [],
                "stages": [],
                "streams": [],
                "tasks": [],
                "pipes": [],
                "jobs": [],
                "job_window_stats": [],
                "warehouse_window_stats": [
                    {
                        "window_days": 7,
                        "warehouse_name": "WH_A",
                        "est_credits": 1344.0,
                        "est_vcore_hours": 1344.0,
                        "est_cu_hours_fabric_warehouse": 672.0,
                    },
                    {
                        "window_days": 7,
                        "warehouse_name": "WH_B",
                        "est_credits": 1344.0,
                        "est_vcore_hours": 1344.0,
                        "est_cu_hours_fabric_warehouse": 672.0,
                    },
                ],
                "caveats": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    repo = FilesystemRunRepo(runs_dir)
    report = EstateIndex(repo).build()

    assert len(report.workspaces) == 1
    ws = report.workspaces[0]
    # 1344 CU-hr / 7 days / 24 hr = 8.0 sustained CU/day.
    assert ws.projected_fabric_cu == 8.0
    assert ws.recommended_fabric_sku is not None
    assert ws.recommended_fabric_sku.startswith("F")
    assert ws.source_type == "snowflake"
    # extras["platform"]="azure" must route the cloud bucket.
    assert ws.cloud == "azure"
