"""Run-attribution migration endpoints.

Wraps :func:`usma.web.jobs.migrate_run_attribution` for the SPA's
Configuration page so users can rewrite legacy ``run.json`` files that
mis-attributed non-Azure scopes to whatever Azure tenant/subscription
was sitting in ``.env`` at the time. ``GET`` is a dry-run probe so the
button can show/hide itself; ``POST`` rewrites.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import AppState, get_state
from ..jobs import migrate_run_attribution

router = APIRouter()


class MigrationStatus(BaseModel):
    needed: bool
    pending: list[str]
    skipped_count: int
    errors: list[str]


class MigrationResult(BaseModel):
    updated: list[str]
    skipped_count: int
    errors: list[str]


@router.get("/run-attribution", response_model=MigrationStatus)
def get_status(state: AppState = Depends(get_state)) -> MigrationStatus:
    result = migrate_run_attribution(state.runs_dir, dry_run=True)
    return MigrationStatus(
        needed=bool(result["updated"]),
        pending=result["updated"],
        skipped_count=len(result["skipped"]),
        errors=result["errors"],
    )


@router.post("/run-attribution", response_model=MigrationResult)
def run_migration(state: AppState = Depends(get_state)) -> MigrationResult:
    result = migrate_run_attribution(state.runs_dir, dry_run=False)
    return MigrationResult(
        updated=result["updated"],
        skipped_count=len(result["skipped"]),
        errors=result["errors"],
    )
