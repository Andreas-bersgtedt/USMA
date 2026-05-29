"""Tests for the v2.6.0 carry-forward feature.

When a control-plane run selects only a subset of modules, the runner
copies every other module's artefacts forward from the most recent
completed run with the same workspace identity. The carried module is
recorded on :class:`RunMeta` so the SPA can surface provenance.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from usma.web import create_app
from usma.web.deps import get_state
from usma.web.jobs import JobRunner
from usma.web.storage import FilesystemRunRepo


# ---------------------------------------------------------------------------
# Fake module dispatch (same shape as the runner expects)
# ---------------------------------------------------------------------------


def _factory(name: str):
    def make(_cfg, _progress=None):  # noqa: ANN202
        def writer(result, out_dir, formats):  # noqa: ANN001
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            paths: list[Path] = []
            for fmt in formats:
                ext = {"json": "json", "csv": "csv", "markdown": "md", "html": "html"}[fmt]
                p = Path(out_dir) / f"{name}.{ext}"
                if ext == "json":
                    p.write_text(json.dumps(result), encoding="utf-8")
                else:
                    p.write_text(f"{name} {fmt}", encoding="utf-8")
                paths.append(p)
            return paths
        return {"module": name, "ok": True}, writer
    return make


def _dispatch() -> dict:
    return {
        "dedicated_pools": _factory("dedicated_pools"),
        "pipelines": _factory("pipelines"),
        "storage": _factory("storage"),
        "spark_pools": _factory("spark_pools"),
    }


def _loader_for(workspace: str):
    def _load(_path):  # noqa: ANN202
        return SimpleNamespace(
            azure=SimpleNamespace(
                tenant_id="t-1",
                client_id="c-1",
                subscription_id="s-1",
                resource_group="rg-1",
                workspace_name=workspace,
                dedicated_pool=None,
            ),
            sql=SimpleNamespace(odbc_driver="d"),
        )
    return _load


def _run_and_wait(client: TestClient, modules: list[str], *, timeout: float = 5.0) -> str:
    r = client.post(
        "/api/runs",
        json={"modules": modules},
        headers={"X-SMA-API": "1"},
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/runs/{run_id}")
        if r.json()["status"] in ("ok", "failed", "cancelled"):
            return run_id
        time.sleep(0.05)
    pytest.fail(f"run {run_id} did not finish in {timeout}s")
    return run_id  # unreachable


def _install_runner(app, runs_dir: Path, env_file: Path, *, workspace: str) -> None:
    repo = FilesystemRunRepo(runs_dir)
    runner = JobRunner(
        repo, env_file=env_file,
        dispatch=_dispatch(), load_cfg=_loader_for(workspace),
    )
    state = app.dependency_overrides[get_state]()
    state._repo = repo  # type: ignore[attr-defined]
    state._runner = runner  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_carry_forward_copies_missing_modules_from_prior_run(tmp_path: Path) -> None:
    """Run A produces dedicated+pipelines; run B requests only storage.
    Run B's directory must contain all three module JSONs, with the
    dedicated+pipelines entries marked ``carried`` and pointing back to A.
    """
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)
    _install_runner(app, runs_dir, env_file, workspace="ws-1")

    with TestClient(app) as c:
        run_a = _run_and_wait(c, ["dedicated_pools", "pipelines"])
        run_b = _run_and_wait(c, ["storage"])

        # Run B's on-disk directory has all 3 artefacts.
        b_dir = runs_dir / run_b
        assert (b_dir / "dedicated_pools.json").exists()
        assert (b_dir / "pipelines.json").exists()
        assert (b_dir / "storage.json").exists()

        # Module endpoint serves the carried JSON.
        for module in ("dedicated_pools", "pipelines", "storage"):
            r = c.get(f"/api/runs/{run_b}/modules/{module}")
            assert r.status_code == 200, f"{module} missing on run B"

        meta = c.get(f"/api/runs/{run_b}").json()
        carried_from = meta["carried_from"]
        assert carried_from == {
            "dedicated_pools": run_a,
            "pipelines": run_a,
        }, carried_from
        by_name = {m["name"]: m for m in meta["modules"]}
        assert by_name["storage"]["state"] == "ok"
        assert by_name["dedicated_pools"]["state"] == "carried"
        assert by_name["dedicated_pools"]["carried_from_run_id"] == run_a
        assert by_name["pipelines"]["state"] == "carried"


def test_carry_forward_ignores_other_workspaces(tmp_path: Path) -> None:
    """Runs from a different workspace identity must not be carried over."""
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)

    _install_runner(app, runs_dir, env_file, workspace="ws-other")
    with TestClient(app) as c:
        _run_and_wait(c, ["dedicated_pools"])

    # Swap the runner to a different workspace identity.
    _install_runner(app, runs_dir, env_file, workspace="ws-1")
    with TestClient(app) as c:
        run_b = _run_and_wait(c, ["storage"])
        b_dir = runs_dir / run_b
        assert (b_dir / "storage.json").exists()
        assert not (b_dir / "dedicated_pools.json").exists()
        meta = c.get(f"/api/runs/{run_b}").json()
        assert meta["carried_from"] == {}


def test_carry_forward_skips_modules_being_refreshed(tmp_path: Path) -> None:
    """When the user re-runs a module, the prior version must NOT be
    carried in alongside it — the freshly-written version wins."""
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)
    _install_runner(app, runs_dir, env_file, workspace="ws-1")

    with TestClient(app) as c:
        _run_and_wait(c, ["dedicated_pools"])
        run_b = _run_and_wait(c, ["dedicated_pools"])
        meta = c.get(f"/api/runs/{run_b}").json()
        # No carry: dedicated_pools was in this run's selection.
        assert meta["carried_from"] == {}
        by_name = {m["name"]: m for m in meta["modules"]}
        assert by_name["dedicated_pools"]["state"] == "ok"


def test_carry_forward_skips_cancelled_or_running_prior_runs(tmp_path: Path) -> None:
    """A prior run that never reached ``ok``/``failed`` must not be a
    carry-forward source even if its artefact happens to be on disk."""
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    repo = FilesystemRunRepo(runs_dir)
    # Hand-craft a "running" RunMeta with an artefact on disk.
    from datetime import datetime, timezone

    from usma.web.schemas import ModuleStatus, RunMeta

    stale = RunMeta(
        id=repo.new_id(),
        status="running",  # never completed
        started_at=datetime.now(timezone.utc),
        config_hash="hash",
        modules=[ModuleStatus(name="dedicated_pools", state="running")],
        tenant_id="t-1", subscription_id="s-1",
        resource_group="rg-1", workspace_name="ws-1",
    )
    repo.create(stale)
    (runs_dir / stale.id / "dedicated_pools.json").write_text("{}", encoding="utf-8")

    # Now run something else for the same workspace.
    app = create_app(runs_dir=runs_dir, env_file=env_file)
    _install_runner(app, runs_dir, env_file, workspace="ws-1")
    with TestClient(app) as c:
        run_b = _run_and_wait(c, ["storage"])
        meta = c.get(f"/api/runs/{run_b}").json()
        assert meta["carried_from"] == {}, "should not carry from a non-terminal run"


def test_carry_forward_chain_collapses_to_original_source(tmp_path: Path) -> None:
    """Run A produces X. Run B carries X forward. Run C must point at
    A (the original producer), not at B."""
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)
    _install_runner(app, runs_dir, env_file, workspace="ws-1")

    with TestClient(app) as c:
        run_a = _run_and_wait(c, ["dedicated_pools"])
        _run_and_wait(c, ["pipelines"])  # run B: carries dedicated from A
        run_c = _run_and_wait(c, ["storage"])  # run C: carries both
        meta = c.get(f"/api/runs/{run_c}").json()
        # The dedicated_pools source MUST be A, not B.
        assert meta["carried_from"]["dedicated_pools"] == run_a
