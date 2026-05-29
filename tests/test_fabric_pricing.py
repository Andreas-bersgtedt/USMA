"""Tests for the Azure Retail Prices API client (fabric_pricing)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from usma.modules.cost import fabric_pricing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"Items": items, "NextPageLink": ""}


def _patch_http(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]] | Exception,
) -> list[str]:
    """Stub urllib.request.urlopen to return a queue of JSON payloads.

    Returns a list that will be appended-to with each fetched URL so tests can
    assert on the OData filters that were sent.
    """
    calls: list[str] = []

    class _Resp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

    queue = list(responses) if isinstance(responses, list) else responses

    def fake_urlopen(req: Any, timeout: float = 5.0) -> Any:  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        if isinstance(queue, Exception):
            raise queue
        if not queue:
            return _Resp({"Items": [], "NextPageLink": ""})
        return _Resp(queue.pop(0))

    monkeypatch.setattr(fabric_pricing.urllib.request, "urlopen", fake_urlopen)
    return calls


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the on-disk cache to a tmp file so tests don't pollute ~/.cache."""
    cache = tmp_path / "fabric_prices.json"
    monkeypatch.setattr(fabric_pricing, "_CACHE_PATH", cache)
    monkeypatch.delenv("USMA_OFFLINE", raising=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_offline_short_circuit_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USMA_OFFLINE", "1")
    calls = _patch_http(monkeypatch, [])
    rates = fabric_pricing.fetch_fabric_rates(region="westeurope")
    assert rates["source"] == "offline"
    assert rates["payg_per_cu_hour"] is None
    assert rates["ri_1y_per_cu_month"] is None
    assert rates["ri_3y_per_cu_month"] is None
    assert calls == []  # API never hit


def test_payg_and_ri_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payg_payload = _fake_payload([
        {
            "serviceName": "Microsoft Fabric",
            "productName": "Fabric Capacity",
            "meterName": "Compute Pool Capacity Usage",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.18,
            "priceType": "Consumption",
            "armRegionName": "westeurope",
        }
    ])
    ri_payload = _fake_payload([
        {
            "serviceName": "Microsoft Fabric",
            "productName": "Fabric Capacity Reservation",
            "reservationTerm": "1 Year",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 1146.0,
            "armRegionName": "westeurope",
        },
        {
            "serviceName": "Microsoft Fabric",
            "productName": "Fabric Capacity Reservation",
            "reservationTerm": "3 Years",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 3438.0,
            "armRegionName": "westeurope",
        },
    ])
    _patch_http(monkeypatch, [payg_payload, ri_payload])

    rates = fabric_pricing.fetch_fabric_rates(region="westeurope")
    assert rates["source"] == "live"
    assert rates["payg_per_cu_hour"] == pytest.approx(0.18)
    assert rates["ri_1y_per_cu_month"] == pytest.approx(95.5)  # 1146 / 12
    assert rates["ri_3y_per_cu_month"] == pytest.approx(95.5)  # 3438 / 36


def test_estimate_monthly_costs_for_f64(monkeypatch: pytest.MonkeyPatch) -> None:
    payg = _fake_payload([
        {"unitOfMeasure": "1 Hour", "retailPrice": 0.18}
    ])
    ri = _fake_payload([
        {"reservationTerm": "1 Year", "retailPrice": 1094.0},
        {"reservationTerm": "3 Years", "retailPrice": 3282.0},
    ])
    _patch_http(monkeypatch, [payg, ri])

    out = fabric_pricing.estimate_monthly_costs("F64", region="italynorth")
    assert out["cu"] == 64
    # 64 CU × $0.18/hr × 730 = $8,409.60
    assert out["payg_monthly"] == pytest.approx(8409.60, rel=1e-4)
    # 64 × (1094 / 12) ≈ $5,834.67
    assert out["ri_1y_monthly"] == pytest.approx(64 * 1094 / 12, rel=1e-4)
    assert out["ri_3y_monthly"] == pytest.approx(64 * 3282 / 36, rel=1e-4)
    assert out["source"] == "live"


def test_estimate_monthly_costs_unknown_sku_returns_nones() -> None:
    out = fabric_pricing.estimate_monthly_costs("F999")
    assert out["cu"] is None
    assert out["payg_monthly"] is None
    assert out["ri_1y_monthly"] is None
    assert out["ri_3y_monthly"] is None


def test_network_failure_returns_none_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, OSError("connection refused"))
    rates = fabric_pricing.fetch_fabric_rates(region="westeurope")
    assert rates["source"] == "none"
    assert rates["payg_per_cu_hour"] is None
    assert rates["ri_1y_per_cu_month"] is None
    assert rates["ri_3y_per_cu_month"] is None


def test_cache_hit_on_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    payg = _fake_payload([{"unitOfMeasure": "1 Hour", "retailPrice": 0.18}])
    ri = _fake_payload([{"reservationTerm": "1 Year", "retailPrice": 1146.0}])
    calls = _patch_http(monkeypatch, [payg, ri])

    first = fabric_pricing.fetch_fabric_rates(region="westeurope")
    assert first["source"] == "live"
    n_after_first = len(calls)

    second = fabric_pricing.fetch_fabric_rates(region="westeurope")
    assert second["source"] == "cache"
    assert second["payg_per_cu_hour"] == pytest.approx(0.18)
    # No additional HTTP calls when the cache is warm.
    assert len(calls) == n_after_first


def test_large_sku_returns_live_value_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """F128 has no static fallback; it must come from the live API."""
    payg = _fake_payload([{"unitOfMeasure": "1 Hour", "retailPrice": 0.18}])
    ri = _fake_payload([])
    _patch_http(monkeypatch, [payg, ri])

    out = fabric_pricing.estimate_monthly_costs("F128", region="westeurope")
    # 128 × 0.18 × 730 = $16,819.20 — matches Microsoft's pricing page.
    assert out["payg_monthly"] == pytest.approx(16819.20, rel=1e-4)
    assert out["ri_1y_monthly"] is None
    assert out["ri_3y_monthly"] is None
