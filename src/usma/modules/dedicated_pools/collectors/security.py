from __future__ import annotations

from collections import defaultdict

from ..models import SecurityPrincipal
from ..sql_client import DedicatedPoolSqlClient


def collect_security(sql: DedicatedPoolSqlClient) -> list[SecurityPrincipal]:
    rows = sql.fetch_query_file("security")
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows:
        key = (r["principal_name"], r["principal_type"])
        role = r.get("role_name")
        if role:
            grouped[key].append(role)
    return [
        SecurityPrincipal(name=name, type=ptype, role_memberships=sorted(set(roles)))
        for (name, ptype), roles in sorted(grouped.items())
    ]
