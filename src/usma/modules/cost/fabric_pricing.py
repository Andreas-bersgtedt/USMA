"""Live Fabric F-SKU pricing via the unauthenticated Azure Retail Prices API.

We hit ``https://prices.azure.com/api/retail/prices`` (no auth, no SDK) and
cache the per-region per-currency answer to ``~/.cache/usma/fabric_prices.json``
for 24 h. Failures are silent: every public function returns ``None`` (or a
``None``-valued field) so callers can fall back to the static F2–F64 table or
omit the value entirely. We deliberately never invent prices for SKUs above
F64 — anything above the static fallback range must come from the live API.

Two scopes are queried:

* ``productName eq 'Fabric Capacity'`` with ``priceType eq 'Consumption'`` →
  pay-as-you-go per-CU-hour rate (validated: 2 CU × $0.18 × 730 ≈ $262.80 = F2).
* ``productName eq 'Fabric Capacity Reservation'`` → upfront $/CU for the
  full term (1 Year or 3 Years). Per-month per-CU = retailPrice / 12 or / 36.

Region defaults to ``USMA_FABRIC_REGION`` env var, falling back to
``westeurope``. Currency defaults to ``USMA_FABRIC_CURRENCY`` (USD). Set
``USMA_OFFLINE=1`` to skip the API entirely.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_API = "https://prices.azure.com/api/retail/prices"
_CACHE_PATH = Path.home() / ".cache" / "usma" / "fabric_prices.json"
_CACHE_TTL_SEC = 24 * 3600
_HTTP_TIMEOUT = 5.0
_MAX_PAGES = 3
_HOURS_PER_MONTH = 730.0  # Microsoft's published normalization (≈ 365.25/12 × 24)

# F-SKU → Capacity Units. Mirrors fabric_mapping.cu_projection._FSKU_TABLE.
_FSKU_CU: dict[str, int] = {
    "F2": 2, "F4": 4, "F8": 8, "F16": 16, "F32": 32, "F64": 64,
    "F128": 128, "F256": 256, "F512": 512, "F1024": 1024, "F2048": 2048,
}


def cu_for_sku(sku: str | None) -> int | None:
    if not sku:
        return None
    return _FSKU_CU.get(sku.upper())


def default_region() -> str:
    return (os.environ.get("USMA_FABRIC_REGION") or "westeurope").strip().lower()


def default_currency() -> str:
    return (os.environ.get("USMA_FABRIC_CURRENCY") or "USD").strip().upper()


def _is_offline() -> bool:
    return (os.environ.get("USMA_OFFLINE") or "").strip() == "1"


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

def _cache_read() -> dict[str, Any]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        blob = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fetched_at = float(blob.get("fetched_at") or 0)
    if time.time() - fetched_at > _CACHE_TTL_SEC:
        return {}
    entries = blob.get("entries")
    return entries if isinstance(entries, dict) else {}


def _cache_write(entries: dict[str, Any]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({"fetched_at": time.time(), "entries": entries}),
            encoding="utf-8",
        )
    except OSError as exc:
        log.debug("fabric_pricing: cache write failed: %s", exc)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_get_json(url: str) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # nosec B310 - public price API
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - fail-soft for any network/parse error
        log.debug("fabric_pricing: GET %s failed: %s", url, exc)
        return None


def _fetch_all(odata_filter: str, currency: str) -> list[dict[str, Any]]:
    qs = urllib.parse.urlencode({"$filter": odata_filter, "currencyCode": currency})
    url = f"{_API}?{qs}"
    items: list[dict[str, Any]] = []
    for _ in range(_MAX_PAGES):
        payload = _http_get_json(url)
        if not payload:
            break
        items.extend(payload.get("Items") or [])
        next_url = payload.get("NextPageLink") or ""
        if not next_url:
            break
        url = next_url
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fabric_rates(
    region: str | None = None,
    currency: str | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return per-CU rates for the given region.

    Shape::

        {
            "region": "westeurope",
            "currency": "USD",
            "payg_per_cu_hour": 0.18 | None,
            "ri_1y_per_cu_month": 77.5 | None,
            "ri_3y_per_cu_month": 77.5 | None,
            "source": "live" | "cache" | "offline" | "none",
        }
    """
    region_v = (region or default_region()).strip().lower()
    currency_v = (currency or default_currency()).strip().upper()
    base: dict[str, Any] = {
        "region": region_v,
        "currency": currency_v,
        "payg_per_cu_hour": None,
        "ri_1y_per_cu_month": None,
        "ri_3y_per_cu_month": None,
        "source": "none",
    }

    if _is_offline():
        base["source"] = "offline"
        return base

    cache_key = f"{region_v}:{currency_v}"
    if not force_refresh:
        cached = _cache_read().get(cache_key)
        if isinstance(cached, dict):
            return {**base, **cached, "source": "cache"}

    payg_items = _fetch_all(
        f"serviceName eq 'Microsoft Fabric' and productName eq 'Fabric Capacity' "
        f"and armRegionName eq '{region_v}' and priceType eq 'Consumption'",
        currency_v,
    )
    ri_items = _fetch_all(
        f"serviceName eq 'Microsoft Fabric' "
        f"and productName eq 'Fabric Capacity Reservation' "
        f"and armRegionName eq '{region_v}'",
        currency_v,
    )

    payg_per_cu_hour: float | None = None
    for it in payg_items:
        # The per-CU-hour meter has unitOfMeasure '1 Hour'; pick the cheapest
        # to avoid burst/overage meter variants if present.
        if (it.get("unitOfMeasure") or "").strip() != "1 Hour":
            continue
        price = _as_float(it.get("retailPrice"))
        if price is None or price <= 0:
            continue
        if payg_per_cu_hour is None or price < payg_per_cu_hour:
            payg_per_cu_hour = price

    ri_1y_pcm: float | None = None
    ri_3y_pcm: float | None = None
    for it in ri_items:
        term = (it.get("reservationTerm") or "").strip()
        price = _as_float(it.get("retailPrice"))
        if price is None or price <= 0:
            continue
        if term == "1 Year":
            monthly = price / 12.0
            if ri_1y_pcm is None or monthly < ri_1y_pcm:
                ri_1y_pcm = monthly
        elif term == "3 Years":
            monthly = price / 36.0
            if ri_3y_pcm is None or monthly < ri_3y_pcm:
                ri_3y_pcm = monthly

    result = {
        "region": region_v,
        "currency": currency_v,
        "payg_per_cu_hour": payg_per_cu_hour,
        "ri_1y_per_cu_month": ri_1y_pcm,
        "ri_3y_per_cu_month": ri_3y_pcm,
    }
    if payg_per_cu_hour is not None or ri_1y_pcm is not None or ri_3y_pcm is not None:
        entries = _cache_read()
        entries[cache_key] = result
        _cache_write(entries)
        return {**result, "source": "live"}
    return {**base, "source": "none"}


def estimate_monthly_costs(
    sku: str | None,
    region: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Return live PAYG + 1Y/3Y RI monthly cost for an F-SKU.

    Shape::

        {
            "sku": "F64",
            "cu": 64,
            "payg_monthly": 8409.6 | None,
            "ri_1y_monthly": 5002.7 | None,
            "ri_3y_monthly": 5002.7 | None,
            "source": "live" | "cache" | "offline" | "none",
            "region": "westeurope",
            "currency": "USD",
        }
    """
    cu = cu_for_sku(sku)
    if cu is None:
        return {
            "sku": sku,
            "cu": None,
            "payg_monthly": None,
            "ri_1y_monthly": None,
            "ri_3y_monthly": None,
            "source": "none",
            "region": (region or default_region()).lower(),
            "currency": (currency or default_currency()).upper(),
        }
    rates = fetch_fabric_rates(region=region, currency=currency)
    payg_h = rates.get("payg_per_cu_hour")
    ri1 = rates.get("ri_1y_per_cu_month")
    ri3 = rates.get("ri_3y_per_cu_month")
    return {
        "sku": (sku or "").upper(),
        "cu": cu,
        "payg_monthly": (payg_h * cu * _HOURS_PER_MONTH) if payg_h else None,
        "ri_1y_monthly": (ri1 * cu) if ri1 else None,
        "ri_3y_monthly": (ri3 * cu) if ri3 else None,
        "source": rates.get("source", "none"),
        "region": rates.get("region"),
        "currency": rates.get("currency"),
    }


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
