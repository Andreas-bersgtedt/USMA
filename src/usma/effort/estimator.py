"""Turn recommendations + module artefacts into per-step person-hour estimates.

The contract is intentionally simple: each :class:`RunbookStep` gets a
``p50`` / ``p90`` figure plus an explainable ``breakdown`` dict. The
breakdown is what makes the model auditable — every hour ultimately
comes from a coefficient in the :class:`RateCard` multiplied by a count
that came from the analyser's own artefacts.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..modules.fabric_mapping.models import Recommendation, RunbookStep
from .rate_card import RateCard


@dataclass(frozen=True)
class EffortBreakdown:
    """Human-readable explanation of one step's estimate."""
    area: str | None
    components: list[str] = field(default_factory=list)
    area_total_hours: float = 0.0
    phase_share_hours: float = 0.0
    fallback_used: bool = False
    capped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "components": list(self.components),
            "area_total_hours": round(self.area_total_hours, 3),
            "phase_share_hours": round(self.phase_share_hours, 3),
            "fallback_used": self.fallback_used,
            "capped": self.capped,
        }


@dataclass(frozen=True)
class EffortEstimate:
    """Resolved P50 / P90 figures for a single step."""
    step_order: int
    p50_hours: float
    p90_hours: float
    breakdown: EffortBreakdown


# --------------------------------------------------------------------------- #
# Counts derived from the loaded module payloads.
# --------------------------------------------------------------------------- #

def _count_dedicated_tables(payload: Mapping[str, Any]) -> int:
    n = 0
    for pool in payload.get("pools") or []:
        n += len(pool.get("tables") or [])
    return n


def _count_dedicated_indexes(payload: Mapping[str, Any]) -> int:
    n = 0
    for pool in payload.get("pools") or []:
        n += len(pool.get("indexes") or [])
    return n


def _count_spark_notebooks(payload: Mapping[str, Any]) -> int:
    return len(payload.get("notebooks") or [])


def _count_spark_pools(payload: Mapping[str, Any]) -> int:
    return len(payload.get("pools") or [])


def _count_pipelines(payload: Mapping[str, Any]) -> int:
    return len(payload.get("pipelines") or [])


def _count_linked_services(payload: Mapping[str, Any]) -> int:
    return len(payload.get("linked_services") or [])


def _count_triggers(payload: Mapping[str, Any]) -> int:
    return len(payload.get("triggers") or [])


def _count_integration_runtimes(payload: Mapping[str, Any]) -> int:
    return len(payload.get("integration_runtimes") or [])


def _count_external_tables(payload: Mapping[str, Any]) -> int:
    return len(payload.get("external_tables") or [])


def _count_serverless_queries(payload: Mapping[str, Any]) -> int:
    return len(payload.get("queries") or [])


def _gather_counts(loaded: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Convert ``loaded`` payloads into the named counts the rules reference."""
    counts: dict[str, int] = {}
    dp = loaded.get("dedicated_pools") or {}
    if dp:
        counts["tables"] = _count_dedicated_tables(dp)
        counts["indexes"] = _count_dedicated_indexes(dp)
    sp = loaded.get("spark_pools") or {}
    if sp:
        counts["notebooks"] = _count_spark_notebooks(sp)
        counts["spark_pools"] = _count_spark_pools(sp)
    pl = loaded.get("pipelines") or {}
    if pl:
        counts["pipelines"] = _count_pipelines(pl)
        counts["linked_services"] = _count_linked_services(pl)
        counts["triggers"] = _count_triggers(pl)
        counts["integration_runtimes"] = _count_integration_runtimes(pl)
    sl = loaded.get("serverless_pools") or {}
    if sl:
        counts["external_tables"] = _count_external_tables(sl)
        counts["serverless_queries"] = _count_serverless_queries(sl)
    return counts


# --------------------------------------------------------------------------- #
# Per-area total = rule coefficients × counts (with sensible defaults).
# --------------------------------------------------------------------------- #


def _recs_by_area(
    recommendations: Iterable[Recommendation],
) -> dict[str, list[Recommendation]]:
    out: dict[str, list[Recommendation]] = defaultdict(list)
    for r in recommendations:
        out[r.area].append(r)
    return out


def _count_severity(recs: Iterable[Recommendation], sev: str) -> int:
    return sum(1 for r in recs if r.severity == sev)


def _area_total(
    area: str,
    card: RateCard,
    recs_in_area: list[Recommendation],
    counts: dict[str, int],
) -> tuple[float, list[str]]:
    """Compute the gross hours for ``area`` and a list of breakdown strings."""
    rule = card.rules.get(area)
    components: list[str] = []
    if rule is None or not rule.units:
        return 0.0, components

    total = 0.0

    def _add(label: str, units: float, hours_per: float) -> None:
        nonlocal total
        if units <= 0 or hours_per <= 0:
            return
        contribution = units * hours_per
        total += contribution
        components.append(f"{_fmt_units(units)} {label} × {hours_per} h")

    # Map well-known unit names to the right count source.
    for unit_key, hours_per in rule.units.items():
        if unit_key == "hours_per_table":
            _add("tables", counts.get("tables", 0), hours_per)
        elif unit_key == "hours_per_index_recommendation":
            _add("index recs", len(recs_in_area), hours_per)
        elif unit_key == "hours_per_distribution_change":
            _add("distribution changes", len(recs_in_area), hours_per)
        elif unit_key == "hours_per_recommendation":
            _add("recommendations", len(recs_in_area), hours_per)
        elif unit_key == "hours_per_blocker":
            _add("blockers", _count_severity(recs_in_area, "blocker"), hours_per)
        elif unit_key == "hours_per_warning":
            _add("warnings", _count_severity(recs_in_area, "warning"), hours_per)
        elif unit_key == "hours_per_view":
            _add("materialised views", len(recs_in_area), hours_per)
        elif unit_key == "hours_per_stale_stat":
            _add("stale stats", len(recs_in_area), hours_per)
        elif unit_key == "hours_per_notebook":
            _add("notebooks", counts.get("notebooks", 0), hours_per)
        elif unit_key == "hours_per_pool":
            _add("spark pools", counts.get("spark_pools", 0), hours_per)
        elif unit_key == "hours_per_library":
            _add("libraries", len(recs_in_area), hours_per)
        elif unit_key == "hours_per_pipeline":
            _add("pipelines", counts.get("pipelines", 0), hours_per)
        elif unit_key == "hours_per_blocker_activity":
            _add(
                "blocker activities",
                _count_severity(recs_in_area, "blocker"),
                hours_per,
            )
        elif unit_key == "hours_per_linked_service":
            _add("linked services", counts.get("linked_services", 0), hours_per)
        elif unit_key == "hours_per_inline_secret":
            _add("inline secrets", len(recs_in_area), hours_per)
        elif unit_key == "hours_per_trigger":
            _add("triggers", counts.get("triggers", 0), hours_per)
        elif unit_key == "hours_per_expression_blocker":
            _add(
                "expression blockers",
                _count_severity(recs_in_area, "blocker"),
                hours_per,
            )
        elif unit_key == "hours_per_runtime":
            _add(
                "integration runtimes",
                counts.get("integration_runtimes", 0),
                hours_per,
            )
        elif unit_key == "hours_per_external_table":
            _add("external tables", counts.get("external_tables", 0), hours_per)
        elif unit_key == "hours_per_query":
            _add("queries", counts.get("serverless_queries", 0), hours_per)
        else:
            # Unknown unit — fall back to one-per-recommendation so a
            # customer-defined coefficient still produces a number.
            _add(unit_key, len(recs_in_area), hours_per)

    return total, components


def _fmt_units(units: float) -> str:
    if units == int(units):
        return f"{int(units)}"
    return f"{units:.1f}"


# --------------------------------------------------------------------------- #
# Public entry points.
# --------------------------------------------------------------------------- #


def estimate_for_step(
    step: RunbookStep,
    *,
    rec: Recommendation | None,
    card: RateCard,
    counts: dict[str, int],
    area_share: float,
    phase_share: float,
    area_capped: bool,
    components: list[str],
) -> EffortEstimate:
    """Build an :class:`EffortEstimate` for one step.

    The caller is expected to have already divided the area total by the
    number of steps in that area (``area_share``) and the phase base by
    the number of steps in the phase (``phase_share``).
    """
    area = rec.area if rec is not None else None
    fallback = False
    p50 = area_share + phase_share
    if p50 <= 0:
        # No rule fired — fall back to the qualitative bucket so every
        # step ends up with at least a non-zero hint.
        p50 = card.qualitative.get(step.effort, 0.0)
        fallback = p50 > 0
        components = list(components)
        if fallback:
            components.append(
                f"qualitative '{step.effort}' fallback × {p50} h"
            )

    if card.team_velocity > 0:
        p50 = p50 / card.team_velocity

    p90 = p50 * card.confidence_p50_to_p90_multiplier
    return EffortEstimate(
        step_order=step.order,
        p50_hours=round(p50, 2),
        p90_hours=round(p90, 2),
        breakdown=EffortBreakdown(
            area=area,
            components=list(components),
            area_total_hours=round(area_share, 2),
            phase_share_hours=round(phase_share, 2),
            fallback_used=fallback,
            capped=area_capped,
        ),
    )


def build_estimates(
    steps: Iterable[RunbookStep],
    recommendations: Iterable[Recommendation],
    loaded: Mapping[str, Mapping[str, Any]],
    card: RateCard,
) -> list[EffortEstimate]:
    """Estimate every step in ``steps`` against ``card``.

    Returns one :class:`EffortEstimate` per input step, in the same order.
    """
    steps_list = list(steps)
    rec_index: dict[str, Recommendation] = {
        r.id: r for r in recommendations
    }
    counts = _gather_counts(loaded)
    recs_by_area = _recs_by_area(recommendations)

    # 1) Per-area totals (with cap applied).
    area_totals: dict[str, tuple[float, list[str], bool]] = {}
    for area, recs in recs_by_area.items():
        total, components = _area_total(area, card, recs, counts)
        capped = False
        rule = card.rules.get(area)
        if rule and rule.cap_hours is not None and total > rule.cap_hours:
            total = rule.cap_hours
            capped = True
            components.append(f"capped at {rule.cap_hours} h")
        area_totals[area] = (total, components, capped)

    # 2) Count how many steps belong to each area and each phase.
    steps_per_area: dict[str, int] = defaultdict(int)
    steps_per_phase: dict[str, int] = defaultdict(int)
    step_area: dict[int, str | None] = {}
    for step in steps_list:
        rec = rec_index.get(step.source_recommendation_id or "")
        area = rec.area if rec is not None else None
        step_area[step.order] = area
        if area is not None:
            steps_per_area[area] += 1
        steps_per_phase[step.phase] += 1

    # 3) Build estimates.
    out: list[EffortEstimate] = []
    for step in steps_list:
        area = step_area.get(step.order)
        if area is not None and area in area_totals:
            total, components, capped = area_totals[area]
            n = max(steps_per_area[area], 1)
            area_share = total / n
        else:
            area_share = 0.0
            components = []
            capped = False
        phase = card.phases.get(step.phase)
        phase_base = phase.base_hours if phase is not None else 0.0
        n_phase = max(steps_per_phase[step.phase], 1)
        phase_share = phase_base / n_phase
        rec = rec_index.get(step.source_recommendation_id or "")
        out.append(
            estimate_for_step(
                step,
                rec=rec,
                card=card,
                counts=counts,
                area_share=area_share,
                phase_share=phase_share,
                area_capped=capped,
                components=components,
            )
        )
    return out
