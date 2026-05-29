from __future__ import annotations

from ..models import ColumnCollation
from ..sql_client import DedicatedPoolSqlClient


def collect_column_collations(sql: DedicatedPoolSqlClient) -> list[ColumnCollation]:
    rows = sql.fetch_query_file("column_collation")
    return [
        ColumnCollation(
            schema_name=r["schema_name"],
            table_name=r["table_name"],
            column_name=r["column_name"],
            data_type=r.get("data_type"),
            max_length=r.get("max_length"),
            collation_name=r.get("collation_name"),
            db_collation=r.get("db_collation"),
            differs_from_db=bool(r.get("differs_from_db") or 0),
        )
        for r in rows
    ]
