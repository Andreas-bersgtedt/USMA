from __future__ import annotations

from ..models import SchemaInfo
from ..sql_client import DedicatedPoolSqlClient


def collect_schemas(sql: DedicatedPoolSqlClient) -> list[SchemaInfo]:
    rows = sql.fetch_query_file("schemas")
    return [SchemaInfo(schema_name=r["schema_name"], object_count=int(r["object_count"])) for r in rows]
