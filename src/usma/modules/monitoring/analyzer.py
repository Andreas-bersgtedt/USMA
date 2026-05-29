"""Orchestrator for the monitoring module."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ...config import AppConfig
from ...progress import NullProgress, ProgressReporter
from . import dwu_hours
from .models import DwuDayStat, MonitoringAnalysis
from .monitor_client import (
    DEFAULT_AGGREGATION,
    DEFAULT_DEDICATED_POOL_METRICS,
    DEFAULT_INTERVAL,
    MonitoringClient,
    build_series,
    default_window,
)

log = logging.getLogger(__name__)


def _pool_concurrency() -> int:
    try:
        return max(1, int(os.getenv("SMA_MONITORING_POOL_CONCURRENCY", "4")))
    except ValueError:
        return 4


class MonitoringAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._cfg = cfg
        self._client = MonitoringClient(cfg.azure)
        self._progress = progress or NullProgress()

    def run(self) -> MonitoringAnalysis:
        days = int(os.getenv("SMA_MONITORING_DAYS", "7"))
        interval = os.getenv("SMA_MONITORING_INTERVAL", DEFAULT_INTERVAL)
        aggregation = os.getenv("SMA_MONITORING_AGG", DEFAULT_AGGREGATION)
        start, end = default_window(days)

        result = MonitoringAnalysis(
            workspace_name=self._cfg.azure.workspace_name,
            subscription_id=self._cfg.azure.subscription_id,
            resource_group=self._cfg.azure.resource_group,
            generated_at=datetime.now(timezone.utc),
            window_start=start,
            window_end=end,
            interval=interval,
        )

        try:
            pools = self._client.list_dedicated_pool_resource_ids()
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to list dedicated pools: %s", exc)
            result.errors.append(f"list_dedicated_pools: {exc}")
            self._progress.start(0, label="metric listing failed")
            return result

        # 1 step per pool metric fetch + 1 dwu_hours rollup.
        self._progress.start(max(1, len(pools)) + 1, label=f"{len(pools)} pool(s)")

        def _one(pool_tuple):
            pool_name, resource_id = pool_tuple
            try:
                metrics = self._client.fetch_metrics(
                    resource_id,
                    DEFAULT_DEDICATED_POOL_METRICS,
                    start,
                    end,
                    interval=interval,
                    aggregation=aggregation,
                )
                return pool_name, resource_id, metrics, None
            except Exception as exc:  # noqa: BLE001
                return pool_name, resource_id, None, exc

        if not pools:
            outputs = []
        else:
            max_workers = min(len(pools), _pool_concurrency())
            if max_workers <= 1:
                outputs = [_one(p) for p in pools]
            else:
                with ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="sma-mon-pool") as ex:
                    outputs = list(ex.map(_one, pools))

        for pool_name, resource_id, metrics, exc in outputs:
            if exc is not None:
                log.warning("Failed to fetch metrics for pool %s: %s", pool_name, exc)
                result.errors.append(f"metrics[{pool_name}]: {exc}")
                self._progress.step(label=f"{pool_name} (failed)")
                continue
            for metric_name, unit, points in metrics:
                result.series.append(build_series(
                    resource_id=resource_id,
                    pool_name=pool_name,
                    metric_name=metric_name,
                    unit=unit,
                    aggregation=aggregation,
                    interval=interval,
                    points=points,
                ))
            self._progress.step(label=pool_name)

        if not pools:
            # No pools — still consume the placeholder slot so totals close.
            self._progress.step(label="no pools")

        # v2 — derive active DWU hours per day.
        try:
            for d in dwu_hours.derive_dwu_days(
                [s.model_dump(mode="json") for s in result.series],
                interval=interval,
            ):
                result.dwu_days.append(DwuDayStat(
                    pool_name=d.pool_name,
                    day=d.day.isoformat(),
                    active_hours=d.active_hours,
                    active_dwu_hours=d.active_dwu_hours,
                    peak_dwu=d.peak_dwu,
                    peak_pct=d.peak_pct,
                ))
        except Exception as exc:  # noqa: BLE001
            log.warning("dwu_hours derivation failed: %s", exc)
            result.errors.append(f"dwu_hours: {exc}")
        self._progress.step(label="dwu_hours")

        return result
