"""Phase 2.7-B — backend scope-aware artefact serving.

Covers:
- ``FilesystemRunRepo.scope_module_path`` validation + path safety
- ``FilesystemRunRepo.list_scopes`` enumeration
- ``GET /api/runs/{id}/scopes`` route
- ``GET /api/runs/{id}/modules/{module}?scope=<dir>`` route
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from usma.web.app import create_app
from usma.web.deps import get_state
from usma.web.jobs import JobRunner
from usma.web.schemas import ScopeRef
from usma.web.storage import FilesystemRunRepo


def _fake_loader(_path: Path | None):  # noqa: ANN202
    return SimpleNamespace(
        azure=SimpleNamespace(
            tenant_id="t", client_id="c", subscription_id="s",
            resource_group="rg", workspace_name="ws", dedicated_pool=None,
        ),
        sql=SimpleNamespace(odbc_driver="d"),
    )


def _fake_factory(name: str):
    def make(cfg, progress):  # noqa: ANN001
        progress.start(1, label="phase")
        progress.step(label="done")
        payload = {
            "module": name,
            "workspace_name": getattr(cfg.azure, "workspace_name", None),
        }

        def writer(_result, out_dir, formats):  # noqa: ANN001
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            p = Path(out_dir) / f"{name}.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            return [p]

        return payload, writer

    return make


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------


def test_scope_module_path_rejects_bad_scope(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    # Need a valid run-id pattern to get past _validate_id.
    run_id = "20260519T120000Z-abcdef12"
    (tmp_path / "runs" / run_id).mkdir(parents=True)

    with pytest.raises(ValueError, match="invalid scope dir"):
        repo.scope_module_path(run_id, "../escape", "dedicated_pools")
    with pytest.raises(ValueError, match="invalid scope dir"):
        repo.scope_module_path(run_id, "unknown_source__ws", "dedicated_pools")
    with pytest.raises(ValueError, match="invalid scope dir"):
        repo.scope_module_path(run_id, "synapse_workspace__", "dedicated_pools")


def test_scope_module_path_accepts_valid(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    run_id = "20260519T120000Z-abcdef12"
    (tmp_path / "runs" / run_id).mkdir(parents=True)

    path = repo.scope_module_path(run_id, "synapse_workspace__my-ws", "pipelines")
    expected_suffix = Path(run_id) / "synapse_workspace__my-ws" / "pipelines.json"
    assert str(path).endswith(str(expected_suffix))


def test_list_scopes_returns_empty_for_flat_run(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    run_id = "20260519T120000Z-abcdef12"
    rdir = tmp_path / "runs" / run_id
    rdir.mkdir(parents=True)
    (rdir / "dedicated_pools.json").write_text("{}", encoding="utf-8")

    assert repo.list_scopes(run_id) == []


def test_list_scopes_enumerates_subdirs(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    run_id = "20260519T120000Z-abcdef12"
    rdir = tmp_path / "runs" / run_id
    rdir.mkdir(parents=True)

    syn = rdir / "synapse_workspace__syn-1"
    syn.mkdir()
    (syn / "dedicated_pools.json").write_text("{}", encoding="utf-8")
    (syn / "pipelines.json").write_text("{}", encoding="utf-8")

    adf = rdir / "adf__factory-a"
    adf.mkdir()
    (adf / "pipelines.json").write_text("{}", encoding="utf-8")

    # Noise: a non-matching directory (e.g. a stray events log dir) is ignored.
    (rdir / "logs").mkdir()
    (rdir / "logs" / "junk.txt").write_text("noise", encoding="utf-8")

    scopes = repo.list_scopes(run_id)
    assert len(scopes) == 2
    by_dir = {s["dir"]: s for s in scopes}
    assert by_dir["adf__factory-a"]["source_type"] == "adf"
    assert by_dir["adf__factory-a"]["slug"] == "factory-a"
    assert by_dir["adf__factory-a"]["modules"] == ["pipelines"]
    assert by_dir["synapse_workspace__syn-1"]["modules"] == ["dedicated_pools", "pipelines"]


def test_list_scopes_unknown_run_returns_empty(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    # Valid pattern, but the directory does not exist.
    assert repo.list_scopes("20260519T120000Z-deadbeef") == []


def test_list_scopes_rejects_invalid_run_id(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    # Invalid id format short-circuits before any filesystem access.
    assert repo.list_scopes("../escape") == []


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


def _setup_multi_scope_app(tmp_path: Path):
    """Build a configured app + client; caller wraps in ``with TestClient(...)``."""
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)

    repo = FilesystemRunRepo(runs_dir)
    runner = JobRunner(
        repo, env_file=env_file,
        dispatch={"dedicated_pools": _fake_factory("dedicated_pools")},
        load_cfg=_fake_loader,
    )
    state = app.dependency_overrides[get_state]()
    state._repo = repo  # type: ignore[attr-defined]
    state._runner = runner  # type: ignore[attr-defined]
    return app


_MULTI_SCOPE_BODY = {
    "modules": ["dedicated_pools"],
    "label": "multi",
    "scopes": [
        {
            "source_type": "synapse_workspace",
            "id": "/subscriptions/s/resourceGroups/rg1/providers/Microsoft.Synapse/workspaces/syn-1",
            "display_name": "syn-1",
            "resource_group": "rg1",
        },
        {
            "source_type": "adf",
            "id": "/subscriptions/s/resourceGroups/rg2/providers/Microsoft.DataFactory/factories/adf-1",
            "display_name": "adf-1",
            "resource_group": "rg2",
            "extras": {"factory_name": "adf-1"},
        },
    ],
}


def _wait_for_ok(c: TestClient, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = c.get(f"/api/runs/{run_id}")
        last = r.json()
        if last["status"] in ("ok", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert last is not None and last["status"] == "ok", last


def test_get_scopes_endpoint_returns_per_scope_artefacts(tmp_path: Path) -> None:
    app = _setup_multi_scope_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/runs", json=_MULTI_SCOPE_BODY, headers={"X-SMA-API": "1"})
        assert r.status_code == 202, r.text
        run_id = r.json()["id"]
        _wait_for_ok(c, run_id)

        r = c.get(f"/api/runs/{run_id}/scopes")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["run_id"] == run_id
        dirs = sorted(s["dir"] for s in body["scopes"])
        assert dirs == ["adf__adf-1", "synapse_workspace__syn-1"]
        adf_entry = next(s for s in body["scopes"] if s["dir"] == "adf__adf-1")
        assert adf_entry["source_type"] == "adf"
        assert adf_entry["slug"] == "adf-1"
        assert adf_entry["modules"] == ["dedicated_pools"]


def test_get_module_with_scope_query_reads_per_scope_artefact(tmp_path: Path) -> None:
    app = _setup_multi_scope_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/runs", json=_MULTI_SCOPE_BODY, headers={"X-SMA-API": "1"})
        run_id = r.json()["id"]
        _wait_for_ok(c, run_id)

        r = c.get(
            f"/api/runs/{run_id}/modules/dedicated_pools",
            params={"scope": "adf__adf-1"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["workspace_name"] == "adf-1"

        r = c.get(
            f"/api/runs/{run_id}/modules/dedicated_pools",
            params={"scope": "synapse_workspace__syn-1"},
        )
        assert r.status_code == 200
        assert r.json()["workspace_name"] == "syn-1"


def test_get_module_unknown_scope_returns_404(tmp_path: Path) -> None:
    app = _setup_multi_scope_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/runs", json=_MULTI_SCOPE_BODY, headers={"X-SMA-API": "1"})
        run_id = r.json()["id"]
        _wait_for_ok(c, run_id)

        r = c.get(
            f"/api/runs/{run_id}/modules/dedicated_pools",
            params={"scope": "adf__does-not-exist"},
        )
        assert r.status_code == 404
        assert "adf__does-not-exist" in r.json()["detail"]


def test_get_module_invalid_scope_returns_400(tmp_path: Path) -> None:
    app = _setup_multi_scope_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/runs", json=_MULTI_SCOPE_BODY, headers={"X-SMA-API": "1"})
        run_id = r.json()["id"]
        _wait_for_ok(c, run_id)

        r = c.get(
            f"/api/runs/{run_id}/modules/dedicated_pools",
            params={"scope": "../escape"},
        )
        assert r.status_code == 400


def test_get_module_no_scope_param_keeps_legacy_lookup(tmp_path: Path) -> None:
    """Single-scope runs still respond on the un-scoped route shape."""
    app = _setup_multi_scope_app(tmp_path)
    with TestClient(app) as c:
        r = c.post(
            "/api/runs",
            json={"modules": ["dedicated_pools"], "label": "single"},
            headers={"X-SMA-API": "1"},
        )
        run_id = r.json()["id"]
        _wait_for_ok(c, run_id)

        # No ?scope= -> flat lookup.
        r = c.get(f"/api/runs/{run_id}/modules/dedicated_pools")
        assert r.status_code == 200
        assert r.json()["module"] == "dedicated_pools"

        # /scopes returns empty (no per-scope subdirs were written).
        r = c.get(f"/api/runs/{run_id}/scopes")
        assert r.status_code == 200
        assert r.json()["scopes"] == []


# ---------------------------------------------------------------------------
# Phase 3 — multi-scope diff
# ---------------------------------------------------------------------------


def test_diff_walks_scope_subdirs_for_multi_scope_runs(tmp_path: Path) -> None:
    """A diff between two multi-scope runs surfaces one entry per scope+module.

    Names are scope-prefixed (``synapse_workspace__syn-1/dedicated_pools``)
    so the matcher in ``diff_manifests`` pairs same-scope artefacts
    correctly and never collapses two scopes into a single colliding
    ``dedicated_pools`` row.
    """
    app = _setup_multi_scope_app(tmp_path)
    with TestClient(app) as c:
        run_ids: list[str] = []
        for i in range(2):
            if i > 0:
                # Run ids encode second-resolution timestamps; ensure the
                # base/head pair lands on distinct seconds so the diff's
                # auto-base lookup ("most recent finished run *before* this
                # one") deterministically resolves to the first run.
                time.sleep(1.1)
            r = c.post("/api/runs", json=_MULTI_SCOPE_BODY, headers={"X-SMA-API": "1"})
            assert r.status_code == 202, r.text
            rid = r.json()["id"]
            _wait_for_ok(c, rid)
            run_ids.append(rid)

        head = run_ids[-1]
        r = c.get(f"/api/runs/{head}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["head"] == head
        assert body["base"] == run_ids[0]

        names = sorted(entry["name"] for entry in body["delta"])
        # Both scopes show up, each scoped to its own dir prefix.
        assert "adf__adf-1/dedicated_pools" in names
        assert "synapse_workspace__syn-1/dedicated_pools" in names
        # No unprefixed name collision (would indicate the walker
        # collapsed scopes onto a single key).
        assert "dedicated_pools" not in names


def test_diff_legacy_single_scope_run_keeps_flat_names(tmp_path: Path) -> None:
    """Single-scope (flat) runs keep their bare ``<module>`` artefact name
    so existing diff consumers stay byte-identical.
    """
    app = _setup_multi_scope_app(tmp_path)
    with TestClient(app) as c:
        run_ids: list[str] = []
        for i in range(2):
            if i > 0:
                time.sleep(1.1)
            r = c.post(
                "/api/runs",
                json={"modules": ["dedicated_pools"], "label": "single"},
                headers={"X-SMA-API": "1"},
            )
            assert r.status_code == 202, r.text
            rid = r.json()["id"]
            _wait_for_ok(c, rid)
            run_ids.append(rid)

        r = c.get(f"/api/runs/{run_ids[-1]}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        names = sorted(entry["name"] for entry in body["delta"])
        assert names == ["dedicated_pools"]

