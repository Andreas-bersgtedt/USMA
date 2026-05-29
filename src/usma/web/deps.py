"""Shared dependencies + app state for the FastAPI app."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from fastapi import Depends

if TYPE_CHECKING:
    from .jobs import JobRunner
    from .storage import FilesystemRunRepo


@dataclass
class AppState:
    """Process-wide state held by the FastAPI app.

    Lazily constructs the run repo + job runner so unit tests can
    swap in fakes without touching disk.
    """

    runs_dir: Path
    env_file: Path
    _lock: RLock = field(default_factory=RLock, repr=False)
    _repo: "FilesystemRunRepo | None" = field(default=None, repr=False)
    _runner: "JobRunner | None" = field(default=None, repr=False)
    _estate: "object | None" = field(default=None, repr=False)

    @property
    def repo(self) -> "FilesystemRunRepo":
        with self._lock:
            if self._repo is None:
                from .storage import FilesystemRunRepo

                self._repo = FilesystemRunRepo(self.runs_dir)
            return self._repo

    @property
    def runner(self) -> "JobRunner":
        with self._lock:
            if self._runner is None:
                from .jobs import JobRunner

                self._runner = JobRunner(self.repo, env_file=self.env_file)
            return self._runner

    @property
    def estate(self):  # type: ignore[override]
        with self._lock:
            if self._estate is None:
                from .estate import EstateIndex

                self._estate = EstateIndex(self.repo)
            return self._estate


def get_state() -> AppState:  # overridden via app.dependency_overrides
    raise RuntimeError("AppState not configured; create_app must be used.")


# Convenience aliases used by route signatures.
AppStateDep = Depends(get_state)
