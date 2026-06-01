"""Session log viewer endpoints.

Exposes the in-process :class:`LogBufferHandler` so the SPA's
Configuration → Diagnostics panel can render recent log records
without going to disk. Process-local; restarts wipe the buffer.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from ..log_buffer import get_log_buffer

router = APIRouter()

_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


@router.get("")
def list_logs(
    level: str = Query("WARNING", description="Minimum level (DEBUG/INFO/WARNING/ERROR/CRITICAL)."),
    since_seq: int | None = Query(None, ge=0, description="Return only records with seq > since_seq."),
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    """Return buffered log records, newest last."""
    buf = get_log_buffer()
    min_level = _LEVEL_NAMES.get(level.upper(), logging.WARNING)
    records = buf.snapshot(min_level=min_level, since_seq=since_seq, limit=limit)
    return {
        "records": records,
        "capacity": buf.maxlen,
        "handler_level": logging.getLevelName(buf.level),
    }


@router.delete("")
def clear_logs() -> dict[str, int]:
    """Drop all buffered log records."""
    dropped = get_log_buffer().clear()
    return {"dropped": dropped}
