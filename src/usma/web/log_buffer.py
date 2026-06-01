"""In-process bounded log buffer for the local web control plane.

A :class:`logging.Handler` keeps the most recent ``maxlen`` records in a
thread-safe deque so the SPA's Diagnostics → Session log viewer can
poll them without any disk I/O. Records are pre-formatted (message +
traceback) at capture time; consumers receive plain dicts.

The buffer is process-local and cleared on restart; "session" here
means the lifetime of the current Python process.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

# Defaults chosen to comfortably cover a single analyzer run (typically
# < 500 records at INFO) without bounding process memory.
DEFAULT_MAX = 2000
DEFAULT_LEVEL = logging.WARNING


class LogBufferHandler(logging.Handler):
    """A :class:`logging.Handler` that retains records in a bounded deque."""

    def __init__(self, *, maxlen: int = DEFAULT_MAX, level: int = DEFAULT_LEVEL) -> None:
        super().__init__(level=level)
        self._records: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            msg = record.getMessage()
        except Exception as exc:  # noqa: BLE001 — never raise from a handler
            msg = f"<log message format error: {exc!r}>"
        exc_text: str | None = None
        if record.exc_info:
            try:
                exc_text = self.format(record).split(msg, 1)[-1].strip() or None
            except Exception:  # noqa: BLE001
                exc_text = None
            if not exc_text:
                # Fall back to the stdlib renderer.
                import traceback

                exc_text = "".join(traceback.format_exception(*record.exc_info)).strip()
        with self._lock:
            self._seq += 1
            self._records.append({
                "seq": self._seq,
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                "level": record.levelname,
                "level_no": record.levelno,
                "logger": record.name,
                "message": msg,
                "exc": exc_text,
            })

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------
    def snapshot(
        self,
        *,
        min_level: int | None = None,
        since_seq: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return a copy of the buffered records, newest last.

        ``min_level`` filters by numeric log level (e.g. ``logging.WARNING``).
        ``since_seq`` returns only records with ``seq > since_seq`` so the
        SPA can poll incrementally. ``limit`` caps the result size.
        """
        with self._lock:
            items = list(self._records)
        if since_seq is not None:
            items = [r for r in items if r["seq"] > since_seq]
        if min_level is not None:
            items = [r for r in items if r["level_no"] >= min_level]
        if limit is not None and limit >= 0:
            items = items[-limit:]
        return items

    def clear(self) -> int:
        """Drop all buffered records. Returns the number dropped."""
        with self._lock:
            n = len(self._records)
            self._records.clear()
            return n

    @property
    def maxlen(self) -> int:
        return self._records.maxlen or 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_singleton: LogBufferHandler | None = None
_singleton_lock = threading.Lock()


def get_log_buffer() -> LogBufferHandler:
    """Return the process-wide log buffer, creating it on first call."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = LogBufferHandler()
        return _singleton


def attach_to_root() -> LogBufferHandler:
    """Attach the singleton handler to the root logger (idempotent)."""
    handler = get_log_buffer()
    root = logging.getLogger()
    if handler not in root.handlers:
        root.addHandler(handler)
        # Ensure the root logger's effective level lets WARNING through
        # even when the CLI hasn't configured it (test contexts, library
        # use). We never *lower* the root level the caller already set.
        if root.level == logging.NOTSET or root.level > handler.level:
            root.setLevel(handler.level)
    return handler
