"""End-to-end smoke tests for the FastAPI app.

These tests intentionally avoid Azure: the run lifecycle test injects a
fake module dispatch table so it doesn't require any credentials.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from usma.web import create_app
from usma.web.deps import get_state
from usma.web.jobs import JobRunner
from usma.web.storage import FilesystemRunRepo


def _client(tmp_path: Path) -> TestClient:
    runs = tmp_path / "runs"
    env = tmp_path / ".env"
    app = create_app(runs_dir=runs, env_file=env)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Healthz + schema + middleware
# ---------------------------------------------------------------------------


def test_healthz(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body


def test_schema_list_and_known(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/schema")
        assert r.status_code == 200
        modules = r.json()["modules"]
        assert "dedicated_pools" in modules

        r = c.get("/api/schema/dedicated_pools")
        assert r.status_code == 200
        s = r.json()
        # JSON Schema essentials.
        assert "title" in s or "$defs" in s or "properties" in s
        assert s["x-sma-module"] == "dedicated_pools"


def test_schema_unknown_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/schema/not_a_module")
        assert r.status_code == 404


def test_schema_path_traversal_rejected(tmp_path: Path) -> None:
    """A '..' module name must not escape the registry."""
    with _client(tmp_path) as c:
        # FastAPI normalises '..' in path; the router rejects it as
        # unknown either way.
        r = c.get("/api/schema/..%2Fcli")
        assert r.status_code in (404, 400)


def test_state_changing_requires_marker(tmp_path: Path) -> None:
    """POST/PUT/DELETE without X-SMA-API: 1 must be 400."""
    with _client(tmp_path) as c:
        r = c.post("/api/runs", json={"modules": ["dedicated_pools"]})
        assert r.status_code == 400
        assert "X-SMA-API" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------


def test_config_roundtrip(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/config")
        assert r.status_code == 200
        body = r.json()
        assert body["azure"]["client_secret"] == "unset"
        assert body["env_file_exists"] is False

        # Write a new value.
        update = {
            "azure": {
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "client_id": "00000000-0000-0000-0000-000000000002",
                "subscription_id": "00000000-0000-0000-0000-000000000003",
                "resource_group": "rg",
                "workspace_name": "ws",
                "client_secret": "topsecret",
            },
        }
        r = c.put("/api/config", json=update, headers={"X-SMA-API": "1"})
        assert r.status_code == 200, r.text

        # Read back. The secret must be redacted.
        r = c.get("/api/config")
        body = r.json()
        assert body["azure"]["tenant_id"] == "00000000-0000-0000-0000-000000000001"
        assert body["azure"]["client_secret"] == "set"

        # Validate.
        r = c.post("/api/config/validate", headers={"X-SMA-API": "1"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True


# ---------------------------------------------------------------------------
# Run lifecycle (uses a fake module dispatch)
# ---------------------------------------------------------------------------


class _FakeAnalyzer:
    def __init__(self, name: str) -> None:
        self.name = name

    def run(self) -> dict:
        return {"module": self.name, "ok": True}


def _fake_dispatch():  # noqa: ANN202
    def factory(name: str):  # noqa: ANN202
        def make(_cfg, _progress=None):  # noqa: ANN202
            def writer(result, out_dir, formats):  # noqa: ANN001
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                p = Path(out_dir) / f"{name}.json"
                p.write_text(json.dumps(result), encoding="utf-8")
                return [p]
            return _FakeAnalyzer(name).run(), writer
        return make
    return {
        "dedicated_pools": factory("dedicated_pools"),
        "pipelines": factory("pipelines"),
    }


def _fake_loader(_path: Path | None):  # noqa: ANN202
    """Return a config-like object with the attributes hash_config touches."""
    from types import SimpleNamespace

    return SimpleNamespace(
        azure=SimpleNamespace(
            tenant_id="t", client_id="c", subscription_id="s",
            resource_group="rg", workspace_name="ws", dedicated_pool=None,
        ),
        sql=SimpleNamespace(odbc_driver="d"),
    )


def test_run_lifecycle(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)

    # Override the runner with our fake dispatch + loader.
    repo = FilesystemRunRepo(runs_dir)
    runner = JobRunner(repo, env_file=env_file, dispatch=_fake_dispatch(), load_cfg=_fake_loader)

    state = app.dependency_overrides[get_state]()
    state._repo = repo  # type: ignore[attr-defined]
    state._runner = runner  # type: ignore[attr-defined]

    with TestClient(app) as c:
        r = c.post(
            "/api/runs",
            json={"modules": ["dedicated_pools", "pipelines"], "label": "test"},
            headers={"X-SMA-API": "1"},
        )
        assert r.status_code == 202, r.text
        run_id = r.json()["id"]

        # Wait for completion (max 5s).
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            r = c.get(f"/api/runs/{run_id}")
            assert r.status_code == 200
            if r.json()["status"] in ("ok", "failed", "cancelled"):
                break
            time.sleep(0.05)
        meta = r.json()
        assert meta["status"] == "ok", meta
        assert all(m["state"] == "ok" for m in meta["modules"])

        # Module endpoint serves the per-module JSON.
        r = c.get(f"/api/runs/{run_id}/modules/dedicated_pools")
        assert r.status_code == 200
        assert r.json()["module"] == "dedicated_pools"

        # List shows the run.
        r = c.get("/api/runs")
        assert any(item["id"] == run_id for item in r.json())


def test_run_invalid_module(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)
    state = app.dependency_overrides[get_state]()
    state._repo = FilesystemRunRepo(runs_dir)  # type: ignore[attr-defined]
    state._runner = JobRunner(
        state._repo, env_file=env_file,
        dispatch=_fake_dispatch(), load_cfg=_fake_loader,
    )  # type: ignore[attr-defined]

    with TestClient(app) as c:
        r = c.post(
            "/api/runs",
            json={"modules": ["definitely_not_real"]},
            headers={"X-SMA-API": "1"},
        )
        assert r.status_code == 400


def test_run_id_path_traversal_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/runs/..%2Fetc")
        assert r.status_code in (400, 404)


@pytest.mark.parametrize("module", ["..", "dedicated_pools/../", "foo/bar", "FOO", "1abc", "abc-def"])
def test_module_path_traversal_rejected(tmp_path: Path, module: str) -> None:
    """The repo's module_path validator must reject anything but [a-z][a-z0-9_]*."""
    repo = FilesystemRunRepo(tmp_path / "runs")
    rid = repo.new_id()
    from datetime import datetime, timezone

    from usma.web.schemas import RunMeta

    repo.create(RunMeta(id=rid, status="ok", started_at=datetime.now(timezone.utc), config_hash="x"))
    with pytest.raises(ValueError):
        repo.module_path(rid, module)


def test_run_id_format_rejected(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    for bad in ("..", "../escape", "abc", "1234567T123456Z-deadbeef"):
        with pytest.raises(ValueError):
            repo._validate_id(bad)

def test_diff_endpoint_serialises_diff_entries(tmp_path: Path) -> None:
    """Regression: ManifestDiffEntry is a frozen dataclass, not a Pydantic model.

    The endpoint previously called `entry.model_dump_json()` which raised
    `AttributeError` and produced an HTTP 500.
    """
    runs_dir = tmp_path / "runs"
    env_file = tmp_path / ".env"
    app = create_app(runs_dir=runs_dir, env_file=env_file)
    state = app.dependency_overrides[get_state]()
    state._repo = FilesystemRunRepo(runs_dir)  # type: ignore[attr-defined]
    state._runner = JobRunner(
        state._repo, env_file=env_file,
        dispatch=_fake_dispatch(), load_cfg=_fake_loader,
    )  # type: ignore[attr-defined]

    with TestClient(app) as c:
        # Two consecutive runs so we have a base + head.
        run_ids: list[str] = []
        for _ in range(2):
            r = c.post(
                "/api/runs",
                json={"modules": ["dedicated_pools"]},
                headers={"X-SMA-API": "1"},
            )
            assert r.status_code == 202, r.text
            rid = r.json()["id"]
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                meta = c.get(f"/api/runs/{rid}").json()
                if meta["status"] in ("ok", "failed", "cancelled"):
                    break
                time.sleep(0.05)
            assert meta["status"] == "ok", meta
            run_ids.append(rid)

        head = run_ids[-1]
        r = c.get(f"/api/runs/{head}/diff")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["head"] == head
        # `delta` must be a list of plain dicts (JSON-serialisable).
        assert isinstance(body["delta"], list)
        for entry in body["delta"]:
            assert isinstance(entry, dict)
            assert "name" in entry and "status" in entry
