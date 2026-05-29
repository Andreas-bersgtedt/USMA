"""Tests for the Estate Overview aggregator."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from usma.web import create_app
from usma.web.estate import EstateIndex, workspace_key
from usma.web.storage import FilesystemRunRepo


def _make_run(
    runs_dir: Path,
    *,
    run_id: str,
    workspace_name: str,
    subscription_id: str = "sub-a",
    resource_group: str = "rg-a",
    tenant_id: str = "tenant-1",
    status: str = "ok",
    finished_offset_min: int = 0,
    readiness_score: float | None = 80.0,
    readiness_bucket: str | None = "ready-with-effort",
    blockers: list[str] | None = None,
    warnings: int = 1,
    info: int = 2,
    tsql_pct: float | None = 92.5,
    cu: float | None = 12.0,
    actual_monthly: float | None = 4500.0,
    fabric_estimated_monthly: float | None = 3200.0,
    include_fabric_mapping: bool = True,
    include_cost: bool = True,
) -> Path:
    blockers = blockers or []
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    started = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(minutes=finished_offset_min)
    finished = started + timedelta(minutes=2)
    meta = {
        "id": run_id,
        "label": workspace_name,
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "config_hash": "h",
        "modules": [
            {"name": "dedicated_pools", "state": "ok"},
            {"name": "fabric_mapping", "state": "ok"},
            {"name": "cost", "state": "ok"},
        ],
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "workspace_name": workspace_name,
    }
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")

    if include_fabric_mapping:
        recs = [
            {
                "id": f"BLK-{i}",
                "area": "dedicated_pools.tsql_surface",
                "title": title,
                "severity": "blocker",
                "effort": "medium",
                "fabric_action": "Rewrite",
                "detail": "x",
            }
            for i, title in enumerate(blockers)
        ]
        recs.extend([
            {
                "id": f"WARN-{i}",
                "area": "pipelines.activities",
                "title": f"warn {i}",
                "severity": "warning",
                "effort": "low",
                "detail": "x",
            }
            for i in range(warnings)
        ])
        recs.extend([
            {
                "id": f"INFO-{i}",
                "area": "monitoring.dwu",
                "title": f"info {i}",
                "severity": "info",
                "effort": "low",
                "detail": "x",
            }
            for i in range(info)
        ])
        fm = {
            "workspace_name": workspace_name,
            "generated_at": finished.isoformat(),
            "inputs": [],
            "recommendations": recs,
            "readiness": {
                "score": readiness_score,
                "bucket": readiness_bucket,
                "counts": {"blocker": len(blockers), "warning": warnings, "info": info},
                "top_blockers": [],
                "tsql_compatibility_pct": tsql_pct,
                "tsql_objects_total": 100,
                "tsql_objects_incompatible": 5,
                "tsql_objects_needs_review": 3,
            },
            "runbook": [],
            "capacity_projection": {
                "peak_dwu": 200.0,
                "peak_dwu_with_headroom": 240.0,
                "estimated_cu": cu,
                "recommended_sku": "F32",
                "headroom_pct": 20,
                "notes": [],
            },
        }
        (run_dir / "fabric_mapping.json").write_text(
            json.dumps(fm), encoding="utf-8"
        )

    if include_cost:
        cost = {
            "workspace_name": workspace_name,
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "generated_at": finished.isoformat(),
            "window_start": started.isoformat(),
            "window_end": finished.isoformat(),
            "rows": [
                {
                    "month": "2026-04",
                    "resource_kind": "dedicated_pool",
                    "cost": (actual_monthly or 0.0),
                    "currency": "USD",
                    "usage_quantity": 1.0,
                }
            ],
            "monthly_totals": {"2026-04": actual_monthly},
            "by_resource_kind": {},
            "by_resource_name": {},
            "fabric_comparison": {
                "synapse_avg_monthly_cost": actual_monthly,
                "fabric_capacity_sku": "F32",
                "fabric_estimated_monthly_cost": fabric_estimated_monthly,
                "delta_abs": (actual_monthly or 0.0) - (fabric_estimated_monthly or 0.0),
                "delta_pct": -29.0,
            },
            "findings": [],
            "errors": [],
            "collection_status": "ok",
        }
        (run_dir / "cost.json").write_text(json.dumps(cost), encoding="utf-8")

    return run_dir


def _id(prefix: str, suffix: str) -> str:
    # The repo enforces "YYYYMMDDTHHMMSSZ-xxxxxxxx".
    return f"{prefix}-{suffix}"


def test_estate_index_groups_workspaces_and_picks_latest_ok(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    # Workspace A: 3 runs, latest OK
    _make_run(runs_dir, run_id=_id("20260501T120000Z", "aaaaaaa1"),
              workspace_name="ws-a", finished_offset_min=0, readiness_score=70)
    _make_run(runs_dir, run_id=_id("20260502T120000Z", "aaaaaaa2"),
              workspace_name="ws-a", finished_offset_min=60 * 24, readiness_score=78,
              status="failed", include_fabric_mapping=False)
    _make_run(runs_dir, run_id=_id("20260503T120000Z", "aaaaaaa3"),
              workspace_name="ws-a", finished_offset_min=60 * 48, readiness_score=85,
              blockers=["mv-redo"])
    # Workspace B: 1 run, blocked
    _make_run(runs_dir, run_id=_id("20260501T120000Z", "bbbbbbb1"),
              workspace_name="ws-b", subscription_id="sub-b",
              resource_group="rg-b", finished_offset_min=10,
              readiness_score=42, readiness_bucket="blocked",
              blockers=["mv-redo", "spark-runtime"])

    repo = FilesystemRunRepo(runs_dir)
    idx = EstateIndex(repo)
    report = idx.build()

    assert report.totals.workspaces == 2
    assert report.totals.runs == 4
    assert report.totals.tenants == 1
    assert report.totals.subscriptions == 2
    assert report.totals.blocked == 1

    by_name = {w.workspace_name: w for w in report.workspaces}
    ws_a = by_name["ws-a"]
    assert ws_a.run_count == 3
    # Latest OK is the run finished at 48h offset.
    assert ws_a.latest_run_id.endswith("aaaaaaa3")
    assert ws_a.readiness_score == 85
    assert ws_a.actual_monthly_cost is not None
    # History is chronological (oldest first), capped to 50.
    assert [h.run_id for h in ws_a.history][0].endswith("aaaaaaa1")
    assert ws_a.history[-1].run_id.endswith("aaaaaaa3")

    ws_b = by_name["ws-b"]
    assert ws_b.blocker_count == 2
    assert ws_b.subscription_id == "sub-b"


def test_estate_index_top_blockers_aggregate_workspace_count(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _make_run(runs_dir, run_id=_id("20260501T120000Z", "aaaaaaa1"),
              workspace_name="ws-a", blockers=["mv-redo", "tsql-merge"])
    _make_run(runs_dir, run_id=_id("20260501T120000Z", "bbbbbbb1"),
              workspace_name="ws-b", subscription_id="sub-b",
              resource_group="rg-b", blockers=["mv-redo"])

    idx = EstateIndex(FilesystemRunRepo(runs_dir))
    report = idx.build()
    titles = {b.title: b for b in report.top_blockers}
    assert titles["mv-redo"].workspaces == 2
    assert titles["mv-redo"].occurrences == 2
    assert titles["tsql-merge"].workspaces == 1
    # Highest workspace-count blocker comes first.
    assert report.top_blockers[0].title == "mv-redo"


def test_estate_index_caches_and_invalidates_on_mtime(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    rd = _make_run(runs_dir, run_id=_id("20260501T120000Z", "aaaaaaa1"),
                   workspace_name="ws-a", readiness_score=70)
    idx = EstateIndex(FilesystemRunRepo(runs_dir))
    r1 = idx.build()
    assert r1.workspaces[0].readiness_score == 70

    # Mutate run.json -> mtime changes -> rebuild picks it up.
    meta = json.loads((rd / "run.json").read_text())
    fm = json.loads((rd / "fabric_mapping.json").read_text())
    fm["readiness"]["score"] = 91
    (rd / "fabric_mapping.json").write_text(json.dumps(fm))
    # Touch run.json to bump mtime (a real run always rewrites it on
    # state transitions).
    (rd / "run.json").write_text(json.dumps(meta))
    r2 = idx.build()
    assert r2.workspaces[0].readiness_score == 91


def test_workspace_key_handles_missing_components() -> None:
    k = workspace_key(
        tenant_id=None, subscription_id="s", resource_group=None, workspace_name="w"
    )
    assert k == "s|w"


def test_get_estate_endpoint(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _make_run(runs_dir, run_id=_id("20260501T120000Z", "aaaaaaa1"),
              workspace_name="ws-a")
    app = create_app(runs_dir=runs_dir, env_file=tmp_path / ".env")
    with TestClient(app) as c:
        r = c.get("/api/estate")
        assert r.status_code == 200
        body = r.json()
        assert body["totals"]["workspaces"] == 1
        assert body["workspaces"][0]["workspace_name"] == "ws-a"

        # CSV export
        r = c.get("/api/estate/export.csv")
        assert r.status_code == 200
        assert "ws-a" in r.text
        assert r.headers["content-type"].startswith("text/csv")


def test_get_estate_workspace_endpoint(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _make_run(runs_dir, run_id=_id("20260501T120000Z", "aaaaaaa1"),
              workspace_name="ws-a")
    app = create_app(runs_dir=runs_dir, env_file=tmp_path / ".env")
    key = workspace_key(
        tenant_id="tenant-1", subscription_id="sub-a",
        resource_group="rg-a", workspace_name="ws-a",
    )
    with TestClient(app) as c:
        r = c.get(f"/api/estate/workspaces/{key}")
        assert r.status_code == 200
        assert r.json()["workspace_name"] == "ws-a"

        r = c.get("/api/estate/workspaces/does-not-exist")
        assert r.status_code == 404
