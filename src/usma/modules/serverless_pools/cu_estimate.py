"""Project Fabric SQL Analytics Endpoint CU cost from serverless SQL usage.

Heuristic only. Serverless SQL in Synapse bills per **TB scanned** with no
notion of compute time; Fabric capacity instead bills per **CU-second**.
Microsoft does not publish a closed-form conversion between the two, so this
module applies the following baseline heuristic agreed with the user:

    For every **60 GB** of data scanned by a single query, that query is
    assumed to drive **0.02 CU** of Fabric SQL Analytics Endpoint compute
    while it runs. Integrated over the query's wall-clock duration this
    becomes CU-seconds:

        CU_seconds(query) = (data_scanned_GB / 60) * 0.02 * duration_seconds

Equivalently, the marginal rate is ``0.02 / 60 ≈ 3.333e-4 CU-seconds per
(GB · second)``.

Sanity-check from the request: a 120 GB scan running for 100 s
→ ``(120 / 60) * 0.02 * 100 = 4 CU-seconds``. ✓

Aggregation
-----------
We aggregate to a *peak-day* CU-hours number so the result composes with the
existing Spark + Pipelines components in
:mod:`usma.modules.fabric_mapping.cu_projection`,
which divides peak-day CU-hours by Fabric's 24 h smoothing window to obtain
a sustained CU number.

To avoid shipping per-query rows for full-coverage estimation, the daily
SQL aggregate (``serverless_pools/queries/data_processed.sql``) emits
``mb_seconds = SUM(data_processed_mb * duration_seconds)`` per UTC day.
Per-day CU-hours collapses to:

    CU_hours(day) = (mb_seconds / 1024 / 60) * 0.02 / 3600

The peak across days is what callers should use as the sizing basis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

# Heuristic constants — see module docstring for derivation.
SERVERLESS_GB_PER_CU = 60.0   # 60 GB scanned ≈ 0.02 CU instantaneous
SERVERLESS_CU_PER_GB = 0.02

# Derived: CU-seconds per (GB · second).
_CU_SEC_PER_GB_SEC = SERVERLESS_CU_PER_GB / SERVERLESS_GB_PER_CU


@dataclass(frozen=True)
class ServerlessCuProjection:
    """Result of :func:`project_serverless_cu`."""

    peak_day_cu_hours: float
    peak_day: str | None
    window_days: int
    total_cu_seconds: float


def cu_seconds_for_query(*, data_scanned_gb: float, duration_seconds: float) -> float:
    """CU-seconds for a single serverless query.

    ``(data_scanned_gb / 60) * 0.02 * duration_seconds``. Returns ``0.0`` for
    non-positive inputs.
    """
    if data_scanned_gb <= 0 or duration_seconds <= 0:
        return 0.0
    return (data_scanned_gb / SERVERLESS_GB_PER_CU) * SERVERLESS_CU_PER_GB * duration_seconds


def _day_cu_hours_from_mb_seconds(mb_seconds: float) -> float:
    """Convert daily ``mb_seconds`` to Fabric CU-hours for that day."""
    if mb_seconds <= 0:
        return 0.0
    gb_seconds = mb_seconds / 1024.0
    cu_seconds = gb_seconds * _CU_SEC_PER_GB_SEC
    return cu_seconds / 3600.0


def project_serverless_cu(
    daily_usage: Iterable[Mapping] | None,
) -> ServerlessCuProjection | None:
    """Compute peak-day CU-hours for serverless SQL.

    ``daily_usage`` is the list of ``ServerlessDailyUsage`` rows (dicts) from
    ``serverless_pools.json``; each entry must carry ``mb_seconds`` for the
    estimate to be non-zero. Returns ``None`` when nothing usable is present.
    """
    if not daily_usage:
        return None

    peak_cu_h = 0.0
    peak_day: str | None = None
    total_cu_seconds = 0.0
    window_days = 0
    for row in daily_usage:
        if not isinstance(row, Mapping):
            continue
        window_days += 1
        mb_seconds = float(row.get("mb_seconds") or 0)
        if mb_seconds <= 0:
            continue
        cu_h = _day_cu_hours_from_mb_seconds(mb_seconds)
        total_cu_seconds += cu_h * 3600.0
        if cu_h > peak_cu_h:
            peak_cu_h = cu_h
            peak_day = str(row.get("day") or "") or None

    if peak_cu_h <= 0 and total_cu_seconds <= 0:
        return None

    return ServerlessCuProjection(
        peak_day_cu_hours=peak_cu_h,
        peak_day=peak_day,
        window_days=window_days,
        total_cu_seconds=total_cu_seconds,
    )
