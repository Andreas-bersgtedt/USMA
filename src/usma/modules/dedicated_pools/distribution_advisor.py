"""Distribution-key candidate scorer for dedicated SQL pool tables.

Pure-python heuristic that ranks columns as candidates for a HASH distribution key.
Inputs come from the v1 collectors plus the new v2 column-statistics collector
(see ``queries/column_stats.sql``); when statistics are missing we fall back to
index-based scoring so the advisor still produces output for partial collections.

Heuristic (higher score = better candidate):
* selectivity (distinct/rows) close to 1.0           +50
* column appears as the leading key of an index      +20
* column appears in any unique constraint            +15
* not nullable                                       +5
* numeric or date type                               +5
* low (< 5 %) skew (max-frequency / rows)            +10

Penalties:
* very low cardinality (< 60 distinct values)        −40
* mostly-NULL                                        −30
* string types over 200 chars                        −10

Output: a list of ``CandidateScore`` per (schema, table), top-N per table.
The advisor never recommends a key for tables already on a sensible HASH key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class CandidateScore:
    schema_name: str
    table_name: str
    column_name: str
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)


# Types that are sensible hash keys; everything else still scores but with a small penalty.
_NUMERIC_DATE_TYPES = frozenset({
    "tinyint", "smallint", "int", "bigint",
    "date", "datetime", "datetime2", "datetimeoffset", "smalldatetime",
    "decimal", "numeric", "money", "smallmoney",
    "uniqueidentifier",
})


def score_columns(
    *,
    tables: Iterable[dict[str, Any]],
    indexes: Iterable[dict[str, Any]] = (),
    column_stats: Iterable[dict[str, Any]] = (),
    filter_usage: dict[tuple[str, str, str], int] | None = None,
    top_n: int = 3,
) -> list[CandidateScore]:
    """Return up to ``top_n`` distribution-key candidates per ROUND_ROBIN / large REPLICATE table.

    Tables already on HASH distribution are skipped — the existing key is assumed deliberate.

    ``filter_usage`` is an optional mapping ``{(schema, table, column): hit_count}`` of how
    often each column appears in WHERE / JOIN predicates across the pool's code objects.
    Columns with high filter usage get a score boost — hash-distributing on them eliminates
    shuffle for those queries.
    """
    filter_usage = filter_usage or {}
    idx_first_keys: dict[tuple[str, str], set[str]] = {}
    idx_unique_cols: dict[tuple[str, str], set[str]] = {}
    for ix in indexes:
        key = (ix.get("schema_name") or "", ix.get("table_name") or "")
        # The collector currently captures only one row per index; first_key / unique flags
        # are extension fields populated by the v2 column_stats collector.
        first_key = ix.get("first_key_column")
        if first_key:
            idx_first_keys.setdefault(key, set()).add(first_key)
        if ix.get("is_unique") and ix.get("first_key_column"):
            idx_unique_cols.setdefault(key, set()).add(ix["first_key_column"])

    stats_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in column_stats:
        stats_by_table.setdefault(
            (s.get("schema_name") or "", s.get("table_name") or ""), []
        ).append(s)

    out: list[CandidateScore] = []
    for t in tables:
        dist = (t.get("distribution_policy") or "").upper()
        if dist == "HASH":
            continue
        rows = t.get("row_count") or 0
        # No point picking a hash key for tiny tables.
        if dist == "REPLICATE" and rows < 50_000_000:
            continue
        if dist == "ROUND_ROBIN" and rows < 1_000_000:
            continue

        key = (t.get("schema_name") or "", t.get("table_name") or "")
        candidates: list[CandidateScore] = []
        for col in stats_by_table.get(key, []):
            score, reasons = _score_one(
                col,
                idx_first_keys.get(key, set()),
                idx_unique_cols.get(key, set()),
                filter_usage.get((key[0], key[1], col.get("column_name") or ""), 0),
            )
            if score <= 0:
                continue
            candidates.append(CandidateScore(
                schema_name=key[0], table_name=key[1],
                column_name=col.get("column_name") or "?",
                score=score, reasons=tuple(reasons),
            ))

        # Fallback: no stats available — recommend leading-index columns.
        if not candidates:
            for c in idx_first_keys.get(key, set()):
                hits = filter_usage.get((key[0], key[1], c), 0)
                base = 20
                reasons_fb = ["leading index key (no column stats available)"]
                if hits >= 3:
                    base += 10
                    reasons_fb.append(f"filter usage hits={hits}")
                candidates.append(CandidateScore(
                    schema_name=key[0], table_name=key[1], column_name=c,
                    score=base, reasons=tuple(reasons_fb),
                ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        out.extend(candidates[:top_n])
    return out


def _score_one(
    col: dict[str, Any],
    first_keys: set[str],
    unique_cols: set[str],
    filter_hits: int = 0,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    name = col.get("column_name") or ""
    rows = col.get("row_count") or 0
    distinct = col.get("distinct_count") or 0
    nulls = col.get("null_count") or 0
    max_freq = col.get("max_frequency") or 0
    data_type = (col.get("data_type") or "").lower()
    max_length = col.get("max_length") or 0
    is_nullable = col.get("is_nullable", True)

    if rows > 0 and distinct > 0:
        sel = distinct / rows
        if sel >= 0.95:
            score += 50
            reasons.append(f"selectivity={sel:.2f}")
        elif sel >= 0.5:
            score += 30
            reasons.append(f"selectivity={sel:.2f}")
        elif sel >= 0.1:
            score += 10
            reasons.append(f"selectivity={sel:.2f}")

    if name in first_keys:
        score += 20
        reasons.append("leading index key")
    if name in unique_cols:
        score += 15
        reasons.append("unique constraint")

    if not is_nullable:
        score += 5
        reasons.append("NOT NULL")

    if data_type in _NUMERIC_DATE_TYPES:
        score += 5
        reasons.append(f"type={data_type}")

    if rows > 0 and max_freq > 0:
        skew = max_freq / rows
        if skew < 0.05:
            score += 10
            reasons.append(f"skew={skew:.2%}")

    # Filter-selectivity heuristic: columns referenced in WHERE / JOIN predicates of
    # the pool's code objects make better hash keys (less shuffle for those queries).
    if filter_hits >= 10:
        score += 15
        reasons.append(f"filter usage hits={filter_hits}")
    elif filter_hits >= 3:
        score += 8
        reasons.append(f"filter usage hits={filter_hits}")
    elif filter_hits > 0:
        score += 3
        reasons.append(f"filter usage hits={filter_hits}")

    # Penalties
    if distinct and distinct < 60:
        score -= 40
        reasons.append(f"low cardinality (distinct={distinct})")
    if rows and nulls / rows > 0.5:
        score -= 30
        reasons.append(f"mostly NULL ({nulls / rows:.0%})")
    if data_type.startswith(("nvarchar", "varchar", "char")) and max_length > 200:
        score -= 10
        reasons.append(f"wide string ({max_length})")

    return score, reasons
