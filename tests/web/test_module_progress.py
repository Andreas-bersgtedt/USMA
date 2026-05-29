"""Tests for the ``module_progress`` SSE event flow."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from usma.web.jobs import JobRunner
from usma.web.storage import FilesystemRunRepo


def _fake_loader(_path: Path | None):  # noqa: ANN202
    return SimpleNamespace(
        azure=SimpleNamespace(
            tenant_id="t", client_id="c", subscription_id="s",
            resource_group="rg", workspace_name="ws", dedicated_pool=None,
        ),
        sql=SimpleNamespace(odbc_driver="d"),
    )


def _factory_with_progress(name: str):
    """Build a fake module factory that drives ``progress`` end-to-end."""

    def make(_cfg, progress):  # noqa: ANN001, ANN202
        progress.start(3, label="phase-init")
        progress.step(label="step-1")
        progress.step(label="step-2")
        progress.step(label="done")

        def writer(result, out_dir, formats):  # noqa: ANN001
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            p = Path(out_dir) / f"{name}.json"
            p.write_text(json.dumps({"ok": True}), encoding="utf-8")
            return [p]

        return {"module": name, "ok": True}, writer

    return make


@pytest.mark.asyncio
async def test_module_progress_events_emitted(tmp_path: Path) -> None:
    repo = FilesystemRunRepo(tmp_path / "runs")
    runner = JobRunner(
        repo,
        env_file=tmp_path / ".env",
        dispatch={"dedicated_pools": _factory_with_progress("dedicated_pools")},
        load_cfg=_fake_loader,
    )

    meta = await runner.start(["dedicated_pools"], label="t")

    # Subscribe before the worker thread fans out events.
    q = runner.subscribe(meta.id)

    # Wait for `done` (or timeout) while collecting events.
    events: list[dict] = []
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        events.append(ev)
        if ev.get("type") == "done":
            break
    runner.unsubscribe(meta.id, q)

    # Sanity: run finished cleanly.
    assert any(e.get("type") == "done" for e in events), events

    # At least one ``module_progress`` event reached the live subscriber.
    progress_events = [e for e in events if e.get("type") == "module_progress"]
    assert progress_events, [e.get("type") for e in events]
    last = progress_events[-1]
    assert last["module"] == "dedicated_pools"
    assert last["total"] == 3
    assert last["current"] == 3

    # Persisted RunMeta carries the latest snapshot for the module.
    final = repo.get(meta.id)
    assert final is not None
    ms = next(m for m in final.modules if m.name == "dedicated_pools")
    assert ms.progress is not None
    assert ms.progress.total == 3
    assert ms.progress.current == 3
