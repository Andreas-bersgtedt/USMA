from __future__ import annotations

from ..models import MaterializedView
from ..sql_client import DedicatedPoolSqlClient


def collect_materialized_views(sql: DedicatedPoolSqlClient) -> list[MaterializedView]:
    rows = sql.fetch_query_file("materialized_views")
    return [
        MaterializedView(
            schema_name=r["schema_name"],
            view_name=r["view_name"],
            create_date=r.get("create_date"),
            modify_date=r.get("modify_date"),
            definition=r.get("definition"),
        )
        for r in rows
    ]
