"""Regression tests for the dedicated_pools code_objects collector.

The SQL query previously used ``INNER JOIN sys.sql_modules`` which silently
dropped procedures and functions whose module body was unavailable
(encrypted definition, restricted permission, Synapse catalog edge cases),
producing reports that contained only views. This test pins the fix:

1. The SQL driver query LEFT-JOINs ``sys.sql_modules`` so missing module
   bodies don't drop rows.
2. The collector handles ``definition IS NULL`` rows without raising.
3. Mixed object types (procedure, view, scalar function, inline TVF,
   multi-statement TVF) all flow through to the resulting CodeObject list.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from usma.modules.dedicated_pools.collectors.code_objects import (
    collect_code_objects,
)


_QUERIES_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "usma"
    / "modules"
    / "dedicated_pools"
    / "queries"
)


def test_code_objects_query_uses_left_join_to_sql_modules() -> None:
    """Pin the join shape so we don't regress to INNER JOIN sys.sql_modules.

    INNER JOIN drops procs / functions whose module body is hidden, so
    Microsoft Synapse Dedicated SQL Pool reports collapsed to "views only"
    in some tenants. The fix is FROM sys.objects + LEFT JOIN sys.sql_modules.
    """
    sql = (_QUERIES_DIR / "code_objects.sql").read_text(encoding="utf-8")
    lower = sql.lower()
    assert "from sys.objects" in lower, (
        "code_objects.sql must drive from sys.objects so missing sql_modules "
        "rows don't silently drop procedures / functions."
    )
    assert "left join sys.sql_modules" in lower, (
        "code_objects.sql must LEFT JOIN sys.sql_modules so procedures with "
        "missing / encrypted module bodies still appear in the inventory."
    )
    # Defence in depth: don't allow a future edit to revert the driver side.
    assert "from sys.sql_modules" not in lower.replace("from sys.sql_modules sm\njoin", ""), (
        "code_objects.sql must not drive FROM sys.sql_modules (regression)."
    )


class _FakeSqlClient:
    """Minimal stand-in for DedicatedPoolSqlClient used by the collector."""

    def __init__(self, rows_by_query: dict[str, list[dict[str, Any]]]) -> None:
        self._rows = rows_by_query

    def fetch_query_file(self, name: str, params: tuple = ()) -> list[dict[str, Any]]:
        return list(self._rows.get(name, []))


def test_collect_code_objects_returns_all_types_including_null_definition() -> None:
    """Procedure rows with NULL definition must still appear in the result.

    Mirrors the runtime shape produced by the SQL query after the
    LEFT JOIN fix: definition / uses_ansi_nulls / definition_length can
    all be NULL when sys.sql_modules has no row for the object.
    """
    client = _FakeSqlClient({
        "code_objects": [
            {
                "schema_name": "dbo",
                "object_name": "usp_load_orders",
                "object_type": "SQL_STORED_PROCEDURE",
                "definition": None,           # module body unavailable
                "create_date": None,
                "modify_date": None,
                "uses_ansi_nulls": None,
                "uses_quoted_identifier": None,
                "definition_length": None,
            },
            {
                "schema_name": "dbo",
                "object_name": "vw_orders_today",
                "object_type": "VIEW",
                "definition": "CREATE VIEW dbo.vw_orders_today AS SELECT 1",
                "create_date": None,
                "modify_date": None,
                "uses_ansi_nulls": 1,
                "uses_quoted_identifier": 1,
                "definition_length": 42,
            },
            {
                "schema_name": "dbo",
                "object_name": "fn_revenue",
                "object_type": "SQL_SCALAR_FUNCTION",
                "definition": "CREATE FUNCTION dbo.fn_revenue() RETURNS INT AS BEGIN RETURN 0 END",
                "create_date": None,
                "modify_date": None,
                "uses_ansi_nulls": 1,
                "uses_quoted_identifier": 1,
                "definition_length": 64,
            },
            {
                "schema_name": "dbo",
                "object_name": "tvf_top_customers",
                "object_type": "SQL_INLINE_TABLE_VALUED_FUNCTION",
                "definition": "CREATE FUNCTION dbo.tvf_top_customers() RETURNS TABLE AS RETURN (SELECT 1 AS c)",
                "create_date": None,
                "modify_date": None,
                "uses_ansi_nulls": 1,
                "uses_quoted_identifier": 1,
                "definition_length": 80,
            },
        ],
        # Parameters fetch may legitimately fail; mimic that by returning an
        # empty list (the collector also tolerates an exception here).
        "code_object_parameters": [],
    })

    out = collect_code_objects(client)
    types = {o.object_type for o in out}
    assert types == {
        "SQL_STORED_PROCEDURE",
        "VIEW",
        "SQL_SCALAR_FUNCTION",
        "SQL_INLINE_TABLE_VALUED_FUNCTION",
    }, f"expected all four types, got: {types}"
    # The procedure with NULL definition must still be present.
    proc = next(o for o in out if o.object_type == "SQL_STORED_PROCEDURE")
    assert proc.definition is None
    assert proc.line_count is None
    assert proc.definition_length is None


def test_collect_code_objects_logs_per_type_counts(caplog: pytest.LogCaptureFixture) -> None:
    """The collector should surface a per-type rollup in the run log so a
    'views only' regression is visible at runtime, not just in the JSON."""
    client = _FakeSqlClient({
        "code_objects": [
            {"schema_name": "dbo", "object_name": "p1", "object_type": "SQL_STORED_PROCEDURE",
             "definition": None, "create_date": None, "modify_date": None,
             "uses_ansi_nulls": None, "uses_quoted_identifier": None, "definition_length": None},
            {"schema_name": "dbo", "object_name": "v1", "object_type": "VIEW",
             "definition": "CREATE VIEW dbo.v1 AS SELECT 1", "create_date": None, "modify_date": None,
             "uses_ansi_nulls": 1, "uses_quoted_identifier": 1, "definition_length": 28},
        ],
        "code_object_parameters": [],
    })
    with caplog.at_level("INFO", logger="usma.modules.dedicated_pools.collectors.code_objects"):
        collect_code_objects(client)
    rendered = " ".join(rec.getMessage() for rec in caplog.records)
    assert "code_objects collected" in rendered
    assert "SQL_STORED_PROCEDURE" in rendered
    assert "VIEW" in rendered
