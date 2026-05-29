"""sqlglot-based feature detection for BigQuery query text.

Captured ``query_text`` (from ``INFORMATION_SCHEMA.JOBS_BY_PROJECT.query``)
is parsed with ``sqlglot``'s ``bigquery`` dialect and scanned for the
SQL surface features that drive Fabric / Spark migration effort:
scripting (``DECLARE`` / ``BEGIN..END``), procedural code (``CREATE PROCEDURE``),
``MERGE``, JavaScript / remote UDFs, ``ML.*`` model invocations, geospatial
functions, ``EXPORT DATA``, etc.

Each detected feature is emitted as a stable kebab-case slug. Slugs map
1:1 to Fabric / Spark mapping rules so downstream readiness scoring can
join on them directly. Detection is best-effort — when ``sqlglot`` can't
parse the text (BigQuery scripting still has gaps) we fall back to a
substring / regex sniff so users still see *something* useful.

The detector is intentionally side-effect-free and synchronous; the
analyzer calls it inline while building the jobs list.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

try:
    import sqlglot
    from sqlglot import exp
except Exception:  # pragma: no cover - sqlglot is a required dep
    sqlglot = None  # type: ignore[assignment]
    exp = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


# Regex sniffs used both as primary signal for scripting (where sqlglot
# parses the wrapper as ``Command``) and as a fallback when parsing fails.
# All patterns are case-insensitive and anchored on a word boundary so
# they don't catch substrings inside identifiers / strings (mostly).
_REGEX_FEATURES: dict[str, re.Pattern[str]] = {
    "scripting-declare": re.compile(r"\bDECLARE\s+\w+", re.IGNORECASE),
    "scripting-set": re.compile(r"\bSET\s+\w+\s*=", re.IGNORECASE),
    "scripting-begin-end": re.compile(r"\bBEGIN\b(?!\s+TRANSACTION)", re.IGNORECASE),
    "scripting-if": re.compile(r"\bIF\b.*\bTHEN\b", re.IGNORECASE | re.DOTALL),
    "scripting-loop": re.compile(r"\b(WHILE|FOR|LOOP|REPEAT)\b", re.IGNORECASE),
    "scripting-raise": re.compile(r"\bRAISE\b", re.IGNORECASE),
    "procedure-create": re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?PROCEDURE\b", re.IGNORECASE),
    "procedure-call": re.compile(r"\bCALL\s+`?[\w\.\-]+`?\s*\(", re.IGNORECASE),
    "udf-javascript": re.compile(r"\bLANGUAGE\s+js\b", re.IGNORECASE),
    "udf-remote": re.compile(r"\bREMOTE\s+WITH\s+CONNECTION\b", re.IGNORECASE),
    "merge": re.compile(r"\bMERGE\s+(INTO\s+)?", re.IGNORECASE),
    "export-data": re.compile(r"\bEXPORT\s+DATA\b", re.IGNORECASE),
    "load-data": re.compile(r"\bLOAD\s+DATA\b", re.IGNORECASE),
    "ml-predict": re.compile(r"\bML\.\w+\s*\(", re.IGNORECASE),
    "geo-function": re.compile(r"\bST_\w+\s*\(", re.IGNORECASE),
    "wildcard-table": re.compile(r"`?[\w\-]+\.[\w\-]+\.[\w\-]*\*`?", re.IGNORECASE),
    "table-suffix": re.compile(r"\b_TABLE_SUFFIX\b", re.IGNORECASE),
    "session-temp-table": re.compile(r"\bCREATE\s+TEMP\s+TABLE\b", re.IGNORECASE),
    "transaction": re.compile(r"\bBEGIN\s+TRANSACTION\b", re.IGNORECASE),
    "assert": re.compile(r"\bASSERT\b", re.IGNORECASE),
    "create-schema": re.compile(r"\bCREATE\s+SCHEMA\b", re.IGNORECASE),
    "create-search-index": re.compile(r"\bCREATE\s+SEARCH\s+INDEX\b", re.IGNORECASE),
    "create-vector-index": re.compile(r"\bCREATE\s+VECTOR\s+INDEX\b", re.IGNORECASE),
    "json-function": re.compile(r"\bJSON_\w+\s*\(", re.IGNORECASE),
}


def detect_features(query_text: str | None) -> list[str]:
    """Return sorted list of feature slugs detected in ``query_text``.

    Empty / null input yields ``[]``. Detection combines (1) regex sniffs
    that work even when sqlglot can't parse (BigQuery scripting), and
    (2) when parsing succeeds, AST-walk checks that catch a few cases
    regex can miss (e.g. ``MERGE`` inside a CTE, scalar-subquery shape).
    """
    if not query_text or not query_text.strip():
        return []
    found: set[str] = set()

    # ----- regex pass (cheap, works on scripting and on parse-failures) ---
    for slug, pat in _REGEX_FEATURES.items():
        if pat.search(query_text):
            found.add(slug)

    # ----- AST pass (only when sqlglot can parse the statement) -----------
    if sqlglot is not None and exp is not None:
        try:
            trees = sqlglot.parse(query_text, dialect="bigquery")
        except Exception as ex:  # noqa: BLE001
            log.debug("sqlglot bigquery parse failed: %s", ex)
            trees = []
        for tree in trees:
            if tree is None:
                continue
            try:
                _ast_walk(tree, found)
            except Exception as ex:  # noqa: BLE001 - defensive; never let detector raise
                log.debug("AST walk failed: %s", ex)
                continue
    return sorted(found)


def _ast_walk(tree, out: set[str]) -> None:
    """Walk a parsed tree and add AST-level feature slugs to ``out``.

    Kept narrow: only catch features that the regex pass either misses
    or false-positives on. Don't duplicate everything — sqlglot's
    BigQuery dialect doesn't yet model every script construct.
    """
    # MERGE statements (sqlglot exposes them as exp.Merge).
    if isinstance(tree, exp.Merge):
        out.add("merge")
    for node in tree.find_all(exp.Merge):
        out.add("merge")

    # UNNEST(arr) is a frequent Spark migration hotspot (becomes explode()).
    for node in tree.find_all(exp.Unnest):
        out.add("unnest")
        break

    # Array constructor literals — ``[1,2,3]`` / ``ARRAY<...>`` — common in
    # BigQuery but require explicit Spark mapping.
    for node in tree.find_all(exp.Array):
        out.add("array-literal")
        break

    # STRUCT literals (becomes struct() in Spark; surfacing helps planners).
    for node in tree.find_all(exp.Struct):
        out.add("struct-literal")
        break

    # Window functions — flag once so the migration team knows they exist.
    for node in tree.find_all(exp.Window):
        out.add("window-function")
        break

    # Table-valued functions / unsupported syntactic shapes show up as
    # ``Anonymous`` nodes; surface a generic marker for any name that
    # looks BigQuery-specific. Common offenders: BQML, INFORMATION_SCHEMA
    # table-valued functions, ARRAY_AGG with ORDER BY, etc.
    for node in tree.find_all(exp.Anonymous):
        name = (node.name or "").upper()
        if name.startswith("ARRAY_AGG"):
            out.add("array-agg")


def summarise_features(feature_lists: Iterable[Iterable[str]]) -> dict[str, int]:
    """Aggregate per-job feature lists into a ``{slug: job_count}`` map.

    Each job contributes 1 toward each *distinct* feature it carries —
    a job with two ``MERGE`` statements still counts once for ``"merge"``.
    """
    counts: dict[str, int] = {}
    for flist in feature_lists:
        if not flist:
            continue
        for slug in set(flist):
            counts[slug] = counts.get(slug, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


__all__ = ["detect_features", "summarise_features"]
