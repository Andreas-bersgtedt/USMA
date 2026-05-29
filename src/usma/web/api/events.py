"""Server-Sent Events stream for live run progress.

Replays buffered events on connect (resilient to reconnects) and then
forwards live ones until the run finishes.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..deps import AppState, get_state

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    state: AppState = Depends(get_state),
) -> EventSourceResponse:
    try:
        meta = state.repo.get(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    queue = state.runner.subscribe(run_id)

    async def gen():
        try:
            # Replay buffered events.
            for ev in state.repo.read_events(run_id):
                yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}
            # Then live ones.
            terminal = state.repo.get(run_id)
            if terminal is not None and terminal.status in ("ok", "failed", "cancelled"):
                # Already finished; send a final 'done' marker if the
                # ndjson didn't already include it.
                yield {
                    "event": "done",
                    "data": json.dumps({"type": "done", "status": terminal.status}),
                }
                return
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat so proxies / browsers don't kill the
                    # connection.
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}
                if ev.get("type") == "done":
                    return
        finally:
            state.runner.unsubscribe(run_id, queue)

    return EventSourceResponse(gen())
