"""Progress reporting primitives for analyzer modules.

A :class:`ProgressReporter` is a tiny, dependency-free bridge that lets each
analyzer publish how many sub-steps it has completed and how many are still
ahead. The CLI wires up :class:`NullProgress` (a no-op) so command-line
behavior is unchanged. The web :mod:`~.web.jobs` runner wires up a
:class:`CallbackProgress` that fans events out to the SSE channel and the
SPA Run page renders them as a determinate progress bar.

Reporters are thread-safe: dedicated_pools / serverless_pools / storage /
monitoring all parallelize across pools and call ``step()`` from worker
threads concurrently.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class ProgressReporter:
    """Base reporter — does nothing. Subclasses override the four hooks."""

    # -- mutation -------------------------------------------------------
    def start(self, total: int, *, label: str = "") -> None:
        """Declare the initial total and (re)set ``current`` to 0."""

    def step(self, n: int = 1, *, label: str = "") -> None:
        """Advance ``current`` by ``n``. ``label`` describes the sub-step."""

    def add_total(self, n: int) -> None:
        """Grow ``total`` mid-run when discovery uncovers more work."""

    def message(self, text: str) -> None:
        """Emit a status message without changing counters."""

    # -- introspection (used by tests + the CLI smoke harness) ----------
    @property
    def current(self) -> int:  # pragma: no cover - abstract default
        return 0

    @property
    def total(self) -> int:  # pragma: no cover
        return 0


class NullProgress(ProgressReporter):
    """No-op reporter used by the CLI and tests. Zero-cost."""

    __slots__ = ()


class CallbackProgress(ProgressReporter):
    """Forwards every state change to a callable.

    The callable receives a snapshot dict::

        {
            "current": int,
            "total": int,
            "label": str,           # last seen step or start label
            "message": str | None,  # latest message() text, if any
        }

    Mutations (start / step / add_total / message) are guarded by an internal
    lock so concurrent worker threads cannot interleave updates. The callback
    itself runs **inside** the lock; keep it cheap (e.g. enqueue an event)
    and never re-enter the reporter from it.
    """

    __slots__ = ("_on_event", "_lock", "_current", "_total", "_label", "_message")

    def __init__(self, on_event: Callable[[dict[str, Any]], None]) -> None:
        self._on_event = on_event
        self._lock = threading.Lock()
        self._current = 0
        self._total = 0
        self._label = ""
        self._message: str | None = None

    @property
    def current(self) -> int:
        with self._lock:
            return self._current

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "current": self._current,
            "total": self._total,
            "label": self._label,
            "message": self._message,
        }

    def start(self, total: int, *, label: str = "") -> None:
        with self._lock:
            self._current = 0
            self._total = max(0, int(total))
            self._label = label
            self._message = None
            snap = self._snapshot_locked()
        self._safe_emit(snap)

    def step(self, n: int = 1, *, label: str = "") -> None:
        with self._lock:
            self._current += int(n)
            if label:
                self._label = label
            snap = self._snapshot_locked()
        self._safe_emit(snap)

    def add_total(self, n: int) -> None:
        with self._lock:
            self._total += int(n)
            snap = self._snapshot_locked()
        self._safe_emit(snap)

    def message(self, text: str) -> None:
        with self._lock:
            self._message = text
            snap = self._snapshot_locked()
        self._safe_emit(snap)

    def _safe_emit(self, snap: dict[str, Any]) -> None:
        try:
            self._on_event(snap)
        except Exception:  # noqa: BLE001 - never let UI plumbing crash a run
            pass


__all__ = ["ProgressReporter", "NullProgress", "CallbackProgress"]
