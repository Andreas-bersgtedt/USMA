from __future__ import annotations

from ..models import ColumnStat
from ..sql_client import DedicatedPoolSqlClient


def collect_column_stats(sql: DedicatedPoolSqlClient) -> list[ColumnStat]:
    rows = sql.fetch_query_file("column_stats")
    return [
        ColumnStat(
            schema_name=r["schema_name"],
            table_name=r["table_name"],
            column_name=r["column_name"],
            data_type=r.get("data_type"),
            max_length=r.get("max_length"),
            is_nullable=bool(r.get("is_nullable")) if r.get("is_nullable") is not None else True,
            row_count=r.get("row_count"),
            distinct_count=r.get("distinct_count"),
            null_count=r.get("null_count"),
            max_frequency=r.get("max_frequency"),
        )
        for r in rows
    ]
