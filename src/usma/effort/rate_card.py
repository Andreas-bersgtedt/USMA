"""Configurable rate card for migration effort estimates.

The card is a small, deterministic JSON document that turns a sequenced
runbook (and the inputs the analyzer already collected) into person-hour
estimates. Every coefficient lives here so customers can tune the model
to their own delivery velocity without forking the code.

Shape (see ``DEFAULT_CARD`` for the shipped values)::

    {
      "version": 1,
      "team_velocity": 1.0,
      "confidence_p50_to_p90_multiplier": 1.8,
      "qualitative": {"low": 2, "medium": 8, "high": 24},
      "phases": {
        "<phase>": {"base_hours": <float>},
        ...
      },
      "rules": {
        "<recommendation.area>": {
          "<unit>": <hours-per-unit-float>,
          ...
          "cap_hours": <optional-float>
        },
        ...
      }
    }

The card is intentionally JSON (not YAML or TOML) so it has zero new
dependencies and can be round-tripped through the SPA editor.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class PhaseCoeffs(BaseModel):
    base_hours: float = 0.0


class RuleCoeffs(BaseModel):
    # Free-form hours-per-unit map plus optional cap. Concrete unit names are
    # documented per-rule in ``DEFAULT_CARD`` and in user-guide chapter 19.
    units: dict[str, float] = Field(default_factory=dict)
    cap_hours: float | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RuleCoeffs":
        cap = raw.get("cap_hours")
        units = {k: float(v) for k, v in raw.items() if k != "cap_hours"}
        return cls(units=units, cap_hours=float(cap) if cap is not None else None)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {k: v for k, v in self.units.items()}
        if self.cap_hours is not None:
            out["cap_hours"] = self.cap_hours
        return out


class RateCard(BaseModel):
    version: int = 1
    team_velocity: float = 1.0
    confidence_p50_to_p90_multiplier: float = 1.8
    qualitative: dict[str, float] = Field(default_factory=dict)
    phases: dict[str, PhaseCoeffs] = Field(default_factory=dict)
    rules: dict[str, RuleCoeffs] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported rate-card version {v}; expected 1")
        return v

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "team_velocity": self.team_velocity,
            "confidence_p50_to_p90_multiplier": self.confidence_p50_to_p90_multiplier,
            "qualitative": dict(self.qualitative),
            "phases": {k: {"base_hours": v.base_hours} for k, v in self.phases.items()},
            "rules": {k: v.to_dict() for k, v in self.rules.items()},
        }


# --------------------------------------------------------------------------- #
# Shipped defaults
# --------------------------------------------------------------------------- #

# Person-hour coefficients calibrated against typical Synapse -> Fabric
# migrations. These are conservative middle-of-the-road numbers, not a
# guarantee. Customers should tune ``team_velocity`` after the first sprint
# and then refine per-rule unit hours as actuals come in.
DEFAULT_CARD: dict[str, Any] = {
    "version": 1,
    "team_velocity": 1.0,
    "confidence_p50_to_p90_multiplier": 1.8,
    "qualitative": {"low": 2.0, "medium": 8.0, "high": 24.0},
    "phases": {
        "foundation": {"base_hours": 8.0},
        "data_plane_prep": {"base_hours": 16.0},
        "ingest_shortcuts": {"base_hours": 4.0},
        "compute_migration": {"base_hours": 8.0},
        "orchestration_migration": {"base_hours": 8.0},
        "verification": {"base_hours": 16.0},
    },
    "rules": {
        # Distribution / table layout work. Roughly 15 min per table to
        # review + classify. Capped to keep one rule from dominating the
        # estimate on very wide warehouses.
        "dedicated_pools.tables": {
            "hours_per_table": 0.25,
            "cap_hours": 80.0,
        },
        "dedicated_pools.distribution_advisor": {
            "hours_per_recommendation": 1.5,
            "cap_hours": 60.0,
        },
        # T-SQL surface gaps: 2 h per blocker (rewrite + test), 30 min per
        # warning (review + maybe leave as-is).
        "dedicated_pools.tsql_surface": {
            "hours_per_blocker": 2.0,
            "hours_per_warning": 0.5,
            "cap_hours": 120.0,
        },
        # Indexes: review + rebuild plan.
        "dedicated_pools.indexes": {
            "hours_per_index_recommendation": 0.5,
            "cap_hours": 40.0,
        },
        # Materialised views: rebuild + verify.
        "dedicated_pools.materialized_views": {
            "hours_per_view": 1.5,
        },
        "dedicated_pools.statistics": {
            "hours_per_stale_stat": 0.1,
            "cap_hours": 20.0,
        },
        # Spark notebooks: 90 min per notebook (lint + Fabric runtime),
        # plus 1 h per lint blocker that needs a rewrite.
        "spark_pools.notebooks": {
            "hours_per_notebook": 1.5,
            "cap_hours": 80.0,
        },
        "spark_pools.lint": {
            "hours_per_blocker": 1.0,
            "hours_per_warning": 0.25,
            "cap_hours": 60.0,
        },
        "spark_pools.runtime": {
            "hours_per_pool": 1.0,
        },
        "spark_pools.libraries": {
            "hours_per_library": 0.5,
        },
        # Pipelines: 45 min per pipeline to port shape + linked services,
        # plus 1 h per activity flagged as incompatible.
        "pipelines.activities": {
            "hours_per_pipeline": 0.75,
            "hours_per_blocker_activity": 1.0,
            "cap_hours": 120.0,
        },
        "pipelines.linked_services": {
            "hours_per_linked_service": 0.5,
            "hours_per_inline_secret": 0.5,
            "cap_hours": 40.0,
        },
        "pipelines.triggers": {
            "hours_per_trigger": 0.5,
        },
        "pipelines.expressions": {
            "hours_per_expression_blocker": 0.5,
            "cap_hours": 40.0,
        },
        "pipelines.integration_runtimes": {
            "hours_per_runtime": 4.0,
        },
        # Serverless / shortcuts: 15 min per external table + lookup.
        "serverless_pools.external_tables": {
            "hours_per_external_table": 0.25,
            "cap_hours": 30.0,
        },
        "serverless_pools.queries": {
            "hours_per_query": 0.25,
        },
        # Capacity / monitoring scaffolding.
        "monitoring.dwu": {
            "hours_per_recommendation": 1.0,
        },
        "monitoring.connections": {
            "hours_per_recommendation": 0.5,
        },
    },
}


def default_card() -> RateCard:
    return _from_payload(copy.deepcopy(DEFAULT_CARD))


def days_from_hours(hours: float | None) -> int | None:
    """Convert estimated hours to resource-days.

    Formula: ``ceil((hours / 8) * 1.15)`` \u2014 8 working hours per day plus
    a flat 15 % spillage / context-switch buffer, rounded up to the next
    whole day. Returns ``None`` for ``None`` or non-positive inputs.
    """
    if hours is None or hours <= 0:
        return 0 if hours == 0 else None
    return math.ceil((float(hours) / 8.0) * 1.15)


def _from_payload(raw: dict[str, Any]) -> RateCard:
    phases = {k: PhaseCoeffs(**v) for k, v in (raw.get("phases") or {}).items()}
    rules = {
        k: RuleCoeffs.from_dict(v) for k, v in (raw.get("rules") or {}).items()
    }
    return RateCard(
        version=int(raw.get("version", 1)),
        team_velocity=float(raw.get("team_velocity", 1.0)),
        confidence_p50_to_p90_multiplier=float(
            raw.get("confidence_p50_to_p90_multiplier", 1.8),
        ),
        qualitative={k: float(v) for k, v in (raw.get("qualitative") or {}).items()},
        phases=phases,
        rules=rules,
    )


def merge_with_default(overrides: dict[str, Any]) -> RateCard:
    """Deep-merge ``overrides`` on top of the shipped default.

    Top-level scalars are replaced; ``qualitative``, ``phases`` and
    ``rules`` maps are merged key-by-key so a customer can override a
    single rule without re-stating the others.
    """
    merged = copy.deepcopy(DEFAULT_CARD)
    for k, v in overrides.items():
        if k in ("qualitative", "phases") and isinstance(v, dict):
            merged.setdefault(k, {}).update(v)
        elif k == "rules" and isinstance(v, dict):
            base_rules = merged.setdefault("rules", {})
            for rule_key, rule_val in v.items():
                if isinstance(rule_val, dict):
                    base_rules.setdefault(rule_key, {}).update(rule_val)
                else:
                    base_rules[rule_key] = rule_val
        else:
            merged[k] = v
    return _from_payload(merged)


def load_card(path: str | os.PathLike[str] | None = None) -> RateCard:
    """Load a rate card from ``path``; fall back to the shipped default.

    Resolution order:

    1. Explicit ``path`` argument (raises if not found).
    2. ``$SMA_EFFORT_CARD`` environment variable (raises if set but not found).
    3. ``./effort-card.json`` relative to CWD, if it exists.
    4. The shipped default.

    The loaded file is interpreted as a *partial* override and merged on
    top of the default — a customer card needs only the keys it wishes
    to change.
    """
    if path is not None:
        return _load_file(Path(path))
    env = os.environ.get("SMA_EFFORT_CARD")
    if env:
        return _load_file(Path(env))
    discovered = Path("effort-card.json")
    if discovered.is_file():
        return _load_file(discovered)
    return default_card()


def _load_file(path: Path) -> RateCard:
    if not path.is_file():
        raise FileNotFoundError(f"rate card not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"rate card at {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"rate card at {path} must be a JSON object at the top level")
    try:
        return merge_with_default(raw)
    except ValidationError as exc:
        raise ValueError(f"rate card at {path} failed validation: {exc}") from exc


def dump_card(card: RateCard, path: str | os.PathLike[str]) -> Path:
    """Write ``card`` to ``path`` as pretty-printed JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(card.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return p
