from __future__ import annotations

from ..models import WorkloadGroup
from ..sql_client import DedicatedPoolSqlClient


def collect_workload_groups(sql: DedicatedPoolSqlClient) -> list[WorkloadGroup]:
    rows = sql.fetch_query_file("workload")
    return [
        WorkloadGroup(
            name=r["name"],
            classifier_count=int(r.get("classifier_count") or 0),
            importance=r.get("importance"),
            min_resource_pct=r.get("min_resource_pct"),
            cap_resource_pct=r.get("cap_resource_pct"),
            request_min_resource_grant_pct=r.get("request_min_resource_grant_pct"),
        )
        for r in rows
    ]
