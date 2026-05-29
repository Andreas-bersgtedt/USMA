from __future__ import annotations

from ..models import IndexInfo
from ..sql_client import DedicatedPoolSqlClient


def collect_indexes(sql: DedicatedPoolSqlClient) -> list[IndexInfo]:
    rows = sql.fetch_query_file("indexes")
    return [
        IndexInfo(
            schema_name=r["schema_name"],
            table_name=r["table_name"],
            index_name=r.get("index_name"),
            index_type=r["index_type"],
            is_unique=bool(r.get("is_unique") or 0),
            is_primary_key=bool(r.get("is_primary_key") or 0),
            first_key_column=r.get("first_key_column"),
        )
        for r in rows
    ]
