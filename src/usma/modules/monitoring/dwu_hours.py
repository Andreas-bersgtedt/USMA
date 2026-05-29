"""Derive an "active DWU hours per day" series from raw monitoring metric points.

Definition: a pool is "active" in an interval when ``DWUUsedPercent > active_threshold``
(default 5 %). Active DWU hours = sum over active intervals of
``(DWULimit_at_t * DWUUsedPercent_at_t / 100) * interval_hours``.

This is a sizing aid, not a billing proxy. Synapse is billed per provisioned DWU-hour
regardless of utilization; Fabric is capacity-priced. The derived series is meant to
help the customer pick a Fabric capacity SKU that covers the *peaks they actually used*.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable


@dataclass(frozen=True)
class DwuDay:
    pool_name: str
    day: date
    active_hours: float
    active_dwu_hours: float
    peak_dwu: float
    peak_pct: float


_INTERVAL_HOURS = {
    "PT1M": 1 / 60, "PT5M": 5 / 60, "PT15M": 15 / 60,
    "PT30M": 30 / 60, "PT1H": 1.0, "PT6H": 6.0, "P1D": 24.0,
}


def derive_dwu_days(
    series: Iterable[dict],
    *,
    interval: str = "PT1H",
    active_threshold_pct: float = 5.0,
) -> list[DwuDay]:
    """Compute per-pool, per-day active-DWU summaries from monitoring metric series.

    Each ``series`` entry is the JSON shape produced by
    :class:`usma.modules.monitoring.models.MetricSeries`.
    """
    interval_hours = _INTERVAL_HOURS.get(interval, 1.0)

    # Index points by (pool, metric, timestamp) → value
    by_pool_metric: dict[tuple[str, str], dict[datetime, float]] = defaultdict(dict)
    for s in series:
        pool = s.get("resource_name") or "?"
        metric = s.get("metric_name") or "?"
        for p in s.get("points", []) or []:
            ts, val = _unpack_point(p)
            if ts is None or val is None:
                continue
            by_pool_metric[(pool, metric)][ts] = float(val)

    pools = {p for (p, _) in by_pool_metric}
    out: list[DwuDay] = []
    for pool in sorted(pools):
        used_pct = by_pool_metric.get((pool, "DWUUsedPercent"), {})
        dwu_limit = by_pool_metric.get((pool, "DWULimit"), {})
        if not used_pct:
            continue
        per_day_active_hours: dict[date, float] = defaultdict(float)
        per_day_dwu_hours: dict[date, float] = defaultdict(float)
        per_day_peak_dwu: dict[date, float] = defaultdict(float)
        per_day_peak_pct: dict[date, float] = defaultdict(float)
        for ts, pct in used_pct.items():
            day = ts.date()
            if pct > active_threshold_pct:
                per_day_active_hours[day] += interval_hours
                limit = dwu_limit.get(ts, 0.0)
                dwu_at_t = limit * pct / 100.0
                per_day_dwu_hours[day] += dwu_at_t * interval_hours
                per_day_peak_dwu[day] = max(per_day_peak_dwu[day], dwu_at_t)
            per_day_peak_pct[day] = max(per_day_peak_pct[day], pct)

        for day in sorted(per_day_peak_pct):
            out.append(DwuDay(
                pool_name=pool, day=day,
                active_hours=round(per_day_active_hours.get(day, 0.0), 2),
                active_dwu_hours=round(per_day_dwu_hours.get(day, 0.0), 1),
                peak_dwu=round(per_day_peak_dwu.get(day, 0.0), 1),
                peak_pct=round(per_day_peak_pct.get(day, 0.0), 1),
            ))
    return out


def _unpack_point(p: object) -> tuple[datetime | None, float | None]:
    """Accept either ``[ts, value]`` (after JSON round-trip) or ``(ts, value)`` tuples."""
    if isinstance(p, (list, tuple)) and len(p) == 2:
        ts, val = p
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.rstrip("Z"))
            except ValueError:
                return None, None
        if not isinstance(ts, datetime):
            return None, None
        if val is None:
            return ts, None
        try:
            return ts, float(val)
        except (TypeError, ValueError):
            return ts, None
    return None, None


_DEFAULT_DAY = timedelta(days=1)
