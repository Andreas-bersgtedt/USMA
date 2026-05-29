"""Extract referenced tables/views from a submitted T-SQL command.

We use sqlglot's AST so we don't have to maintain bespoke regex pattern
sets for FROM / JOIN / INSERT INTO / MERGE / UPDATE / DELETE / CTAS /
CTE / etc. The parser is permissive — anything sqlglot can't handle is
reported as ``"failed"`` rather than crashing the run.

Match-kind taxonomy
-------------------

For each ``exp.Table`` node we cross-check against the per-pool catalog
of real tables + views and assign one of:

``qualified``
    A 2-part (``schema.name``) or 3-part name whose ``(schema, name)``
    pair exists in the catalog. The most trustworthy signal.

``unqualified-resolved``
    A 1-part name (``SELECT * FROM fact_sales``) whose unqualified
    name maps to exactly one schema in the catalog — we attribute the
    reference to that single owner.

``ambiguous``
    A 1-part name that exists in more than one schema. We attribute one
    hit to *each* candidate (so the surfacing UI can show the cluster
    as suspect) but mark them all ``ambiguous``.

Anything not in the catalog is dropped — that excludes CTE names,
table-valued parameter aliases, temp tables, system DMVs, and typos.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedTableRef:
    """One resolved table/view reference inside a parsed command."""
    schema_name: str
    object_name: str
    object_type: str  # "table" | "view" — taken from the catalog
    match_kind: str   # "qualified" | "unqualified-resolved" | "ambiguous"


# Catalog shape used by the parser. Keyed by lowercased object_name to
# enable fast 1-part-name resolution.
#
#   { "fact_sales": [("dbo", "table"), ("stg", "table")], ... }
#
# Plus a second view keyed by ("schema_lower", "name_lower") for exact
# 2-part qualified lookups.
Catalog = dict[str, list[tuple[str, str]]]  # name_lower -> [(schema_lower, type)]
QualifiedIndex = dict[tuple[str, str], tuple[str, str]]
# (schema_lower, name_lower) -> (canonical_schema, canonical_name)
# (We carry the canonical-case form so the output uses the real
# database-side casing, not whatever the SQL author wrote.)
QualifiedCanonical = dict[tuple[str, str], tuple[str, str, str]]
# (schema_lower, name_lower) -> (canonical_schema, canonical_name, type)


def build_catalog(rows: list[dict]) -> tuple[Catalog, QualifiedCanonical]:
    """Build the two lookup structures the parser needs.

    ``rows`` should be the output of the ``workload_catalog`` query —
    each row has ``schema_name``, ``object_name``, ``object_type``.
    """
    by_name: Catalog = {}
    by_qualified: QualifiedCanonical = {}
    for r in rows:
        schema = str(r.get("schema_name") or "")
        name = str(r.get("object_name") or "")
        otype = str(r.get("object_type") or "table")
        if not schema or not name:
            continue
        s_lo, n_lo = schema.lower(), name.lower()
        by_name.setdefault(n_lo, []).append((s_lo, otype))
        by_qualified[(s_lo, n_lo)] = (schema, name, otype)
    return by_name, by_qualified


def _table_parts(table: exp.Table) -> tuple[str, str]:
    """Return ``(schema_lower, name_lower)``; schema is '' if absent.

    sqlglot exposes the parts as ``catalog.db.this`` — for T-SQL the
    ``db`` component is the schema. 4-part names (linked-server) are
    flattened by taking the rightmost two parts.
    """
    name = (table.name or "").strip("[]\"").lower()
    schema = ""
    db = table.args.get("db")
    if db is not None:
        schema = (db.name or "").strip("[]\"").lower()
    return schema, name


def _collect_cte_names(tree: exp.Expression) -> set[str]:
    """All CTE alias names declared anywhere in the tree (lowercased)."""
    out: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            out.add(alias.strip("[]\"").lower())
    return out


def parse_command(
    command: str,
    by_name: Catalog,
    by_qualified: QualifiedCanonical,
) -> tuple[list[ParsedTableRef], str]:
    """Parse ``command`` and return resolved table references + status.

    Status is one of:
        ``"ok"``     parsed and at least one table resolved against the catalog
        ``"empty"``  parsed but no recognized tables (e.g. ``SELECT 1``)
        ``"failed"`` sqlglot raised — likely an unsupported dialect feature
    """
    if not command or not command.strip():
        return [], "empty"
    try:
        trees = sqlglot.parse(command, dialect="tsql")
    except Exception as exc:  # noqa: BLE001
        log.debug("sqlglot parse failed: %s", exc)
        return [], "failed"

    seen: dict[tuple[str, str], ParsedTableRef] = {}
    for tree in trees:
        if tree is None:
            continue
        cte_names = _collect_cte_names(tree)
        for table in tree.find_all(exp.Table):
            schema_lo, name_lo = _table_parts(table)
            if not name_lo:
                continue
            # Skip table-valued function calls (those parse as Table too).
            if isinstance(table.parent, exp.Anonymous):
                continue
            # Skip CTE references.
            if not schema_lo and name_lo in cte_names:
                continue
            # Skip temp tables / table variables — never in the catalog.
            if name_lo.startswith("#") or name_lo.startswith("@"):
                continue

            if schema_lo:
                hit = by_qualified.get((schema_lo, name_lo))
                if hit is None:
                    continue  # references an object outside the pool catalog
                can_schema, can_name, otype = hit
                key = (can_schema.lower(), can_name.lower())
                if key not in seen:
                    seen[key] = ParsedTableRef(can_schema, can_name, otype, "qualified")
                continue

            # 1-part name — try to resolve via unqualified index.
            owners = by_name.get(name_lo) or []
            if not owners:
                continue
            if len(owners) == 1:
                s_lo, otype = owners[0]
                hit = by_qualified.get((s_lo, name_lo))
                if hit is None:
                    continue
                can_schema, can_name, otype2 = hit
                key = (can_schema.lower(), can_name.lower())
                if key not in seen:
                    seen[key] = ParsedTableRef(
                        can_schema, can_name, otype2, "unqualified-resolved",
                    )
            else:
                # Ambiguous — attribute one hit to each candidate.
                for s_lo, _ in owners:
                    hit = by_qualified.get((s_lo, name_lo))
                    if hit is None:
                        continue
                    can_schema, can_name, otype = hit
                    key = (can_schema.lower(), can_name.lower())
                    if key not in seen:
                        seen[key] = ParsedTableRef(
                            can_schema, can_name, otype, "ambiguous",
                        )

    refs = list(seen.values())
    if not refs:
        return [], "empty"
    return refs, "ok"
