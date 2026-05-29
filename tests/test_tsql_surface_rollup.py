from usma.modules.dedicated_pools import (
    query_pattern_extractor,
    tsql_surface_gap,
    distribution_advisor,
)
from usma.modules.dedicated_pools.models import CodeObject


def test_stable_code_object_id_is_deterministic():
    a = tsql_surface_gap.stable_code_object_id("dbo", "MyProc", "SQL_STORED_PROCEDURE")
    b = tsql_surface_gap.stable_code_object_id("dbo", "MyProc", "SQL_STORED_PROCEDURE")
    assert a == b
    assert a == "dbo.myproc.sql_stored_procedure"


def test_stable_code_object_id_handles_messy_input():
    # spaces, brackets, mixed case all collapse to safe slug
    cid = tsql_surface_gap.stable_code_object_id("My Schema", "[Weird Name!]", "view")
    assert cid == "my_schema.weird_name.view"


def test_build_gaps_links_findings_to_stable_id():
    objs = [
        CodeObject(
            schema_name="dbo", object_name="proc_a", object_type="SQL_STORED_PROCEDURE",
            definition="MERGE INTO target USING src ON target.id = src.id;",
            code_object_id="dbo.proc_a.sql_stored_procedure",
        ),
        CodeObject(
            schema_name="dbo", object_name="proc_b", object_type="SQL_STORED_PROCEDURE",
            definition="DECLARE c CURSOR FOR SELECT 1; OPEN c;",
            code_object_id="dbo.proc_b.sql_stored_procedure",
        ),
    ]
    gaps = tsql_surface_gap.build_gaps(objs)
    by_obj = {(g.code_object_id, g.rule_id) for g in gaps}
    assert ("dbo.proc_a.sql_stored_procedure", "merge") in by_obj
    assert ("dbo.proc_b.sql_stored_procedure", "cursor") in by_obj
    # Each gap carries a fabric_action.
    assert all(g.fabric_action for g in gaps)


def test_build_gaps_falls_back_to_computed_id_when_missing():
    objs = [CodeObject(
        schema_name="dbo", object_name="vNoId", object_type="VIEW",
        definition="MERGE INTO x USING y ON x.k = y.k;",
        code_object_id=None,
    )]
    gaps = tsql_surface_gap.build_gaps(objs)
    assert gaps and gaps[0].code_object_id == "dbo.vnoid.view"


def test_summarize_per_object_aggregates_counts():
    objs = [CodeObject(
        schema_name="dbo", object_name="proc_x", object_type="SQL_STORED_PROCEDURE",
        definition="MERGE INTO a USING b ON a.k=b.k; DECLARE c CURSOR FOR SELECT 1; OPEN c;",
        code_object_id="dbo.proc_x.sql_stored_procedure",
    )]
    gaps = tsql_surface_gap.build_gaps(objs)
    summary = tsql_surface_gap.summarize_per_object(gaps)
    assert len(summary) == 1
    assert summary[0].code_object_id == "dbo.proc_x.sql_stored_procedure"
    assert summary[0].rule_count >= 2


def test_extract_filter_usage_counts_predicate_columns():
    objs = [
        {"schema_name": "dbo", "object_name": "p1", "definition":
            "SELECT * FROM dbo.orders o WHERE o.customer_id = @c AND o.status = 'X'"},
        {"schema_name": "dbo", "object_name": "p2", "definition":
            "SELECT * FROM dbo.orders WHERE customer_id IN (1,2,3)"},
    ]
    usage = query_pattern_extractor.extract_filter_usage(objs)
    assert usage[("dbo", "orders", "customer_id")] >= 2
    assert usage[("dbo", "orders", "status")] >= 1


def test_extract_filter_usage_handles_join_predicates():
    objs = [
        {"schema_name": "dbo", "object_name": "p", "definition":
            "SELECT * FROM dbo.orders o JOIN dbo.customer c ON o.customer_id = c.id "
            "WHERE c.region = 'EU'"},
    ]
    usage = query_pattern_extractor.extract_filter_usage(objs)
    # ON predicate columns from both sides are counted.
    assert usage.get(("dbo", "orders", "customer_id"), 0) >= 1
    assert usage.get(("dbo", "customer", "id"), 0) >= 1


def test_distribution_advisor_filter_usage_breaks_ties():
    tables = [{
        "schema_name": "dbo", "table_name": "orders",
        "distribution_policy": "ROUND_ROBIN", "row_count": 10_000_000,
    }]
    column_stats = [
        {"schema_name": "dbo", "table_name": "orders", "column_name": "customer_id",
         "data_type": "int", "is_nullable": False, "row_count": 10_000_000,
         "distinct_count": 5_000_000, "null_count": 0, "max_frequency": 50_000},
        {"schema_name": "dbo", "table_name": "orders", "column_name": "order_id",
         "data_type": "int", "is_nullable": False, "row_count": 10_000_000,
         "distinct_count": 5_000_000, "null_count": 0, "max_frequency": 50_000},
    ]
    # Without filter usage, both columns score similarly.
    base = distribution_advisor.score_columns(
        tables=tables, indexes=[], column_stats=column_stats,
    )
    # With filter usage favoring customer_id, it should rank first and score higher.
    boosted = distribution_advisor.score_columns(
        tables=tables, indexes=[], column_stats=column_stats,
        filter_usage={("dbo", "orders", "customer_id"): 12},
    )
    assert boosted[0].column_name == "customer_id"
    base_customer = next(c for c in base if c.column_name == "customer_id")
    boost_customer = next(c for c in boosted if c.column_name == "customer_id")
    assert boost_customer.score > base_customer.score
    assert any("filter usage" in r for r in boost_customer.reasons)
