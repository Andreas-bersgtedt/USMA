"""Orchestrator for the bigquery_workloads module."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from .collector import BigQueryWorkloadsCollector
from .models import BigQueryWorkloadsAnalysis, QueryFeatureCount
from .query_features import detect_features, summarise_features
from .run_stats import (
    SLOT_TO_CU_CAVEAT,
    aggregate_jobs_by_day,
    aggregate_jobs_by_dimension,
    aggregate_jobs_by_window,
    aggregate_table_usage,
    populate_slot_hours,
)


log = logging.getLogger(__name__)


class BigQueryWorkloadsAnalyzer:
    """Enumerate datasets / tables / routines / scheduled queries / jobs."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
        collector: BigQueryWorkloadsCollector | None = None,
        project_id: str | None = None,
        project_number: str | None = None,
        location: str | None = None,
        job_lookback_days: int = 180,
        collect_jobs: bool = True,
        jobs_source: str = "information_schema",
        capture_query_text: bool = False,
    ) -> None:
        self._cfg = cfg
        self._progress = progress or NullProgress()
        self._collector = collector
        self._project_id = project_id
        self._project_number = project_number
        self._location = location
        self._job_lookback_days = job_lookback_days
        self._collect_jobs = collect_jobs
        # ``information_schema`` (default) reads from
        # ``INFORMATION_SCHEMA.JOBS_BY_PROJECT`` — 180-day retention, no
        # Data Access audit-log requirement. ``audit_log`` keeps the
        # original Cloud Logging path for orgs that ship logs to a
        # central project and want to mine them there. ``both`` runs
        # INFORMATION_SCHEMA first and falls back to audit logs only if
        # zero rows came back.
        self._jobs_source = jobs_source
        self._capture_query_text = capture_query_text

    # ------------------------------------------------------------------ factory

    @classmethod
    def for_descriptor(
        cls,
        cfg: AppConfig,
        descriptor: Any,
        creds: Any,
        *,
        progress: ProgressReporter | None = None,
    ) -> "BigQueryWorkloadsAnalyzer":
        """Build an analyzer wired to live BigQuery + Cloud Logging clients.

        Raises ``ValueError`` when ``descriptor`` is not a BigQuery
        source — this module is BigQuery-only.
        """
        from ...sources import SourceType
        from ...sources.bigquery.provider import BigQueryProvider

        if getattr(descriptor, "type", None) is not SourceType.BIGQUERY:
            raise ValueError(
                "BigQueryWorkloadsAnalyzer.for_descriptor requires a "
                "BigQuery SourceDescriptor",
            )
        bundle = BigQueryProvider().make_clients(descriptor, creds)
        bq_client, log_client, dts_client = _build_clients(
            bundle.credentials, bundle.project_id,
        )
        # Allow ops to override the jobs source via env without code change.
        # Accepted values: ``information_schema`` (default), ``audit_log``,
        # ``both``. Anything else falls back to the default + logs.
        jobs_source_env = (os.environ.get("SMA_GCP_JOBS_SOURCE") or "").strip().lower()
        if jobs_source_env in ("information_schema", "audit_log", "both"):
            jobs_source = jobs_source_env
        else:
            if jobs_source_env:
                log.warning(
                    "ignoring SMA_GCP_JOBS_SOURCE=%r — expected one of "
                    "information_schema | audit_log | both",
                    jobs_source_env,
                )
            jobs_source = "information_schema"
        # Lookback window. JOBS_BY_PROJECT retains 180 days; we default to
        # the full retention so daily timeseries are usable. Ops can dial
        # it down for very busy projects via env var.
        try:
            lookback = int(os.environ.get("SMA_BQ_JOB_LOOKBACK_DAYS") or "180")
            if lookback < 1:
                lookback = 1
            if lookback > 180:
                lookback = 180
        except ValueError:
            lookback = 180
        capture_query_text = (
            os.environ.get("SMA_BQ_CAPTURE_QUERY_TEXT", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        return cls(
            cfg,
            progress=progress,
            collector=BigQueryWorkloadsCollector(
                bq_client,
                log_client,
                project_id=bundle.project_id,
                dts_client=dts_client,
            ),
            project_id=bundle.project_id,
            project_number=bundle.project_number,
            location=bundle.location,
            jobs_source=jobs_source,
            job_lookback_days=lookback,
            capture_query_text=capture_query_text,
        )

    # ------------------------------------------------------------------ run

    def run(self) -> BigQueryWorkloadsAnalysis:
        if self._collector is None or self._project_id is None:
            raise RuntimeError(
                "BigQueryWorkloadsAnalyzer requires a collector — call "
                "for_descriptor(cfg, descriptor, creds) or pass collector=...",
            )

        result = BigQueryWorkloadsAnalysis(
            project_id=self._project_id,
            project_number=self._project_number,
            location=self._location,
            generated_at=datetime.now(timezone.utc),
        )

        steps = 3 + (1 if self._collect_jobs else 0) + 1  # datasets, tables, routines, [jobs], scheduled
        self._progress.start(steps, label="enumerating BigQuery workloads")

        # ----- datasets -------------------------------------------------
        try:
            result.datasets = list(self._collector.iter_datasets())
        except Exception as exc:  # noqa: BLE001
            log.warning("dataset enumeration failed: %s", exc)
            result.errors.append(format_error("datasets", exc))
        self._progress.step(label="datasets")

        # ----- tables ---------------------------------------------------
        all_tables = []
        for ds in result.datasets:
            try:
                tables = list(self._collector.iter_tables(ds.dataset_id))
            except Exception as exc:  # noqa: BLE001
                log.warning("table enumeration failed for %s: %s", ds.dataset_id, exc)
                result.errors.append(format_error(f"tables[{ds.dataset_id}]", exc))
                continue
            all_tables.extend(tables)
            ds.table_count = len(tables)
        result.tables = all_tables
        self._progress.step(label="tables")

        # ----- routines -------------------------------------------------
        all_routines = []
        for ds in result.datasets:
            try:
                routines = list(self._collector.iter_routines(ds.dataset_id))
            except Exception as exc:  # noqa: BLE001
                log.warning("routine enumeration failed for %s: %s", ds.dataset_id, exc)
                result.errors.append(format_error(f"routines[{ds.dataset_id}]", exc))
                continue
            all_routines.extend(routines)
        result.routines = all_routines
        self._progress.step(label="routines")

        # ----- scheduled queries ---------------------------------------
        try:
            result.scheduled_queries = list(self._collector.iter_scheduled_queries())
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduled-query enumeration failed: %s", exc)
            result.errors.append(format_error("scheduled_queries", exc))
        if self._collector._dts is None:  # noqa: SLF001 — internal flag, intentional
            result.caveats.append(
                "Scheduled-query enumeration skipped: google-cloud-bigquery-datatransfer "
                "client not provided (install the [bigquery] extra and pass a "
                "DataTransferServiceClient to BigQueryWorkloadsCollector to enable).",
            )
        self._progress.step(label="scheduled_queries")

        # ----- jobs (INFORMATION_SCHEMA preferred, audit-log fallback) ---
        if self._collect_jobs:
            jobs: list = []
            sources_tried: list[str] = []
            wants_info_schema = self._jobs_source in ("information_schema", "both")
            wants_audit_log = self._jobs_source in ("audit_log", "both")

            if wants_info_schema:
                sources_tried.append("information_schema")
                try:
                    jobs = list(
                        self._collector.iter_jobs_information_schema(
                            lookback_days=self._job_lookback_days,
                            region=self._location,
                            capture_query_text=self._capture_query_text,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("INFORMATION_SCHEMA job enumeration failed: %s", exc)
                    result.errors.append(format_error("jobs.information_schema", exc))

            if not jobs and wants_audit_log:
                sources_tried.append("audit_log")
                try:
                    jobs = list(
                        self._collector.iter_jobs(lookback_days=self._job_lookback_days),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("audit-log job enumeration failed: %s", exc)
                    result.errors.append(format_error("jobs.audit_log", exc))

            result.jobs = jobs

            if not jobs:
                # INFORMATION_SCHEMA returning empty is the canonical
                # signal that the project is genuinely idle — it does
                # not depend on Data Access audit logs and retains 180
                # days. Tailor the caveat by which sources we tried so
                # users aren't pointed at the wrong knob.
                if "information_schema" in sources_tried and "audit_log" not in sources_tried:
                    result.caveats.append(
                        "0 BigQuery jobs found in "
                        f"INFORMATION_SCHEMA.JOBS_BY_PROJECT for the last "
                        f"{self._job_lookback_days} days. This is the "
                        "canonical source (180-day retention, no audit-log "
                        "dependency) so the project is likely genuinely "
                        "idle. If you expect activity, verify the signed-in "
                        "principal has 'bigquery.jobs.listAll' on the "
                        "project (roles/bigquery.resourceViewer) and that "
                        "the BigQuery region matches the project's data "
                        f"location ({(self._location or 'US').upper()})."
                    )
                else:
                    result.caveats.append(
                        "0 BigQuery jobs found in the audit-log lookback "
                        f"window ({self._job_lookback_days} days). If the "
                        "project has user activity, the most likely cause "
                        "is that BigQuery Data Access audit logs are "
                        "disabled (off by default). Enable them at IAM & "
                        "Admin -> Audit Logs, select 'Cloud BigQuery', and "
                        "check both 'Data Read' and 'Data Write'. The "
                        "signed-in principal also needs "
                        "'logging.logEntries.list' on the project."
                    )
            self._progress.step(label="jobs")

        # ----- slot-hour → CU-hour conversion + rolling windows (Slice 5-C) ---
        if result.jobs:
            applied_any = False
            for j in result.jobs:
                if j.total_slot_ms is not None:
                    populate_slot_hours(j)
                    applied_any = True
            if applied_any and SLOT_TO_CU_CAVEAT not in result.caveats:
                result.caveats.append(SLOT_TO_CU_CAVEAT)
            try:
                result.job_window_stats = aggregate_jobs_by_window(result.jobs)
            except Exception as exc:  # noqa: BLE001
                log.warning("job window aggregation failed: %s", exc)
                result.errors.append(format_error("job_window_stats", exc))

            # ----- table usage rollup ("most used tables", Synapse SQL Surface analogue) --
            try:
                result.table_usage = aggregate_table_usage(
                    result.jobs, catalog=result.tables,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("table usage aggregation failed: %s", exc)
                result.errors.append(format_error("table_usage", exc))

            # ----- daily timeseries (dedicated-pool daily-execution analogue) ---
            try:
                result.daily_stats = aggregate_jobs_by_day(result.jobs)
            except Exception as exc:  # noqa: BLE001
                log.warning("daily stats aggregation failed: %s", exc)
                result.errors.append(format_error("daily_stats", exc))

            # ----- per-dimension breakdowns (user / statement_type / job_type / reservation / edition / priority) ---
            try:
                breakdowns: list = []
                breakdowns.extend(aggregate_jobs_by_dimension(
                    result.jobs, dimension="user", key=lambda j: j.user_email,
                ))
                breakdowns.extend(aggregate_jobs_by_dimension(
                    result.jobs, dimension="statement_type", key=lambda j: j.statement_type,
                ))
                breakdowns.extend(aggregate_jobs_by_dimension(
                    result.jobs, dimension="job_type", key=lambda j: j.job_type,
                ))
                breakdowns.extend(aggregate_jobs_by_dimension(
                    result.jobs, dimension="reservation", key=lambda j: j.reservation_id,
                ))
                breakdowns.extend(aggregate_jobs_by_dimension(
                    result.jobs, dimension="edition", key=lambda j: j.edition,
                ))
                breakdowns.extend(aggregate_jobs_by_dimension(
                    result.jobs, dimension="priority", key=lambda j: j.priority,
                ))
                result.breakdowns = breakdowns
            except Exception as exc:  # noqa: BLE001
                log.warning("job breakdown aggregation failed: %s", exc)
                result.errors.append(format_error("breakdowns", exc))

            # ----- sqlglot query-text feature detection ----------------
            # Only meaningful when query_text capture was enabled (else
            # every job's text is None). Detect per-job features inline
            # and stamp the result on the job; the aggregate goes on the
            # report so consumers don't have to re-walk every job.
            try:
                any_text = False
                for j in result.jobs:
                    if j.query_text:
                        any_text = True
                        j.features = detect_features(j.query_text)
                if any_text:
                    counts = summarise_features(j.features for j in result.jobs)
                    result.query_features = [
                        QueryFeatureCount(feature=slug, job_count=n)
                        for slug, n in counts.items()
                    ]
            except Exception as exc:  # noqa: BLE001
                log.warning("query feature detection failed: %s", exc)
                result.errors.append(format_error("query_features", exc))

        # ----- roll-ups -------------------------------------------------
        result.dataset_count = len(result.datasets)
        result.table_count = sum(1 for t in result.tables if t.table_type == "TABLE")
        result.view_count = sum(1 for t in result.tables if t.table_type == "VIEW")
        result.materialized_view_count = sum(
            1 for t in result.tables if t.table_type == "MATERIALIZED_VIEW"
        )
        result.external_table_count = sum(
            1 for t in result.tables if t.table_type == "EXTERNAL"
        )
        result.routine_count = len(result.routines)
        result.scheduled_query_count = len(result.scheduled_queries)
        result.job_count = len(result.jobs)
        result.unsupported_table_count = sum(
            1 for t in result.tables if t.support == "unsupported"
        )
        result.partial_table_count = sum(
            1 for t in result.tables if t.support == "partial"
        )
        return result


def _build_clients(credentials: Any, project_id: str) -> tuple[Any, Any, Any]:
    """Build ``(bq_client, log_client, dts_client)`` from ADC credentials.

    The DTS client is ``None`` when ``google-cloud-bigquery-datatransfer``
    is not installed; the analyzer surfaces that as a caveat rather
    than failing the run.
    """
    from google.cloud import bigquery as bq, logging_v2

    bq_client = bq.Client(project=project_id, credentials=credentials)
    log_client = logging_v2.Client(project=project_id, credentials=credentials)
    dts_client: Any = None
    try:
        from google.cloud import bigquery_datatransfer_v1
    except ImportError:
        dts_client = None
    else:
        try:
            dts_client = bigquery_datatransfer_v1.DataTransferServiceClient(
                credentials=credentials,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("DataTransferServiceClient construction failed: %s", exc)
            dts_client = None
    return bq_client, log_client, dts_client


__all__ = ["BigQueryWorkloadsAnalyzer"]
