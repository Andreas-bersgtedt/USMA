"""Collector for the top-N slowest requests on a dedicated SQL pool."""
from __future__ import annotations

from ..models import TopQuery
from ..sql_client import DedicatedPoolSqlClient

# Command text can be very long (especially generated INSERT/MERGE batches).
# 4 KB is plenty for the dashboard preview + drill-down pane.
_COMMAND_TEXT_MAX_CHARS = 4000


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def collect_top_queries(sql: DedicatedPoolSqlClient) -> list[TopQuery]:
    """Return the top-100 longest-running requests from the last 14 days.

    Sourced from ``sys.dm_pdw_exec_requests`` (the dedicated pool's request
    history DMV). The DMV is a rolling buffer so older entries may already
    be evicted by the time we read it; that is expected.
    """
    rows = sql.fetch_query_file("top_queries")
    out: list[TopQuery] = []
    for r in rows:
        command_text = r.get("command_text")
        if command_text is not None:
            command_text = str(command_text)[:_COMMAND_TEXT_MAX_CHARS] or None
        out.append(
            TopQuery(
                request_id=_coerce_str(r.get("request_id")),
                session_id=_coerce_str(r.get("session_id")),
                status=_coerce_str(r.get("status")),
                submit_time=r.get("submit_time"),
                start_time=r.get("start_time"),
                end_time=r.get("end_time"),
                total_elapsed_ms=_coerce_int(r.get("total_elapsed_ms")),
                resource_class=_coerce_str(r.get("resource_class")),
                importance=_coerce_str(r.get("importance")),
                query_label=_coerce_str(r.get("query_label")),
                error_id=_coerce_str(r.get("error_id")),
                login_name=_coerce_str(r.get("login_name")),
                command_text=command_text,
            )
        )
    return out
