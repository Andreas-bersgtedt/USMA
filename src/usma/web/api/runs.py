"""PR-2 stub: run lifecycle endpoints (POST/list/get/delete).

Implemented in PR-2; see the package PLAN_CONTROL_PLANE.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import AppState, get_state
from ..schemas import RunMeta, StartRunRequest, StartRunResponse

router = APIRouter()


@router.post("", response_model=StartRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    body: StartRunRequest,
    state: AppState = Depends(get_state),
) -> StartRunResponse:
    try:
        meta = await state.runner.start(
            body.modules, label=body.label, days=body.days,
            scopes=body.scopes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StartRunResponse(id=meta.id, status=meta.status)


@router.get("", response_model=list[RunMeta])
def list_runs(
    limit: int = 50,
    state: AppState = Depends(get_state),
) -> list[RunMeta]:
    return state.repo.list(limit=limit)


@router.get("/{run_id}", response_model=RunMeta)
def get_run(run_id: str, state: AppState = Depends(get_state)) -> RunMeta:
    try:
        meta = state.repo.get(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return meta


@router.delete("", status_code=status.HTTP_200_OK)
def delete_all_runs(state: AppState = Depends(get_state)) -> dict[str, object]:
    """Purge every run on disk, including stalled in-flight ones.

    Best-effort cancels any run still marked ``queued``/``running`` (a stale
    server restart can leave runs flagged as running with no live job behind
    them) and then deletes the on-disk directory regardless of status.
    Returns counts so the UI can surface how many runs were affected.
    """
    metas = state.repo.list(limit=10_000)
    cancelled = 0
    deleted = 0
    failed: list[str] = []
    for meta in metas:
        if meta.status in ("queued", "running"):
            try:
                if state.runner.cancel(meta.id):
                    cancelled += 1
            except Exception:  # noqa: BLE001 - best-effort
                pass
        try:
            if state.repo.delete(meta.id):
                deleted += 1
                try:
                    state.estate.invalidate(meta.id)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            failed.append(meta.id)
    return {
        "deleted": deleted,
        "cancelled": cancelled,
        "total": len(metas),
        "failed": failed,
    }


@router.delete("/{run_id}", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(run_id: str, state: AppState = Depends(get_state)) -> dict[str, bool]:
    try:
        state.repo.get(run_id)  # validates id
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cancelled = state.runner.cancel(run_id)
    return {"cancelled": cancelled}


@router.delete("/{run_id}/data")
def delete_run_data(run_id: str, state: AppState = Depends(get_state)) -> dict[str, bool]:
    """Purge a run's on-disk artefacts.

    Refuses while the run is still in flight — cancel first via
    ``DELETE /api/runs/{id}`` then call this endpoint to remove the data.
    """
    try:
        meta = state.repo.get(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if meta.status in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} is still {meta.status}; cancel before deleting",
        )
    deleted = state.repo.delete(run_id)
    try:
        state.estate.invalidate(run_id)
    except Exception:  # noqa: BLE001
        pass
    return {"deleted": deleted}
