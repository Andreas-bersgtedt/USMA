"""Unit tests for fabric_validation pure-Python diff helpers."""
from __future__ import annotations

from usma.modules.fabric_validation.diff import (
    diff_collation,
    diff_object_counts,
    diff_row_counts,
    evaluate_tsql_surface_resolutions,
    summarize,
)


def test_diff_object_counts_match_missing_extra() -> None:
    expected = {("dbo", "USER_TABLE"): 5, ("sales", "VIEW"): 2}
    actual = [
        {"schema_name": "dbo", "type_desc": "USER_TABLE", "cnt": 5},
        {"schema_name": "sales", "type_desc": "VIEW", "cnt": 1},
        {"schema_name": "extra", "type_desc": "USER_TABLE", "cnt": 3},
    ]
    diff = diff_object_counts(expected, actual)
    by_key = {(c.schema_name, c.object_kind): c for c in diff}
    assert by_key[("dbo", "USER_TABLE")].status == "match"
    assert by_key[("sales", "VIEW")].status == "missing"
    assert by_key[("sales", "VIEW")].delta == -1
    assert by_key[("extra", "USER_TABLE")].status == "extra"
    assert by_key[("extra", "USER_TABLE")].delta == 3


def test_diff_row_counts_with_tolerance() -> None:
    expected = {("dbo", "fact"): 1_000_000}
    actual = {("dbo", "fact"): 1_000_005}
    out = diff_row_counts(expected, actual, tolerance_pct=0.0)
    assert out[0].status == "mismatch"
    out = diff_row_counts(expected, actual, tolerance_pct=0.001)  # 0.1% tolerance
    assert out[0].status == "match"


def test_diff_row_counts_missing_table() -> None:
    expected = {("dbo", "missing"): 100}
    actual: dict = {("dbo", "missing"): None}
    out = diff_row_counts(expected, actual)
    assert out[0].status == "missing"


def test_diff_collation_match_and_mismatch() -> None:
    expected = {
        ("dbo", "t1", "c1"): "Latin1_General_100_CI_AS_SC_UTF8",
        ("dbo", "t1", "c2"): "SQL_Latin1_General_CP1_CI_AS",
    }
    actual = [
        {"schema_name": "dbo", "table_name": "t1", "column_name": "c1",
         "collation": "Latin1_General_100_CI_AS_SC_UTF8"},
        {"schema_name": "dbo", "table_name": "t1", "column_name": "c2",
         "collation": "Latin1_General_100_BIN2"},
    ]
    out = {(c.column_name): c for c in diff_collation(expected, actual)}
    assert out["c1"].status == "match"
    assert out["c2"].status == "mismatch"


def test_evaluate_tsql_surface_resolved_when_object_absent() -> None:
    findings = [
        {"code_object_id": "co1", "schema_name": "dbo", "object_name": "old_proc",
         "finding_id": "tsql.merge"},
    ]
    out = evaluate_tsql_surface_resolutions(findings, fabric_objects_present=set())
    assert out[0].status == "resolved"


def test_evaluate_tsql_surface_still_present_when_object_in_target() -> None:
    findings = [
        {"code_object_id": "co1", "schema_name": "dbo", "object_name": "old_proc",
         "finding_id": "tsql.merge"},
    ]
    out = evaluate_tsql_surface_resolutions(
        findings, fabric_objects_present={("dbo", "old_proc")},
    )
    assert out[0].status == "still_present"


def test_summarize_rolls_up_status_counts() -> None:
    obj = diff_object_counts({("a", "T"): 1, ("b", "T"): 1},
                             [{"schema_name": "a", "type_desc": "T", "cnt": 1}])
    rows = diff_row_counts({("a", "t"): 5}, {("a", "t"): 5})
    summary = summarize(obj, rows)
    assert summary.get("match", 0) >= 2
    assert summary.get("missing", 0) >= 1
