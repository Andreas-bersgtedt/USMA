"""Module results for a given run.

Returns the analyzer's per-module JSON file (e.g. ``dedicated_pools.json``).
The file path is built via ``FilesystemRunRepo.module_path`` which
validates the module name and resolves under ``runs_dir`` so a malicious
``../`` cannot escape.

Phase 2.7 — when a run is multi-scope the analyzer writes per-scope
artefacts to ``run_dir/<source_type>__<slug>/<module>.json``. Callers
can pass an optional ``?scope=<source_type>__<slug>`` query param to
fetch a specific scope's artefact; omitting it preserves the legacy
flat-layout lookup so single-scope runs and existing clients keep
working unchanged.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..deps import AppState, get_state
from ..schemas import KNOWN_MODULES

router = APIRouter()


@router.get("/{run_id}/modules/{module}")
def get_module(
    run_id: str,
    module: str,
    scope: str | None = Query(
        None,
        description=(
            "Optional per-scope sub-directory (``<source_type>__<slug>``) "
            "for multi-scope runs. Omit for single-scope / legacy layout."
        ),
    ),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    if module not in KNOWN_MODULES:
        raise HTTPException(status_code=404, detail=f"unknown module {module!r}")
    try:
        if scope:
            path = state.repo.scope_module_path(run_id, scope, module)
        else:
            path = state.repo.module_path(run_id, module)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        suffix = f" in scope {scope!r}" if scope else ""
        raise HTTPException(
            status_code=404,
            detail=f"{module}.json not produced for run {run_id}{suffix}",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"could not read {module}.json: {exc}") from exc
    return JSONResponse(content=data)


@router.get("/{run_id}/scopes")
def list_scopes(
    run_id: str,
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Enumerate per-scope sub-directories produced by a multi-scope run.

    Returns an empty list for single-scope (legacy flat-layout) runs;
    the SPA loader falls back to the un-scoped ``module_path`` route
    in that case. Each entry includes the directory name (suitable
    to pass back as ``?scope=`` on the per-module route), the
    ``source_type`` parsed from the prefix, the slug, and the list
    of module artefacts that exist in that sub-directory.
    """
    try:
        scopes = state.repo.list_scopes(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content={"run_id": run_id, "scopes": scopes})
