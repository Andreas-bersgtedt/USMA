from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ServerlessDatabase(BaseModel):
    name: str
    collation: str | None = None
    create_date: datetime | None = None


class ExternalDataSource(BaseModel):
    database: str
    name: str
    location: str | None = None
    type: str | None = None


class ExternalTable(BaseModel):
    database: str
    schema_name: str
    table_name: str
    data_source: str | None = None
    file_format: str | None = None
    location: str | None = None


class ServerlessUsageStat(BaseModel):
    metric: str
    value: float | int | str | None = None
    unit: str | None = None
    captured_at: datetime | None = None


class ServerlessTopQuery(BaseModel):
    request_id: str | None = None
    login_name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: int | None = None
    status: str | None = None
    error_code: str | None = None
    data_processed_mb: int | None = None
    command_text: str | None = None


class ServerlessDailyUsage(BaseModel):
    day: str
    request_count: int = 0
    data_processed_mb: int = 0
    # Sum of per-query DATEDIFF(SECOND, start_time, end_time) for the day.
    # Lets us surface aggregate query-execution time alongside query volume
    # and data volume. Optional for backwards compat with pre-v2.7 artefacts.
    duration_seconds: int = 0
    # SUM(data_processed_mb * duration_seconds) for the day. Powers the
    # Fabric SQL Analytics Endpoint CU projection (data x time).
    # Optional for backwards compat with pre-v2.7 artefacts.
    mb_seconds: int = 0


class ServerlessHourlyUsage(BaseModel):
    """Per-UTC-hour usage rollup over the trailing 24h.

    Mirrors :class:`ServerlessDailyUsage` at hourly granularity so the
    dashboard can render a 24h companion chart alongside the 28-day view.
    """

    hour: str  # ISO UTC hour, e.g. ``2026-05-18T14:00:00Z``
    request_count: int = 0
    data_processed_mb: int = 0
    duration_seconds: int = 0
    mb_seconds: int = 0


class ServerlessCostEstimate(BaseModel):
    """Rough cost estimate based on data processed and a per-TB list price.

    Serverless SQL in Synapse is billed at ~USD 5 per TB of data processed (list price
    at time of writing; check current Azure pricing for your region). This is a guide,
    not an invoice."""

    window_days: int
    total_data_processed_tb: float
    list_price_usd_per_tb: float
    estimated_cost_usd: float
    notes: str | None = None


# --- v2 -----------------------------------------------------------------------

class ExternalTableColumn(BaseModel):
    database: str
    schema_name: str
    table_name: str
    column_name: str
    data_type: str | None = None
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    is_nullable: bool = True
    column_id: int | None = None


class StorageAccountUsage(BaseModel):
    storage_account: str
    query_count: int
    data_processed_mb: int
    estimated_cost_usd: float


class ServerlessAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    endpoint_fqdn: str
    generated_at: datetime
    databases: list[ServerlessDatabase] = Field(default_factory=list)
    external_data_sources: list[ExternalDataSource] = Field(default_factory=list)
    external_tables: list[ExternalTable] = Field(default_factory=list)
    usage: list[ServerlessUsageStat] = Field(default_factory=list)
    top_queries: list[ServerlessTopQuery] = Field(default_factory=list)
    daily_usage: list[ServerlessDailyUsage] = Field(default_factory=list)
    hourly_usage: list[ServerlessHourlyUsage] = Field(default_factory=list)
    cost_estimate: ServerlessCostEstimate | None = None
    # v2
    external_table_columns: list[ExternalTableColumn] = Field(default_factory=list)
    storage_account_usage: list[StorageAccountUsage] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
