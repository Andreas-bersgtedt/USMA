"""Tests for SQL-plane code-object compatibility classification + summary rollup."""
from __future__ import annotations

from usma.modules.dedicated_pools.models import CodeObject, TsqlSurfaceGap
from usma.modules.dedicated_pools.tsql_surface_gap import (
    classify_compatibility,
    normalize_object_type,
    stable_code_object_id,
    stamp_compatibility,
    summarize_code_objects,
)


# --- classify_compatibility --------------------------------------------------

def test_classify_compatibility_blocker_wins():
    assert classify_compatibility(["info", "warning", "blocker"]) == "incompatible"


def test_classify_compatibility_warning_only():
    assert classify_compatibility(["info", "warning"]) == "needs_review"


def test_classify_compatibility_info_or_empty():
    assert classify_compatibility([]) == "compatible"
    assert classify_compatibility(["info"]) == "compatible"


def test_classify_compatibility_handles_none_and_case():
    # Defensive: empty / None entries should not crash; case-insensitive.
    assert classify_compatibility([None, "", "BLOCKER"]) == "incompatible"  # type: ignore[list-item]


# --- normalize_object_type ---------------------------------------------------

def test_normalize_object_type_known_buckets():
    assert normalize_object_type("SQL_STORED_PROCEDURE") == "procedure"
    assert normalize_object_type("VIEW") == "view"
    assert normalize_object_type("SQL_SCALAR_FUNCTION") == "scalar_function"
    assert normalize_object_type("SQL_INLINE_TABLE_VALUED_FUNCTION") == "inline_tvf"
    assert normalize_object_type("SQL_TABLE_VALUED_FUNCTION") == "multi_stmt_tvf"


def test_normalize_object_type_unknown_to_other():
    assert normalize_object_type(None) == "other"
    assert normalize_object_type("EXTENDED_PROCEDURE") == "other"


# --- stamp_compatibility -----------------------------------------------------

def _obj(schema: str, name: str, type_: str = "SQL_STORED_PROCEDURE") -> CodeObject:
    cid = stable_code_object_id(schema, name, type_)
    return CodeObject(
        schema_name=schema,
        object_name=name,
        object_type=type_,
        code_object_id=cid,
    )


def _gap(cid: str, severity: str, rule: str = "merge") -> TsqlSurfaceGap:
    return TsqlSurfaceGap(
        code_object_id=cid,
        schema_name="dbo",
        object_name="x",
        object_type="SQL_STORED_PROCEDURE",
        rule_id=rule,
        label=rule,
        severity=severity,
        matches=1,
        fabric_action=None,
    )


def test_stamp_compatibility_marks_objects():
    a = _obj("dbo", "with_blocker")
    b = _obj("dbo", "with_warning")
    c = _obj("dbo", "clean")
    gaps = [
        _gap(a.code_object_id, "blocker", "merge"),
        _gap(a.code_object_id, "info", "rowversion"),
        _gap(b.code_object_id, "warning", "cursor"),
    ]

    stamp_compatibility([a, b, c], gaps)

    assert a.compatibility == "incompatible"
    assert a.gap_count == 2
    assert "blocker" in a.gap_severities
    assert b.compatibility == "needs_review"
    assert b.gap_count == 1
    assert c.compatibility == "compatible"
    assert c.gap_count == 0
    assert c.gap_severities == []


# --- summarize_code_objects --------------------------------------------------

def test_summarize_code_objects_rollup_math():
    objs = [
        _obj("dbo", "p1"),
        _obj("dbo", "p2"),
        _obj("dbo", "v1", "VIEW"),
        _obj("dbo", "f1", "SQL_SCALAR_FUNCTION"),
    ]
    # p1 incompatible, p2 needs_review, v1 compatible, f1 compatible
    objs[0].compatibility = "incompatible"
    objs[1].compatibility = "needs_review"
    objs[2].compatibility = "compatible"
    objs[3].compatibility = "compatible"

    s = summarize_code_objects(objs)

    assert s.total == 4
    assert s.by_type == {"procedure": 2, "scalar_function": 1, "view": 1}
    assert s.by_compatibility == {
        "compatible": 2, "needs_review": 1, "incompatible": 1,
    }
    # 2 / 4 compatible -> 50.0%
    assert s.compatibility_pct == 50.0
    assert objs[0].code_object_id in s.incompatible_object_ids
    assert objs[1].code_object_id in s.needs_review_object_ids


def test_summarize_code_objects_empty():
    s = summarize_code_objects([])
    assert s.total == 0
    assert s.compatibility_pct is None
    assert s.by_type == {}
