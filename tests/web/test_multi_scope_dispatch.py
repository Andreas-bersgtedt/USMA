"""Phase 2.7 — multi-scope dispatch in :class:`JobRunner._run`.

When a run is started with more than one scope on ``StartRunRequest.scopes``
each selected module must execute once per scope, with its artefacts
landing in a ``<source_type>__<slug>`` sub-directory under ``run_dir``.
Single-scope runs continue to write to the flat ``run_dir`` so existing
loaders / diff / carry-forward behavior is unaffected.

These tests use ``SimpleNamespace`` fake config + an in-memory fake
module factory so we don't touch Azure SDKs.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from usma.web.jobs import (
    JobRunner,
    _cfg_for_scope,
    _is_multi_scope,
    _scope_slug,
)
from usma.web.schemas import RunMeta, ScopeRef
from usma.web.storage import FilesystemRunRepo


def _fake_loader(_path: Path | None):  # noqa: ANN202
    return SimpleNamespace(
        azure=SimpleNamespace(
            tenant_id="t", client_id="c", subscription_id="s",
            resource_group="rg-default", workspace_name="ws-default", dedicated_pool=None,
        ),
        sql=SimpleNamespace(odbc_driver="d"),
    )


def _scope_aware_factory(name: str):
    """Fake module factory that records the cfg.azure identity it saw.

    The writer drops a per-output JSON file capturing the source-type
    env + workspace name so the test can verify per-scope dispatch
    landed each invocation in its own directory with its own identity.
    """

    def make(cfg, progress):  # noqa: ANN001
        progress.start(1, label="phase-init")
        progress.step(label="done")
        captured = {
            "module": name,
            "source_type_env": os.environ.get("SMA_SOURCE_TYPE"),
            "workspace_name": getattr(cfg.azure, "workspace_name", None),
            "resource_group": getattr(cfg.azure, "resource_group", None),
        }

        def writer(_result, out_dir, formats):  # noqa: ANN001
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            p = Path(out_dir) / f"{name}.json"
            p.write_text(json.dumps(captured), encoding="utf-8")
            return [p]

        return captured, writer

    return make


# ---------------------------------------------------------------------------
# Unit-level: helper functions
# ---------------------------------------------------------------------------


def test_scope_slug_is_filesystem_safe():
    scope = ScopeRef(source_type="synapse_workspace", id="x", display_name="My Synapse WS")
    assert _scope_slug(scope) == "my-synapse-ws"


def test_scope_slug_falls_back_to_id():
    scope = ScopeRef(source_type="adf", id="some-factory-id", display_name="")
    assert _scope_slug(scope) == "some-factory-id"


def test_cfg_for_scope_swaps_identity(tmp_path):
    cfg = _fake_loader(None)
    scope = ScopeRef(
        source_type="adf",
        id="/sub/x/factories/f",
        display_name="adf-factory-1",
        resource_group="rg-adf",
        extras={"factory_name": "adf-factory-1"},
    )
    scope_dir = tmp_path / "adf__adf-factory-1"
    new_cfg = _cfg_for_scope(cfg, scope, scope_dir)
    assert new_cfg.azure.workspace_name == "adf-factory-1"
    assert new_cfg.azure.resource_group == "rg-adf"
    assert new_cfg.output_dir == scope_dir
    # Original cfg untouched.
    assert cfg.azure.workspace_name == "ws-default"
    assert cfg.azure.resource_group == "rg-default"


def test_is_multi_scope_threshold():
    one = RunMeta(
        id="r1", status="queued", started_at=__import__("datetime").datetime.now(),
        config_hash="x",
        scopes=[ScopeRef(source_type="synapse_workspace", id="a", display_name="a")],
    )
    two = RunMeta(
        id="r2", status="queued", started_at=__import__("datetime").datetime.now(),
        config_hash="x",
        scopes=[
            ScopeRef(source_type="synapse_workspace", id="a", display_name="a"),
            ScopeRef(source_type="adf", id="b", display_name="b"),
        ],
    )
    assert _is_multi_scope(one) is False
    assert _is_multi_scope(two) is True


# ---------------------------------------------------------------------------
# Integration-level: full JobRunner._run multi-scope dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_scope_run_writes_per_scope_subdirs(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    runner = JobRunner(
        repo,
        env_file=tmp_path / ".env",
        dispatch={"dedicated_pools": _scope_aware_factory("dedicated_pools")},
        load_cfg=_fake_loader,
    )

    scopes = [
        ScopeRef(
            source_type="synapse_workspace",
            id="/subscriptions/s/resourceGroups/rg1/providers/Microsoft.Synapse/workspaces/syn-1",
            display_name="syn-1",
            resource_group="rg1",
        ),
        ScopeRef(
            source_type="adf",
            id="/subscriptions/s/resourceGroups/rg2/providers/Microsoft.DataFactory/factories/adf-1",
            display_name="adf-1",
            resource_group="rg2",
            extras={"factory_name": "adf-1"},
        ),
    ]
    meta = await runner.start(["dedicated_pools"], label="multi", scopes=scopes)

    # Wait for completion.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        final = repo.get(meta.id)
        if final and final.status in {"ok", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.05)
    final = repo.get(meta.id)
    assert final is not None
    assert final.status == "ok", final

    run_dir = repo.run_dir(meta.id)
    syn_dir = run_dir / "synapse_workspace__syn-1"
    adf_dir = run_dir / "adf__adf-1"
    assert syn_dir.is_dir()
    assert adf_dir.is_dir()

    syn_payload = json.loads((syn_dir / "dedicated_pools.json").read_text(encoding="utf-8"))
    adf_payload = json.loads((adf_dir / "dedicated_pools.json").read_text(encoding="utf-8"))

    assert syn_payload["workspace_name"] == "syn-1"
    assert syn_payload["resource_group"] == "rg1"
    assert syn_payload["source_type_env"] == "synapse_workspace"

    assert adf_payload["workspace_name"] == "adf-1"
    assert adf_payload["resource_group"] == "rg2"
    assert adf_payload["source_type_env"] == "adf"

    # Legacy flat-layout artefact must NOT exist for multi-scope runs.
    assert not (run_dir / "dedicated_pools.json").exists()


@pytest.mark.asyncio
async def test_single_scope_run_keeps_flat_layout(tmp_path: Path) -> None:
    """Backward-compat: ``len(scopes) <= 1`` keeps the legacy flat layout."""
    repo = FilesystemRunRepo(tmp_path / "runs")
    runner = JobRunner(
        repo,
        env_file=tmp_path / ".env",
        dispatch={"dedicated_pools": _scope_aware_factory("dedicated_pools")},
        load_cfg=_fake_loader,
    )

    meta = await runner.start(["dedicated_pools"], label="single")

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        final = repo.get(meta.id)
        if final and final.status in {"ok", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.05)
    final = repo.get(meta.id)
    assert final is not None
    assert final.status == "ok"

    run_dir = repo.run_dir(meta.id)
    # Flat layout: artefact directly under run_dir, no scope subdir.
    assert (run_dir / "dedicated_pools.json").exists()
    assert not any(p.is_dir() and "__" in p.name for p in run_dir.iterdir())


@pytest.mark.asyncio
async def test_env_restored_after_multi_scope_run(tmp_path: Path, monkeypatch) -> None:
    """``SMA_SOURCE_TYPE`` must be restored to its pre-run value."""
    monkeypatch.setenv("SMA_SOURCE_TYPE", "synapse_workspace")
    repo = FilesystemRunRepo(tmp_path / "runs")
    runner = JobRunner(
        repo,
        env_file=tmp_path / ".env",
        dispatch={"dedicated_pools": _scope_aware_factory("dedicated_pools")},
        load_cfg=_fake_loader,
    )

    scopes = [
        ScopeRef(source_type="adf", id="/x/a", display_name="a"),
        ScopeRef(source_type="adf", id="/x/b", display_name="b"),
    ]
    meta = await runner.start(["dedicated_pools"], scopes=scopes)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        final = repo.get(meta.id)
        if final and final.status in {"ok", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.05)

    assert os.environ.get("SMA_SOURCE_TYPE") == "synapse_workspace"
