"""Phase 7 Slice 7-H — Snowflake cost client + CostAnalyzer dispatch."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules.cost.snowflake_cost_client import SnowflakeCostClient
from usma.sources import SourceDescriptor, SourceType


def _descriptor(**extras) -> SourceDescriptor:
    return SourceDescriptor(
        type=SourceType.SNOWFLAKE,
        id="acme-prod",
        display_name="acme-prod",
        extras={"platform": "azure", **extras},
    )


# ---------------------------------------------------------------------------
# Short-circuit paths — no Snowflake connection should be attempted.
# ---------------------------------------------------------------------------


def test_snowflake_cost_client_requires_account_id() -> None:
    with pytest.raises(ValueError):
        SnowflakeCostClient(
            SourceDescriptor(type=SourceType.SNOWFLAKE, id="", display_name="")
        )


def test_snowflake_cost_client_live_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMA_COST_DISABLE_LIVE", "1")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acme-prod")
    rows, status = SnowflakeCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rows == []
    assert status == "live_disabled"


def test_snowflake_cost_client_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    rows, status = SnowflakeCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rows == []
    assert status == "missing_config"


# ---------------------------------------------------------------------------
# Live path — mock the Snowflake provider so the connect callable returns a
# fake connection whose cursors play back canned rows for each SQL.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows_by_marker: dict[str, list[tuple]]):
        self._rows_by_marker = rows_by_marker
        self._captured: list[tuple] = []
        self._next: list[tuple] = []

    def execute(self, sql: str, params: dict) -> None:
        self._captured.append((sql, params))
        if "USAGE_IN_CURRENCY_DAILY" in sql and "usage_type IN" in sql:
            payload = self._rows_by_marker.get("rate", [])
        elif "METERING_HISTORY" in sql:
            payload = self._rows_by_marker.get("compute", [])
        elif "USAGE_IN_CURRENCY_DAILY" in sql:
            payload = self._rows_by_marker.get("storage", [])
        elif "CURRENT_ROLE" in sql:
            payload = self._rows_by_marker.get("current_role", [("USMA_RO",)])
        elif "CURRENT_AVAILABLE_ROLES" in sql:
            payload = self._rows_by_marker.get(
                "available_roles", [('["USMA_RO"]',)],
            )
        else:  # pragma: no cover - defensive
            payload = []
        if isinstance(payload, BaseException):
            raise payload
        self._next = list(payload)

    def fetchone(self) -> tuple | None:
        return self._next[0] if self._next else None

    def fetchall(self) -> list[tuple]:
        return self._next

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, rows_by_marker: dict[str, list[tuple]]):
        self._rows_by_marker = rows_by_marker
        self.cursors: list[_FakeCursor] = []
        self.closed = False

    def cursor(self) -> _FakeCursor:
        cur = _FakeCursor(self._rows_by_marker)
        self.cursors.append(cur)
        return cur

    def close(self) -> None:
        self.closed = True


def _patch_provider(monkeypatch: pytest.MonkeyPatch, conn: _FakeConn) -> None:
    """Patch ``SnowflakeProvider.make_clients`` to return a bundle whose
    ``connect()`` yields ``conn``. Also stub out
    ``snowflake.connector`` so the SDK-presence guard passes."""
    import sys
    import types

    fake_connector = types.ModuleType("snowflake.connector")
    fake_snowflake = types.ModuleType("snowflake")
    fake_snowflake.connector = fake_connector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", fake_snowflake)
    monkeypatch.setitem(sys.modules, "snowflake.connector", fake_connector)

    from usma.sources.snowflake import provider as sf_provider

    def _fake_make_clients(self, descriptor, creds=None):
        return sf_provider.SnowflakeClientBundle(
            connect=lambda: conn,
            account=descriptor.id,
            user="u",
            role="r",
            warehouse="w",
            platform="azure",
            region=None,
            extras={},
        )

    monkeypatch.setattr(
        sf_provider.SnowflakeProvider, "make_clients", _fake_make_clients
    )


def test_snowflake_cost_client_live_query_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acme-prod")

    # rate query: $2.50 per credit ($250 / 100 credits)
    # compute query: WH_A used 50 credits in 2026-04
    # storage query: 12.34 TB-month at $24.68 in 2026-04
    conn = _FakeConn(
        {
            "rate": [(250.0, 100.0, "USD")],
            "compute": [
                ("2026-04", "WH_A", 50.0, 45.0, 5.0),
                ("2026-04", "CLOUD_SERVICES", 7.0, 0.0, 7.0),
            ],
            "storage": [
                ("2026-04", 12.34, "USD", 24.68),
            ],
        }
    )
    _patch_provider(monkeypatch, conn)

    rows, status = SnowflakeCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    assert status == "ok"
    assert conn.closed is True
    assert len(rows) == 3
    wh_row = next(r for r in rows if r.resource_name == "WH_A")
    assert wh_row.resource_kind == "snowflake_warehouse"
    assert wh_row.cost == pytest.approx(50.0 * 2.5)
    assert wh_row.currency == "USD"
    assert wh_row.usage_quantity == pytest.approx(50.0)
    assert wh_row.sku == "credits"
    cs_row = next(r for r in rows if r.resource_name == "CLOUD_SERVICES")
    assert cs_row.resource_kind == "cloud_services"
    storage_row = next(r for r in rows if r.resource_kind == "storage")
    assert storage_row.cost == pytest.approx(24.68)
    assert storage_row.usage_unit == "tb-month"


def test_snowflake_cost_client_empty_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acme-prod")

    conn = _FakeConn({"rate": [], "compute": [], "storage": []})
    _patch_provider(monkeypatch, conn)

    rows, status = SnowflakeCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert rows == []
    assert status == "empty_window"


def test_snowflake_cost_client_handles_zero_credit_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When USAGE_IN_CURRENCY_DAILY has no rate data, compute cost is 0
    but the row still shows the credit usage volume."""
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acme-prod")

    conn = _FakeConn(
        {
            "rate": [],
            "compute": [("2026-04", "WH_A", 50.0, 50.0, 0.0)],
            "storage": [],
        }
    )
    _patch_provider(monkeypatch, conn)

    rows, status = SnowflakeCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert status == "ok"
    assert len(rows) == 1
    assert rows[0].cost == pytest.approx(0.0)
    assert rows[0].usage_quantity == pytest.approx(50.0)


def test_snowflake_cost_client_degrades_when_currency_view_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY is missing
    (Standard Edition / account without org-billing visibility), the
    client must still return credit-only rows from METERING_HISTORY
    rather than failing the whole cost step."""
    monkeypatch.delenv("SMA_COST_DISABLE_LIVE", raising=False)
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acme-prod")

    err = RuntimeError(
        "002003 (42S02): SQL compilation error: Object "
        "'SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY' does not "
        "exist or not authorized."
    )
    conn = _FakeConn(
        {
            "rate": err,
            "compute": [("2026-04", "WH_A", 50.0, 50.0, 0.0)],
            "storage": err,
        }
    )
    _patch_provider(monkeypatch, conn)

    rows, status = SnowflakeCostClient(_descriptor()).fetch_monthly_breakdown_with_status(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert status == "ok"
    assert len(rows) == 1
    assert rows[0].cost == pytest.approx(0.0)
    assert rows[0].usage_quantity == pytest.approx(50.0)
    assert rows[0].usage_unit == "credits"


# ---------------------------------------------------------------------------
# CostAnalyzer dispatch — Snowflake scopes route to SnowflakeCostClient
# ---------------------------------------------------------------------------


def _snowflake_cfg(tmp_path: Path) -> AppConfig:
    azure = AzureConfig(
        tenant_id="", client_id="", client_secret="",
        subscription_id="acme-prod", resource_group="",
        workspace_name="acme-prod",
    )
    descriptor = SourceDescriptor(
        type=SourceType.SNOWFLAKE,
        id="acme-prod",
        display_name="acme-prod",
        extras={"platform": "azure"},
    )
    return AppConfig(
        azure=azure,
        sql=SqlConfig(),
        output_dir=tmp_path,
        scopes=(descriptor,),
    )


def test_cost_analyzer_routes_snowflake_to_snowflake_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SMA_COST_DISABLE_LIVE", "1")
    from usma.modules.cost.analyzer import CostAnalyzer

    analyzer = CostAnalyzer(_snowflake_cfg(tmp_path))
    assert isinstance(analyzer._cc, SnowflakeCostClient)

    result = analyzer.run()
    assert result.collection_status == "live_disabled"
    assert result.workspace_name == "acme-prod"
    assert result.subscription_id == "acme-prod"
    assert result.resource_group == ""
    assert result.rows == []


def test_module_spec_cost_supports_snowflake() -> None:
    from usma.modules.spec import MODULE_SPECS

    assert SourceType.SNOWFLAKE in MODULE_SPECS["cost"].supports
