from __future__ import annotations

from datetime import datetime

from ..models import UsageStat
from ..sql_client import DedicatedPoolSqlClient


def collect_usage(sql: DedicatedPoolSqlClient) -> list[UsageStat]:
    rows = sql.fetch_query_file("usage")
    out: list[UsageStat] = []
    for r in rows:
        captured = r.get("captured_at")
        if captured is not None and not isinstance(captured, datetime):
            captured = None
        out.append(
            UsageStat(
                metric=r["metric"],
                value=r.get("value"),
                unit=r.get("unit"),
                captured_at=captured,
            )
        )
    return out
