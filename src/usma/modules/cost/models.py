from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MonthlyCostRow(BaseModel):
    """One row in the monthly breakdown — combination of (month, scope, kind, SKU)."""
    month: str  # ISO yyyy-mm
    resource_kind: str  # "synapse_workspace" | "dedicated_pool" | "serverless_pool" | "spark_pool" | "adf_factory" | "databricks_workspace" | "storage" | "other"
    resource_name: str | None = None
    resource_id: str | None = None  # full ARM id (used for scope-aware filtering)
    sku: str | None = None
    cost: float = 0.0
    currency: str = "USD"
    usage_quantity: float = 0.0
    usage_unit: str | None = None


class FabricCostComparison(BaseModel):
    """Side-by-side TCO delta vs. the Fabric capacity projection.

    ``fabric_estimated_monthly_cost`` is the pay-as-you-go monthly figure
    (kept as the primary value for backward compatibility with existing
    reports / SPA fields). The 1Y and 3Y reservation equivalents are
    surfaced separately so the UI can show the discounted alternative.

    ``pricing_source`` tracks provenance: ``"live"`` / ``"cache"`` from the
    Azure Retail Prices API, ``"static"`` from the hard-coded F2–F64
    fallback table, ``"offline"`` when ``USMA_OFFLINE=1`` is set, or
    ``"none"`` when no price could be determined (e.g. F128+ without API
    access — we deliberately never invent prices above the static range).
    """
    synapse_avg_monthly_cost: float
    fabric_capacity_sku: str | None
    fabric_estimated_monthly_cost: float | None
    fabric_estimated_monthly_cost_1y_ri: float | None = None
    fabric_estimated_monthly_cost_3y_ri: float | None = None
    pricing_source: str | None = None
    pricing_region: str | None = None
    pricing_currency: str | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None
    notes: str | None = None


class CostFinding(BaseModel):
    """Severity-tagged finding emitted by the cost rules engine."""
    rule_id: str
    severity: str  # high | medium | low | info
    title: str
    detail: str | None = None
    resource: str | None = None


class CostAnalysis(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    rows: list[MonthlyCostRow] = Field(default_factory=list)
    monthly_totals: dict[str, float] = Field(default_factory=dict)  # month -> cost
    by_resource_kind: dict[str, float] = Field(default_factory=dict)
    by_resource_name: dict[str, float] = Field(default_factory=dict)  # resource_name -> cost
    fabric_comparison: FabricCostComparison | None = None
    findings: list[CostFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    collection_status: str = "ok"  # ok | sdk_missing | live_disabled | empty_window | error | skipped
    source_type: str = "azure"  # azure | bigquery | snowflake | databricks (informational; drives finding wording)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
