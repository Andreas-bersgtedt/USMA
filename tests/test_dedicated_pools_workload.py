"""Unit tests for the dedicated-pool workload parser + cache."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from usma.modules.dedicated_pools.workload_cache import (
    CACHE_SCHEMA_VERSION,
    cache_bounds,
    cache_path,
    load,
    merge,
    prune,
    save,
)
from usma.modules.dedicated_pools.workload_parser import (
    build_catalog,
    parse_command,
)


# ---------------------------------------------------------------------------
# Catalog fixture: two schemas owning the same table name so the parser has
# to exercise the qualified / unqualified-resolved / ambiguous branches.
# ---------------------------------------------------------------------------

@pytest.fixture
def catalog():
    rows = [
        {"schema_name": "dbo", "object_name": "fact_sales", "object_type": "table"},
        {"schema_name": "stg", "object_name": "fact_sales", "object_type": "table"},
        {"schema_name": "dbo", "object_name": "dim_customer", "object_type": "table"},
        {"schema_name": "rpt", "object_name": "v_revenue", "object_type": "view"},
    ]
    return build_catalog(rows)


def test_qualified_two_part(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        "SELECT * FROM dbo.fact_sales WHERE region = 'EU';",
        by_name,
        by_qualified,
    )
    assert status == "ok"
    assert len(refs) == 1
    assert refs[0].schema_name == "dbo"
    assert refs[0].object_name == "fact_sales"
    assert refs[0].match_kind == "qualified"


def test_unqualified_resolved(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        "SELECT customer_id FROM dim_customer;",
        by_name,
        by_qualified,
    )
    assert status == "ok"
    assert len(refs) == 1
    assert refs[0].schema_name == "dbo"
    assert refs[0].match_kind == "unqualified-resolved"


def test_ambiguous_attribution_to_each_owner(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        "SELECT COUNT(*) FROM fact_sales;",
        by_name,
        by_qualified,
    )
    assert status == "ok"
    schemas = sorted(r.schema_name.lower() for r in refs)
    assert schemas == ["dbo", "stg"]
    assert all(r.match_kind == "ambiguous" for r in refs)


def test_cte_names_are_ignored(catalog):
    by_name, by_qualified = catalog
    sql = """
        WITH fact_sales AS (
            SELECT 1 AS x
        )
        SELECT * FROM fact_sales JOIN dbo.dim_customer ON 1=1;
    """
    refs, status = parse_command(sql, by_name, by_qualified)
    assert status == "ok"
    names = {(r.schema_name.lower(), r.object_name.lower()) for r in refs}
    # CTE-shadowed `fact_sales` must not be in the result.
    assert ("dbo", "fact_sales") not in names
    assert ("stg", "fact_sales") not in names
    assert ("dbo", "dim_customer") in names


def test_unknown_tables_are_dropped(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        "SELECT * FROM dbo.does_not_exist;",
        by_name,
        by_qualified,
    )
    assert status == "empty"
    assert refs == []


def test_temp_tables_are_ignored(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        "SELECT * FROM #temp JOIN dbo.fact_sales ON 1=1;",
        by_name,
        by_qualified,
    )
    assert status == "ok"
    names = {(r.schema_name, r.object_name) for r in refs}
    assert names == {("dbo", "fact_sales")}


def test_three_part_name_is_qualified(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        "SELECT * FROM mydb.dbo.fact_sales;",
        by_name,
        by_qualified,
    )
    assert status == "ok"
    assert len(refs) == 1
    assert refs[0].match_kind == "qualified"


def test_view_object_type(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        "SELECT * FROM rpt.v_revenue;",
        by_name,
        by_qualified,
    )
    assert status == "ok"
    assert refs[0].object_type == "view"


def test_garbage_command_returns_failed(catalog):
    by_name, by_qualified = catalog
    # sqlglot rejects this nonsense at the lexer.
    refs, status = parse_command("@@!!", by_name, by_qualified)
    assert refs == []
    assert status in {"failed", "empty"}


def test_empty_command():
    by_name, by_qualified = build_catalog([])
    refs, status = parse_command("   ", by_name, by_qualified)
    assert status == "empty"
    assert refs == []


def test_quoted_identifiers(catalog):
    by_name, by_qualified = catalog
    refs, status = parse_command(
        'SELECT * FROM [dbo].[fact_sales];',
        by_name,
        by_qualified,
    )
    assert status == "ok"
    assert refs[0].schema_name == "dbo"
    assert refs[0].object_name == "fact_sales"
    assert refs[0].match_kind == "qualified"


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _make_entry(rid: str, days_ago: int = 0) -> dict:
    submit = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "request_id": rid,
        "submit_time": submit.isoformat(),
        "elapsed_ms": 1000,
        "tables": [
            {"schema_name": "dbo", "object_name": "t1", "object_type": "table",
             "match_kind": "qualified"},
        ],
    }


def test_cache_save_load_roundtrip(tmp_path: Path):
    path = cache_path(tmp_path, "ws", "pool")
    cache = {"r1": _make_entry("r1")}
    save(path, "ws", "pool", cache)
    loaded = load(path)
    assert "r1" in loaded
    assert loaded["r1"]["request_id"] == "r1"


def test_cache_load_missing_returns_empty(tmp_path: Path):
    path = tmp_path / "nope.json"
    assert load(path) == {}


def test_cache_load_corrupt_returns_empty(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    assert load(path) == {}


def test_cache_load_wrong_version_discards(tmp_path: Path):
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps({"version": CACHE_SCHEMA_VERSION + 99, "requests": []}),
        encoding="utf-8",
    )
    assert load(path) == {}


def test_cache_merge_dedups_by_request_id():
    existing = {"r1": _make_entry("r1")}
    new = [_make_entry("r1"), _make_entry("r2")]
    merged = merge(existing, new)
    assert set(merged.keys()) == {"r1", "r2"}


def test_cache_prune_drops_old_entries():
    cache = {
        "old": _make_entry("old", days_ago=60),
        "new": _make_entry("new", days_ago=1),
    }
    pruned = prune(cache, ttl_days=30)
    assert set(pruned.keys()) == {"new"}


def test_cache_bounds():
    cache = {
        "a": _make_entry("a", days_ago=5),
        "b": _make_entry("b", days_ago=1),
    }
    oldest, newest = cache_bounds(cache)
    assert oldest is not None and newest is not None
    assert oldest < newest


def test_cache_path_sanitizes_segments(tmp_path: Path):
    path = cache_path(tmp_path, "ws/with:bad..chars", "pool name")
    assert "/" not in path.name
    assert ":" not in path.name
    assert path.parent.exists()
