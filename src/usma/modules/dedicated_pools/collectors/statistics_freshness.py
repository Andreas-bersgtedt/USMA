from __future__ import annotations

from ..models import StatisticInfo
from ..sql_client import DedicatedPoolSqlClient


def collect_statistics(sql: DedicatedPoolSqlClient) -> list[StatisticInfo]:
    rows = sql.fetch_query_file("statistics_freshness")
    return [
        StatisticInfo(
            schema_name=r["schema_name"],
            table_name=r["table_name"],
            stat_name=r["stat_name"],
            user_created=bool(r.get("user_created") or 0),
            auto_created=bool(r.get("auto_created") or 0),
            last_updated=r.get("last_updated"),
            rows=r.get("rows"),
            rows_sampled=r.get("rows_sampled"),
            modification_counter=r.get("modification_counter"),
            days_since_update=r.get("days_since_update"),
        )
        for r in rows
    ]
