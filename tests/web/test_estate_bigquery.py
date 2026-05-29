"""Slice 5-F — BigQuery CU rollup in the Estate Overview aggregator."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from usma.web.estate import (
    EstateIndex,
    _bigquery_daily_cu,
)
from usma.web.storage import FilesystemRunRepo


# ---------------------------------------------------------------------------
# Direct unit tests for the helper.
# ---------------------------------------------------------------------------


def test_bigquery_daily_cu_returns_zero_when_no_payload() -> None:
    assert _bigquery_daily_cu(None) == 0.0
    assert _bigquery_daily_cu({}) == 0.0


def test_bigquery_daily_cu_returns_zero_when_no_window_stats() -> None:
    assert _bigquery_daily_cu({"job_window_stats": []}) == 0.0
    assert _bigquery_daily_cu({"job_window_stats": "not-a-list"}) == 0.0


def test_bigquery_daily_cu_prefers_seven_day_window() -> None:
    payload = {
        "job_window_stats": [
            {"window_days": 28, "est_cu_hours_fabric_spark": 280.0},
            {"window_days": 7, "est_cu_hours_fabric_spark": 168.0},
            {"window_days": 14, "est_cu_hours_fabric_spark": 140.0},
        ]
    }
    # 168 CU-hr / 7 days / 24 hr = exactly 1.0 sustained CU/day.
    assert _bigquery_daily_cu(payload) == 1.0


def test_bigquery_daily_cu_falls_back_to_largest_window() -> None:
    # No 7-day row → analyzer takes the largest window available.
    payload = {
        "job_window_stats": [
            {"window_days": 28, "est_cu_hours_fabric_spark": 672.0},
            {"window_days": 14, "est_cu_hours_fabric_spark": 168.0},
        ]
    }
    # 672 CU-hr / 28 days / 24 hr = exactly 1.0 sustained CU/day.
    assert _bigquery_daily_cu(payload) == 1.0


def test_bigquery_daily_cu_swallows_bad_numbers() -> None:
    payload = {
        "job_window_stats": [
            {"window_days": 7, "est_cu_hours_fabric_spark": "not-a-number"},
        ]
    }
    assert _bigquery_daily_cu(payload) == 0.0


def test_bigquery_daily_cu_rejects_zero_or_missing_window_days() -> None:
    assert (
        _bigquery_daily_cu(
            {"job_window_stats": [{"window_days": 0, "est_cu_hours_fabric_spark": 10.0}]}
        )
        == 0.0
    )
    assert (
        _bigquery_daily_cu(
            {"job_window_stats": [{"est_cu_hours_fabric_spark": 10.0}]}
        )
        == 0.0
    )


# ---------------------------------------------------------------------------
# End-to-end: a BigQuery-only run lights up projected_fabric_cu +
# recommended_fabric_sku in the Estate Overview, even with no
# dedicated-pool capacity projection.
# ---------------------------------------------------------------------------


def test_estate_overview_uses_bigquery_cu_for_bigquery_only_run(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = runs_dir / "20260520T120000Z-f0000001"
    run_dir.mkdir()

    started = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
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
                    {"name": "bigquery_workloads", "state": "ok"},
                    {"name": "fabric_mapping", "state": "ok"},
                ],
                "tenant_id": "tenant-1",
                "subscription_id": "gcp-proj-id",
                "resource_group": None,
                "workspace_name": "gcp-proj-id",
                "scopes": [{"source_type": "bigquery"}],
            }
        ),
        encoding="utf-8",
    )

    # Minimal fabric_mapping.json with NO capacity_projection — typical of
    # a BigQuery-only run before Slice 5-H wires GCP cost in.
    (run_dir / "fabric_mapping.json").write_text(
        json.dumps(
            {
                "workspace_name": "gcp-proj-id",
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

    # BigQuery analyzer output with a 7-day window that resolves to a
    # sustained 8 CU/day (8 × 7 × 24 = 1344 CU-hr).
    (run_dir / "bigquery_workloads.json").write_text(
        json.dumps(
            {
                "project_id": "gcp-proj-id",
                "generated_at": finished.isoformat(),
                "datasets": [],
                "tables": [],
                "routines": [],
                "scheduled_queries": [],
                "jobs": [],
                "job_window_stats": [
                    {
                        "window_days": 7,
                        "job_count": 1000,
                        "completed_count": 1000,
                        "succeeded_count": 980,
                        "failed_count": 20,
                        "success_rate": 0.98,
                        "avg_duration_seconds": 12.0,
                        "total_slot_hours": 5376.0,
                        "total_billed_bytes": 0,
                        "avg_slot_hours_per_job": 5.376,
                        "est_cu_hours_fabric_spark": 1344.0,
                    },
                    {
                        "window_days": 28,
                        "job_count": 4000,
                        "completed_count": 4000,
                        "succeeded_count": 3920,
                        "failed_count": 80,
                        "success_rate": 0.98,
                        "avg_duration_seconds": 12.0,
                        "total_slot_hours": 21504.0,
                        "total_billed_bytes": 0,
                        "avg_slot_hours_per_job": 5.376,
                        "est_cu_hours_fabric_spark": 5376.0,
                    },
                ],
                "caveats": [],
                "errors": [],
                "dataset_count": 0,
                "table_count": 0,
                "view_count": 0,
                "materialized_view_count": 0,
                "external_table_count": 0,
                "routine_count": 0,
                "scheduled_query_count": 0,
                "job_count": 1000,
                "unsupported_table_count": 0,
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
    # No DW capacity SKU was present, so the helper must have derived one
    # from the combined daily CU via _smallest_sku_covering(8.0).
    assert ws.recommended_fabric_sku is not None
    assert ws.recommended_fabric_sku.startswith("F")
    assert ws.source_type == "bigquery"
