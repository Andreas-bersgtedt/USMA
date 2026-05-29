"""Azure Monitor metrics client for Synapse dedicated pool metrics.

Pulls last N days (default 7) of DWU and query metrics from each dedicated SQL pool
and aggregates min/avg/max/p95.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Iterable

from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.synapse import SynapseManagementClient

from ...auth import get_credential
from ...config import AzureConfig
from .models import MetricSeries

log = logging.getLogger(__name__)

# Metric names available on Microsoft.Synapse/workspaces/sqlPools.
# NOTE: keep this list in sync with the metrics actually exposed by the provider.
# `FailedConnections` does NOT exist on this resource type — use
# `ConnectionsBlockedByFirewall` instead.
DEFAULT_DEDICATED_POOL_METRICS: tuple[str, ...] = (
    "DWULimit",
    "DWUUsed",
    "DWUUsedPercent",
    "ActiveQueries",
    "QueuedQueries",
    "Connections",
    "ConnectionsBlockedByFirewall",
    "MemoryUsedPercent",
    "CPUPercent",
)

DEFAULT_AGGREGATION = "Average"
DEFAULT_INTERVAL = "PT1H"  # one hour


def _iso_z(dt: datetime) -> str:
    """Format `dt` as `YYYY-MM-DDTHH:MM:SSZ` (UTC, no microseconds, no '+').

    Azure Monitor's timespan parameter is placed into the request URL. A literal
    '+' in a URL is decoded as space, which causes
    `BadRequest: Detected invalid time interval input: ...19:00:00 00:00`.
    Normalising to UTC + 'Z' avoids the problem entirely.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0, tzinfo=None).isoformat() + "Z"


_INVALID_METRIC_RE = re.compile(r"metric:\s*([A-Za-z0-9_]+)", re.IGNORECASE)


def _parse_invalid_metric_name(message: str) -> str | None:
    """Pull the offending metric name out of a Azure Monitor ``BadRequest``.

    Example message:
        ``Failed to find metric configuration for provider: Microsoft.Synapse,
        resource Type: workspaces/sqlPools, metric: FailedConnections, Valid
        metrics: DWULimit,...``
    """
    if "Failed to find metric configuration" not in message:
        return None
    m = _INVALID_METRIC_RE.search(message)
    return m.group(1) if m else None


class MonitoringClient:
    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        cred = get_credential(azure)
        self._monitor = MonitorManagementClient(cred, azure.subscription_id)
        self._synapse = SynapseManagementClient(cred, azure.subscription_id)

    def list_dedicated_pool_resource_ids(self) -> list[tuple[str, str]]:
        """Return [(pool_name, resource_id), ...] for every dedicated SQL pool in the workspace."""
        rg = self._azure.resource_group
        ws = self._azure.workspace_name
        ids: list[tuple[str, str]] = []
        for pool in self._synapse.sql_pools.list_by_workspace(rg, ws):
            ids.append((pool.name, pool.id))
        return ids

    def fetch_metrics(
        self,
        resource_id: str,
        metric_names: Iterable[str],
        window_start: datetime,
        window_end: datetime,
        interval: str = DEFAULT_INTERVAL,
        aggregation: str = DEFAULT_AGGREGATION,
    ) -> list[tuple[str, str | None, list[tuple[datetime, float | None]]]]:
        """Return [(metric_name, unit, points), ...].

        If one or more metric names are not exposed by the provider for this
        resource type, Azure Monitor rejects the *whole* request with
        ``BadRequest: Failed to find metric configuration ... metric: <name>,
        Valid metrics: ...``. We parse the rejected name out of the error
        message, drop it from the request, and retry. This makes the analyzer
        resilient against future changes to the provider's metric catalogue
        without forcing every caller to update the default list.
        """
        # Azure Monitor expects ISO-8601 timespan. Use the 'Z' UTC suffix and drop
        # microseconds: a literal '+' in the URL gets decoded as a space and the
        # service rejects "...19:00:00 00:00" as an invalid interval.
        timespan = f"{_iso_z(window_start)}/{_iso_z(window_end)}"
        names = list(metric_names)
        result = None
        # Bound retries to len(names) so we cannot loop forever on a malformed error.
        for _ in range(len(names) + 1):
            if not names:
                return []
            try:
                result = self._monitor.metrics.list(
                    resource_id,
                    timespan=timespan,
                    interval=interval,
                    metricnames=",".join(names),
                    aggregation=aggregation,
                )
                break
            except Exception as exc:  # noqa: BLE001 - SDK + test fakes raise varied types
                bad = _parse_invalid_metric_name(str(exc))
                if not bad or bad not in names:
                    raise
                log.warning(
                    "Dropping unsupported metric %r for %s: %s", bad, resource_id, exc,
                )
                names = [n for n in names if n != bad]
        if result is None:
            return []

        out: list[tuple[str, str | None, list[tuple[datetime, float | None]]]] = []
        for m in result.value or []:
            unit = getattr(m, "unit", None)
            points: list[tuple[datetime, float | None]] = []
            for ts in (m.timeseries or []):
                for d in (ts.data or []):
                    val = getattr(d, aggregation.lower(), None)
                    points.append((d.time_stamp, float(val) if val is not None else None))
            out.append((m.name.value if hasattr(m.name, "value") else str(m.name), unit, points))
        return out


def build_series(
    resource_id: str,
    pool_name: str,
    metric_name: str,
    unit: str | None,
    aggregation: str,
    interval: str,
    points: list[tuple[datetime, float | None]],
) -> MetricSeries:
    values = [v for _, v in points if v is not None]
    return MetricSeries(
        resource_id=resource_id,
        resource_kind="dedicated_pool",
        resource_name=pool_name,
        metric_name=metric_name,
        unit=unit,
        aggregation=aggregation,
        interval=interval,
        points=points,
        min_value=min(values) if values else None,
        max_value=max(values) if values else None,
        avg_value=(sum(values) / len(values)) if values else None,
        p95_value=_percentile(values, 95) if values else None,
    )


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    return float(statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1])


def default_window(days: int = 7) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    return start, end
