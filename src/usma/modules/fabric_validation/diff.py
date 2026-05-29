"""Pure-Python comparators for Fabric-side validation.

Inputs are simple dicts/lists so callers can build them from JSON / SDK payloads.
"""
from __future__ import annotations

from typing import Iterable

from .models import CollationCheck, ObjectCountCheck, RowCountCheck, TsqlSurfaceCheck


# Synapse object types -> sys.objects.type_desc as Fabric reports them.
_TYPE_TO_DESC = {
    "table": "USER_TABLE",
    "view": "VIEW",
    "procedure": "SQL_STORED_PROCEDURE",
    "function_scalar": "SQL_SCALAR_FUNCTION",
    "function_inline_tvf": "SQL_INLINE_TABLE_VALUED_FUNCTION",
    "function_tvf": "SQL_TABLE_VALUED_FUNCTION",
}


def diff_object_counts(
    expected: dict[tuple[str, str], int],
    actual: Iterable[dict],
) -> list[ObjectCountCheck]:
    """Compare two count maps keyed by ``(schema, type_desc)``."""
    actual_map: dict[tuple[str, str], int] = {}
    for row in actual:
        actual_map[(row["schema_name"], row["type_desc"])] = int(row["cnt"])

    keys = set(expected) | set(actual_map)
    out: list[ObjectCountCheck] = []
    for schema, type_desc in sorted(keys):
        exp = expected.get((schema, type_desc), 0)
        act = actual_map.get((schema, type_desc), 0)
        delta = act - exp
        if act == exp:
            status = "match"
        elif act < exp:
            status = "missing"
        else:
            status = "extra"
        out.append(ObjectCountCheck(
            schema_name=schema,
            object_kind=type_desc,
            expected=exp,
            actual=act,
            delta=delta,
            status=status,
        ))
    return out


def diff_row_counts(
    expected: dict[tuple[str, str], int],
    actual: dict[tuple[str, str], int | None],
    tolerance_pct: float = 0.0,
) -> list[RowCountCheck]:
    """Compare row counts; ``tolerance_pct`` (e.g. 0.01) accepts small drift."""
    out: list[RowCountCheck] = []
    keys = set(expected) | set(actual)
    for schema, table in sorted(keys):
        exp = expected.get((schema, table), 0)
        act = actual.get((schema, table))
        if act is None:
            out.append(RowCountCheck(
                schema_name=schema,
                table_name=table,
                expected_rows=exp,
                actual_rows=None,
                delta=None,
                status="missing",
            ))
            continue
        delta = act - exp
        within = exp == 0 or abs(delta) <= max(1, int(exp * tolerance_pct))
        out.append(RowCountCheck(
            schema_name=schema,
            table_name=table,
            expected_rows=exp,
            actual_rows=act,
            delta=delta,
            status="match" if within else "mismatch",
        ))
    return out


def diff_collation(
    expected: dict[tuple[str, str, str], str],
    actual: Iterable[dict],
) -> list[CollationCheck]:
    actual_map: dict[tuple[str, str, str], str | None] = {}
    for row in actual:
        actual_map[(row["schema_name"], row["table_name"], row["column_name"])] = row.get("collation")
    out: list[CollationCheck] = []
    for key, exp_collation in sorted(expected.items()):
        act_collation = actual_map.get(key)
        if act_collation is None:
            status = "missing"
        elif act_collation == exp_collation:
            status = "match"
        else:
            status = "mismatch"
        out.append(CollationCheck(
            schema_name=key[0],
            table_name=key[1],
            column_name=key[2],
            expected_collation=exp_collation,
            actual_collation=act_collation,
            status=status,
        ))
    return out


def evaluate_tsql_surface_resolutions(
    findings: Iterable[dict],
    fabric_objects_present: set[tuple[str, str]],
) -> list[TsqlSurfaceCheck]:
    """A finding is *resolved* if the offending object no longer exists in Fabric.

    This is intentionally coarse for v0 — full T-SQL re-scan against the Fabric
    Warehouse SQL endpoint is a follow-up. ``fabric_objects_present`` is the set
    of ``(schema, name)`` pairs that exist in Fabric.
    """
    out: list[TsqlSurfaceCheck] = []
    for f in findings:
        schema = f.get("schema_name")
        name = f.get("object_name")
        present = (schema, name) in fabric_objects_present if schema and name else False
        out.append(TsqlSurfaceCheck(
            code_object_id=str(f.get("code_object_id", "")),
            schema_name=schema,
            object_name=name,
            finding_id=str(f.get("finding_id") or f.get("rule_id", "")),
            expected_resolution=f.get("expected_resolution", "removed"),
            status="still_present" if present else "resolved",
        ))
    return out


def summarize(*checks: Iterable) -> dict[str, int]:
    """Roll up status counts across check categories."""
    summary: dict[str, int] = {}
    for batch in checks:
        for c in batch or []:
            status = getattr(c, "status", None)
            if status:
                summary[status] = summary.get(status, 0) + 1
    return summary
