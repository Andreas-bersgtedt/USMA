"""Rollup helpers turning per-step estimates into a project-level summary."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from ..modules.fabric_mapping.models import RunbookStep
from .estimator import EffortEstimate

# v2.11 — default parallel worker count used for the critical-path projection.
# Two workers reflects how most migrations actually run: a lead engineer plus
# one or two contributors per phase. The number is exposed in the JSON so
# downstream consumers can recompute with their own team size.
DEFAULT_PARALLEL_WORKERS = 2


@dataclass(frozen=True)
class PhaseEffortRollup:
    phase: str
    p50_hours: float
    p90_hours: float
    step_count: int
    # v2.11 — critical-path / parallel projections. With N workers, the
    # phase finishes in max(longest_step, total_hours / N) — whichever is
    # the bottleneck. ``max_step_*`` is the longest single step in the
    # phase and represents the absolute floor regardless of team size.
    parallel_p50_hours: float = 0.0
    parallel_p90_hours: float = 0.0
    max_step_p50_hours: float = 0.0
    max_step_p90_hours: float = 0.0


@dataclass(frozen=True)
class EffortRollup:
    total_p50_hours: float
    total_p90_hours: float
    per_phase: list[PhaseEffortRollup] = field(default_factory=list)
    card_source: str = "default"
    card_version: int = 1
    # v2.11 — project-level critical-path numbers. ``parallel_*`` sum the
    # per-phase critical-path values (phases stay sequential between each
    # other; only steps within a phase parallelise).
    parallel_p50_hours: float = 0.0
    parallel_p90_hours: float = 0.0
    parallel_workers: int = DEFAULT_PARALLEL_WORKERS

    def to_dict(self) -> dict:
        return {
            "total_p50_hours": round(self.total_p50_hours, 2),
            "total_p90_hours": round(self.total_p90_hours, 2),
            "parallel_p50_hours": round(self.parallel_p50_hours, 2),
            "parallel_p90_hours": round(self.parallel_p90_hours, 2),
            "parallel_workers": self.parallel_workers,
            "per_phase": [
                {
                    "phase": p.phase,
                    "p50_hours": round(p.p50_hours, 2),
                    "p90_hours": round(p.p90_hours, 2),
                    "step_count": p.step_count,
                    "parallel_p50_hours": round(p.parallel_p50_hours, 2),
                    "parallel_p90_hours": round(p.parallel_p90_hours, 2),
                    "max_step_p50_hours": round(p.max_step_p50_hours, 2),
                    "max_step_p90_hours": round(p.max_step_p90_hours, 2),
                }
                for p in self.per_phase
            ],
            "card_source": self.card_source,
            "card_version": self.card_version,
        }


def build_rollup(
    steps: Iterable[RunbookStep],
    estimates: Iterable[EffortEstimate],
    *,
    card_source: str,
    card_version: int,
    parallel_workers: int = DEFAULT_PARALLEL_WORKERS,
) -> EffortRollup:
    """Sum estimates by phase and overall, preserving phase order.

    Also computes the critical-path projection: with ``parallel_workers``
    people working a phase, the phase finishes in
    ``max(longest_step, total_hours / parallel_workers)``. Phases stay
    sequential between each other.
    """
    workers = max(1, parallel_workers)
    estimates_by_order = {e.step_order: e for e in estimates}
    phase_p50: dict[str, float] = defaultdict(float)
    phase_p90: dict[str, float] = defaultdict(float)
    phase_count: dict[str, int] = defaultdict(int)
    phase_max_p50: dict[str, float] = defaultdict(float)
    phase_max_p90: dict[str, float] = defaultdict(float)
    phase_order: list[str] = []

    for step in steps:
        if step.phase not in phase_count:
            phase_order.append(step.phase)
        est = estimates_by_order.get(step.order)
        if est is None:
            continue
        phase_p50[step.phase] += est.p50_hours
        phase_p90[step.phase] += est.p90_hours
        phase_count[step.phase] += 1
        if est.p50_hours > phase_max_p50[step.phase]:
            phase_max_p50[step.phase] = est.p50_hours
        if est.p90_hours > phase_max_p90[step.phase]:
            phase_max_p90[step.phase] = est.p90_hours

    per_phase = [
        PhaseEffortRollup(
            phase=p,
            p50_hours=round(phase_p50[p], 2),
            p90_hours=round(phase_p90[p], 2),
            step_count=phase_count[p],
            parallel_p50_hours=round(
                max(phase_max_p50[p], phase_p50[p] / workers), 2
            ),
            parallel_p90_hours=round(
                max(phase_max_p90[p], phase_p90[p] / workers), 2
            ),
            max_step_p50_hours=round(phase_max_p50[p], 2),
            max_step_p90_hours=round(phase_max_p90[p], 2),
        )
        for p in phase_order
    ]
    total_p50 = round(sum(phase_p50.values()), 2)
    total_p90 = round(sum(phase_p90.values()), 2)
    parallel_p50 = round(sum(p.parallel_p50_hours for p in per_phase), 2)
    parallel_p90 = round(sum(p.parallel_p90_hours for p in per_phase), 2)
    return EffortRollup(
        total_p50_hours=total_p50,
        total_p90_hours=total_p90,
        per_phase=per_phase,
        card_source=card_source,
        card_version=card_version,
        parallel_p50_hours=parallel_p50,
        parallel_p90_hours=parallel_p90,
        parallel_workers=workers,
    )
