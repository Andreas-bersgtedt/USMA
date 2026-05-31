"""Regression test: crashed / stale runs must be deletable from the UI.

Repro: a run is marked ``running`` in ``run.json`` but no live worker is
behind it (the analyzer process was killed, or the server restarted before
status could be written). Before the fix, ``DELETE /api/runs/{id}/data``
returned 409 and the row was un-removable from RunsHistory.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from usma.web import create_app


def _client(tmp_path: Path) -> TestClient:
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    env = tmp_path / ".env"
    return TestClient(create_app(runs_dir=runs, env_file=env))


def _seed_stale_running_run(runs_dir: Path, run_id: str) -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "id": run_id,
                "status": "running",
                "started_at": "2026-05-27T12:48:43.105463Z",
                "modules": [
                    {"name": "databricks_workflows", "state": "running"},
                ],
                "config_hash": "deadbeef",
                "workspace_name": "DummyWorkspace",
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_delete_stale_running_run_succeeds(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_id = "20260527T124843Z-c4026b50"
    run_dir = _seed_stale_running_run(runs, run_id)
    assert run_dir.is_dir()

    with TestClient(create_app(runs_dir=runs, env_file=tmp_path / ".env")) as c:
        # Sanity: the run is visible and still flagged running.
        r = c.get(f"/api/runs/{run_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "running"

        # Delete — must succeed even though status == running, because no
        # live worker is registered with the in-process JobRunner.
        r = c.delete(
            f"/api/runs/{run_id}/data",
            headers={"X-SMA-API": "1"},
        )
        assert r.status_code == 200, r.text

        # Directory is gone, and a follow-up GET 404s.
        assert not run_dir.exists()
        r = c.get(f"/api/runs/{run_id}")
        assert r.status_code == 404
