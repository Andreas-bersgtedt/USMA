"""Tests for serverless storage-account cost attribution."""
from usma.modules.serverless_pools import cost_attribution


def test_extracts_host_from_openrowset_url_and_sorts_descending():
    queries = [
        {
            "command_text": "SELECT * FROM OPENROWSET(BULK 'abfss://c@sa1.dfs.core.windows.net/x', "
                            "FORMAT='PARQUET') AS r",
            "data_processed_mb": 200,
        },
        {
            "command_text": "SELECT * FROM OPENROWSET(BULK 'abfss://c@sa2.dfs.core.windows.net/y', "
                            "FORMAT='PARQUET') AS r",
            "data_processed_mb": 800,
        },
        {
            "command_text": "SELECT * FROM OPENROWSET(BULK 'abfss://c@sa1.dfs.core.windows.net/z', "
                            "FORMAT='PARQUET') AS r",
            "data_processed_mb": 100,
        },
    ]
    out = cost_attribution.attribute(top_queries=queries)
    assert [a.storage_account for a in out] == [
        "sa2.dfs.core.windows.net", "sa1.dfs.core.windows.net",
    ]
    assert out[0].data_processed_mb == 800
    assert out[1].data_processed_mb == 300


def test_query_with_no_url_is_ignored():
    out = cost_attribution.attribute(top_queries=[
        {"command_text": "SELECT 1", "data_processed_mb": 0}
    ])
    assert out == []
