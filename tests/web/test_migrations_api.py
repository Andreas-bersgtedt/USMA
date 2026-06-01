"""API surface for the run-attribution migration endpoints."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from usma.web import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(runs_dir=tmp_path / "runs", env_file=tmp_path / ".env"))


def _write_bq_run(runs: Path, run_id: str) -> None:
    rd = runs / run_id
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(
        json.dumps({
            "id": run_id,
            "status": "ok",
            "started_at": "2025-01-01T00:00:00+00:00",
            "config_hash": "x",
            "modules": [],
            "tenant_id": "azure-t",
            "subscription_id": "azure-s",
            "resource_group": "rg",
            "workspace_name": "azws",
            "scopes": [{
                "source_type": "bigquery",
                "id": "gcp-proj",
                "display_name": "gcp-proj",
            }],
        }),
        encoding="utf-8",
    )


def test_status_reports_needed_when_legacy_bq_run_present(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    _write_bq_run(runs, "20250101T000000Z-aaaaaaaa")
    with _client(tmp_path) as c:
        r = c.get("/api/migrations/run-attribution")
        assert r.status_code == 200
        body = r.json()
        assert body["needed"] is True
        assert "20250101T000000Z-aaaaaaaa" in body["pending"]


def test_status_reports_not_needed_when_clean(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    with _client(tmp_path) as c:
        r = c.get("/api/migrations/run-attribution")
        assert r.status_code == 200
        body = r.json()
        assert body["needed"] is False
        assert body["pending"] == []


def test_post_migrates_and_clears_status(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    _write_bq_run(runs, "20250101T000000Z-bbbbbbbb")
    with _client(tmp_path) as c:
        r = c.post(
            "/api/migrations/run-attribution",
            headers={"X-SMA-API": "1"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated"] == ["20250101T000000Z-bbbbbbbb"]

        # Status should now report nothing pending.
        r = c.get("/api/migrations/run-attribution")
        assert r.json()["needed"] is False

        # File on disk has Azure identity stripped.
        meta = json.loads(
            (runs / "20250101T000000Z-bbbbbbbb" / "run.json").read_text(encoding="utf-8")
        )
        assert meta["tenant_id"] is None
        assert meta["subscription_id"] is None
        assert meta["workspace_name"] == "gcp-proj"


def test_post_requires_csrf_header(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    with _client(tmp_path) as c:
        r = c.post("/api/migrations/run-attribution")
        assert r.status_code == 400
