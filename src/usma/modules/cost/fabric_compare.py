"""Pure-Python TCO comparison: Synapse vs Fabric capacity projection.

Pricing strategy:

* **F2–F64** — when the live Azure Retail Prices API is unreachable or
  returns nothing usable for the requested region, fall back to the
  hard-coded sticker prices below. These match the public
  https://azure.microsoft.com/pricing/details/microsoft-fabric/ page
  (PAYG monthly, USD).
* **F128 and above** — *no* static fallback. If the live API can't price
  the SKU, ``fabric_estimated_monthly_cost`` is ``None`` and the UI
  shows "—". We never extrapolate to F2048 because real-world deals at
  that scale are EA-priced and a fabricated number would be misleading.

Reservation (1Y / 3Y) monthly equivalents come from the live API only;
they are ``None`` when the API is unavailable, regardless of SKU.
"""
from __future__ import annotations

from typing import Any

from . import fabric_pricing
from .models import FabricCostComparison


# Fabric Capacity PAYG sticker prices (monthly, USD) — matches Microsoft's
# public pricing page. F128+ intentionally omitted (see module docstring).
_FABRIC_STATIC_MONTHLY_PAYG_USD: dict[str, float] = {
    "F2":   262.80,
    "F4":   525.60,
    "F8":  1051.20,
    "F16": 2102.40,
    "F32": 4204.80,
    "F64": 8409.60,
}


def estimate_fabric_monthly_cost(
    sku: str | None,
    *,
    region: str | None = None,
    currency: str | None = None,
) -> float | None:
    """Backward-compatible PAYG monthly price lookup.

    Live API first, static F2–F64 fallback second, ``None`` otherwise.
    """
    if not sku:
        return None
    sku_u = sku.upper()
    live = fabric_pricing.estimate_monthly_costs(sku_u, region=region, currency=currency)
    payg = live.get("payg_monthly")
    if payg is not None:
        return float(payg)
    return _FABRIC_STATIC_MONTHLY_PAYG_USD.get(sku_u)


def compare_to_fabric(
    synapse_avg_monthly_cost: float,
    fabric_projection: dict[str, Any] | None,
    *,
    region: str | None = None,
    currency: str | None = None,
) -> FabricCostComparison | None:
    """Build a :class:`FabricCostComparison` from the fabric_mapping CU projection.

    ``fabric_projection`` is the dict shape produced by
    ``fabric_mapping.cu_projection``. We tolerate missing keys.
    """
    if fabric_projection is None:
        return None
    sku = fabric_projection.get("recommended_sku") or fabric_projection.get("sku")
    sku_u = (sku or "").upper() or None

    live = fabric_pricing.estimate_monthly_costs(sku_u, region=region, currency=currency)
    fabric_cost = live.get("payg_monthly")
    ri_1y = live.get("ri_1y_monthly")
    ri_3y = live.get("ri_3y_monthly")
    source: str = str(live.get("source") or "none")

    if fabric_cost is None and sku_u in _FABRIC_STATIC_MONTHLY_PAYG_USD:
        fabric_cost = _FABRIC_STATIC_MONTHLY_PAYG_USD[sku_u]
        source = "static"

    delta_abs: float | None = None
    delta_pct: float | None = None
    if fabric_cost is not None:
        delta_abs = fabric_cost - synapse_avg_monthly_cost
        if synapse_avg_monthly_cost:
            delta_pct = delta_abs / synapse_avg_monthly_cost

    notes_bits: list[str] = []
    if source == "live":
        notes_bits.append("PAYG from Azure Retail Prices API (live)")
    elif source == "cache":
        notes_bits.append("PAYG from Azure Retail Prices API (cached < 24h)")
    elif source == "static":
        notes_bits.append("PAYG from hard-coded F2-F64 sticker prices (API unavailable)")
    elif source == "offline":
        notes_bits.append("USMA_OFFLINE=1 - live pricing skipped")
    if ri_1y is None and ri_3y is None and source != "static":
        notes_bits.append("Reservation pricing unavailable for region")
    if fabric_cost is None and sku_u and sku_u not in _FABRIC_STATIC_MONTHLY_PAYG_USD:
        notes_bits.append(
            f"No live price for {sku_u} and no static fallback above F64 - "
            "consult your EA/CSP rep for large-SKU pricing"
        )

    return FabricCostComparison(
        synapse_avg_monthly_cost=synapse_avg_monthly_cost,
        fabric_capacity_sku=sku_u,
        fabric_estimated_monthly_cost=fabric_cost,
        fabric_estimated_monthly_cost_1y_ri=ri_1y,
        fabric_estimated_monthly_cost_3y_ri=ri_3y,
        pricing_source=source,
        pricing_region=live.get("region"),
        pricing_currency=live.get("currency"),
        delta_abs=delta_abs,
        delta_pct=delta_pct,
        notes="; ".join(notes_bits) or None,
    )
