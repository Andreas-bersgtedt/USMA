"""Pure aggregation of pipeline / activity run records into rolling-window stats.

This module contains *no* Azure SDK imports — it operates on plain dicts so
it can be exhaustively unit-tested with fixtures. The collector
(``run_history_client.py``) is responsible for paginating the Synapse Artifacts
API and feeding well-shaped dicts into :func:`aggregate_runs`.

Raw shapes consumed (subset of fields, all optional/duck-typed):

``RawRun`` (one item per pipeline execution):
    - ``run_id``            : str
    - ``pipeline_name``     : str
    - ``status``            : str   (``"Succeeded"`` | ``"Failed"`` | ``"InProgress"`` | ``"Queued"`` | ``"Cancelled"`` | ...)
    - ``run_end``           : datetime (timezone-aware preferred)
    - ``run_start``         : datetime
    - ``duration_in_ms``    : int | float | None

``RawActivityRun`` (zero or more per run, only fetched for pipelines that
contain data-movement activities):
    - ``activity_name``     : str
    - ``activity_type``     : str   (``"Copy"`` | ``"ExecuteDataFlow"`` | ``"Lookup"`` | ...)
    - ``status``            : str
    - ``output``            : dict  (Copy: ``dataRead`` / ``dataWritten`` in bytes;
                                       Dataflow: ``bytesProcessed`` or ``runStatus.metrics``;
                                       Azure-IR billing: ``billingReference.billableDuration[]``
                                       with ``unit == "DIUHours"``)

DIU-hours → Fabric CU-hours
---------------------------

When a Copy / Mapping Data Flow activity reports Azure-IR billing, the
aggregator sums ``billableDuration[].duration`` across all activity runs of a
pipeline (only for entries with ``unit == "DIUHours"``) and projects an
equivalent Fabric CU-hours figure via :data:`DIU_TO_CU_HOURS` (default 1.5).
This is a *heuristic* placeholder — Microsoft does not publish a fixed
DIU→CU multiplier; verify with the current Fabric capacity sizing
guidance before quoting the number to customers.

vCore-seconds → Fabric CU-seconds (Mapping Data Flows)
------------------------------------------------------

Mapping Data Flow (``ExecuteDataFlow``) activities don't bill in DIU-hours —
they spin up a managed Spark cluster on the Azure Integration Runtime.
Rather than trust the service-side ``billingReference`` (which is a
post-billing rollup that includes cluster spin-up and warm-pool reuse),
we derive vCore-hours **from the actual Spark cluster that executed
the data flow**:

    vCore-hours per run = compute.coreCount × wall_clock_runtime_hours

``compute.coreCount`` is captured at pipeline-parse time from each
ExecuteDataFlow activity's static type-properties (supported values:
8, 16, 32, 48, 80, 144, 272 — total cluster cores across driver +
workers). When the activity uses the default autoresolve IR or a
runtime expression for ``coreCount``, the aggregator falls back to
``DEFAULT_DATAFLOW_CORES`` (smallest General Purpose: 8 vCores).

Wall-clock runtime comes from ``activity_run.output.executionDuration``
(seconds, published by ADF/Synapse once the cluster releases) and falls
back to ``activity_run.duration_in_ms``.

For a migration target of "rebuild the data flow as a Fabric Spark job",
we project the consumed compute to Fabric Capacity Units assuming
**1 vCore-second = 0.5 CU-second** (equivalently 1 vCore-hour =
0.5 CU-hour). This matches the Fabric Spark billing factor at the time
of writing; verify against current Fabric capacity guidance before
quoting numbers.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .models import (
    RUN_STATS_WINDOWS_DAYS,
    PipelineRunStats,
    PipelineRunWindowStats,
)

# Activity types that, when present in a pipeline's static definition, mean
# we should attribute "data movement" metrics to that pipeline.
DATA_MOVEMENT_ACTIVITY_TYPES: frozenset[str] = frozenset({
    "Copy",
    "ExecuteDataFlow",
    "Lookup",
})

# Heuristic: 1 Azure-IR DIU-hour ≈ 1.5 Fabric CU-hours. See module docstring
# for caveats — this is a placeholder, not a Microsoft-published constant.
DIU_TO_CU_HOURS: float = 1.5

# Mapping Data Flow Spark compute → Fabric Spark job CU. 1 vCore-second
# consumed on the Synapse-managed Spark cluster maps to 0.5 CU-second on
# Fabric (equivalently 0.5 CU-hour per vCore-hour). See module docstring.
VCORE_HOURS_TO_CU_HOURS: float = 0.5

# Default Spark cluster size assumed for an ExecuteDataFlow activity that
# uses the autoresolve Azure Integration Runtime without an explicit
# ``compute.coreCount`` override. Per Microsoft docs the autoresolve IR
# provisions the smallest General Purpose cluster (4 driver + 4 worker
# cores = 8 vCores total). Override via ``aggregate_runs(default_dataflow_cores=...)``.
DEFAULT_DATAFLOW_CORES: int = 8

# Billing-unit aliases for vCore-time emitted by the Azure-IR Spark cluster
# under Mapping Data Flow activities. We strip non-alphanumerics and lower-case
# before matching, so payloads with units like ``vCore-Hour`` / ``Core Hours``
# / ``vCoreHours`` all collapse to the same canonical token.
_VCORE_UNITS: frozenset[str] = frozenset({
    "corehour", "corehours",
    "vcorehour", "vcorehours",
})


def _normalize_unit(value: Any) -> str:
    """Lower-case + strip non-alphanumeric characters from a billing unit string."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _billing_entries(activity_run: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ``billableDuration`` entries from an activity run, or [].

    Tolerates real-world payload shapes: ``output``, ``billingReference``
    and ``billableDuration`` can each arrive as a JSON-encoded string
    instead of a parsed dict/list (the SDK preserves whatever the service
    returned). A single billable-duration entry may also be sent as a dict
    rather than a one-element list.
    """
    output = activity_run.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (ValueError, TypeError):
            return []
    if not isinstance(output, dict):
        return []
    billing = output.get("billingReference")
    if isinstance(billing, str):
        try:
            billing = json.loads(billing)
        except (ValueError, TypeError):
            return []
    if not isinstance(billing, dict):
        return []
    entries = billing.get("billableDuration")
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except (ValueError, TypeError):
            return []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]

# Microsoft-published Fabric Pipelines meter rate for the Data Orchestration
# engine type: 0.0056 CU-hours per non-copy activity run. (Copy activity is
# billed under the Data Movement meter via DIU-hours.) Source: Fabric Updates
# Blog "Announcing Fabric Capacities — everything you need to know" (Hoang &
# Schacht, 2024) and Microsoft Learn "Fabric data factory pricing" tables.
ORCHESTRATION_CU_HOURS_PER_ACTIVITY: float = 0.0056

# Activity types billed under the Data Movement meter (DIU-hours), NOT the
# Data Orchestration meter. We exclude these when counting orchestration
# activity runs to avoid double-counting.
_DATA_MOVEMENT_BILLED_TYPES: frozenset[str] = frozenset({"copy"})

_TERMINAL_SUCCESS = {"succeeded"}
_TERMINAL_FAILURE = {"failed"}


@dataclass
class _RunBucket:
    count: int = 0
    succeeded: int = 0
    failed: int = 0
    other: int = 0
    durations_ms: list[float] = field(default_factory=list)
    data_bytes: list[int] = field(default_factory=list)  # per-run total bytes (only when >0 / known)
    diu_hours: list[float] = field(default_factory=list)  # per-run total DIU-hours (only when reported)
    vcore_hours: list[float] = field(default_factory=list)  # per-run total vCore-hours (Mapping Data Flow Spark)
    # Non-copy activity run counts. ``observed_non_copy`` accumulates exact
    # counts from runs we sampled activity-runs for; ``unsampled_runs`` is
    # the count of runs in this window we did *not* fetch activity-runs for
    # (so the aggregator can apply the static-count fallback for them).
    observed_non_copy: int = 0
    unsampled_runs: int = 0
    # v2.6.3 — per-UTC-day total CU-hours (DIU + MDF vCore + orchestration)
    # for runs that ended on that day. ``max(values())`` feeds
    # ``PipelineRunWindowStats.peak_day_cu_hours`` so the SKU sizer can
    # cover the busiest day instead of the window average.
    daily_cu_hours: dict = field(default_factory=dict)


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            # Python's fromisoformat handles "...+00:00"; convert "Z" first.
            txt = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(txt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (good enough for run durations, no numpy dep)."""
    if not values:
        return None
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_vals)))
    return sorted_vals[rank - 1]


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < 0 or f != f:  # reject negatives and NaN
        return None
    return f


def extract_diu_hours(activity_run: dict[str, Any]) -> float | None:
    """Best-effort extraction of Data Integration Units consumed by an activity run.

    Synapse / ADF Copy and Mapping Data Flow activities running on the Azure
    Integration Runtime expose billing in ``output.billingReference``::

        {
          "billingReference": {
            "activityType": "ExternalActivity",
            "billableDuration": [
              { "meterType": "AzureIR", "duration": 0.066, "unit": "DIUHours" }
            ]
          }
        }

    Returns the sum of ``duration`` across all billable-duration entries with
    ``unit == "DIUHours"``. Returns ``None`` when no DIU-hour entry is present
    (so callers can distinguish "not reported" from "0.0 DIU-hours").
    """
    entries = _billing_entries(activity_run)
    if not entries:
        return None

    total: float = 0.0
    seen = False
    for entry in entries:
        unit = _normalize_unit(entry.get("unit"))
        if unit != "diuhours":
            continue
        duration = _safe_float(entry.get("duration"))
        if duration is None:
            continue
        total += duration
        seen = True
    return total if seen else None


def extract_vcore_hours(activity_run: dict[str, Any]) -> float | None:
    """Best-effort extraction of vCore-hours consumed by an activity run.

    Mapping Data Flow (``ExecuteDataFlow``) activities run on a managed
    Spark cluster on the Azure Integration Runtime. Their billing reference
    in ``output.billingReference.billableDuration[]`` looks like::

        [
          { "meterType": "General",          "duration": 0.066, "unit": "coreHour" },
          { "meterType": "MemoryOptimized",  "duration": 0.50,  "unit": "vCoreHour" }
        ]

    where ``meterType`` reflects the Spark cluster shape (``General`` /
    ``MemoryOptimized`` / ``ComputeOptimized``). We sum every entry whose
    unit (case-insensitive) is a recognised vCore-time alias.

    Returns ``None`` when no vCore-time entry is present (so callers can
    distinguish "not reported" from "0.0 vCore-hours").
    """
    entries = _billing_entries(activity_run)
    if not entries:
        return None

    total: float = 0.0
    seen = False
    for entry in entries:
        unit = _normalize_unit(entry.get("unit"))
        if unit not in _VCORE_UNITS:
            continue
        duration = _safe_float(entry.get("duration"))
        if duration is None:
            continue
        total += duration
        seen = True
    return total if seen else None


def extract_vcore_hours_from_runtime(
    activity_run: dict[str, Any],
    cores: int,
) -> float | None:
    """Derive vCore-hours from runtime statistics for an ExecuteDataFlow activity.

    Reflects the Spark cluster that actually executed the data flow:
    ``vCore-hours = cluster_cores × wall_clock_runtime_hours``.

    Runtime is sourced from (in order of preference):
        1. ``output.executionDuration`` (seconds) — published by ADF/Synapse
           for ExecuteDataFlow once the cluster releases.
        2. ``activity_run.duration_in_ms`` — activity wall-clock (includes
           cluster acquisition time when cold).

    Returns ``None`` when neither runtime signal is available or when
    ``cores <= 0`` so callers can distinguish "not reported" from "0".
    """
    if not isinstance(cores, int) or cores <= 0:
        return None
    a_type = (activity_run.get("activity_type") or "").lower()
    if a_type != "executedataflow":
        return None
    seconds: float | None = None
    output = activity_run.get("output")
    if isinstance(output, dict):
        execd = _safe_float(output.get("executionDuration"))
        if execd is not None and execd > 0:
            seconds = execd
    if seconds is None:
        ms = _safe_float(activity_run.get("duration_in_ms"))
        if ms is not None and ms > 0:
            seconds = ms / 1000.0
    if seconds is None:
        return None
    return cores * seconds / 3600.0


def extract_data_bytes(activity_run: dict[str, Any]) -> int | None:
    """Best-effort extraction of bytes moved by a single activity run.

    Returns ``None`` when the activity doesn't expose data-movement counters
    (so callers can distinguish "no data movement" from "0 bytes").
    """
    output = activity_run.get("output")
    if not isinstance(output, dict):
        return None
    a_type = (activity_run.get("activity_type") or "").lower()

    if a_type == "copy":
        # Synapse Copy activity: dataRead / dataWritten are bytes.
        read = _safe_int(output.get("dataRead"))
        written = _safe_int(output.get("dataWritten"))
        if read is None and written is None:
            return None
        # Use written when present, otherwise read. (Don't double-count.)
        return written if written is not None else read

    if a_type == "executedataflow":
        # Mapping data flows surface metrics under runStatus.metrics.<sink>.bytes.
        # Real Synapse payloads sometimes serialise ``runStatus`` as a
        # JSON-encoded string, so accept either shape.
        run_status = output.get("runStatus")
        if isinstance(run_status, str):
            try:
                run_status = json.loads(run_status)
            except (ValueError, TypeError):
                run_status = None
        if isinstance(run_status, dict):
            metrics = run_status.get("metrics")
            if isinstance(metrics, str):
                try:
                    metrics = json.loads(metrics)
                except (ValueError, TypeError):
                    metrics = None
            if isinstance(metrics, dict):
                total = 0
                seen = False
                for sink_metrics in metrics.values():
                    if isinstance(sink_metrics, dict):
                        # Some payloads use ``bytes`` (int) and some use
                        # ``rowsWritten`` / ``progressState`` only. We only
                        # claim a number when an explicit byte count exists.
                        b = _safe_int(sink_metrics.get("bytes"))
                        if b is not None:
                            total += b
                            seen = True
                if seen:
                    return total
        b = _safe_int(output.get("bytesProcessed"))
        return b

    if a_type == "lookup":
        # Lookups don't carry bytes; surface 0 only when explicitly reported.
        return _safe_int(output.get("dataRead"))

    return None


def aggregate_runs(
    *,
    pipeline_names: Iterable[str],
    pipelines_with_data_movement: set[str],
    runs: Iterable[dict[str, Any]],
    activity_runs_by_run_id: dict[str, list[dict[str, Any]]] | None = None,
    non_copy_activity_counts: dict[str, int] | None = None,
    dataflow_cores_by_pipeline_activity: dict[str, dict[str, int]] | None = None,
    default_dataflow_cores: int = DEFAULT_DATAFLOW_CORES,
    now: datetime | None = None,
    windows_days: tuple[int, ...] = RUN_STATS_WINDOWS_DAYS,
) -> list[PipelineRunStats]:
    """Bucket runs into rolling windows and emit per-pipeline stats.

    ``pipeline_names`` ensures every known pipeline gets a row (with zero
    counts) even if it has not run in the fetch window.

    ``dataflow_cores_by_pipeline_activity`` provides static cluster sizes
    (``pipeline_name -> activity_name -> total Spark vCores``) for
    ExecuteDataFlow activities. When an ExecuteDataFlow activity-run is
    sampled, vCore-hours are derived as ``cores × wall-clock runtime`` —
    i.e. from the Spark cluster that executed the data flow — rather than
    from the service-side billing reference. Activities not in the map use
    ``default_dataflow_cores`` (defaults to ``DEFAULT_DATAFLOW_CORES``).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    activity_runs_by_run_id = activity_runs_by_run_id or {}
    non_copy_activity_counts = non_copy_activity_counts or {}
    dataflow_cores_by_pipeline_activity = dataflow_cores_by_pipeline_activity or {}

    # Pre-compute window cutoffs so each run is bucketed in O(W).
    sorted_windows = sorted(windows_days)
    cutoffs = [(w, now - timedelta(days=w)) for w in sorted_windows]

    by_pipe_window: dict[str, dict[int, _RunBucket]] = defaultdict(
        lambda: {w: _RunBucket() for w in sorted_windows}
    )
    last_run: dict[str, tuple[datetime, str | None]] = {}

    for run in runs:
        pname = run.get("pipeline_name") or run.get("pipeline")
        if not pname:
            continue
        end_dt = _coerce_datetime(run.get("run_end") or run.get("run_start"))
        if end_dt is None:
            continue

        status = (run.get("status") or "").strip()
        status_lc = status.lower()
        duration = run.get("duration_in_ms")
        try:
            duration_f = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_f = None

        # Track latest run per pipeline.
        prev = last_run.get(pname)
        if prev is None or end_dt > prev[0]:
            last_run[pname] = (end_dt, status or None)

        # Sum data bytes + DIU-hours from activity runs. None → unknown.
        run_id = run.get("run_id")
        bytes_for_run: int | None = None
        diu_for_run: float | None = None
        vcore_for_run: float | None = None
        non_copy_for_run: int | None = None
        sampled = bool(run_id and run_id in activity_runs_by_run_id)
        if sampled:
            ars = activity_runs_by_run_id[run_id]
            non_copy_for_run = 0
            cores_for_pipe = dataflow_cores_by_pipeline_activity.get(pname, {})
            for ar in ars:
                if pname in pipelines_with_data_movement:
                    b = extract_data_bytes(ar)
                    if b is not None:
                        bytes_for_run = (bytes_for_run or 0) + b
                    d = extract_diu_hours(ar)
                    if d is not None:
                        diu_for_run = (diu_for_run or 0.0) + d
                    a_type_lc = (ar.get("activity_type") or "").lower()
                    if a_type_lc == "executedataflow":
                        # Derive vCore-hours from the underlying Spark
                        # cluster shape × wall-clock runtime, not from
                        # billingReference. Cores come from the static
                        # pipeline JSON (compute.coreCount); fall back to
                        # the autoresolve-IR default when not specified.
                        a_name = ar.get("activity_name") or ""
                        cores = cores_for_pipe.get(a_name)
                        if not isinstance(cores, int) or cores <= 0:
                            cores = default_dataflow_cores
                        v = extract_vcore_hours_from_runtime(ar, cores)
                        if v is not None:
                            vcore_for_run = (vcore_for_run or 0.0) + v
                a_type = (ar.get("activity_type") or "").strip().lower()
                if a_type and a_type not in _DATA_MOVEMENT_BILLED_TYPES:
                    non_copy_for_run += 1

        for w, cutoff in cutoffs:
            if end_dt < cutoff:
                continue
            bucket = by_pipe_window[pname][w]
            bucket.count += 1
            if status_lc in _TERMINAL_SUCCESS:
                bucket.succeeded += 1
            elif status_lc in _TERMINAL_FAILURE:
                bucket.failed += 1
            else:
                bucket.other += 1
            if duration_f is not None:
                bucket.durations_ms.append(duration_f)
            if bytes_for_run is not None:
                bucket.data_bytes.append(bytes_for_run)
            if diu_for_run is not None:
                bucket.diu_hours.append(diu_for_run)
            if vcore_for_run is not None:
                bucket.vcore_hours.append(vcore_for_run)
            if sampled and non_copy_for_run is not None:
                bucket.observed_non_copy += non_copy_for_run
            else:
                bucket.unsampled_runs += 1
            # v2.6.3 \u2014 per-day CU contribution from this run. For sampled
            # runs we have exact non-copy counts; for unsampled runs we
            # use the static fallback (matches the window-level totals).
            run_cu = (
                (diu_for_run or 0.0) * DIU_TO_CU_HOURS
                + (vcore_for_run or 0.0) * VCORE_HOURS_TO_CU_HOURS
            )
            if sampled and non_copy_for_run is not None:
                run_cu += non_copy_for_run * ORCHESTRATION_CU_HOURS_PER_ACTIVITY
            else:
                run_cu += (
                    max(0, int(non_copy_activity_counts.get(pname, 0)))
                    * ORCHESTRATION_CU_HOURS_PER_ACTIVITY
                )
            if run_cu > 0:
                day = end_dt.astimezone(timezone.utc).date()
                bucket.daily_cu_hours[day] = bucket.daily_cu_hours.get(day, 0.0) + run_cu

    # Materialize results, including zero rows for pipelines with no runs.
    out: list[PipelineRunStats] = []
    for pname in pipeline_names:
        windows: list[PipelineRunWindowStats] = []
        buckets = by_pipe_window.get(pname, {w: _RunBucket() for w in sorted_windows})
        for w in sorted_windows:
            b = buckets[w]
            terminal = b.succeeded + b.failed
            success_rate = (b.succeeded / terminal) if terminal else None
            avg_dur = (sum(b.durations_ms) / len(b.durations_ms)) if b.durations_ms else None
            p95 = _percentile(b.durations_ms, 95.0)

            has_dm = pname in pipelines_with_data_movement
            avg_mb_per_run: float | None = None
            total_mb: float | None = None
            if has_dm:
                if b.data_bytes:
                    total_bytes = sum(b.data_bytes)
                    total_mb = total_bytes / (1024 * 1024)
                    avg_mb_per_run = total_mb / len(b.data_bytes)
                else:
                    # Pipeline can move data but no metrics observed in window:
                    # leave as None (unknown) rather than reporting 0.
                    total_mb = 0.0 if b.count == 0 else None
                    avg_mb_per_run = None

            avg_diu_per_run: float | None = None
            total_diu: float | None = None
            est_cu_hours: float | None = None
            if has_dm:
                if b.diu_hours:
                    total_diu = sum(b.diu_hours)
                    avg_diu_per_run = total_diu / len(b.diu_hours)
                    est_cu_hours = total_diu * DIU_TO_CU_HOURS
                else:
                    total_diu = 0.0 if b.count == 0 else None
                    avg_diu_per_run = None
                    est_cu_hours = 0.0 if b.count == 0 else None

            # Mapping Data Flow Spark compute (vCore-hours). Projected to
            # Fabric Spark job CU-hours via VCORE_HOURS_TO_CU_HOURS (=0.5,
            # i.e. 1 vCore-second = 0.5 CU-second).
            avg_vcore_per_run: float | None = None
            total_vcore: float | None = None
            est_cu_hours_from_vcore: float | None = None
            if has_dm:
                if b.vcore_hours:
                    total_vcore = sum(b.vcore_hours)
                    avg_vcore_per_run = total_vcore / len(b.vcore_hours)
                    est_cu_hours_from_vcore = total_vcore * VCORE_HOURS_TO_CU_HOURS
                else:
                    total_vcore = 0.0 if b.count == 0 else None
                    avg_vcore_per_run = None
                    est_cu_hours_from_vcore = 0.0 if b.count == 0 else None

            # Data Orchestration meter: 0.0056 CU-hr per non-copy activity
            # run. Use observed counts from sampled activity-runs (which
            # naturally include ForEach / Until / If fan-out) and fall back
            # to (static non-copy count × unsampled run count) for runs we
            # did not fetch activity-runs for.
            non_copy_per_run_static = max(0, int(non_copy_activity_counts.get(pname, 0)))
            est_orch_runs = b.observed_non_copy + non_copy_per_run_static * b.unsampled_runs
            est_orch_cu_hours = est_orch_runs * ORCHESTRATION_CU_HOURS_PER_ACTIVITY

            windows.append(PipelineRunWindowStats(
                window_days=w,
                run_count=b.count,
                succeeded=b.succeeded,
                failed=b.failed,
                other=b.other,
                success_rate=success_rate,
                avg_duration_ms=avg_dur,
                p95_duration_ms=p95,
                avg_data_moved_mb_per_run=avg_mb_per_run,
                total_data_moved_mb=total_mb,
                avg_diu_hours_per_run=avg_diu_per_run,
                total_diu_hours=total_diu,
                est_cu_hours_from_diu=est_cu_hours,
                avg_vcore_hours_per_run=avg_vcore_per_run,
                total_vcore_hours=total_vcore,
                est_cu_hours_from_vcore=est_cu_hours_from_vcore,
                est_non_copy_activity_runs=est_orch_runs,
                est_cu_hours_from_orchestration=est_orch_cu_hours,
                peak_day_cu_hours=(
                    max(b.daily_cu_hours.values()) if b.daily_cu_hours
                    else (0.0 if b.count == 0 else None)
                ),
            ))

        last = last_run.get(pname)
        out.append(PipelineRunStats(
            pipeline=pname,
            has_data_movement=pname in pipelines_with_data_movement,
            last_run_at=last[0] if last else None,
            last_run_status=last[1] if last else None,
            windows=windows,
        ))
    return out


def pipelines_with_data_movement(activities: Iterable[dict[str, Any]]) -> set[str]:
    """Return the set of pipelines that statically contain a data-movement activity."""
    out: set[str] = set()
    for a in activities:
        a_type = a.get("type") or ""
        if a_type in DATA_MOVEMENT_ACTIVITY_TYPES:
            pname = a.get("pipeline")
            if pname:
                out.add(pname)
    return out


def non_copy_activity_counts(activities: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Count non-Copy activities per pipeline (for orchestration CU estimation).

    Microsoft's Fabric pipelines meter charges 0.0056 CU-hr per non-copy
    activity *run*. We approximate runs as (this count) * (pipeline run
    count in window).
    """
    counts: dict[str, int] = defaultdict(int)
    for a in activities:
        pname = a.get("pipeline")
        if not pname:
            continue
        a_type = (a.get("type") or "").strip().lower()
        if a_type in _DATA_MOVEMENT_BILLED_TYPES:
            continue
        counts[pname] += 1
    return dict(counts)


def dataflow_cores_by_pipeline_activity(
    activities: Iterable[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Index ExecuteDataFlow activities by ``pipeline -> activity -> cores``.

    Source: the static ``dataflow_cores`` field captured from each
    ExecuteDataFlow activity's ``compute.coreCount`` at pipeline-parse
    time. Activities without an explicit value (default autoresolve IR or
    dynamic expression) are simply omitted from the map — callers should
    fall back to ``DEFAULT_DATAFLOW_CORES`` for unmapped activities.
    """
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for a in activities:
        if (a.get("type") or "") != "ExecuteDataFlow":
            continue
        pname = a.get("pipeline")
        aname = a.get("name")
        cores = a.get("dataflow_cores")
        if not (pname and aname):
            continue
        if not isinstance(cores, int) or cores <= 0:
            continue
        out[pname][aname] = cores
    return dict(out)
