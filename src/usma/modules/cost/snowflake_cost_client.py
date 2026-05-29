"""Snowflake cost client — reads ``SNOWFLAKE.ACCOUNT_USAGE`` views.

Mirrors :mod:`cost_client` (Azure Cost Management) and
:mod:`gcp_cost_client` (BigQuery billing-export) so
:class:`~usma.modules.cost.analyzer.CostAnalyzer` can dispatch on the
primary scope type and re-use the existing aggregation, rules, and
Fabric-comparison machinery.

Wiring
------
Snowflake exposes two account-level usage views that, joined together,
give a monthly cost breakdown without needing an external billing
export:

* ``SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY`` — credits consumed per
  warehouse per hour (and a ``CLOUD_SERVICES`` rollup row).
* ``SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY`` — usage cost in
  the account's contract currency, broken down by usage type. We use
  this view to derive the effective $/credit rate and to capture the
  storage line item.

The reader runs two queries (compute + storage) and merges the rows
into the shared :class:`MonthlyCostRow` shape.

Required configuration
----------------------
The client expects credentials to be in the environment already (the
same env vars exercised by :class:`SnowflakeProvider.make_clients`).
Honors ``SMA_COST_DISABLE_LIVE`` (the kill-switch shared with the
Azure and GCP clients) so offline test runs never connect.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from ...sources import SourceDescriptor
from .models import MonthlyCostRow

log = logging.getLogger(__name__)


def _live_disabled() -> bool:
    return os.getenv("SMA_COST_DISABLE_LIVE", "").strip() in {"1", "true", "yes"}


def _account_configured() -> bool:
    return bool((os.getenv("SNOWFLAKE_ACCOUNT") or "").strip())


class SnowflakeCostClient:
    """Reads :data:`SNOWFLAKE.ACCOUNT_USAGE` cost views for one account."""

    def __init__(self, descriptor: SourceDescriptor) -> None:
        if not descriptor.id:
            raise ValueError(
                "SnowflakeCostClient requires a descriptor with an account id"
            )
        self._descriptor = descriptor
        self._account = descriptor.id

    def fetch_monthly_breakdown(
        self,
        start: datetime,
        end: datetime,
    ) -> list[MonthlyCostRow]:
        rows, _ = self.fetch_monthly_breakdown_with_status(start, end)
        return rows

    def fetch_monthly_breakdown_with_status(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[MonthlyCostRow], str]:
        """Return ``(rows, status)``.

        Status vocabulary mirrors the Azure / GCP clients:
        ``ok`` | ``sdk_missing`` | ``live_disabled`` | ``missing_config``
        | ``empty_window`` | ``error``.
        """
        if _live_disabled():
            return [], "live_disabled"

        if not _account_configured():
            log.info(
                "Snowflake account env var not configured; "
                "skipping cost fetch (status=missing_config)"
            )
            return [], "missing_config"

        try:  # pragma: no cover - exercised only when SDK is installed
            from ...sources.snowflake import SnowflakeProvider
        except ImportError:
            log.debug("snowflake provider unavailable; skipping cost collect")
            return [], "sdk_missing"

        try:  # pragma: no cover - exercised only when SDK is installed
            import snowflake.connector  # noqa: F401
        except ImportError:
            log.debug("snowflake-connector-python not installed; skipping cost collect")
            return [], "sdk_missing"

        try:
            provider = SnowflakeProvider()
            bundle = provider.make_clients(self._descriptor)
        except Exception as exc:  # noqa: BLE001
            log.warning("Snowflake client bundle creation failed: %s", exc)
            raise

        try:
            conn = bundle.connect()
        except Exception as exc:  # noqa: BLE001
            log.warning("Snowflake connection failed: %s", exc)
            raise

        try:
            try:
                # USAGE_IN_CURRENCY_DAILY is not present in every
                # Snowflake edition / account configuration (notably
                # Standard Edition + non-org-billing accounts). When
                # the view is missing or unauthorized we degrade to
                # credits-only output (no $ conversion, no storage
                # line) instead of failing the whole cost step —
                # METERING_HISTORY is universally available with
                # IMPORTED PRIVILEGES.
                try:
                    rate_per_credit, currency = _fetch_credit_rate(conn, start, end)
                except Exception as exc:  # noqa: BLE001
                    if _is_currency_view_missing(exc):
                        log.warning(
                            "USAGE_IN_CURRENCY_DAILY unavailable (%s); "
                            "emitting credits-only rows", exc,
                        )
                        rate_per_credit, currency = 0.0, "USD"
                    else:
                        raise
                compute_rows = _fetch_compute_rows(
                    conn, start, end, rate_per_credit, currency,
                )
                try:
                    storage_rows = _fetch_storage_rows(conn, start, end, currency)
                except Exception as exc:  # noqa: BLE001
                    if _is_currency_view_missing(exc):
                        storage_rows = []
                    else:
                        raise
            except Exception as exc:  # noqa: BLE001
                # Re-raise with active session role + available roles
                # so the cost finding can tell the user whether the
                # OAuth token is bound to a different role than the
                # one configured (the most common cause of "object
                # does not exist or not authorized" against
                # ACCOUNT_USAGE views).
                context = _probe_session_role_context(conn, bundle.role)
                if context:
                    raise RuntimeError(f"{exc} [{context}]") from exc
                raise
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

        rows = compute_rows + storage_rows
        return rows, ("ok" if rows else "empty_window")


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


_COMPUTE_SQL = """
    SELECT
      TO_CHAR(DATE_TRUNC('MONTH', start_time), 'YYYY-MM') AS month,
      COALESCE(name, 'CLOUD_SERVICES') AS warehouse_name,
      SUM(credits_used) AS credits,
      SUM(credits_used_compute) AS credits_compute,
      SUM(credits_used_cloud_services) AS credits_cloud_services
    FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY
    WHERE start_time >= %(window_start)s
      AND start_time <  %(window_end)s
    GROUP BY month, warehouse_name
    ORDER BY month, warehouse_name
"""

_STORAGE_SQL = """
    SELECT
      TO_CHAR(DATE_TRUNC('MONTH', usage_date), 'YYYY-MM') AS month,
      SUM(usage) AS usage,
      ANY_VALUE(currency) AS currency,
      SUM(usage_in_currency) AS cost
    FROM SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY
    WHERE usage_date >= %(window_start)s
      AND usage_date <  %(window_end)s
      AND usage_type = 'storage'
    GROUP BY month
    ORDER BY month
"""

_RATE_SQL = """
    SELECT
      SUM(usage_in_currency) AS total_currency,
      SUM(usage) AS total_credits,
      ANY_VALUE(currency) AS currency
    FROM SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY
    WHERE usage_date >= %(window_start)s
      AND usage_date <  %(window_end)s
      AND usage_type IN ('compute', 'cloud services')
"""


def _fetch_credit_rate(
    conn: Any,
    start: datetime,
    end: datetime,
) -> tuple[float, str]:
    """Derive an effective ``$/credit`` rate from USAGE_IN_CURRENCY_DAILY.

    Returns ``(rate, currency)``. ``rate`` is ``0.0`` when no rows are
    returned (we still report credits-only rows in that case so the
    caller sees usage volume).
    """
    cur = conn.cursor()
    try:
        cur.execute(_RATE_SQL, {"window_start": start, "window_end": end})
        row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
    if not row:
        return 0.0, "USD"
    total_currency, total_credits, currency = _normalize_row(row, 3)
    try:
        total_currency_f = float(total_currency or 0.0)
        total_credits_f = float(total_credits or 0.0)
    except (TypeError, ValueError):
        return 0.0, str(currency or "USD")
    if total_credits_f <= 0:
        return 0.0, str(currency or "USD")
    return total_currency_f / total_credits_f, str(currency or "USD")


def _fetch_compute_rows(
    conn: Any,
    start: datetime,
    end: datetime,
    rate_per_credit: float,
    currency: str,
) -> list[MonthlyCostRow]:
    cur = conn.cursor()
    try:
        cur.execute(_COMPUTE_SQL, {"window_start": start, "window_end": end})
        rows = cur.fetchall()
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
    out: list[MonthlyCostRow] = []
    for raw in rows or []:
        month, warehouse, credits, _credits_compute, _credits_cs = _normalize_row(raw, 5)
        try:
            credits_f = float(credits or 0.0)
        except (TypeError, ValueError):
            credits_f = 0.0
        if credits_f <= 0 and not warehouse:
            continue
        cost = credits_f * rate_per_credit
        warehouse_name = str(warehouse or "").strip() or None
        kind = (
            "cloud_services"
            if warehouse_name and warehouse_name.upper() == "CLOUD_SERVICES"
            else "snowflake_warehouse"
        )
        out.append(
            MonthlyCostRow(
                month=str(month or ""),
                resource_kind=kind,
                resource_name=warehouse_name,
                sku="credits",
                cost=cost,
                currency=currency,
                usage_quantity=credits_f,
                usage_unit="credits",
            )
        )
    return out


def _fetch_storage_rows(
    conn: Any,
    start: datetime,
    end: datetime,
    fallback_currency: str,
) -> list[MonthlyCostRow]:
    cur = conn.cursor()
    try:
        cur.execute(_STORAGE_SQL, {"window_start": start, "window_end": end})
        rows = cur.fetchall()
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
    out: list[MonthlyCostRow] = []
    for raw in rows or []:
        month, usage, currency, cost = _normalize_row(raw, 4)
        try:
            usage_f = float(usage or 0.0)
            cost_f = float(cost or 0.0)
        except (TypeError, ValueError):
            usage_f = 0.0
            cost_f = 0.0
        if cost_f <= 0 and usage_f <= 0:
            continue
        out.append(
            MonthlyCostRow(
                month=str(month or ""),
                resource_kind="storage",
                resource_name=None,
                sku="storage_tb_month",
                cost=cost_f,
                currency=str(currency or fallback_currency or "USD"),
                usage_quantity=usage_f,
                usage_unit="tb-month",
            )
        )
    return out


def _is_currency_view_missing(exc: BaseException) -> bool:
    """Detect the Snowflake error raised when
    ``SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY`` is unavailable
    in the current account/edition. Snowflake raises a generic
    SQL-compilation error (``002003 (42S02): Object '...' does not
    exist or not authorized.``) — we narrow on the view name to avoid
    false positives that should still surface as hard errors.
    """
    msg = str(exc)
    return (
        "USAGE_IN_CURRENCY_DAILY" in msg
        and "does not exist or not authorized" in msg
    )


def _probe_session_role_context(conn: Any, configured_role: str) -> str:
    """Best-effort diagnostic: return a short string describing the
    session's active role + whether the configured role is reachable.

    Used to enrich exceptions raised by the cost queries so the
    upstream finding can explain *why* an ACCOUNT_USAGE view appears
    "not authorized" even after the user changed SNOWFLAKE_ROLE in the
    .env file (the most common cause is that the OAuth refresh token
    is still bound to the previous role at consent time — a re-sign-in
    is required). Returns an empty string on any failure so the
    original error wording is preserved.
    """
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT CURRENT_ROLE()")
            row = cur.fetchone() or (None,)
            active = str(row[0] or "").strip()
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
        available = ""
        try:
            cur2 = conn.cursor()
            try:
                cur2.execute("SELECT CURRENT_AVAILABLE_ROLES()")
                row2 = cur2.fetchone() or (None,)
                available = str(row2[0] or "").strip()
            finally:
                try:
                    cur2.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            available = ""
        bits = [
            f"configured_role={configured_role or '<unset>'}",
            f"active_role={active or '<unknown>'}",
        ]
        if available:
            bits.append(f"available_roles={available}")
        return "; ".join(bits)
    except Exception:  # noqa: BLE001
        return ""


def _normalize_row(row: Any, n: int) -> tuple[Any, ...]:
    """Best-effort row→tuple conversion. Pads with ``None`` to ``n``."""
    if isinstance(row, dict):
        # Snowflake connector returns dicts only with dict-cursor; default
        # is tuple. Support both for tests.
        values = list(row.values())
    elif isinstance(row, (list, tuple)):
        values = list(row)
    else:
        # Last resort: iterate.
        try:
            values = list(row)
        except TypeError:
            values = []
    while len(values) < n:
        values.append(None)
    return tuple(values[:n])
