"""Collector for BigQuery workloads (datasets / tables / routines / scheduled queries / jobs).

Wraps three duck-typed clients so unit tests can inject MagicMocks
exposing only the call surface we touch:

* ``bigquery.Client`` — datasets, tables, routines.
* ``logging_v2.Client`` — audit-log entries for jobs / slot-hours.
* ``bigquery_datatransfer_v1.DataTransferServiceClient`` (optional) —
  scheduled queries. When the client is ``None`` (e.g. the
  ``google-cloud-bigquery-datatransfer`` extra is not installed), the
  collector emits a single caveat and skips scheduled-query enumeration.

Audit-log filter + field paths
------------------------------
The Cloud Logging filter mirrors the pattern in the reference
``constantino-casado/GCP_logs`` project (audit-log-only, declined as a
dependency for licensing + packaging reasons — see Phase 5 manifest).
The filter expression is:

    resource.type="bigquery_resource"
    protoPayload.serviceName="bigquery.googleapis.com"
    protoPayload.metadata.@type="type.googleapis.com/google.cloud.audit.BigQueryAuditMetadata"
    severity>=INFO

We then read job stats from one of two payload shapes:

* **v2 (modern)** — ``protoPayload.metadata.jobChange.job.jobStats``
  (``createTime``, ``startTime``, ``endTime``, ``queryStats.totalSlotMs``,
  ``queryStats.totalBilledBytes``, ``queryStats.totalProcessedBytes``).
* **v1 (legacy)** — ``protoPayload.serviceData.jobCompletedEvent.job``
  (``jobStatistics.totalSlotMs``, ``totalBilledBytes`` / ``totalProcessedBytes``).

The :func:`_extract_job_from_entry` helper tries both, returning
``None`` for entries that don't look like a finished job.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .fabric_compat import classify_routine, classify_table
from .models import (
    BigQueryJob,
    Dataset,
    Routine,
    ScheduledQuery,
    Table,
)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------- helpers


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Safe attribute lookup that also handles ``dict``-shaped responses."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _dig(obj: Any, *path: str, default: Any = None) -> Any:
    """Walk ``path`` through nested attributes / dicts safely."""
    cur: Any = obj
    for key in path:
        cur = _get(cur, key, None)
        if cur is None:
            return default
    return cur


def _enum_value(obj: Any) -> str | None:
    """Return the canonical string for an SDK enum-or-string value."""
    if obj is None:
        return None
    val = _get(obj, "value", None)
    if isinstance(val, str):
        return val
    if isinstance(obj, str):
        return obj
    name = getattr(obj, "name", None)
    if isinstance(name, str):
        return name
    return str(obj) if obj is not None else None


def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _to_datetime(val: Any) -> datetime | None:
    """Best-effort datetime parse for SDK / audit-log timestamps."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        # epoch seconds
        try:
            return datetime.fromtimestamp(float(val), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(val, str):
        try:
            # RFC3339 / ISO 8601, possibly with trailing 'Z'
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _fqn_table_refs(items: Any) -> list[str]:
    """Render audit-log / INFORMATION_SCHEMA table references as fqn strings.

    Both the v2 audit-log ``referencedTables`` list and the
    ``INFORMATION_SCHEMA.JOBS_BY_PROJECT.referenced_tables`` column emit
    structs with ``projectId``/``project_id`` + ``datasetId``/``dataset_id``
    + ``tableId``/``table_id``. v1 audit logs use ``projectId/datasetId/tableId``
    + an alternate ``//bigquery.googleapis.com/projects/<p>/datasets/<d>/tables/<t>``
    URI shape. Accept all of them and emit canonical ``project.dataset.table``
    strings (deduplicated, preserving first-seen order).
    """
    if not items:
        return []
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        proj = _get(it, "projectId", None) or _get(it, "project_id", None)
        ds = _get(it, "datasetId", None) or _get(it, "dataset_id", None)
        tbl = _get(it, "tableId", None) or _get(it, "table_id", None)
        if not (proj and ds and tbl):
            # Fall back to the resource-URI shape used by some v1 entries.
            uri = _get(it, "name", None) or (it if isinstance(it, str) else None)
            if isinstance(uri, str) and "/datasets/" in uri and "/tables/" in uri:
                try:
                    proj = uri.split("/projects/", 1)[1].split("/", 1)[0]
                    ds = uri.split("/datasets/", 1)[1].split("/", 1)[0]
                    tbl = uri.split("/tables/", 1)[1].split("/", 1)[0]
                except (IndexError, ValueError):
                    continue
            else:
                continue
        fqn = f"{proj}.{ds}.{tbl}"
        if fqn not in seen:
            seen.add(fqn)
            out.append(fqn)
    return out


# ----------------------------------------------------- audit-log extraction


# Public for tests / Slice 5-C reuse.
#
# Clamped to **job-completion events only** — covers query (incl. DML:
# INSERT / UPDATE / MERGE / DELETE / SCRIPT) plus load (data import),
# extract (export), and copy jobs. Without this clamp Cloud Logging
# returns every ``bigquery.googleapis.com`` entry (tableservice reads,
# datasetservice, IAM SetIamPolicy, jobservice.insert "started" events
# with no statistics) and the extractor discards ~95% of them — wasting
# log-read quota and paginating past real completions on chatty projects.
#
# The disjunction catches both shapes:
#   * v1 (legacy):  ``protoPayload.methodName="jobservice.jobcompleted"``
#                   carries ``serviceData.jobCompletedEvent.job``.
#   * v2 (current): ``protoPayload.metadata.jobChange.after="DONE"``
#                   carries ``metadata.jobChange.job`` with full stats.
# Pattern mirrors https://github.com/constantino-casado/GCP_logs but
# adds the v2 leg so we don't miss completions on modern audit pipelines.
AUDIT_LOG_FILTER_TEMPLATE = (
    'resource.type="bigquery_resource" '
    'AND protoPayload.serviceName="bigquery.googleapis.com" '
    'AND timestamp>="{start_iso}" '
    'AND ('
    'protoPayload.methodName="jobservice.jobcompleted" '
    'OR protoPayload.metadata.jobChange.after="DONE"'
    ')'
)


def _extract_job_from_entry(entry: Any, *, project_id: str) -> BigQueryJob | None:
    """Convert a Cloud Logging entry to :class:`BigQueryJob`, or ``None``.

    Tolerant of both the v2 (``jobChange``) and v1 (``jobCompletedEvent``)
    audit-log shapes. Returns ``None`` when the entry is not a job event
    (e.g. a tableservice call) or when the job id is missing.
    """
    payload = _get(entry, "payload", None)
    if payload is None:
        payload = _get(entry, "proto_payload", None)
    if payload is None:
        return None

    # ---- v2 path: payload.metadata.jobChange.job ----------------------
    v2_job = _dig(payload, "metadata", "jobChange", "job")
    if v2_job:
        job_id = str(
            _dig(v2_job, "jobName")
            or _dig(v2_job, "jobReference", "jobId")
            or ""
        )
        if not job_id:
            return None
        config = _dig(v2_job, "jobConfig") or {}
        stats = _dig(v2_job, "jobStats") or {}
        query_stats = _dig(stats, "queryStats") or {}
        load_stats = _dig(stats, "loadStats") or {}
        status = _dig(v2_job, "jobStatus") or {}
        error = _dig(status, "errorResult") or _dig(status, "additionalErrors")
        start = _to_datetime(_dig(stats, "startTime"))
        end = _to_datetime(_dig(stats, "endTime"))
        duration = (
            (end - start).total_seconds() if (start and end) else None
        )
        total_bytes = _to_int(
            _dig(query_stats, "totalBilledBytes")
            or _dig(load_stats, "totalBilledBytes"),
        )
        processed = _to_int(
            _dig(query_stats, "totalProcessedBytes")
            or _dig(load_stats, "inputDataBytes"),
        )
        referenced = _dig(query_stats, "referencedTables") or []
        referenced_ids = _fqn_table_refs(referenced)
        outcome = _job_outcome(_enum_value(_dig(status, "jobState")), error)
        return BigQueryJob(
            job_id=job_id.split("/")[-1],
            project_id=project_id,
            location=_dig(v2_job, "jobReference", "location"),
            user_email=_dig(entry, "principal_email")
                or _dig(payload, "authenticationInfo", "principalEmail"),
            job_type=_normalise_job_type(_dig(config, "type")),
            statement_type=_dig(query_stats, "statementType"),
            state=_enum_value(_dig(status, "jobState")),
            outcome=outcome,
            start_time=start,
            end_time=end,
            duration_seconds=duration,
            total_slot_ms=_to_int(_dig(query_stats, "totalSlotMs")),
            total_billed_bytes=total_bytes,
            total_processed_bytes=processed,
            referenced_tables=referenced_ids,
            referenced_table_count=len(referenced_ids),
            cache_hit=_dig(query_stats, "cacheHit"),
            error_result=str(error) if error else None,
            reservation_id=_dig(stats, "reservationUsage", 0, "name"),
        )

    # ---- v1 path: payload.serviceData.jobCompletedEvent.job -----------
    v1_job = _dig(payload, "serviceData", "jobCompletedEvent", "job")
    if v1_job:
        job_id = str(_dig(v1_job, "jobName", "jobId") or "")
        if not job_id:
            return None
        stats = _dig(v1_job, "jobStatistics") or {}
        config = _dig(v1_job, "jobConfiguration") or {}
        status = _dig(v1_job, "jobStatus") or {}
        error = _dig(status, "error") or _dig(status, "additionalErrors")
        start = _to_datetime(_dig(stats, "startTime"))
        end = _to_datetime(_dig(stats, "endTime"))
        duration = (
            (end - start).total_seconds() if (start and end) else None
        )
        # v1 reports type via the populated sub-config (query / load / extract / copy).
        v1_type = "QUERY" if _dig(config, "query") else (
            "LOAD" if _dig(config, "load") else (
                "EXTRACT" if _dig(config, "extract") else (
                    "COPY" if _dig(config, "tableCopy") else "UNKNOWN"
                )
            )
        )
        outcome = _job_outcome(_enum_value(_dig(status, "state")), error)
        return BigQueryJob(
            job_id=job_id,
            project_id=project_id,
            location=_dig(v1_job, "jobName", "location"),
            user_email=_dig(payload, "authenticationInfo", "principalEmail"),
            job_type=v1_type,  # type: ignore[arg-type]
            statement_type=_dig(stats, "query", "statementType"),
            state=_enum_value(_dig(status, "state")),
            outcome=outcome,
            start_time=start,
            end_time=end,
            duration_seconds=duration,
            total_slot_ms=_to_int(_dig(stats, "totalSlotMs")),
            total_billed_bytes=_to_int(_dig(stats, "query", "totalBilledBytes")),
            total_processed_bytes=_to_int(_dig(stats, "totalProcessedBytes")),
            referenced_tables=_fqn_table_refs(_dig(stats, "query", "referencedTables") or []),
            referenced_table_count=len(_fqn_table_refs(_dig(stats, "query", "referencedTables") or [])),
            cache_hit=_dig(stats, "query", "cacheHit"),
            error_result=str(error) if error else None,
        )

    return None


def _normalise_job_type(raw: Any) -> str:
    """Map a v2 ``jobConfig.type`` value to our :class:`JobType` literal."""
    v = _enum_value(raw)
    if not v:
        return "UNKNOWN"
    v = v.upper()
    if v in ("QUERY", "LOAD", "EXTRACT", "COPY"):
        return v
    return "UNKNOWN"


def _job_outcome(state: str | None, error: Any) -> str:
    if error:
        return "failed"
    s = (state or "").upper()
    if s == "DONE":
        return "succeeded"
    if s in ("RUNNING", "PENDING"):
        return "in_progress"
    if s == "CANCELLED":
        return "cancelled"
    return "unknown" if not s else "unknown"


def _job_from_information_schema_row(
    row: Any,
    *,
    project_id: str,
    capture_query_text: bool = False,
) -> BigQueryJob | None:
    """Convert one ``INFORMATION_SCHEMA.JOBS_BY_PROJECT`` row to a :class:`BigQueryJob`.

    The BigQuery Python client returns ``Row`` objects that support both
    ``row["job_id"]`` and ``row.job_id``; treat them as dict-like via
    :func:`_get`. ``referenced_tables`` is an ``ARRAY<STRUCT<project_id,
    dataset_id, table_id>>`` already in the canonical shape that
    :func:`_fqn_table_refs` handles.

    Slot-hour math (``total_slot_ms`` → ``slot_hours``) is intentionally
    left to ``run_stats.populate_slot_hours`` so the audit-log path and
    INFORMATION_SCHEMA path share the same enrichment step.
    """
    job_id = _get(row, "job_id", None)
    if not job_id:
        return None
    state = _get(row, "state", None)
    error_result = _get(row, "error_result", None)
    # ``error_result`` is a STRUCT<reason, location, message, debug_info>;
    # render to a short string so downstream consumers can display it.
    error_str: str | None = None
    if error_result:
        msg = _get(error_result, "message", None) or _get(error_result, "reason", None)
        error_str = str(msg) if msg else str(error_result)
    start = _to_datetime(_get(row, "start_time", None) or _get(row, "creation_time", None))
    end = _to_datetime(_get(row, "end_time", None))
    duration = (end - start).total_seconds() if (start and end) else None
    referenced_ids = _fqn_table_refs(_get(row, "referenced_tables", None) or [])
    outcome = _job_outcome(state, error_str)
    return BigQueryJob(
        job_id=str(job_id),
        project_id=project_id,
        location=None,  # INFORMATION_SCHEMA scopes by region in the FROM clause; we know it externally.
        user_email=_get(row, "user_email", None),
        job_type=_normalise_job_type(_get(row, "job_type", None)),
        statement_type=_get(row, "statement_type", None),
        state=state,
        outcome=outcome,
        start_time=start,
        end_time=end,
        duration_seconds=duration,
        total_slot_ms=_to_int(_get(row, "total_slot_ms", None)),
        total_billed_bytes=_to_int(_get(row, "total_bytes_billed", None)),
        total_processed_bytes=_to_int(_get(row, "total_bytes_processed", None)),
        referenced_tables=referenced_ids,
        referenced_table_count=len(referenced_ids),
        cache_hit=_get(row, "cache_hit", None),
        error_result=error_str,
        reservation_id=_get(row, "reservation_id", None),
        priority=_get(row, "priority", None),
        labels=_labels_from_row(_get(row, "labels", None)),
        parent_job_id=_get(row, "parent_job_id", None),
        edition=_get(row, "edition", None),
        query_text=_get(row, "query", None) if capture_query_text else None,
    )


def _labels_from_row(raw: Any) -> dict[str, str]:
    """Normalize ``INFORMATION_SCHEMA.JOBS_BY_PROJECT.labels`` into a dict.

    BigQuery returns labels as ``ARRAY<STRUCT<key STRING, value STRING>>``.
    Tests pass plain dicts. Tolerate both.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    try:
        for item in raw:
            k = _get(item, "key", None)
            v = _get(item, "value", None)
            if k is not None:
                out[str(k)] = "" if v is None else str(v)
    except TypeError:
        return {}
    return out


# ============================================================ Collector ===


class BigQueryWorkloadsCollector:
    """Enumerate BigQuery datasets/tables/routines/scheduled queries/jobs."""

    def __init__(
        self,
        bq_client: Any,
        logging_client: Any,
        *,
        project_id: str,
        dts_client: Any = None,
        locations: tuple[str, ...] = ("us", "eu"),
    ) -> None:
        self._bq = bq_client
        self._log = logging_client
        self._dts = dts_client
        self._project_id = project_id
        self._locations = locations

    # ------------------------------------------------------------ datasets

    def iter_datasets(self) -> Iterator[Dataset]:
        for ref in self._bq.list_datasets(project=self._project_id):
            try:
                yield self._convert_dataset(ref)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to enrich dataset %s: %s", _get(ref, "dataset_id"), exc)

    def _convert_dataset(self, ref: Any) -> Dataset:
        dataset_id = str(
            _get(ref, "dataset_id", None) or _dig(ref, "reference", "datasetId") or "",
        )
        # ``list_datasets()`` yields ``DatasetListItem`` which has no
        # ``.path`` attribute — passing it directly to ``get_dataset()``
        # raises ``AttributeError``. Hand the underlying ``.reference``
        # (a real ``DatasetReference``) to the client instead; fall back
        # to the list item itself for fakes/mocks that don't expose one.
        target = _get(ref, "reference", None) or ref
        full = self._bq.get_dataset(target) if dataset_id else None
        return Dataset(
            project_id=self._project_id,
            dataset_id=dataset_id,
            location=_get(full, "location"),
            friendly_name=_get(full, "friendly_name"),
            description=_get(full, "description"),
            default_table_expiration_ms=_to_int(_get(full, "default_table_expiration_ms")),
            default_partition_expiration_ms=_to_int(
                _get(full, "default_partition_expiration_ms"),
            ),
            labels=dict(_get(full, "labels", {}) or {}),
            created_at=_to_datetime(_get(full, "created")),
            last_modified_at=_to_datetime(_get(full, "modified")),
        )

    # -------------------------------------------------------------- tables

    def iter_tables(self, dataset_id: str) -> Iterator[Table]:
        try:
            refs = list(self._bq.list_tables(dataset_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to list tables for %s: %s", dataset_id, exc)
            return
        for ref in refs:
            try:
                yield self._convert_table(ref, dataset_id=dataset_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to enrich table %s: %s", _get(ref, "table_id"), exc)

    def _convert_table(self, ref: Any, *, dataset_id: str) -> Table:
        table_id = str(_get(ref, "table_id", "") or "")
        # ``TableListItem`` has no ``.path`` — use ``.reference`` for the
        # ``get_table()`` round-trip. See ``_convert_dataset`` above.
        target = _get(ref, "reference", None) or ref
        full = self._bq.get_table(target) if table_id else None
        table_type = (_enum_value(_get(full, "table_type")) or "UNKNOWN").upper()
        # Map BigQuery SDK kinds onto our literal set.
        if table_type not in ("TABLE", "VIEW", "MATERIALIZED_VIEW", "EXTERNAL", "SNAPSHOT", "CLONE"):
            table_type = "UNKNOWN"
        support, note = classify_table(table_type)
        partition = _get(full, "time_partitioning")
        range_partition = _get(full, "range_partitioning")
        partition_field = _get(partition, "field") or _get(range_partition, "field")
        partition_type = _enum_value(_get(partition, "type_")) if partition else (
            "RANGE" if range_partition else None
        )
        return Table(
            project_id=self._project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            full_table_id=f"{self._project_id}.{dataset_id}.{table_id}",
            table_type=table_type,  # type: ignore[arg-type]
            partition_field=partition_field,
            partition_type=partition_type,
            require_partition_filter=_get(full, "require_partition_filter"),
            clustering_fields=list(_get(full, "clustering_fields", []) or []),
            num_rows=_to_int(_get(full, "num_rows")),
            num_bytes=_to_int(_get(full, "num_bytes")),
            created_at=_to_datetime(_get(full, "created")),
            last_modified_at=_to_datetime(_get(full, "modified")),
            expiration_at=_to_datetime(_get(full, "expires")),
            labels=dict(_get(full, "labels", {}) or {}),
            support=support,
            notes=[note] if note else [],
        )

    # ------------------------------------------------------------ routines

    def iter_routines(self, dataset_id: str) -> Iterator[Routine]:
        list_fn = getattr(self._bq, "list_routines", None)
        if list_fn is None:
            return
        try:
            refs = list(list_fn(dataset_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to list routines for %s: %s", dataset_id, exc)
            return
        for ref in refs:
            try:
                yield self._convert_routine(ref, dataset_id=dataset_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to enrich routine %s: %s", _get(ref, "routine_id"), exc)

    def _convert_routine(self, ref: Any, *, dataset_id: str) -> Routine:
        routine_id = str(_get(ref, "routine_id", "") or "")
        get_fn = getattr(self._bq, "get_routine", None)
        # ``RoutineListItem`` has no ``.path`` — same fix as datasets/tables.
        target = _get(ref, "reference", None) or ref
        full = get_fn(target) if (get_fn and routine_id) else ref
        rtype = (_enum_value(_get(full, "type_")) or _enum_value(_get(full, "routine_type")) or "UNKNOWN").upper()
        if rtype not in ("SCALAR_FUNCTION", "PROCEDURE", "TABLE_VALUED_FUNCTION", "AGGREGATE_FUNCTION"):
            rtype = "UNKNOWN"
        lang = (_enum_value(_get(full, "language")) or "UNKNOWN").upper()
        if lang not in ("SQL", "JAVASCRIPT", "PYTHON"):
            lang = "UNKNOWN"
        support, note = classify_routine(rtype)
        args = _get(full, "arguments", []) or []
        return Routine(
            project_id=self._project_id,
            dataset_id=dataset_id,
            routine_id=routine_id,
            routine_type=rtype,  # type: ignore[arg-type]
            language=lang,  # type: ignore[arg-type]
            arguments_count=len(args) if isinstance(args, list) else 0,
            created_at=_to_datetime(_get(full, "created")),
            last_modified_at=_to_datetime(_get(full, "modified")),
            support=support,
            notes=[note] if note else [],
        )

    # -------------------------------------------------- scheduled queries

    def iter_scheduled_queries(self) -> Iterator[ScheduledQuery]:
        if self._dts is None:
            return
        for loc in self._locations:
            parent = f"projects/{self._project_id}/locations/{loc}"
            try:
                configs = self._dts.list_transfer_configs(parent=parent)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to list transfer configs in %s: %s", loc, exc)
                continue
            for cfg in configs:
                ds_id = _enum_value(_get(cfg, "data_source_id"))
                if ds_id != "scheduled_query":
                    continue
                params = _get(cfg, "params", {}) or {}
                query = None
                # params is a Struct in protobuf-land; tests pass a dict.
                if isinstance(params, dict):
                    query = params.get("query")
                else:
                    fields = _get(params, "fields", {}) or {}
                    if isinstance(fields, dict):
                        q = fields.get("query")
                        query = _get(q, "string_value") if q is not None else None
                yield ScheduledQuery(
                    name=str(_get(cfg, "name", "")),
                    display_name=str(_get(cfg, "display_name", "")),
                    location=loc,
                    destination_dataset_id=_get(cfg, "destination_dataset_id"),
                    schedule=_get(cfg, "schedule"),
                    state=(_enum_value(_get(cfg, "state")) or "UNKNOWN").upper(),  # type: ignore[arg-type]
                    next_run_at=_to_datetime(_get(cfg, "next_run_time")),
                    last_run_at=_to_datetime(_dig(cfg, "update_time")),
                    user_id=str(_get(cfg, "user_id", "")) or None,
                    params_query=query,
                )

    # ---------------------------------------------------------------- jobs

    def iter_jobs(self, *, lookback_days: int = 28) -> Iterator[BigQueryJob]:
        """Yield :class:`BigQueryJob` for every BigQuery audit-log entry in window."""
        start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        filter_ = AUDIT_LOG_FILTER_TEMPLATE.format(
            start_iso=start.replace(microsecond=0).isoformat(),
        )
        try:
            entries = self._log.list_entries(
                filter_=filter_,
                resource_names=[f"projects/{self._project_id}"],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to list audit-log entries: %s", exc)
            return
        for entry in entries:
            try:
                job = _extract_job_from_entry(entry, project_id=self._project_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to parse audit entry: %s", exc)
                continue
            if job is not None:
                yield job

    def iter_jobs_information_schema(
        self,
        *,
        lookback_days: int = 28,
        region: str | None = None,
        row_limit: int | None = None,
        capture_query_text: bool = False,
    ) -> Iterator[BigQueryJob]:
        """Yield :class:`BigQueryJob` rows from ``INFORMATION_SCHEMA.JOBS_BY_PROJECT``.

        Preferred source for the usage map (Slice 5-J): unlike Cloud Logging
        this view does not require Data Access audit logs and retains
        180 days of history regardless of log-bucket retention. Requires
        ``bigquery.jobs.listAll`` on the project for the signed-in
        principal (built into ``roles/bigquery.resourceViewer`` and the
        broader ``roles/bigquery.admin``).

        Region defaults to the first entry of ``self._locations`` (typically
        the project descriptor's pinned location, e.g. ``EU`` / ``US``);
        callers can override with ``region=`` to scan a different multi-region.
        Returns nothing (silently) if the project has no jobs in the window.

        ``capture_query_text=True`` includes the ``query`` column in the
        SELECT and copies it to ``BigQueryJob.query_text``. Off by default
        because SQL text routinely carries embedded literals / PII and
        inflates the JSON artefact by 10-100x on busy projects.
        """
        target_region = (region or (self._locations[0] if self._locations else "US")).lower()
        # BigQuery dataset id form is ``region-<name>`` (e.g. ``region-us``,
        # ``region-eu``, ``region-europe-west1``). Multi-region constants
        # ``US`` / ``EU`` are special-cased; everything else is a single region.
        region_id = f"region-{target_region}"
        limit_clause = f"\n LIMIT {int(row_limit)}" if row_limit and row_limit > 0 else ""
        # ``edition`` was added by Google in 2023; older projects may not
        # have the column. SELECT it via SAFE.* style (try; fall back if
        # the query errors at parse time).
        query_select = "query,\n       " if capture_query_text else ""
        sql = (
            "SELECT job_id, user_email, job_type, statement_type, priority,\n"
            "       creation_time, start_time, end_time,\n"
            "       total_slot_ms, total_bytes_billed, total_bytes_processed,\n"
            "       cache_hit, state, error_result, reservation_id,\n"
            "       parent_job_id, labels, edition,\n"
            "       referenced_tables,\n"
            f"       {query_select}"
            "       1 AS _sentinel\n"
            f"FROM `{self._project_id}.{region_id}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT\n"
            "WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(),\n"
            f"        INTERVAL {int(lookback_days)} DAY)\n"
            "  AND state = 'DONE'"
            f"{limit_clause}"
        )
        try:
            rows = self._bq.query(sql).result()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "INFORMATION_SCHEMA.JOBS_BY_PROJECT query failed in %s: %s",
                region_id,
                exc,
            )
            # Retry without the ``edition`` column for legacy projects /
            # regions where Google hasn't backfilled it yet.
            if "edition" in str(exc).lower() or "unrecognized name" in str(exc).lower():
                sql_legacy = sql.replace(
                    "       parent_job_id, labels, edition,\n",
                    "       parent_job_id, labels,\n",
                )
                log.info(
                    "retrying INFORMATION_SCHEMA query without ``edition`` column",
                )
                try:
                    rows = self._bq.query(sql_legacy).result()
                except Exception as exc2:  # noqa: BLE001
                    log.warning(
                        "INFORMATION_SCHEMA retry also failed in %s: %s",
                        region_id,
                        exc2,
                    )
                    return
            else:
                return
        for row in rows:
            try:
                job = _job_from_information_schema_row(
                    row,
                    project_id=self._project_id,
                    capture_query_text=capture_query_text,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to parse INFORMATION_SCHEMA row: %s", exc)
                continue
            if job is not None:
                yield job


__all__ = [
    "BigQueryWorkloadsCollector",
    "AUDIT_LOG_FILTER_TEMPLATE",
]
