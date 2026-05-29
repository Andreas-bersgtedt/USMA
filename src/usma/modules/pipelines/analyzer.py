"""Orchestrator for the pipelines module."""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from datetime import date as _date
from typing import Any

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from . import expression_compat, run_stats, schedule_mapper
from .artifacts_client import ArtifactsApiClient
from .models import (
    RUN_STATS_WINDOWS_DAYS,
    ExpressionFinding,
    PipelineRunHistory,
    PipelinesAnalysis,
    ScheduleMapping,
)
from .run_history_client import RunHistoryClient
from .adf_run_history_client import AdfRunHistoryClient
from .sources import PipelineCollector
from .sources.adf_collector import AdfCollector
from .sources.synapse_collector import SynapseCollector

log = logging.getLogger(__name__)


class PipelinesAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
        collector: PipelineCollector | None = None,
    ) -> None:
        self._cfg = cfg
        self._api = ArtifactsApiClient(cfg.azure)
        self._progress = progress or NullProgress()
        # Phase 2: source-agnostic enumeration via PipelineCollector.
        # Default preserves the Phase 1 Synapse-workspace behavior so
        # existing callers (cli.py, modules/__init__.py) are unaffected.
        # ADF and future sources are wired by passing a pre-built
        # collector (e.g. ``AdfCollector(bundle)``).
        self._collector: PipelineCollector = collector or SynapseCollector(
            self._api, workspace_name=cfg.azure.workspace_name,
        )
        # Run-history is now wired for both sources. Synapse uses the
        # Artifacts ``pipeline_run`` operations; ADF uses
        # ``DataFactoryManagementClient.pipeline_runs.query_by_factory``.
        # The two clients share an iter_pipeline_runs / iter_activity_runs
        # / count_pipeline_runs duck-typed surface so ``_collect_run_history``
        # stays source-agnostic. Future collectors that don't have an
        # equivalent monitoring API can set this to ``None`` to opt out.
        self._is_synapse_collector = isinstance(self._collector, SynapseCollector)

    @classmethod
    def for_descriptor(
        cls,
        cfg: AppConfig,
        descriptor: Any,
        creds: Any,
        *,
        progress: ProgressReporter | None = None,
    ) -> "PipelinesAnalyzer":
        """Build an analyzer wired to the right ``PipelineCollector`` for
        ``descriptor.type`` (Phase 2 dispatch entry point).

        Synapse descriptors fall back to the existing ``ArtifactsApiClient``
        path (so monitoring-API features stay enabled). ADF descriptors
        get an ``AdfCollector`` over ``DataFactoryManagementClient``.
        """
        # Imports are local to keep the analyzer importable in environments
        # that lack the per-source SDKs.
        from ...sources import SourceType

        source_type = getattr(descriptor, "type", None)
        if source_type == SourceType.ADF:
            from ...sources.adf.provider import AdfProvider
            from .sources.adf_collector import AdfCollector

            bundle = AdfProvider().make_clients(descriptor, creds)
            collector: PipelineCollector = AdfCollector(bundle)
            return cls(cfg, progress=progress, collector=collector)
        # Default: Synapse workspace (legacy single-source path).
        return cls(cfg, progress=progress)

    def run(self) -> PipelinesAnalysis:
        # When a non-Synapse collector is wired (e.g. AdfCollector), it
        # exposes its own ``endpoint`` (typically the factory ARM ID).
        # Falling back to ``self._api.endpoint`` keeps the Synapse path
        # unchanged.
        endpoint = getattr(self._collector, "endpoint", None) or self._api.endpoint
        result = PipelinesAnalysis(
            workspace_name=self._cfg.azure.workspace_name,
            subscription_id=self._cfg.azure.subscription_id,
            resource_group=self._cfg.azure.resource_group,
            artifacts_endpoint=endpoint,
            generated_at=datetime.now(timezone.utc),
        )

        # 7 sub-tasks: pipelines+activities, linked_services, datasets,
        # triggers, integration_runtimes, expression_compat, schedule_mapper.
        # Run history (when enabled) adds N more steps via add_total below.
        self._progress.start(7, label="enumerating pipelines")

        # Pipelines + activities (walked together so we only call the API once).
        try:
            pipes: list = []
            acts: list = []
            for pipe, activities in self._collector.iter_pipelines_with_activities():
                pipes.append(pipe)
                acts.extend(activities)
            result.pipelines = pipes
            result.activities = acts
        except Exception as exc:  # noqa: BLE001
            log.warning("pipelines collection failed: %s", exc)
            result.errors.append(format_error("pipelines", exc))
        self._progress.step(label="pipelines+activities")

        for label, fn, attr in (
            ("linked_services", self._collector.list_linked_services, "linked_services"),
            ("datasets", self._collector.list_datasets, "datasets"),
            ("triggers", self._collector.list_triggers, "triggers"),
            ("integration_runtimes", self._collector.list_integration_runtimes, "integration_runtimes"),
        ):
            try:
                setattr(result, attr, list(fn()))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s collection failed: %s", label, exc)
                result.errors.append(format_error(label, exc))
            self._progress.step(label=label)

        # v2 — expression-language compatibility scan over activity payloads.
        try:
            findings = expression_compat.scan_activities(
                [a.model_dump() for a in result.activities]
            )
            result.expression_findings = [
                ExpressionFinding(
                    rule_id=f.rule_id, label=f.label, severity=f.severity,
                    pipeline=f.pipeline, activity=f.activity, expression=f.expression,
                )
                for f in findings
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("expression_compat failed: %s", exc)
            result.errors.append(f"expression_compat: {exc}")
        self._progress.step(label="expression_compat")

        # v2 — trigger schedule → Fabric schedule mapping.
        try:
            for trig in result.triggers:
                sched = schedule_mapper.map_trigger(trig.model_dump())
                result.schedule_mappings.append(ScheduleMapping(
                    trigger_name=trig.name,
                    trigger_type=trig.type,
                    fabric_kind=sched.kind,
                    every_n=sched.every_n,
                    interval=sched.interval,
                    days_of_week=list(sched.days_of_week),
                    start_time_utc=sched.start_time_utc,
                    end_time_utc=sched.end_time_utc,
                    notes=list(sched.notes),
                    summary=schedule_mapper.render_human(sched),
                ))
        except Exception as exc:  # noqa: BLE001
            log.warning("schedule_mapper failed: %s", exc)
            result.errors.append(f"schedule_mapper: {exc}")
        self._progress.step(label="schedule_mapper")

        # v3 — pipeline run history (counts / success rates / data movement).
        # Both Synapse (Artifacts API) and ADF (DataFactoryManagementClient)
        # are supported; ``_collect_run_history`` picks the right client by
        # looking at ``self._collector``. Any future collector that lacks a
        # run-history backend can be skipped here once the dispatch grows
        # past two sources.
        if _env_flag("SMA_PIPELINES_RUN_HISTORY", default=True):
            self._progress.add_total(1)
            try:
                result.run_history = self._collect_run_history(result)
            except Exception as exc:  # noqa: BLE001
                log.warning("run_history collection failed: %s", exc)
                result.errors.append(format_error("run_history", exc))
            self._progress.step(label="run_history")

        return result

    # ------------------------------------------------------------------ run history

    def _collect_run_history(self, result: PipelinesAnalysis) -> PipelineRunHistory:
        max_window = max(RUN_STATS_WINDOWS_DAYS)
        days = _env_int("SMA_PIPELINES_RUN_DAYS", default=max_window, minimum=1)
        # In daily-fetch mode the limit is cumulative across many small
        # per-day queries, so default it higher than the single-call
        # 5000-row sweet spot. Users with explicit overrides keep them.
        daily_fetch = _env_flag("SMA_PIPELINES_DAILY_FETCH", default=True)
        run_limit_default = 50000 if daily_fetch else 5000
        run_limit = _env_int(
            "SMA_PIPELINES_RUN_LIMIT", default=run_limit_default, minimum=1,
        )
        fetch_activity_runs = _env_flag("SMA_PIPELINES_ACTIVITY_RUNS", default=True)
        # Sampling cap: per-pipeline upper bound on how many terminal runs we
        # actually fetch activity-runs for. The aggregator only needs a
        # *sample* to derive averages — set to 0 to disable the cap.
        ar_sample_per_pipeline = _env_int(
            "SMA_PIPELINES_ACTIVITY_RUN_SAMPLE", default=50, minimum=0,
        )
        # Number of parallel HTTP fetches for activity-runs. Synapse Artifacts
        # is sync HTTP, so a small thread pool gives a big win without hitting
        # service throttling.
        concurrency = _env_int(
            "SMA_PIPELINES_RUN_CONCURRENCY", default=8, minimum=1,
        )

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        history = PipelineRunHistory(window_start=start, window_end=end)

        # Pick the right run-history client for this source. Synapse uses
        # the Artifacts ``pipeline_run`` operations (workspace-scoped);
        # ADF uses ``DataFactoryManagementClient.pipeline_runs.query_by_factory``.
        # Both clients expose the same duck-typed surface
        # (iter_pipeline_runs / iter_activity_runs / count_pipeline_runs).
        client: Any
        if isinstance(self._collector, AdfCollector):
            client = AdfRunHistoryClient(self._collector.bundle)
        else:
            client = RunHistoryClient(self._cfg.azure)

        # Two fetch strategies:
        #   1. Daily-chunked (default): iterate one UTC day at a time and
        #      pull *all* runs for that day via continuation paging. This
        #      avoids the single-call 5000-row ceiling so the aggregator
        #      sees a complete picture for every window, every pipeline,
        #      and every status. A cumulative cap (SMA_PIPELINES_RUN_LIMIT)
        #      still applies as a safety net.
        #   2. Legacy single-call: one big paged query for the whole window.
        #      Kept for environments that prefer fewer API round-trips.
        runs: list[dict] = []
        if daily_fetch:
            # Whole UTC days inside the window, in chronological order.
            first_day = start.astimezone(timezone.utc).date()
            last_day = end.astimezone(timezone.utc).date()
            day_count = (last_day - first_day).days + 1
            self._progress.add_total(day_count)
            self._progress.message(
                f"fetching pipeline runs day-by-day ({day_count} days)"
            )
            cur = first_day
            for idx in range(day_count):
                remaining_days = day_count - idx - 1
                day_start = datetime.combine(cur, datetime.min.time(), timezone.utc)
                day_end = day_start + timedelta(days=1)
                if day_start < start:
                    day_start = start
                if day_end > end:
                    day_end = end
                remaining_budget = run_limit - len(runs)
                if remaining_budget <= 0:
                    history.truncated = True
                    # Still advance the progress bar for the skipped days
                    # so the UI reaches 100%.
                    self._progress.step(
                        1,
                        label=f"pipeline runs {cur.isoformat()} (cap hit, "
                        f"skipping {remaining_days + 1} days)",
                    )
                    cur += timedelta(days=1)
                    continue
                try:
                    for run in client.iter_pipeline_runs(
                        start=day_start, end=day_end, limit=remaining_budget,
                    ):
                        runs.append(run)
                        if len(runs) >= run_limit:
                            history.truncated = True
                            break
                except Exception as exc:  # noqa: BLE001
                    log.debug("daily fetch[%s] failed: %s", cur, exc)
                    result.errors.append(format_error(
                        f"runs_daily[{cur.isoformat()}]", exc,
                    ))
                self._progress.step(
                    1,
                    label=f"pipeline runs {cur.isoformat()} ("
                    f"{remaining_days} day{'s' if remaining_days != 1 else ''} left)",
                )
                cur += timedelta(days=1)
        else:
            # Single-call pull — ordered RunStart DESC server-side, so when
            # the cap is hit the oldest runs are the ones dropped.
            for run in client.iter_pipeline_runs(start=start, end=end, limit=run_limit):
                runs.append(run)
            if len(runs) >= run_limit:
                history.truncated = True
        history.fetched_run_count = len(runs)

        # When the global cap was hit, low-frequency pipelines may have been
        # starved by a few high-volume ones. For every pipeline that ended
        # up with zero runs, do a best-effort per-pipeline backfill query
        # so the dashboard at least shows their last few executions. We cap
        # this aggressively (10 runs / pipeline, batched 25 at a time) to
        # avoid amplifying load on Synapse.
        if history.truncated:
            seen_pipelines: set[str] = {
                r.get("pipeline_name") for r in runs if r.get("pipeline_name")
            }
            missing = [
                p.name for p in result.pipelines if p.name and p.name not in seen_pipelines
            ]
            if missing:
                per_pipeline_cap = _env_int(
                    "SMA_PIPELINES_RUN_BACKFILL_PER_PIPELINE", default=10, minimum=1,
                )
                batch_size = 25
                backfill_total = 0
                for i in range(0, len(missing), batch_size):
                    chunk = missing[i:i + batch_size]
                    # Per-pipeline cap × chunk-size upper bound for this batch.
                    chunk_cap = per_pipeline_cap * len(chunk)
                    try:
                        for r in client.iter_pipeline_runs(
                            start=start,
                            end=end,
                            limit=chunk_cap,
                            pipeline_names=chunk,
                        ):
                            runs.append(r)
                            backfill_total += 1
                    except Exception as exc:  # noqa: BLE001
                        log.debug("backfill[%s] failed: %s", chunk, exc)
                        result.errors.append(format_error(
                            f"runs_backfill[{','.join(chunk)}]", exc,
                        ))
                if backfill_total:
                    log.info(
                        "Backfilled %d runs across %d previously-missing pipelines",
                        backfill_total, len(missing),
                    )
                    history.fetched_run_count = len(runs)

        # Determine which pipelines could plausibly emit data-movement metrics.
        dm_set = run_stats.pipelines_with_data_movement(
            [a.model_dump() for a in result.activities]
        )

        # Decide which runs to query activity-runs for. Only terminal
        # (succeeded/failed) runs are candidates; in-progress runs would skew
        # metrics. We fetch for *all* pipelines (not just data-movement ones)
        # so the orchestration meter can count actual non-copy activity runs
        # — including ForEach/Until fan-out — instead of relying on the static
        # activity-count multiplier. Within each pipeline we take the most
        # recent ``ar_sample_per_pipeline`` runs (sorted by ``run_end`` desc).
        activity_runs_by_run_id: dict[str, list[dict]] = {}
        if fetch_activity_runs and runs:
            candidates_by_pipeline: dict[str, list[dict]] = defaultdict(list)
            for run in runs:
                pname = run.get("pipeline_name")
                run_id = run.get("run_id")
                status = (run.get("status") or "").lower()
                if not (pname and run_id):
                    continue
                if status not in ("succeeded", "failed"):
                    continue
                candidates_by_pipeline[pname].append(run)

            selected: list[dict] = []
            for pname, plist in candidates_by_pipeline.items():
                # Sort by run_end desc — fall back to run_start if missing.
                plist.sort(
                    key=lambda r: r.get("run_end") or r.get("run_start") or datetime.min,
                    reverse=True,
                )
                if ar_sample_per_pipeline > 0:
                    plist = plist[:ar_sample_per_pipeline]
                selected.extend(plist)

            # Cap total activity-run rows like before so a runaway pipeline
            # cannot pull unbounded data into memory.
            ar_budget = run_limit
            ar_count = 0

            def _fetch_one(run: dict) -> tuple[str, list[dict] | None, Exception | None]:
                run_id = run["run_id"]
                pname = run["pipeline_name"]
                try:
                    ars = list(client.iter_activity_runs(
                        pipeline_name=pname,
                        run_id=run_id,
                        start=start,
                        end=end,
                        limit=ar_budget,
                    ))
                    return run_id, ars, None
                except Exception as exc:  # noqa: BLE001
                    return run_id, None, exc

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(_fetch_one, run) for run in selected]
                for fut in as_completed(futures):
                    run_id, ars, err = fut.result()
                    if err is not None:
                        log.debug("activity_runs[%s] failed: %s", run_id, err)
                        result.errors.append(format_error(
                            f"activity_runs[{run_id}]", err,
                        ))
                        continue
                    if not ars:
                        continue
                    if ar_count >= ar_budget:
                        history.truncated = True
                        continue
                    activity_runs_by_run_id[run_id] = ars
                    ar_count += len(ars)
        history.fetched_activity_run_count = sum(
            len(v) for v in activity_runs_by_run_id.values()
        )

        history.by_pipeline = run_stats.aggregate_runs(
            pipeline_names=[p.name for p in result.pipelines],
            pipelines_with_data_movement=dm_set,
            runs=runs,
            activity_runs_by_run_id=activity_runs_by_run_id,
            non_copy_activity_counts=run_stats.non_copy_activity_counts(
                [a.model_dump() for a in result.activities]
            ),
            dataflow_cores_by_pipeline_activity=run_stats.dataflow_cores_by_pipeline_activity(
                [a.model_dump() for a in result.activities]
            ),
            now=end,
        )

        # Per-UTC-day rollup of run outcomes for the daily bar chart.
        # Bucket by the run's end timestamp (falling back to run_start so
        # in-progress / cancelled runs still appear).
        daily: dict[str, dict[str, int]] = {}
        for run in runs:
            ts = run.get("run_end") or run.get("run_start")
            if ts is None:
                continue
            try:
                day = ts.astimezone(timezone.utc).date().isoformat()
            except Exception:  # noqa: BLE001
                continue
            status = (run.get("status") or "").lower()
            bucket = daily.setdefault(day, {"succeeded": 0, "failed": 0, "other": 0})
            if status == "succeeded":
                bucket["succeeded"] += 1
            elif status == "failed":
                bucket["failed"] += 1
            else:
                bucket["other"] += 1

        # When the global run-fetch was truncated, the bucketed counts above
        # only cover the trailing slice of the window (e.g. last 1-2 days of
        # a 28-day window). Fall back to per-day, per-status count queries
        # against Synapse Monitor to fill in the earlier days without
        # buffering the rows. Status buckets are typically small (especially
        # Failed / Cancelled) so this is cheap. Can be disabled with
        # SMA_PIPELINES_DAILY_BACKFILL=0.
        if history.truncated and _env_flag("SMA_PIPELINES_DAILY_BACKFILL", default=True):
            try:
                self._backfill_daily_status(client, start, end, daily)
            except Exception as exc:  # noqa: BLE001
                log.debug("daily-status backfill failed: %s", exc)
                result.errors.append(format_error("daily_status_backfill", exc))
        history.daily_status = daily

        # Per-UTC-hour rollup of the trailing 24 hours. Runs are fetched
        # newest-first (RunStart DESC) so even when the global pull is
        # truncated, the last 24h slice is always present in ``runs``
        # and we can bucket directly without a backfill query.
        end_utc = end.astimezone(timezone.utc)
        # Anchor the 24-bucket window to the top of ``end``'s hour so the
        # right-most bin always represents the most recent (possibly
        # in-progress) hour.
        last_hour = end_utc.replace(minute=0, second=0, microsecond=0)
        first_hour = last_hour - timedelta(hours=23)
        hourly: dict[str, dict[str, int]] = {}
        # Pre-seed every hour so the chart always renders a continuous
        # x-axis even when activity is sparse.
        cur_hour = first_hour
        while cur_hour <= last_hour:
            hourly[cur_hour.isoformat().replace("+00:00", "Z")] = {
                "succeeded": 0,
                "failed": 0,
                "other": 0,
            }
            cur_hour += timedelta(hours=1)
        for run in runs:
            ts = run.get("run_end") or run.get("run_start")
            if ts is None:
                continue
            try:
                ts_utc = ts.astimezone(timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            if ts_utc < first_hour or ts_utc > last_hour + timedelta(hours=1):
                continue
            bin_start = ts_utc.replace(minute=0, second=0, microsecond=0)
            key = bin_start.isoformat().replace("+00:00", "Z")
            bucket = hourly.get(key)
            if bucket is None:
                continue
            status = (run.get("status") or "").lower()
            if status == "succeeded":
                bucket["succeeded"] += 1
            elif status == "failed":
                bucket["failed"] += 1
            else:
                bucket["other"] += 1
        history.hourly_status = hourly
        return history

    def _backfill_daily_status(
        self,
        client: Any,
        start: datetime,
        end: datetime,
        daily: dict[str, dict[str, int]],
    ) -> None:
        """Fill missing per-day counts using server-side per-status queries.

        Strategy: for each whole UTC day in [start, end), and for each of
        Succeeded / Failed / Cancelled, issue a single ``queryByWorkspace``
        scoped to that day-window and that status, and count the rows
        without keeping them. We only touch days not already populated by
        the in-memory rollup so a freshly-cached day is never overwritten.
        """
        # Whole UTC days inside the window.
        first_day = start.astimezone(timezone.utc).date()
        last_day = end.astimezone(timezone.utc).date()
        statuses = (
            ("Succeeded", "succeeded"),
            ("Failed", "failed"),
            ("Cancelled", "other"),
        )
        # Pre-compute which days need a query so we can publish an accurate
        # remaining-days count to the progress reporter.
        missing_days: list[_date] = []
        cur = first_day
        while cur <= last_day:
            existing = daily.get(cur.isoformat())
            if not existing or sum(existing.values()) == 0:
                missing_days.append(cur)
            cur += timedelta(days=1)
        if not missing_days:
            return
        self._progress.add_total(len(missing_days))
        self._progress.message(
            f"backfilling daily run counts ({len(missing_days)} days)"
        )
        backfilled_days = 0
        for idx, cur in enumerate(missing_days, start=1):
            remaining = len(missing_days) - idx
            day_start = datetime.combine(cur, datetime.min.time(), timezone.utc)
            day_end = day_start + timedelta(days=1)
            if day_end > end:
                day_end = end
            bucket = daily.setdefault(
                cur.isoformat(), {"succeeded": 0, "failed": 0, "other": 0},
            )
            touched = False
            for api_name, bucket_key in statuses:
                try:
                    n = client.count_pipeline_runs(
                        start=day_start, end=day_end, status=api_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug(
                        "count_pipeline_runs[%s, %s] failed: %s",
                        cur, api_name, exc,
                    )
                    continue
                if n:
                    bucket[bucket_key] += n
                    touched = True
            if touched:
                backfilled_days += 1
            self._progress.step(
                1,
                label=f"daily backfill {cur.isoformat()} ({remaining} day"
                f"{'s' if remaining != 1 else ''} left)",
            )
        if backfilled_days:
            log.info("Daily-status backfill populated %d days", backfilled_days)


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, *, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return max(minimum, n)
