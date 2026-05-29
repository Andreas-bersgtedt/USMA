from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MetricSeries(BaseModel):
    resource_id: str
    resource_kind: str  # "dedicated_pool"
    resource_name: str
    metric_name: str
    unit: str | None = None
    aggregation: str  # average | total | maximum | minimum
    interval: str  # ISO 8601 duration, e.g. PT1H
    points: list[tuple[datetime, float | None]] = Field(default_factory=list)
    min_value: float | None = None
    max_value: float | None = None
    avg_value: float | None = None
    p95_value: float | None = None


# --- v2 -----------------------------------------------------------------------

class DwuDayStat(BaseModel):
    pool_name: str
    day: str   # ISO 8601 date string
    active_hours: float = 0.0
    active_dwu_hours: float = 0.0
    peak_dwu: float = 0.0
    peak_pct: float = 0.0


class LogAnalyticsResult(BaseModel):
    """One named KQL query's tabular result, normalized to a list of dicts."""
    query_name: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class MonitoringAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    interval: str
    series: list[MetricSeries] = Field(default_factory=list)
    # v2
    dwu_days: list[DwuDayStat] = Field(default_factory=list)
    log_analytics: list[LogAnalyticsResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
