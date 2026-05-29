"""Orchestrator for the snowflake_workloads module."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from .collector import SnowflakeWorkloadsCollector
from .models import SnowflakeTableUsage, SnowflakeWorkloadsAnalysis
from .run_stats import (
    CREDIT_TO_CU_CAVEAT,
    aggregate_jobs_by_day,
    aggregate_jobs_by_dimension,
    aggregate_jobs_by_window,
    aggregate_warehouse_metering_by_window,
    populate_job_credits,
    populate_warehouse_size_metrics,
    summarize_code_objects,
)


log = logging.getLogger(__name__)


class SnowflakeWorkloadsAnalyzer:
    """Enumerate Snowflake warehouses / databases / schemas / objects / jobs."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
        collector: SnowflakeWorkloadsCollector | None = None,
        account: str | None = None,
        platform: str | None = None,
        region: str | None = None,
        edition: str | None = None,
        job_lookback_days: int = 28,
        collect_jobs: bool = True,
        job_limit: int = 50_000,
    ) -> None:
        self._cfg = cfg
        self._progress = progress or NullProgress()
        self._collector = collector
        self._account = account
        self._platform = platform
        self._region = region
        self._edition = edition
        self._job_lookback_days = job_lookback_days
        self._collect_jobs = collect_jobs
        self._job_limit = job_limit

    # ------------------------------------------------------------------ factory

    @classmethod
    def for_descriptor(
        cls,
        cfg: AppConfig,
        descriptor: Any,
        creds: Any,
        *,
        progress: ProgressReporter | None = None,
    ) -> "SnowflakeWorkloadsAnalyzer":
        """Build an analyzer wired to a live Snowflake connection."""
        from ...sources import SourceType
        from ...sources.snowflake.provider import SnowflakeProvider

        if getattr(descriptor, "type", None) is not SourceType.SNOWFLAKE:
            raise ValueError(
                "SnowflakeWorkloadsAnalyzer.for_descriptor requires a "
                "Snowflake SourceDescriptor",
            )
        bundle = SnowflakeProvider().make_clients(descriptor, creds)
        extras = dict(getattr(descriptor, "extras", {}) or {})
        try:
            lookback = int(os.environ.get("SMA_SNOWFLAKE_JOB_LOOKBACK_DAYS") or "28")
            if lookback < 1:
                lookback = 1
            if lookback > 365:
                lookback = 365
        except ValueError:
            lookback = 28
        try:
            job_limit = int(os.environ.get("SMA_SNOWFLAKE_JOB_LIMIT") or "50000")
            if job_limit < 1:
                job_limit = 1
        except ValueError:
            job_limit = 50_000
        return cls(
            cfg,
            progress=progress,
            collector=SnowflakeWorkloadsCollector(
                bundle.connect, account=bundle.account,
            ),
            account=bundle.account,
            platform=bundle.platform or extras.get("platform"),
            region=bundle.region or extras.get("region"),
            edition=extras.get("edition"),
            job_lookback_days=lookback,
            job_limit=job_limit,
        )

    # ------------------------------------------------------------------ run

    def run(self) -> SnowflakeWorkloadsAnalysis:
        if self._collector is None or self._account is None:
            raise RuntimeError(
                "SnowflakeWorkloadsAnalyzer requires a collector — call "
                "for_descriptor(cfg, descriptor, creds) or pass collector=...",
            )

        result = SnowflakeWorkloadsAnalysis(
            account=self._account,
            platform=self._platform,
            region=self._region,
            edition=self._edition,
            generated_at=datetime.now(timezone.utc),
        )

        # warehouses, databases, schemas, per-schema objects, jobs
        steps = 3 + (1 if self._collect_jobs else 0)
        self._progress.start(steps, label="enumerating Snowflake workloads")

        # ----- warehouses ----------------------------------------------
        try:
            result.warehouses = list(self._collector.iter_warehouses())
        except Exception as exc:  # noqa: BLE001
            log.warning("warehouse enumeration failed: %s", exc)
            result.errors.append(format_error("warehouses", exc))
        # Annotate each warehouse with its size-class credit / vCore proxy.
        for wh in result.warehouses:
            populate_warehouse_size_metrics(wh)
        self._progress.step(label="warehouses")

        # ----- databases / schemas -------------------------------------
        try:
            result.databases = list(self._collector.iter_databases())
        except Exception as exc:  # noqa: BLE001
            log.warning("database enumeration failed: %s", exc)
            result.errors.append(format_error("databases", exc))

        skip_system = (
            os.environ.get("SMA_SNOWFLAKE_INCLUDE_SYSTEM_DBS", "").strip().lower()
            not in ("1", "true", "yes", "on")
        )
        system_dbs = {"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"}
        all_schemas = []
        for db in result.databases:
            if skip_system and db.name.upper() in system_dbs:
                continue
            try:
                schemas = list(self._collector.iter_schemas(db.name))
            except Exception as exc:  # noqa: BLE001
                log.warning("schema enumeration failed for %s: %s", db.name, exc)
                result.errors.append(format_error(f"schemas[{db.name}]", exc))
                continue
            # INFORMATION_SCHEMA is a system schema per database — never useful for migration.
            schemas = [s for s in schemas if s.name.upper() != "INFORMATION_SCHEMA"]
            all_schemas.extend(schemas)
            db.schema_count = len(schemas)
        result.schemas = all_schemas
        self._progress.step(label="databases+schemas")

        # ----- per-schema objects --------------------------------------
        for sc in result.schemas:
            self._collect_schema_objects(result, sc.database_name, sc.name)
        self._progress.step(label="objects")

        # ----- ACCOUNT_USAGE fallback ----------------------------------
        # If SHOW returned no user databases (or returned databases but
        # the role couldn't enumerate any tables/views — both signs of
        # missing per-database USAGE), fall back to the ACCOUNT_USAGE
        # inventory views. They are readable with the single
        # ``IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`` grant and cover
        # databases/schemas/tables/views/functions/procedures —
        # everything except per-schema objects (stages/streams/tasks/
        # pipes) which Snowflake does not expose at the account level.
        self._maybe_use_account_usage_fallback(result)

        # ----- jobs ----------------------------------------------------
        if self._collect_jobs:
            try:
                result.jobs = list(
                    self._collector.iter_jobs(
                        lookback_days=self._job_lookback_days,
                        limit=self._job_limit,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("query-history collection failed: %s", exc)
                result.errors.append(format_error("jobs", exc))
            self._progress.step(label="jobs")

        # ----- credit -> vCore -> CU rollup (Slice 7-D) ----------------
        if result.jobs:
            applied_any = False
            for j in result.jobs:
                before = j.est_credits
                populate_job_credits(j)
                if j.est_credits is not None and j.est_credits != before:
                    applied_any = True
            if applied_any and CREDIT_TO_CU_CAVEAT not in result.caveats:
                result.caveats.append(CREDIT_TO_CU_CAVEAT)
            try:
                result.job_window_stats = aggregate_jobs_by_window(result.jobs)
            except Exception as exc:  # noqa: BLE001
                log.warning("job window aggregation failed: %s", exc)
                result.errors.append(format_error("job_window_stats", exc))
            try:
                result.warehouse_window_stats = (
                    aggregate_warehouse_metering_by_window(result.jobs)
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("warehouse window aggregation failed: %s", exc)
                result.errors.append(
                    format_error("warehouse_window_stats", exc),
                )
            try:
                result.daily_stats = aggregate_jobs_by_day(result.jobs)
            except Exception as exc:  # noqa: BLE001
                log.warning("daily job aggregation failed: %s", exc)
                result.errors.append(format_error("daily_stats", exc))
            try:
                result.breakdowns = self._compute_breakdowns(result.jobs)
            except Exception as exc:  # noqa: BLE001
                log.warning("job breakdown aggregation failed: %s", exc)
                result.errors.append(format_error("breakdowns", exc))
            self._collect_table_usage(result)

        # ----- roll-ups ------------------------------------------------
        self._finalise(result)
        self._emit_privilege_caveat(result)
        return result

    # ------------------------------------------------------------------ helpers

    def _collect_schema_objects(
        self, result: SnowflakeWorkloadsAnalysis, database: str, schema: str,
    ) -> None:
        c = self._collector
        assert c is not None
        for label, fn, bucket in (
            ("tables", c.iter_tables, result.tables),
            ("views", c.iter_views, result.tables),
            ("functions", c.iter_functions, result.routines),
            ("procedures", c.iter_procedures, result.routines),
            ("stages", c.iter_stages, result.stages),
            ("streams", c.iter_streams, result.streams),
            ("tasks", c.iter_tasks, result.tasks),
            ("pipes", c.iter_pipes, result.pipes),
        ):
            try:
                items = list(fn(database, schema))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "%s enumeration failed for %s.%s: %s",
                    label, database, schema, exc,
                )
                result.errors.append(
                    format_error(f"{label}[{database}.{schema}]", exc),
                )
                continue
            bucket.extend(items)

    _ACCOUNT_USAGE_LATENCY_CAVEAT = (
        "Inventory (databases / schemas / tables / views / functions / "
        "procedures) was pulled from SNOWFLAKE.ACCOUNT_USAGE because the "
        "analyzer role lacks per-database USAGE. ACCOUNT_USAGE has a "
        "~45-180 minute freshness latency, so very recently created or "
        "dropped objects may be missing or stale. Per-schema objects "
        "(stages, streams, tasks, pipes) cannot be enumerated this way "
        "and are absent from this report — grant USAGE ON DATABASE <db> "
        "to the analyzer role for a fully fresh, complete catalog."
    )

    def _maybe_use_account_usage_fallback(
        self, result: SnowflakeWorkloadsAnalysis,
    ) -> bool:
        """Backfill databases/schemas/tables/views/functions/procedures
        from ACCOUNT_USAGE when the SHOW pass came back empty due to
        missing per-database USAGE. Returns True when the fallback was
        applied so the caller can adjust progress / caveats.
        """
        c = self._collector
        if c is None:
            return False
        # Trigger only when the role clearly can't see user content.
        # Allow the env-var override for tests / forced runs.
        forced = os.environ.get(
            "SMA_SNOWFLAKE_FORCE_ACCOUNT_USAGE_INVENTORY", "",
        ).strip().lower() in ("1", "true", "yes", "on")
        no_databases = not result.databases
        no_tables = bool(result.databases) and not result.tables
        if not (forced or no_databases or no_tables):
            return False
        # Best-effort: any single query failure for a family is logged
        # and skipped; we still want the rest of the inventory.
        if no_databases or forced:
            try:
                result.databases = list(c.iter_databases_from_account_usage())
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "ACCOUNT_USAGE database fallback failed: %s", exc,
                )
                result.errors.append(
                    format_error("databases_account_usage", exc),
                )
        try:
            schemas = list(c.iter_schemas_from_account_usage())
        except Exception as exc:  # noqa: BLE001
            log.warning("ACCOUNT_USAGE schema fallback failed: %s", exc)
            result.errors.append(format_error("schemas_account_usage", exc))
            schemas = []
        if schemas:
            # Replace any partial schema list captured by SHOW (likely
            # empty); recompute db.schema_count from the fresh set.
            result.schemas = schemas
            counts: dict[str, int] = {}
            for s in schemas:
                counts[s.database_name] = counts.get(s.database_name, 0) + 1
            for db in result.databases:
                db.schema_count = counts.get(db.name, 0)
        try:
            tables = list(c.iter_tables_from_account_usage())
        except Exception as exc:  # noqa: BLE001
            log.warning("ACCOUNT_USAGE tables fallback failed: %s", exc)
            result.errors.append(format_error("tables_account_usage", exc))
            tables = []
        try:
            views = list(c.iter_views_from_account_usage())
        except Exception as exc:  # noqa: BLE001
            log.warning("ACCOUNT_USAGE views fallback failed: %s", exc)
            result.errors.append(format_error("views_account_usage", exc))
            views = []
        if tables or views:
            result.tables = tables + views
        try:
            funcs = list(c.iter_functions_from_account_usage())
        except Exception as exc:  # noqa: BLE001
            log.warning("ACCOUNT_USAGE functions fallback failed: %s", exc)
            result.errors.append(format_error("functions_account_usage", exc))
            funcs = []
        try:
            procs = list(c.iter_procedures_from_account_usage())
        except Exception as exc:  # noqa: BLE001
            log.warning("ACCOUNT_USAGE procedures fallback failed: %s", exc)
            result.errors.append(
                format_error("procedures_account_usage", exc),
            )
            procs = []
        if funcs or procs:
            result.routines = funcs + procs
        if self._ACCOUNT_USAGE_LATENCY_CAVEAT not in result.caveats:
            result.caveats.append(self._ACCOUNT_USAGE_LATENCY_CAVEAT)
        return True

    @staticmethod
    def _compute_breakdowns(jobs):  # type: ignore[no-untyped-def]
        out = []
        out.extend(aggregate_jobs_by_dimension(
            jobs, dimension="query_type", key=lambda j: j.query_type,
        ))
        out.extend(aggregate_jobs_by_dimension(
            jobs, dimension="user", key=lambda j: j.user_name,
        ))
        out.extend(aggregate_jobs_by_dimension(
            jobs, dimension="warehouse", key=lambda j: j.warehouse_name,
        ))
        out.extend(aggregate_jobs_by_dimension(
            jobs, dimension="role", key=lambda j: j.role_name,
        ))
        out.extend(aggregate_jobs_by_dimension(
            jobs, dimension="status", key=lambda j: j.execution_status,
        ))
        return out

    _ACCESS_HISTORY_FALLBACK_CAVEAT = (
        "Top-active tables were sourced from QUERY_HISTORY's database / "
        "schema columns rather than ACCESS_HISTORY (which requires "
        "Snowflake Enterprise Edition or higher). Counts are at "
        "DB.SCHEMA grain — individual table names are not available. "
        "Upgrade to Enterprise or grant the role IMPORTED PRIVILEGES "
        "on the SNOWFLAKE database to surface per-table usage."
    )

    def _collect_table_usage(self, result: SnowflakeWorkloadsAnalysis) -> None:
        """Populate ``result.table_usage`` from ACCESS_HISTORY or fallback."""
        c = self._collector
        if c is None or not result.jobs:
            return
        try:
            usage = list(c.iter_table_usage_from_access_history(
                lookback_days=self._job_lookback_days,
                limit=500,
            ))
        except Exception as exc:  # noqa: BLE001
            log.info(
                "ACCESS_HISTORY unavailable (%s); falling back to "
                "QUERY_HISTORY DB.SCHEMA grouping",
                exc,
            )
            usage = []
        if not usage:
            # Fallback: group by (database, schema) from the QUERY_HISTORY
            # rows we already have. Gives a coarser "where is the
            # workload concentrated?" answer when ACCESS_HISTORY is gated
            # by edition or grant.
            bucket: dict[tuple[str, str], dict[str, Any]] = {}
            for j in result.jobs:
                db = j.database_name or "<unknown>"
                sc = j.schema_name or "<unknown>"
                if db == "<unknown>" and sc == "<unknown>":
                    continue
                key = (db, sc)
                b = bucket.setdefault(key, {
                    "usage_count": 0,
                    "bytes": 0,
                    "rows": 0,
                    "exec_ms": 0,
                    "last_seen": None,
                })
                b["usage_count"] += 1
                if j.bytes_scanned:
                    b["bytes"] += int(j.bytes_scanned)
                if j.rows_produced:
                    b["rows"] += int(j.rows_produced)
                if j.execution_ms:
                    b["exec_ms"] += int(j.execution_ms)
                if j.start_time is not None:
                    prev = b["last_seen"]
                    if prev is None or j.start_time > prev:
                        b["last_seen"] = j.start_time
            usage = [
                SnowflakeTableUsage(
                    full_name=f"{db}.{sc}.*",
                    database_name=db,
                    schema_name=sc,
                    object_name="*",
                    object_domain="SCHEMA",
                    usage_count=b["usage_count"],
                    total_bytes_scanned=b["bytes"],
                    total_rows_produced=b["rows"],
                    total_execution_ms=b["exec_ms"],
                    last_seen=b["last_seen"],
                    in_catalog=False,
                    source="query_history_fallback",
                )
                for (db, sc), b in bucket.items()
            ]
            usage.sort(
                key=lambda u: (-u.usage_count, -u.total_bytes_scanned, u.full_name),
            )
            if usage and self._ACCESS_HISTORY_FALLBACK_CAVEAT not in result.caveats:
                result.caveats.append(self._ACCESS_HISTORY_FALLBACK_CAVEAT)
        # Stamp catalog matches + support tier for ACCESS_HISTORY rows.
        catalog = {t.full_name: t for t in result.tables}
        for u in usage:
            t = catalog.get(u.full_name)
            if t is not None:
                u.in_catalog = True
                u.table_support = t.support
        result.table_usage = usage[:200]

    @staticmethod
    def _finalise(result: SnowflakeWorkloadsAnalysis) -> None:
        result.warehouse_count = len(result.warehouses)
        result.database_count = len(result.databases)
        result.schema_count = len(result.schemas)
        result.table_count = sum(1 for t in result.tables if t.kind == "TABLE")
        result.view_count = sum(1 for t in result.tables if t.kind == "VIEW")
        result.materialized_view_count = sum(
            1 for t in result.tables if t.kind == "MATERIALIZED_VIEW"
        )
        result.external_table_count = sum(
            1 for t in result.tables if t.kind == "EXTERNAL_TABLE"
        )
        result.dynamic_table_count = sum(
            1 for t in result.tables if t.kind == "DYNAMIC_TABLE"
        )
        result.iceberg_table_count = sum(
            1 for t in result.tables if t.kind == "ICEBERG_TABLE"
        )
        result.routine_count = len(result.routines)
        result.stage_count = len(result.stages)
        result.stream_count = len(result.streams)
        result.task_count = len(result.tasks)
        result.pipe_count = len(result.pipes)
        result.job_count = len(result.jobs)
        # Count partial / unsupported across every object family carrying ``support``.
        partial = unsupported = 0
        for bucket in (
            result.tables, result.routines, result.stages,
            result.streams, result.tasks, result.pipes,
        ):
            for obj in bucket:
                sup = getattr(obj, "support", "unknown")
                if sup == "partial":
                    partial += 1
                elif sup == "unsupported":
                    unsupported += 1
        result.partial_object_count = partial
        result.unsupported_object_count = unsupported
        # Per-account code-object compatibility roll-up — mirrors the
        # Synapse dedicated-pool ``CodeObjectSummary`` so the SPA can
        # render one consistent compatibility card per source.
        result.code_object_summary = summarize_code_objects(
            result.tables, result.routines,
        )

    @staticmethod
    def _emit_privilege_caveat(result: SnowflakeWorkloadsAnalysis) -> None:
        """Warn when symptoms match an under-privileged role.

        The most common 0-tables / 0-jobs cause is the analyzer role
        lacking ``USAGE`` on user schemas + ``IMPORTED PRIVILEGES ON
        DATABASE SNOWFLAKE`` for ``ACCOUNT_USAGE`` reads. ``SHOW``
        statements silently return empty sets in that case, so we
        detect the pattern here and surface a caveat the user can act
        on instead of a silent empty report.
        """
        likely_under_privileged = (
            result.database_count > 0
            and result.table_count == 0
            and result.view_count == 0
            and result.routine_count == 0
        )
        account_usage_blocked = any(
            "ACCOUNT_USAGE" in (e or "")
            or "Schema 'SNOWFLAKE." in (e or "")
            or "not authorized" in (e or "")
            for e in result.errors
        )
        if not (likely_under_privileged or account_usage_blocked):
            return
        caveat = (
            "Snowflake role appears under-privileged: SHOW returned databases "
            "but no tables / views / routines, and / or ACCOUNT_USAGE reads "
            "were blocked. Grant the analyzer role visibility over user "
            "objects and access to the SNOWFLAKE database, then re-run. "
            "Minimum grants (as ACCOUNTADMIN):\n"
            "    GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <r>;\n"
            "    GRANT USAGE ON DATABASE <db> TO ROLE <r>;\n"
            "    GRANT USAGE ON ALL SCHEMAS IN DATABASE <db> TO ROLE <r>;\n"
            "    GRANT REFERENCES ON ALL TABLES IN DATABASE <db> TO ROLE <r>;\n"
            "    GRANT REFERENCES ON ALL VIEWS IN DATABASE <db> TO ROLE <r>;\n"
            "    GRANT MONITOR USAGE ON ACCOUNT TO ROLE <r>;\n"
            "    GRANT ROLE <r> TO USER <oauth_user>;\n"
            "Then on the Configuration page: set SNOWFLAKE_ROLE=<r>, Save, "
            "AND click 'Sign in to Snowflake' again — refresh tokens are "
            "bound to the role at consent time, so an existing token minted "
            "for PUBLIC will keep failing after the role grant. Note: "
            "Snowflake does not permit granting IMPORTED PRIVILEGES ON "
            "DATABASE SNOWFLAKE to PUBLIC, so <r> must be a non-PUBLIC role."
        )
        if caveat not in result.caveats:
            result.caveats.append(caveat)


__all__ = ["SnowflakeWorkloadsAnalyzer"]
