"""Collector functions. Each collector takes a `DedicatedPoolSqlClient` and returns a list of model objects."""
from __future__ import annotations

from .code_objects import collect_code_objects
from .column_collation import collect_column_collations
from .column_stats import collect_column_stats
from .indexes import collect_indexes
from .materialized_views import collect_materialized_views
from .schema import collect_schemas
from .security import collect_security
from .statistics_freshness import collect_statistics
from .tables import collect_tables
from .top_consumed_objects import collect_top_consumed_objects
from .top_queries import collect_top_queries
from .usage import collect_usage
from .workload import collect_workload_groups

__all__ = [
    "collect_schemas",
    "collect_tables",
    "collect_indexes",
    "collect_usage",
    "collect_security",
    "collect_workload_groups",
    "collect_code_objects",
    # v2
    "collect_column_collations",
    "collect_materialized_views",
    "collect_statistics",
    "collect_column_stats",
    "collect_top_queries",
    "collect_top_consumed_objects",
]
