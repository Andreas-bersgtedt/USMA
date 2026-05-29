"""Extract column-level filter / join usage from raw T-SQL definitions.

The distribution-key advisor benefits from knowing which columns are used in
``WHERE`` predicates and ``JOIN ... ON`` clauses across the pool's stored procs,
views, and functions. Columns that show up frequently are good distribution-key
candidates because hash-distributing on them eliminates shuffle for those queries.

This is a deliberately conservative regex-based extractor — false positives are
expected. The advisor only uses the counts to *boost* a score, never to make
or break a recommendation.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


# Capture "table_alias.column" or bare "column" inside WHERE / ON predicates.
# We intentionally don't try to fully parse SQL — the cost is too high vs the
# value of a heuristic score boost.
_FROM_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+\[?(\w+)\]?\.\[?(\w+)\]?(?:\s+(?:AS\s+)?\[?(\w+)\]?)?",
    re.IGNORECASE,
)
_PREDICATE_RE = re.compile(
    r"\b(?:WHERE|ON|AND|OR)\s+([\[\]\w\.]+)\s*(=|<>|!=|>=|<=|>|<|IN\s*\(|LIKE)",
    re.IGNORECASE,
)
# Right-hand side of equi-joins like ``a.col1 = b.col2`` — captures the second column.
_EQUI_JOIN_RHS_RE = re.compile(
    r"=\s*([\[\]\w]+\.[\[\]\w]+)\b",
)


def _split_qualified(token: str) -> tuple[str | None, str]:
    token = token.strip().strip("[]")
    if "." in token:
        left, _, right = token.rpartition(".")
        return left.strip("[]") or None, right.strip("[]")
    return None, token


def extract_filter_usage(
    code_objects: Iterable[dict],
) -> dict[tuple[str, str, str], int]:
    """Return ``{(schema, table, column): occurrence_count}`` across all code object
    definitions.

    Each ``code_object`` is a dict (or model dump) with at least ``schema_name``,
    ``object_name`` and ``definition`` keys.
    """
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for obj in code_objects:
        text = obj.get("definition") or ""
        if not text:
            continue
        # Build alias → (schema, table) map for this object.
        alias_map: dict[str, tuple[str, str]] = {}
        for m in _FROM_TABLE_RE.finditer(text):
            schema, table, alias = m.group(1), m.group(2), m.group(3)
            if schema and table:
                key = (schema, table)
                if alias:
                    alias_map[alias] = key
                alias_map[table] = key
        # Distinct *tables* in scope (multiple aliases may point at one table).
        distinct_tables = set(alias_map.values())

        def _attribute(qualifier: str | None, column: str) -> None:
            if not column or not column.replace("_", "").isalnum():
                return
            if qualifier and qualifier in alias_map:
                schema, table = alias_map[qualifier]
            elif len(distinct_tables) == 1:
                schema, table = next(iter(distinct_tables))
            else:
                return
            counts[(schema, table, column)] += 1

        for m in _PREDICATE_RE.finditer(text):
            qualifier, column = _split_qualified(m.group(1))
            _attribute(qualifier, column)

        # Equi-join right-hand side: ``a.col = b.col`` — capture the second column too.
        for m in _EQUI_JOIN_RHS_RE.finditer(text):
            qualifier, column = _split_qualified(m.group(1))
            _attribute(qualifier, column)
    return dict(counts)
