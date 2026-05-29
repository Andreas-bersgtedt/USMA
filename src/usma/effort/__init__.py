"""Public API for the configurable effort estimator.

The estimator turns a sequenced :mod:`fabric_mapping` runbook plus the
inputs the analyzer already collected into person-hour estimates
governed by a :class:`RateCard`.
"""
from __future__ import annotations

from .estimator import EffortBreakdown, EffortEstimate, build_estimates, estimate_for_step
from .rate_card import (
    DEFAULT_CARD,
    PhaseCoeffs,
    RateCard,
    RuleCoeffs,
    days_from_hours,
    default_card,
    dump_card,
    load_card,
    merge_with_default,
)
from .rollup import EffortRollup, PhaseEffortRollup, build_rollup

__all__ = [
    "DEFAULT_CARD",
    "EffortBreakdown",
    "EffortEstimate",
    "EffortRollup",
    "PhaseCoeffs",
    "PhaseEffortRollup",
    "RateCard",
    "RuleCoeffs",
    "build_estimates",
    "build_rollup",
    "days_from_hours",
    "default_card",
    "dump_card",
    "estimate_for_step",
    "load_card",
    "merge_with_default",
]
