"""Azure Monitor capacity-metrics client for storage accounts.

Pulls a single PT1H sample of capacity metrics — these metrics are emitted
once per day by the platform, so we only need the most recent point.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from azure.mgmt.monitor import MonitorManagementClient

from ...auth import get_credential
from ...config import AzureConfig

log = logging.getLogger(__name__)

# Account-namespace metrics (Microsoft.Storage/storageAccounts).
ACCOUNT_METRICS: tuple[str, ...] = ("UsedCapacity",)

# Service-namespace metric names. Azure exposes them under a sub-resource
# namespace (`<acct>/blobServices/default`, `/fileServices/default`, etc.).
BLOB_METRICS: tuple[str, ...] = ("BlobCapacity", "BlobCount", "ContainerCount")
FILE_METRICS: tuple[str, ...] = ("FileCapacity", "FileCount")
TABLE_METRICS: tuple[str, ...] = ("TableCapacity",)
QUEUE_METRICS: tuple[str, ...] = ("QueueCapacity",)


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0, tzinfo=None).isoformat() + "Z"


_INVALID_METRIC_RE = re.compile(r"metric:\s*([A-Za-z0-9_]+)", re.IGNORECASE)


def _parse_invalid_metric_name(message: str) -> str | None:
    if "Failed to find metric configuration" not in message:
        return None
    m = _INVALID_METRIC_RE.search(message)
    return m.group(1) if m else None


class StorageMonitorClient:
    """Resilient capacity-metric reader for storage accounts."""

    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        cred = get_credential(azure)
        self._monitor = MonitorManagementClient(cred, azure.subscription_id)

    # ------------------------------------------------------------------ public
    def latest_capacity(self, account_resource_id: str) -> dict[str, float | int | None]:
        """Return the most recent values for capacity metrics on a storage account.

        Returns a dict with keys (any of which may be ``None`` if that metric is
        not exposed for the resource):
        ``UsedCapacity, BlobCapacity, BlobCount, ContainerCount,
        FileCapacity, FileCount, TableCapacity, QueueCapacity``.
        """
        # Capacity metrics emit once per day, so a 2-day window is enough.
        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=2)
        timespan = f"{_iso_z(start)}/{_iso_z(end)}"

        out: dict[str, float | int | None] = {
            "UsedCapacity": None,
            "BlobCapacity": None,
            "BlobCount": None,
            "ContainerCount": None,
            "FileCapacity": None,
            "FileCount": None,
            "TableCapacity": None,
            "QueueCapacity": None,
        }

        # Account-level metric.
        account_vals = self._fetch(account_resource_id, ACCOUNT_METRICS, timespan)
        out.update(account_vals)

        # Service-level metrics live on `<resource_id>/blobServices/default` etc.
        for sub_path, metric_names in (
            ("blobServices/default", BLOB_METRICS),
            ("fileServices/default", FILE_METRICS),
            ("tableServices/default", TABLE_METRICS),
            ("queueServices/default", QUEUE_METRICS),
        ):
            sub_resource = f"{account_resource_id}/{sub_path}"
            try:
                vals = self._fetch(sub_resource, metric_names, timespan)
                out.update(vals)
            except Exception as exc:  # noqa: BLE001
                # Some accounts (e.g. premium block-blob) expose no file/table/queue
                # service — that's expected; just skip.
                log.debug("Skipping %s on %s: %s", sub_path, account_resource_id, exc)

        return out

    # ------------------------------------------------------------------ helpers
    def _fetch(
        self,
        resource_id: str,
        metric_names: Iterable[str],
        timespan: str,
    ) -> dict[str, float | int | None]:
        """Return latest non-null point per metric, dropping unsupported names on retry."""
        names = list(metric_names)
        result = None
        for _ in range(len(names) + 1):
            if not names:
                return {}
            try:
                # Capacity metrics are aggregated as `Average`; the latest point
                # is what we want.
                result = self._monitor.metrics.list(
                    resource_id,
                    timespan=timespan,
                    interval="PT1H",
                    metricnames=",".join(names),
                    aggregation="Average",
                )
                break
            except Exception as exc:  # noqa: BLE001
                bad = _parse_invalid_metric_name(str(exc))
                if not bad or bad not in names:
                    raise
                log.warning("Dropping unsupported metric %r for %s", bad, resource_id)
                names = [n for n in names if n != bad]
        if result is None:
            return {}

        out: dict[str, float | int | None] = {}
        for m in result.value or []:
            metric_name = m.name.value if hasattr(m.name, "value") else str(m.name)
            latest: float | None = None
            for ts in (m.timeseries or []):
                for d in (ts.data or []):
                    val = getattr(d, "average", None)
                    if val is not None:
                        latest = float(val)
            # Counts are integers; capacities are byte counts (also integers in practice).
            if latest is not None and metric_name in {"BlobCount", "ContainerCount", "FileCount"}:
                out[metric_name] = int(latest)
            else:
                out[metric_name] = latest
        return out
