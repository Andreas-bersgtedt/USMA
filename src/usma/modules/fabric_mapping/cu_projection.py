"""Project a Fabric capacity (CU) SKU from observed Synapse DWU usage.

Heuristic only. Derives a recommended Fabric capacity SKU from peak observed DWU and
a configurable safety headroom (default 30 %). The mapping is approximate — confirm
with the current Fabric capacity sizing calculator and a TPC-style POC on real data.

Approach:

1. Pick the highest ``DWUUsedPercent * DWULimit / 100`` across all pools and
   timestamps (= peak active DWU consumed).
2. Apply headroom (default 1.3x).
3. Convert DWU → CU at ``DWU_TO_CU`` (default 0.020, i.e. 100 DWU ≈ 2.0 CU).
4. Look up the smallest Fabric F-SKU whose CU count covers that estimate.

The ``DWU_TO_CU`` value is *not* a Microsoft-published constant — Microsoft does not
publish a linear DWU→CU multiplier and the Fabric Updates Blog post
"Mapping Azure Synapse dedicated SQL pools to Fabric data warehouse compute"
(Hoang & Schacht, 2024;
https://blog.fabric.microsoft.com/blog/mapping-azure-synapse-dedicated-sql-pools-to-fabric-data-warehouse-compute/)
explicitly notes that a simple resource mapping is not accurate. Instead, that
blog publishes empirical TPC-H peer pairs (e.g. F32 ≈ DWU1000 power-run, F64 ≈
DWU1500–3000, F128 ≈ recommended for 10 TB). 0.020 CU/DWU + 30 % headroom
reproduces those performance-parity peers reasonably well across F8 → F2048 and
is intended as a *starting* SKU for a POC, not a final size.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# Microsoft's Fabric capacity F-SKUs in Capacity Units (CU). F2/F4/... up to F2048.
# Subset most relevant for Synapse DW migrations.
_FSKU_TABLE: tuple[tuple[str, int], ...] = (
    ("F2",   2),  ("F4",   4),  ("F8",   8),  ("F16",  16),
    ("F32",  32), ("F64",  64), ("F128", 128), ("F256", 256),
    ("F512", 512), ("F1024", 1024), ("F2048", 2048),
)

# 100 DWU ≈ 2.0 CU. Heuristic; see module docstring for rationale and caveats.
DWU_TO_CU = 0.020


@dataclass(frozen=True)
class CapacityProjection:
    peak_dwu: float
    peak_dwu_with_headroom: float
    estimated_cu: float
    recommended_sku: str
    headroom_pct: int
    notes: tuple[str, ...] = ()
    # Component contributions to ``estimated_cu`` (post-headroom). All
    # default to 0.0 so callers that only care about the legacy DWU path
    # see no behaviour change.
    dwu_cu_contribution: float = 0.0
    spark_cu_contribution: float = 0.0
    pipelines_cu_contribution: float = 0.0
    serverless_cu_contribution: float = 0.0
    # Pre-smoothing peak-day CU-hours for serverless SQL. Reported separately
    # so the SPA can show the raw "how big was the worst day" number
    # alongside the 24h-burndown sustained CU — useful because serverless
    # users typically reason in instantaneous CU, not 24h averages.
    serverless_peak_day_cu_hours: float = 0.0


def _peak_day_cu_from_payload(
    payload: dict | None,
    *,
    spark_field: str | None = None,
    pipeline_fields: tuple[str, ...] = (),
) -> tuple[float, int]:
    """Find the worst single-day CU-hour total across pools/pipelines.

    Fabric capacity smooths CU-second consumption over a rolling 24h
    burndown window, so the relevant sizing signal is **peak day**, not
    weekly average. We pick the 7-day rolling stat as our preferred
    observation window (long enough to catch a typical workday, short
    enough to react to recent migrations); if it's missing we fall back
    to the first window present.

    For each (pool, kind) or pipeline we read the per-window
    ``peak_day_cu_hours`` value (computed at collection time when the
    actual run timestamps are still available). The **maximum** across
    entries is returned, on the assumption that the busiest day for
    pool A and the busiest day for pool B can coincide in the worst
    case \u2014 use the larger of the two as the headroom basis.

    Returns ``(peak_day_cu_hours, window_days)``.
    """
    if not isinstance(payload, dict):
        return 0.0, 0
    peak = 0.0
    window_days = 0

    if spark_field is not None:
        for entry in payload.get("run_stats") or []:
            windows = entry.get("windows") or []
            if not windows:
                continue
            w = next((x for x in windows if x.get("window_days") == 7), windows[0])
            value = w.get("peak_day_cu_hours")
            if value is None:
                # Backwards compatibility with pre-v2.6.3 artefacts: derive
                # a conservative peak-day estimate from the window total.
                # Assume the work concentrates into half the window's days.
                total = float(w.get(spark_field) or 0.0)
                wd = int(w.get("window_days") or 0)
                value = (total / max(1, wd / 2)) if total and wd else 0.0
            peak = max(peak, float(value or 0.0))
            window_days = max(window_days, int(w.get("window_days") or 0))

    if pipeline_fields:
        rh = payload.get("run_history") or {}
        for entry in rh.get("by_pipeline") or []:
            windows = entry.get("windows") or []
            if not windows:
                continue
            w = next((x for x in windows if x.get("window_days") == 7), windows[0])
            value = w.get("peak_day_cu_hours")
            if value is None:
                total = sum(float(w.get(f) or 0.0) for f in pipeline_fields)
                wd = int(w.get("window_days") or 0)
                value = (total / max(1, wd / 2)) if total and wd else 0.0
            peak = max(peak, float(value or 0.0))
            window_days = max(window_days, int(w.get("window_days") or 0))

    return peak, window_days


def _spark_peak_day_cu(payload: dict | None) -> tuple[float, int]:
    """Spark Livy peak-day CU from ``spark_pools.json`` ``run_stats``."""
    if not payload:
        return 0.0, 0
    return _peak_day_cu_from_payload(
        payload,
        spark_field="est_cu_hours_fabric_spark",
    )


def _pipelines_peak_day_cu(payload: dict | None) -> tuple[float, int]:
    """Pipeline peak-day CU (DIU + vCore + orchestration combined)."""
    if not payload:
        return 0.0, 0
    return _peak_day_cu_from_payload(
        payload,
        pipeline_fields=(
            "est_cu_hours_from_diu",
            "est_cu_hours_from_vcore",
            "est_cu_hours_from_orchestration",
        ),
    )


def _serverless_projection(payload: dict | None):
    """Serverless SQL peak-day CU projection from ``serverless_pools.json``.

    Delegates to :func:`...serverless_pools.cu_estimate.project_serverless_cu`
    so the heuristic constants live with the rest of the serverless module.
    Imported lazily to avoid a hard dependency from fabric_mapping on the
    serverless_pools package during partial test setups.
    """
    if not isinstance(payload, dict):
        return None
    try:
        from ..serverless_pools.cu_estimate import project_serverless_cu
    except Exception:  # pragma: no cover — defensive
        return None
    return project_serverless_cu(payload.get("daily_usage") or [])


def project_capacity(
    series: Iterable[dict],
    *,
    headroom_pct: int = 30,
    spark_payload: dict | None = None,
    pipelines_payload: dict | None = None,
    serverless_payload: dict | None = None,
) -> CapacityProjection | None:
    """Compute a Fabric capacity projection from observed Synapse usage.

    Combines four signal sources, all converted to a single sustained
    CU number that is then sized to the smallest covering F-SKU:

    1. **Dedicated SQL pool DWU** \u2014 peak ``DWULimit * DWUUsedPercent / 100``
       across all pools and timestamps, converted at :data:`DWU_TO_CU`.
       DWU is already a rate metric so the peak instantaneous value is
       what matters, not an average over the window.
    2. **Spark Livy** (when ``spark_payload`` is provided) \u2014 the
       worst-day CU-hours across pools, divided by 24h. We use the
       per-day peak (``peak_day_cu_hours``) rather than the weekly
       average because Fabric capacity smoothing is a 24h burndown
       window: a one-day burst that exceeds ``F-SKU \u00d7 24`` CU-hours
       will throttle even if the weekly average is comfortable.
    3. **Pipelines** (when ``pipelines_payload`` is provided) \u2014 same
       per-day peak math, summing DIU + Mapping-Data-Flow vCore +
       orchestration CU-hours across pipelines.
    4. **Serverless SQL** (when ``serverless_payload`` is provided) \u2014
       heuristic 0.02 CU per 60 GB scanned, integrated over query
       duration (``mb_seconds`` daily aggregate), then peak-day CU-h
       / 24. Models the Fabric SQL Analytics Endpoint target. See
       :mod:`...serverless_pools.cu_estimate`.

    Components are added (Fabric capacity is shared across DW / Spark /
    Pipelines / serverless workloads) and a single ``headroom_pct`` is
    applied to the total before SKU lookup.

    Returns ``None`` only when *no* component produced a positive
    contribution.
    """
    peak_dwu = 0.0
    has_limit = False
    has_pct = False

    # Cross-reference DWULimit and DWUUsedPercent points by timestamp per pool.
    by_pool_metric: dict[tuple[str, str], dict] = {}
    for s in series:
        by_pool_metric[(s.get("resource_name", "?"), s.get("metric_name", "?"))] = s

    for (pool, metric), s in by_pool_metric.items():
        if metric != "DWUUsedPercent":
            continue
        has_pct = True
        limit_series = by_pool_metric.get((pool, "DWULimit"), {})
        limit_points = {ts: val for ts, val in (
            _norm_point(p) for p in (limit_series.get("points", []) or [])
        ) if ts}
        if limit_points:
            has_limit = True
        for p in s.get("points", []) or []:
            ts, pct = _norm_point(p)
            if ts is None or pct is None:
                continue
            limit = limit_points.get(ts)
            if limit is None:
                continue
            peak_dwu = max(peak_dwu, limit * pct / 100.0)

    has_dwu = (has_pct and has_limit) and peak_dwu > 0
    dwu_cu = peak_dwu * DWU_TO_CU if has_dwu else 0.0

    # Spark + Pipelines: convert peak-day CU-hours to required sustained
    # CU by dividing by Fabric's 24h burndown smoothing window.
    spark_peak_day, spark_window = _spark_peak_day_cu(spark_payload)
    pipelines_peak_day, pipelines_window = _pipelines_peak_day_cu(pipelines_payload)
    spark_cu = spark_peak_day / 24.0
    pipelines_cu = pipelines_peak_day / 24.0

    # Serverless SQL (Fabric SQL Analytics Endpoint): peak-day CU-hours
    # built from the heuristic 0.02 CU per 60 GB scanned, integrated
    # over query duration. See
    # :mod:`usma.modules.serverless_pools.cu_estimate`.
    serverless_proj = _serverless_projection(serverless_payload)
    serverless_peak_day = serverless_proj.peak_day_cu_hours if serverless_proj else 0.0
    serverless_window = serverless_proj.window_days if serverless_proj else 0
    serverless_cu = serverless_peak_day / 24.0

    base_cu = dwu_cu + spark_cu + pipelines_cu + serverless_cu
    if base_cu <= 0:
        return None

    multiplier = 1 + headroom_pct / 100.0
    cu_estimate = base_cu * multiplier
    sku = _smallest_sku_covering(cu_estimate)

    notes: list[str] = []
    if has_dwu:
        notes.append(
            f"DW peak DWU = {peak_dwu:.0f} → {dwu_cu:.2f} CU"
            f" (rate {DWU_TO_CU} CU/DWU).",
        )
    if spark_cu > 0:
        notes.append(
            f"Spark peak-day = {spark_peak_day:.2f} CU-hr"
            f" → {spark_cu:.2f} CU sustained over Fabric's 24h burndown"
            f" (worst day in last {spark_window} days).",
        )
    if pipelines_cu > 0:
        notes.append(
            f"Pipelines peak-day = {pipelines_peak_day:.2f} CU-hr"
            f" → {pipelines_cu:.2f} CU sustained over Fabric's 24h burndown"
            f" (DIU + MDF vCore + orchestration, worst day in last {pipelines_window} days).",
        )
    if serverless_cu > 0:
        notes.append(
            f"Serverless SQL peak-day = {serverless_peak_day:.2f} CU-hr"
            f" → {serverless_cu:.2f} CU sustained over Fabric's 24h burndown"
            f" (heuristic: 0.02 CU per 60 GB scanned × duration,"
            f" worst day in last {serverless_window} days).",
        )
    notes.append(
        f"Total {base_cu:.2f} CU + {headroom_pct}% headroom →"
        f" {cu_estimate:.2f} CU → smallest covering SKU is {sku}.",
    )
    notes.append(
        "Heuristic only; confirm with the Fabric capacity sizing calculator.",
    )

    # Per-component CU AFTER headroom so the SPA breakdown sums to estimated_cu.
    return CapacityProjection(
        peak_dwu=round(peak_dwu, 1),
        peak_dwu_with_headroom=round(peak_dwu * multiplier, 1),
        estimated_cu=round(cu_estimate, 2),
        recommended_sku=sku,
        headroom_pct=headroom_pct,
        notes=tuple(notes),
        dwu_cu_contribution=round(dwu_cu * multiplier, 2),
        spark_cu_contribution=round(spark_cu * multiplier, 2),
        pipelines_cu_contribution=round(pipelines_cu * multiplier, 2),
        serverless_cu_contribution=round(serverless_cu * multiplier, 2),
        serverless_peak_day_cu_hours=round(serverless_peak_day, 2),
    )


def _smallest_sku_covering(cu: float) -> str:
    for name, sku_cu in _FSKU_TABLE:
        if sku_cu >= cu:
            return name
    return _FSKU_TABLE[-1][0]  # cap


def _norm_point(p: object) -> tuple[object | None, float | None]:
    if isinstance(p, (list, tuple)) and len(p) == 2:
        ts, val = p
        try:
            return ts, float(val) if val is not None else None
        except (TypeError, ValueError):
            return ts, None
    return None, None
