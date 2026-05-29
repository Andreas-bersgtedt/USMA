"""Compute a delta between two runs by reusing the existing run_manifest
helpers. Returns the JSON envelope exactly as ``run_delta.json`` does
when produced by ``analyze-all``.

Phase 3 / multi-scope: when a run has per-scope sub-directories
(``<source_type>__<slug>/``), the diff walks those too so multi-scope
runs surface every per-scope artefact. Artefact names are
scope-prefixed (``synapse_workspace__foo/fabric_mapping``) so the
matcher in :func:`diff_manifests` correctly pairs same-scope artefacts
across runs and never collides on a bare ``fabric_mapping`` stem.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...reporting.run_manifest import (
    ArtifactRecord,
    ManifestDiffEntry,
    RunManifest,
    diff_manifests,
    diff_summary,
    hash_file,
    peek_record_count,
)
from ..deps import AppState, get_state
from ..storage import _SCOPE_DIR_RE


_RUN_META_FILES = {"run.json", "run_manifest.json"}


def _collect_artifacts(run_dir: Path) -> list[ArtifactRecord]:
    """Walk ``run_dir`` (and one level of scope sub-directories) and
    emit one :class:`ArtifactRecord` per ``*.json`` file.

    - Root-level artefacts keep their bare ``name`` (``fabric_mapping``),
      matching the legacy single-scope shape so existing diffs are
      byte-identical.
    - Scope-prefixed artefacts use ``<scope_dir>/<stem>`` as their name
      so they pair across runs without colliding with each other or
      with a root-level artefact of the same module.
    """
    now = datetime.now(timezone.utc)
    out: list[ArtifactRecord] = []
    for entry in sorted(run_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".json" and entry.name not in _RUN_META_FILES:
            sha, size = hash_file(entry)
            out.append(ArtifactRecord(
                name=entry.stem,
                file_name=entry.name,
                sha256=sha,
                size_bytes=size,
                generated_at=now,
                record_count=peek_record_count(entry),
            ))
            continue
        if not entry.is_dir():
            continue
        if not _SCOPE_DIR_RE.fullmatch(entry.name):
            continue
        # Per-scope artefacts — emit one record each, prefixed with the
        # scope directory so cross-run pairing is unique.
        for sub in sorted(entry.iterdir()):
            if not sub.is_file() or sub.suffix != ".json" or sub.name in _RUN_META_FILES:
                continue
            sha, size = hash_file(sub)
            out.append(ArtifactRecord(
                name=f"{entry.name}/{sub.stem}",
                file_name=f"{entry.name}/{sub.name}",
                sha256=sha,
                size_bytes=size,
                generated_at=now,
                record_count=peek_record_count(sub),
            ))
    return out


def _entry_to_dict(entry: ManifestDiffEntry) -> dict[str, Any]:
    """Serialise a ``ManifestDiffEntry`` (frozen dataclass) to a JSON-ready dict."""
    out = dataclasses.asdict(entry)
    for k, v in list(out.items()):
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    out["size_delta"] = entry.size_delta
    out["record_count_delta"] = entry.record_count_delta
    return out

router = APIRouter()


def _build_run_manifest(run_dir: Path) -> RunManifest:
    return RunManifest(
        workspace_name="",
        subscription_id="",
        resource_group="",
        generated_at=datetime.now(timezone.utc),
        sma_version="api",
        artifacts=_collect_artifacts(run_dir),
    )


@router.get("/{run_id}/diff")
def get_diff(
    run_id: str,
    base: str | None = None,
    state: AppState = Depends(get_state),
) -> dict:
    """Return a delta from ``base`` (defaults to the previous run) to ``run_id``.

    The base+head are looked up by id and their on-disk artefacts are
    walked (root + per-scope sub-directories). If only one run exists,
    returns an empty delta.
    """
    try:
        head_meta = state.repo.get(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if head_meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    if base is None:
        # Pick the most recent finished run *before* this one.
        all_runs = state.repo.list(limit=200)
        prev = next(
            (r for r in all_runs if r.id < run_id and r.status in ("ok", "failed")),
            None,
        )
        base_id = prev.id if prev else None
    else:
        try:
            base_meta = state.repo.get(base)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if base_meta is None:
            raise HTTPException(status_code=404, detail=f"run {base} not found")
        base_id = base

    head_manifest = _build_run_manifest(state.repo.run_dir(run_id))
    base_manifest = (
        _build_run_manifest(state.repo.run_dir(base_id)) if base_id else None
    )

    diff = diff_manifests(base_manifest, head_manifest)
    return {
        "base": base_id,
        "head": run_id,
        "summary": diff_summary(diff),
        "delta": [_entry_to_dict(entry) for entry in diff],
    }
