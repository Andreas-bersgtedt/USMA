"""Collector for the top-N most-referenced tables/views on a dedicated pool.

Pipeline:
    1. Pull recent submitted commands from ``sys.dm_pdw_exec_requests``
       (``workload_commands.sql``).
    2. Pull the pool's table/view catalog from ``INFORMATION_SCHEMA``
       (``workload_catalog.sql``).
    3. Parse every *new* command with sqlglot and record each resolved
       table reference (qualified / unqualified-resolved / ambiguous).
    4. Merge those parsed requests into the per-pool workload cache on
       disk, prune entries older than ``ttl_days``, and write the cache
       back atomically.
    5. Aggregate ``(usage_count, elapsed_time_ms, match_kind)`` across
       the *full* cache window and return the top N entries plus a
       ``WorkloadCaptureStats`` health signal.

The collector queries its own catalog rather than reading
``analysis.tables`` so it can be reordered / unit-tested without
worrying about collector dependencies.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import TopConsumedObject, WorkloadCaptureStats
from ..sql_client import DedicatedPoolSqlClient
from ..workload_cache import (
    DEFAULT_TTL_DAYS,
    cache_bounds,
    cache_path,
    load as cache_load,
    merge as cache_merge,
    prune as cache_prune,
    save as cache_save,
)
from ..workload_parser import build_catalog, parse_command

log = logging.getLogger(__name__)

TOP_N = 50


def _coerce_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def collect_top_consumed_objects(
    sql: DedicatedPoolSqlClient,
    *,
    output_dir: Path | None = None,
    workspace_name: str | None = None,
    pool_name: str | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> tuple[list[TopConsumedObject], WorkloadCaptureStats]:
    """Return the top-N consumed objects and their capture-health stats."""
    commands = sql.fetch_query_file("workload_commands")
    catalog_rows = sql.fetch_query_file("workload_catalog")
    by_name, by_qualified = build_catalog(catalog_rows)

    stats = WorkloadCaptureStats(
        dmv_rows=len(commands),
        cache_window_days=ttl_days,
    )

    can_cache = bool(output_dir and workspace_name and pool_name)
    cache: dict[str, dict[str, Any]] = {}
    path: Path | None = None
    if can_cache:
        path = cache_path(Path(output_dir), workspace_name, pool_name)  # type: ignore[arg-type]
        cache = cache_load(path)

    # Parse only requests we haven't seen before.
    new_entries: list[dict[str, Any]] = []
    for row in commands:
        rid = row.get("request_id")
        if not rid:
            continue
        rid_str = str(rid)
        if rid_str in cache:
            continue
        command = row.get("command")
        if not command:
            continue
        refs, status = parse_command(str(command), by_name, by_qualified)
        if status == "ok":
            stats.parsed_ok += 1
        elif status == "failed":
            stats.parsed_failed += 1
        else:  # "empty"
            stats.parsed_empty += 1
        new_entries.append({
            "request_id": rid_str,
            "submit_time": str(row.get("submit_time") or ""),
            "elapsed_ms": _coerce_int(row.get("total_elapsed_time")),
            "status": str(row.get("status") or ""),
            "parse_status": status,
            "tables": [
                {
                    "schema_name": r.schema_name,
                    "object_name": r.object_name,
                    "object_type": r.object_type,
                    "match_kind": r.match_kind,
                }
                for r in refs
            ],
        })

    if can_cache and path is not None:
        cache = cache_merge(cache, new_entries)
        cache = cache_prune(cache, ttl_days=ttl_days)
        try:
            cache_save(path, workspace_name or "", pool_name or "", cache, ttl_days=ttl_days)
        except OSError as exc:  # noqa: BLE001
            log.warning("Could not save workload cache to %s: %s", path, exc)
    else:
        # No cache available — work with just this run's parsed entries.
        cache = {e["request_id"]: e for e in new_entries}

    stats.cache_requests_total = len(cache)
    oldest, newest = cache_bounds(cache)
    stats.oldest_cache_entry = oldest
    stats.newest_cache_entry = newest

    # Aggregate over the full cache window.
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in cache.values():
        elapsed = _coerce_int(entry.get("elapsed_ms"))
        for tref in entry.get("tables") or []:
            if not isinstance(tref, dict):
                continue
            schema = str(tref.get("schema_name") or "")
            name = str(tref.get("object_name") or "")
            if not schema or not name:
                continue
            otype = str(tref.get("object_type") or "table")
            mk = str(tref.get("match_kind") or "qualified")
            key = (schema.lower(), name.lower())
            bucket = agg.setdefault(key, {
                "schema": schema, "name": name, "type": otype,
                "count": 0, "elapsed": 0,
                "mk": {"qualified": 0, "unqualified-resolved": 0, "ambiguous": 0},
            })
            bucket["count"] += 1
            bucket["elapsed"] += elapsed
            if mk in bucket["mk"]:
                bucket["mk"][mk] += 1
            else:
                bucket["mk"][mk] = 1

    items: list[TopConsumedObject] = []
    for b in agg.values():
        mk_counts = b["mk"]
        if mk_counts.get("ambiguous", 0) > 0:
            worst = "ambiguous"
        elif (
            mk_counts.get("unqualified-resolved", 0) > 0
            and mk_counts.get("qualified", 0) == 0
        ):
            worst = "unqualified-resolved"
        else:
            worst = "qualified"
        items.append(TopConsumedObject(
            object_name=f"{b['schema']}.{b['name']}",
            object_type=b["type"],
            usage_count=int(b["count"]),
            elapsed_time_ms=int(b["elapsed"]),
            match_kind=worst,
        ))

    # Default ranking: elapsed time desc, then usage count desc.
    items.sort(key=lambda x: (-x.elapsed_time_ms, -x.usage_count, x.object_name))
    return items[:TOP_N], stats
