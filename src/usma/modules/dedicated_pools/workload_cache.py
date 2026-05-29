"""On-disk cache of parsed workload requests, one file per dedicated pool.

The dedicated-pool exec-requests DMV is a rolling buffer — on a busy
pool it can roll over in minutes, which is why the historical
``top_consumed_objects`` results were inconsistent across runs. To get
a useful "what gets used the most" signal we persist every successfully
parsed request, keyed by ``request_id``, and re-aggregate on each run
across a configurable ageing window (default 30 days).

File layout::

    <output_dir>/.cache/dedicated_pools_workload/<workspace>__<pool>.json

The format is a small JSON document validated at load time. On any
parse error (manual edit, corruption, schema upgrade) we treat the
cache as empty rather than aborting — losing a cache file is annoying
but never blocks the run.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1
DEFAULT_TTL_DAYS = 30

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(value: str) -> str:
    """Make a workspace/pool name safe to embed in a filename."""
    cleaned = _SAFE_SEGMENT.sub("_", value or "").strip("._-")
    return cleaned or "unnamed"


def cache_path(output_dir: Path, workspace: str, pool: str) -> Path:
    """Resolve the on-disk cache file for ``(workspace, pool)``."""
    root = Path(output_dir) / ".cache" / "dedicated_pools_workload"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{_safe_segment(workspace)}__{_safe_segment(pool)}.json"


def _parse_dt(value: Any) -> datetime | None:
    """Parse a stored ISO-8601 string back to an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Load the cache file as a dict keyed by ``request_id``.

    Returns an empty dict on any failure — the caller treats first-run
    and "I deleted the file by hand" identically.
    """
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Workload cache %s unreadable, starting fresh: %s", path, exc)
        return {}
    if not isinstance(doc, dict):
        return {}
    if int(doc.get("version", 0)) != CACHE_SCHEMA_VERSION:
        log.info("Workload cache %s has unsupported version, discarding", path)
        return {}
    requests = doc.get("requests") or []
    out: dict[str, dict[str, Any]] = {}
    for entry in requests:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("request_id")
        if not rid:
            continue
        out[str(rid)] = entry
    return out


def merge(
    existing: dict[str, dict[str, Any]],
    new_entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge new parsed requests into the cache.

    Existing entries take precedence (we never re-parse a request whose
    id we've already seen). The function is pure-ish — it does mutate
    ``existing`` in place for performance but also returns it.
    """
    for entry in new_entries:
        rid = entry.get("request_id")
        if not rid:
            continue
        rid_str = str(rid)
        if rid_str in existing:
            continue
        existing[rid_str] = entry
    return existing


def prune(
    cache: dict[str, dict[str, Any]],
    ttl_days: int = DEFAULT_TTL_DAYS,
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Drop entries older than ``ttl_days``; returns the mutated cache."""
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=ttl_days)
    stale = []
    for rid, entry in cache.items():
        submit = _parse_dt(entry.get("submit_time"))
        if submit is None or submit < horizon:
            stale.append(rid)
    for rid in stale:
        cache.pop(rid, None)
    return cache


def save(
    path: Path,
    workspace: str,
    pool: str,
    cache: dict[str, dict[str, Any]],
    *,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> None:
    """Atomically write the cache to ``path``."""
    payload = {
        "version": CACHE_SCHEMA_VERSION,
        "workspace": workspace,
        "pool": pool,
        "ttl_days": ttl_days,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "requests": list(cache.values()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def cache_bounds(
    cache: dict[str, dict[str, Any]],
) -> tuple[datetime | None, datetime | None]:
    """Oldest and newest submit_time across the cache (UTC), or (None, None)."""
    oldest: datetime | None = None
    newest: datetime | None = None
    for entry in cache.values():
        submit = _parse_dt(entry.get("submit_time"))
        if submit is None:
            continue
        if oldest is None or submit < oldest:
            oldest = submit
        if newest is None or submit > newest:
            newest = submit
    return oldest, newest
