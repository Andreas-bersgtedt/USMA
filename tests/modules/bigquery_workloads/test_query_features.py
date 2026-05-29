"""Tests for the BigQuery sqlglot feature detector (Phase 3)."""
from __future__ import annotations

from usma.modules.bigquery_workloads.query_features import (
    detect_features,
    summarise_features,
)


def test_empty_returns_empty_list():
    assert detect_features(None) == []
    assert detect_features("") == []
    assert detect_features("   \n  ") == []


def test_merge_detected():
    sql = "MERGE INTO ds.tgt T USING ds.src S ON T.id = S.id WHEN MATCHED THEN UPDATE SET T.x = S.x"
    feats = detect_features(sql)
    assert "merge" in feats


def test_scripting_declare_set_begin():
    sql = """
    DECLARE counter INT64 DEFAULT 0;
    SET counter = counter + 1;
    BEGIN
      SELECT 1;
    END;
    """
    feats = detect_features(sql)
    assert "scripting-declare" in feats
    assert "scripting-set" in feats
    assert "scripting-begin-end" in feats


def test_procedure_creation_and_call():
    sql = "CREATE OR REPLACE PROCEDURE ds.do_thing() BEGIN SELECT 1; END;"
    feats = detect_features(sql)
    assert "procedure-create" in feats


def test_javascript_udf():
    sql = "CREATE TEMP FUNCTION f(x INT64) RETURNS INT64 LANGUAGE js AS 'return x * 2;';"
    feats = detect_features(sql)
    assert "udf-javascript" in feats


def test_ml_predict_and_geo():
    sql = "SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT ST_GEOGFROMTEXT('POINT(0 0)') AS g))"
    feats = detect_features(sql)
    assert "ml-predict" in feats
    assert "geo-function" in feats


def test_export_data():
    sql = "EXPORT DATA OPTIONS(uri='gs://b/*') AS SELECT 1"
    feats = detect_features(sql)
    assert "export-data" in feats


def test_unnest_and_array_literal():
    sql = "SELECT x FROM UNNEST([1, 2, 3]) AS x"
    feats = detect_features(sql)
    # AST pass catches these even when regex doesn't.
    assert "unnest" in feats
    assert "array-literal" in feats


def test_window_function_via_ast():
    sql = "SELECT ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) FROM t"
    feats = detect_features(sql)
    assert "window-function" in feats


def test_parse_failure_still_returns_regex_hits():
    # Intentionally garbled SQL — sqlglot will fail but the regex pass
    # should still surface the MERGE signal.
    sql = "MERGE INTO ::: SYNTAX ERROR ::: but the word MERGE is here"
    feats = detect_features(sql)
    assert "merge" in feats


def test_summarise_counts_distinct_jobs_only():
    # Two jobs, both have merge; the second also has unnest.
    job_features = [
        ["merge", "merge", "scripting-declare"],  # duplicate merge in same job → still 1
        ["merge", "unnest"],
    ]
    summary = summarise_features(job_features)
    assert summary["merge"] == 2
    assert summary["unnest"] == 1
    assert summary["scripting-declare"] == 1
    # Sorted descending by count.
    assert list(summary.keys())[0] == "merge"


def test_summarise_empty_input():
    assert summarise_features([]) == {}
    assert summarise_features([[], None]) == {}  # type: ignore[list-item]
