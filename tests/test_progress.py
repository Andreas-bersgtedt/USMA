"""Unit tests for :mod:`usma.progress`."""
from __future__ import annotations

import threading

from usma.progress import (
    CallbackProgress,
    NullProgress,
    ProgressReporter,
)


def test_null_progress_is_a_noop() -> None:
    p = NullProgress()
    assert isinstance(p, ProgressReporter)
    # All four hooks must accept their canonical args without raising.
    p.start(10, label="phase 1")
    p.step()
    p.step(2, label="thing")
    p.add_total(5)
    p.message("hello")


def test_callback_progress_emits_snapshots() -> None:
    events: list[dict] = []
    p = CallbackProgress(events.append)

    p.start(3, label="boot")
    p.step(label="a")
    p.step(label="b")
    p.add_total(2)
    p.step(label="c")
    p.message("almost done")
    p.step(label="d")
    p.step(label="e")

    # Every public hook produced exactly one snapshot.
    assert [e["current"] for e in events] == [0, 1, 2, 2, 3, 3, 4, 5]
    assert [e["total"] for e in events] == [3, 3, 3, 5, 5, 5, 5, 5]
    assert events[0]["label"] == "boot"
    assert events[1]["label"] == "a"
    # ``message`` carries forward on subsequent emits until reset.
    assert events[5]["message"] == "almost done"
    assert events[6]["message"] == "almost done"


def test_callback_progress_is_thread_safe() -> None:
    events: list[dict] = []
    p = CallbackProgress(events.append)
    p.start(0, label="parallel")

    n_workers = 8
    per_worker = 50
    barrier = threading.Barrier(n_workers)
    p.add_total(n_workers * per_worker)

    def worker() -> None:
        barrier.wait()
        for _ in range(per_worker):
            p.step()

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Final state matches what each worker contributed — no lost updates.
    assert p.current == n_workers * per_worker
    assert p.total == n_workers * per_worker


def test_callback_progress_swallows_callback_errors() -> None:
    """A buggy callback must never abort the analyzer."""

    def bad_cb(_event: dict) -> None:
        raise RuntimeError("boom")

    p = CallbackProgress(bad_cb)
    # Must not raise.
    p.start(2, label="x")
    p.step()
    p.message("hi")
    assert p.current == 1
