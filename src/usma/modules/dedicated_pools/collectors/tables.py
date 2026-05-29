from __future__ import annotations

from ..models import TableInfo
from ..sql_client import DedicatedPoolSqlClient


def collect_tables(sql: DedicatedPoolSqlClient) -> list[TableInfo]:
    rows = sql.fetch_query_file("tables")
    return [
        TableInfo(
            schema_name=r["schema_name"],
            table_name=r["table_name"],
            distribution_policy=r.get("distribution_policy"),
            distribution_column=r.get("distribution_column"),
            is_partitioned=bool(r.get("is_partitioned") or 0),
            partition_count=int(r.get("partition_count") or 0),
            row_count=r.get("row_count"),
            reserved_space_mb=r.get("reserved_space_mb"),
            data_space_mb=r.get("data_space_mb"),
            index_space_mb=r.get("index_space_mb"),
            index_type=r.get("index_type"),
        )
        for r in rows
    ]
