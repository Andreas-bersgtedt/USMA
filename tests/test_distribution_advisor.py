"""Tests for the dedicated_pools distribution-key advisor."""
from usma.modules.dedicated_pools import distribution_advisor


def test_high_cardinality_id_column_scores_above_low_cardinality():
    tables = [{
        "schema_name": "dbo", "table_name": "fact_sales",
        "distribution_policy": "ROUND_ROBIN", "row_count": 200_000_000,
    }]
    indexes = [{
        "schema_name": "dbo", "table_name": "fact_sales",
        "first_key_column": "sale_id",
    }]
    column_stats = [
        {"schema_name": "dbo", "table_name": "fact_sales", "column_name": "sale_id",
         "row_count": 200_000_000, "distinct_count": 200_000_000, "null_count": 0,
         "data_type": "bigint", "is_nullable": False, "is_unique": True, "max_length": 8},
        {"schema_name": "dbo", "table_name": "fact_sales", "column_name": "country",
         "row_count": 200_000_000, "distinct_count": 50, "null_count": 0,
         "data_type": "nvarchar", "is_nullable": False, "is_unique": False, "max_length": 100},
    ]
    cands = distribution_advisor.score_columns(
        tables=tables, indexes=indexes, column_stats=column_stats, top_n=2
    )
    assert cands, "expected at least one candidate"
    top = cands[0]
    assert top.column_name == "sale_id"
    assert top.score > 50


def test_hash_distributed_table_is_skipped():
    tables = [{
        "schema_name": "dbo", "table_name": "fact_already_hash",
        "distribution_policy": "HASH", "row_count": 200_000_000,
    }]
    cands = distribution_advisor.score_columns(tables=tables, indexes=[], column_stats=[])
    assert cands == []


def test_falls_back_to_first_key_when_no_column_stats():
    tables = [{
        "schema_name": "dbo", "table_name": "t",
        "distribution_policy": "ROUND_ROBIN", "row_count": 5_000_000,
    }]
    indexes = [{"schema_name": "dbo", "table_name": "t", "first_key_column": "k"}]
    cands = distribution_advisor.score_columns(tables=tables, indexes=indexes, column_stats=[])
    assert cands and cands[0].column_name == "k"
