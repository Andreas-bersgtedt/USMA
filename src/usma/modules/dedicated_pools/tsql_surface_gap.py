"""Build a per-object "T-SQL surface gaps" rollup for dedicated pool code objects.

Each ``CodeObject`` (stored procedure / view / function) is hashed into a stable
``code_object_id`` of the form ``<schema>.<object_name>.<object_type>`` (lowercased,
non-alphanumerics collapsed to ``_``). The id is deterministic across runs as long
as the qualified name does not change, which makes diffing two analyzer runs trivial.

This module deliberately does *not* reach into the T-SQL surface heuristics module —
it consumes the existing :func:`fabric_mapping.tsql_surface.scan` output and reshapes
it for per-object reporting. Rules in fabric_mapping still produce the human-readable
recommendations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..fabric_mapping import tsql_surface
from .models import CodeObject, CodeObjectSummary, TsqlSurfaceGap

_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Per-object Fabric compatibility classification.
# ---------------------------------------------------------------------------

_COMPATIBILITY_RANK = {"compatible": 0, "needs_review": 1, "incompatible": 2}


def classify_compatibility(severities: Iterable[str]) -> str:
    """Reduce a set of T-SQL surface-gap severities into a compatibility verdict.

    Rules (most severe wins):

    * any ``blocker`` -> ``incompatible``
    * any ``warning`` -> ``needs_review``
    * otherwise (only ``info`` or empty) -> ``compatible``
    """
    sev_set = {(s or "").lower() for s in severities}
    if "blocker" in sev_set:
        return "incompatible"
    if "warning" in sev_set:
        return "needs_review"
    return "compatible"


# ---------------------------------------------------------------------------
# Object-type normalisation. ``sys.objects.type_desc`` returns the long form
# (e.g. ``SQL_STORED_PROCEDURE``); we group these into five reporting buckets.
# ---------------------------------------------------------------------------

_TYPE_BUCKETS = {
    "SQL_STORED_PROCEDURE":              "procedure",
    "VIEW":                              "view",
    "SQL_SCALAR_FUNCTION":               "scalar_function",
    "SQL_INLINE_TABLE_VALUED_FUNCTION":  "inline_tvf",
    "SQL_TABLE_VALUED_FUNCTION":         "multi_stmt_tvf",
}


def normalize_object_type(object_type: str | None) -> str:
    if not object_type:
        return "other"
    return _TYPE_BUCKETS.get(object_type.upper(), "other")


def stable_code_object_id(schema_name: str, object_name: str, object_type: str) -> str:
    """Compute a stable, filesystem-safe identifier for a T-SQL code object.

    The id is deterministic — same inputs always yield the same id — and contains only
    lowercase alphanumerics + dots. Use it as a join key when diffing across runs.
    """
    parts = (schema_name or "?", object_name or "?", object_type or "?")
    slug = ".".join(_SLUG_RE.sub("_", p.lower()).strip("_") for p in parts)
    return slug


def build_gaps(code_objects: Iterable[CodeObject]) -> list[TsqlSurfaceGap]:
    """Run the T-SQL surface scanner across each code object and return one row per
    (object, rule) pair with the match count.
    """
    out: list[TsqlSurfaceGap] = []
    for obj in code_objects:
        cid = obj.code_object_id or stable_code_object_id(
            obj.schema_name, obj.object_name, obj.object_type
        )
        for finding in tsql_surface.scan(obj.definition or ""):
            out.append(TsqlSurfaceGap(
                code_object_id=cid,
                schema_name=obj.schema_name,
                object_name=obj.object_name,
                object_type=obj.object_type,
                rule_id=finding.rule_id,
                label=finding.label,
                severity=finding.severity,
                matches=finding.matches,
                fabric_action=tsql_surface.fabric_action_for(finding.rule_id),
            ))
    return out


@dataclass(frozen=True)
class GapSummary:
    code_object_id: str
    rule_count: int
    total_matches: int
    severities: tuple[str, ...]


def summarize_per_object(gaps: Iterable[TsqlSurfaceGap]) -> list[GapSummary]:
    """Aggregate gaps by code_object_id for executive-summary tables."""
    by_obj: dict[str, dict] = {}
    for g in gaps:
        b = by_obj.setdefault(g.code_object_id, {"rules": set(), "matches": 0, "sev": set()})
        b["rules"].add(g.rule_id)
        b["matches"] += g.matches
        b["sev"].add(g.severity)
    return [
        GapSummary(
            code_object_id=cid,
            rule_count=len(b["rules"]),
            total_matches=b["matches"],
            severities=tuple(sorted(b["sev"])),
        )
        for cid, b in sorted(by_obj.items())
    ]


# ---------------------------------------------------------------------------
# Stamp per-object compatibility + roll up the per-pool inventory.
# ---------------------------------------------------------------------------

def stamp_compatibility(
    code_objects: list[CodeObject],
    gaps: Iterable[TsqlSurfaceGap],
) -> list[CodeObject]:
    """Mutate each ``CodeObject`` in place with ``compatibility`` /
    ``gap_severities`` / ``gap_count`` derived from ``gaps``.

    Returns the same list for fluent use. Objects with no matching gaps stay
    ``compatible``.
    """
    sev_by_obj: dict[str, list[str]] = {}
    count_by_obj: dict[str, int] = {}
    for g in gaps:
        sev_by_obj.setdefault(g.code_object_id, []).append(g.severity)
        count_by_obj[g.code_object_id] = count_by_obj.get(g.code_object_id, 0) + 1
    for obj in code_objects:
        cid = obj.code_object_id or stable_code_object_id(
            obj.schema_name, obj.object_name, obj.object_type
        )
        sev = sev_by_obj.get(cid, [])
        # Deterministic, deduped order; severest first for readability.
        ordered = sorted(set(sev), key=lambda s: -_COMPATIBILITY_RANK.get(
            "incompatible" if s == "blocker" else
            "needs_review" if s == "warning" else "compatible", 0))
        obj.gap_severities = ordered
        obj.gap_count = count_by_obj.get(cid, 0)
        obj.compatibility = classify_compatibility(sev)
    return code_objects


def summarize_code_objects(code_objects: Iterable[CodeObject]) -> CodeObjectSummary:
    """Build a :class:`CodeObjectSummary` for the per-pool inventory rollup."""
    by_type: dict[str, int] = {}
    by_compat: dict[str, int] = {"compatible": 0, "needs_review": 0, "incompatible": 0}
    incompatible: list[str] = []
    needs_review: list[str] = []
    total = 0
    for obj in code_objects:
        total += 1
        bucket = normalize_object_type(obj.object_type)
        by_type[bucket] = by_type.get(bucket, 0) + 1
        compat = obj.compatibility or "compatible"
        by_compat[compat] = by_compat.get(compat, 0) + 1
        cid = obj.code_object_id or stable_code_object_id(
            obj.schema_name, obj.object_name, obj.object_type
        )
        if compat == "incompatible":
            incompatible.append(cid)
        elif compat == "needs_review":
            needs_review.append(cid)
    pct: float | None = None
    if total:
        pct = round(by_compat.get("compatible", 0) / total * 100, 1)
    return CodeObjectSummary(
        total=total,
        by_type=dict(sorted(by_type.items())),
        by_compatibility={k: v for k, v in by_compat.items() if v},
        compatibility_pct=pct,
        incompatible_object_ids=sorted(incompatible),
        needs_review_object_ids=sorted(needs_review),
    )
