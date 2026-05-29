"""Run-planning helper — Phase 1.

Given a set of user-selected modules and source scopes, return the
``(scope, module_spec)`` pairs that will actually execute, after
filtering out combinations where the module does not support the
scope's ``SourceType``.

Pure function with no I/O or Azure SDK imports — easy to unit-test and
safe to call from both the CLI and the FastAPI request handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..sources import SourceDescriptor
from .spec import MODULE_SPECS, ModuleSpec


@dataclass(frozen=True)
class PlannedTask:
    """A single (scope, module) work item the run executor will run."""

    scope: SourceDescriptor
    module: ModuleSpec


@dataclass(frozen=True)
class SkippedTask:
    """A (scope, module) combination skipped because of unsupported source."""

    scope: SourceDescriptor
    module_name: str
    reason: str


@dataclass(frozen=True)
class RunPlan:
    """Result of :func:`plan_run`."""

    tasks: tuple[PlannedTask, ...]
    skipped: tuple[SkippedTask, ...]
    unknown_modules: tuple[str, ...]

    def module_names(self) -> list[str]:
        """Distinct module names that will run (any scope)."""
        # Preserve MODULE_SPECS ordering for determinism.
        order = list(MODULE_SPECS)
        present = {t.module.name for t in self.tasks}
        return [n for n in order if n in present]

    def scopes(self) -> list[SourceDescriptor]:
        """Distinct scopes that will participate."""
        seen: dict[str, SourceDescriptor] = {}
        for t in self.tasks:
            seen.setdefault(t.scope.id, t.scope)
        return list(seen.values())


def plan_run(
    modules: Iterable[str],
    scopes: Iterable[SourceDescriptor],
) -> RunPlan:
    """Produce a deterministic, filtered execution plan.

    * Modules not in :data:`MODULE_SPECS` are returned in
      ``unknown_modules`` (callers decide whether to error or warn).
    * (scope, module) pairs where ``module.supports`` does not include
      ``scope.type`` are returned in ``skipped`` so the UI / log can
      explain to the user why a module didn't run.
    * Surviving pairs become ``tasks``, ordered by
      ``(scope-input-order, MODULE_SPECS-canonical-order)``.
    """
    scope_list = list(scopes)
    module_input = list(modules)

    unknown: list[str] = []
    valid_specs: list[ModuleSpec] = []
    # Filter unknown modules but preserve MODULE_SPECS canonical order.
    requested = set(module_input)
    for name in module_input:
        if name not in MODULE_SPECS:
            unknown.append(name)
    for name in MODULE_SPECS:  # canonical order
        if name in requested:
            valid_specs.append(MODULE_SPECS[name])

    tasks: list[PlannedTask] = []
    skipped: list[SkippedTask] = []
    for scope in scope_list:
        for spec in valid_specs:
            if spec.supports_source(scope.type):
                tasks.append(PlannedTask(scope=scope, module=spec))
            else:
                skipped.append(SkippedTask(
                    scope=scope,
                    module_name=spec.name,
                    reason=(
                        f"module '{spec.name}' does not yet support source "
                        f"type {scope.type.value!r}"
                    ),
                ))

    return RunPlan(
        tasks=tuple(tasks),
        skipped=tuple(skipped),
        unknown_modules=tuple(unknown),
    )


__all__ = ["PlannedTask", "SkippedTask", "RunPlan", "plan_run"]
