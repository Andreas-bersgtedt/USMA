"""Collector for Snowflake workloads.

Wraps the duck-typed ``connect`` callable produced by
:class:`~...sources.snowflake.SnowflakeClientBundle`. Each iter method
opens a short-lived connection via ``contextlib.closing`` so cursors +
connections never leak even when the analyzer hits a hard error.

Query strategy
--------------
* **Inventory** (warehouses, databases, schemas, tables, views,
  routines, stages, streams, tasks, pipes) — ``SHOW <kind>`` statements
  return a single dataset describable column-by-column; we normalise
  with :func:`_row_to_dict` since the column ordering is documented
  but stable enough across versions.
* **Jobs** — ``SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`` over a configurable
  lookback window. The view is account-scoped and authoritative; we
  do **not** fall back to ``INFORMATION_SCHEMA.QUERY_HISTORY`` (limited
  to 7 days and current-session warehouse).

The collector deliberately stays free of credit-hour math (Slice 7-D
owns the warehouse-size → vCore proxy + window aggregation). What it
emits here is raw model rows — analyzers downstream apply roll-ups,
support classification, and the F-SKU mapping.
"""
from __future__ import annotations

import logging
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator

from .fabric_compat import classify_object, classify_routine, classify_table
from .models import (
    Database,
    Pipe,
    Routine,
    Schema,
    SnowflakeJob,
    SnowflakeTableUsage,
    Stage,
    Stream,
    Table,
    Task,
    Warehouse,
)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------- helpers


def _row_to_dict(cursor: Any, row: Any) -> dict[str, Any]:
    """Convert a connector row (tuple or dict) into a ``dict`` keyed by
    the cursor's ``description`` column names (upper-cased)."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return {str(k).upper(): v for k, v in row.items()}
    desc = getattr(cursor, "description", None) or []
    out: dict[str, Any] = {}
    for i, col in enumerate(desc):
        name = col[0] if isinstance(col, (tuple, list)) else getattr(col, "name", str(i))
        out[str(name).upper()] = row[i] if i < len(row) else None
    return out


def _str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return str(val)


def _to_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().upper() in ("Y", "YES", "TRUE", "T", "1")
    return bool(val)


def _to_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(float(val), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _normalise_size(val: Any) -> str:
    if not val:
        return "UNKNOWN"
    s = str(val).strip().upper().replace(" ", "-")
    if s in {
        "X-SMALL", "SMALL", "MEDIUM", "LARGE", "X-LARGE",
        "2X-LARGE", "3X-LARGE", "4X-LARGE", "5X-LARGE", "6X-LARGE",
    }:
        return s
    # Snowflake sometimes returns "XSMALL" / "XXLARGE" — normalise.
    alias = {
        "XSMALL": "X-SMALL",
        "XLARGE": "X-LARGE",
        "XXLARGE": "2X-LARGE",
        "XXXLARGE": "3X-LARGE",
    }
    return alias.get(s, "UNKNOWN")


def _normalise_routine_language(val: Any) -> str:
    """Snowflake reports routine language as upper-case in ACCOUNT_USAGE."""
    if not val:
        return "UNKNOWN"
    s = str(val).strip().upper()
    if s in ("SQL", "JAVASCRIPT", "PYTHON", "JAVA", "SCALA"):
        return s
    return "UNKNOWN"


def _classify_account_usage_table_kind(row: dict[str, Any]) -> str:
    """Mirror :py:meth:`SnowflakeWorkloadsCollector._classify_table_kind`
    for the ``ACCOUNT_USAGE.TABLES`` row shape (``TABLE_TYPE`` instead
    of ``KIND``).
    """
    table_type = (_str(row.get("TABLE_TYPE")) or "").upper()
    if table_type in ("EXTERNAL TABLE", "EXTERNAL_TABLE"):
        return "EXTERNAL_TABLE"
    if table_type in ("DYNAMIC TABLE", "DYNAMIC_TABLE"):
        return "DYNAMIC_TABLE"
    if table_type in ("ICEBERG TABLE", "ICEBERG_TABLE"):
        return "ICEBERG_TABLE"
    if table_type == "TEMPORARY TABLE" or table_type == "TEMPORARY":
        return "TEMPORARY"
    if _to_bool(row.get("IS_TRANSIENT")) or table_type == "TRANSIENT":
        return "TRANSIENT"
    if table_type in ("BASE TABLE", "TABLE", ""):
        return "TABLE"
    return "UNKNOWN"


def _outcome(execution_status: str | None) -> str:
    if not execution_status:
        return "unknown"
    up = execution_status.upper()
    if up == "SUCCESS":
        return "succeeded"
    if up in ("FAIL", "FAILED_WITH_ERROR", "FAILED_WITH_INCIDENT"):
        return "failed"
    if up == "INCIDENT":
        return "failed"
    if up in ("CANCELLED", "CANCELED"):
        return "cancelled"
    if up in ("RUNNING", "QUEUED", "RESUMING_WAREHOUSE", "BLOCKED"):
        return "in_progress"
    return "unknown"


# ---------------------------------------------------------------------- collector


class SnowflakeWorkloadsCollector:
    """Issue Snowflake queries against a connection factory.

    ``connect`` is a zero-arg callable returning a ``snowflake.connector``
    connection (the same shape ``SnowflakeClientBundle.connect`` produces).
    Tests inject a stub that returns a stub connection with a stub cursor.
    """

    def __init__(self, connect: Callable[[], Any], *, account: str) -> None:
        self._connect = connect
        self._account = account

    # ------------------------------------------------------------------ internals

    def _rows(self, sql: str) -> list[dict[str, Any]]:
        """Execute ``sql`` and return all rows as upper-cased dicts."""
        with closing(self._connect()) as conn:
            with closing(conn.cursor()) as cur:
                cur.execute(sql)
                fetched = cur.fetchall() or []
                return [_row_to_dict(cur, r) for r in fetched]

    # ------------------------------------------------------------------ warehouses

    def iter_warehouses(self) -> Iterator[Warehouse]:
        for row in self._rows("SHOW WAREHOUSES"):
            yield Warehouse(
                name=_str(row.get("NAME")) or "<unknown>",
                size=_normalise_size(row.get("SIZE")),
                type=(_str(row.get("TYPE")) or "STANDARD").upper().replace(" ", "-"),
                state=_str(row.get("STATE")),
                min_cluster_count=_to_int(row.get("MIN_CLUSTER_COUNT")) or 1,
                max_cluster_count=_to_int(row.get("MAX_CLUSTER_COUNT")) or 1,
                scaling_policy=(
                    (_str(row.get("SCALING_POLICY")) or "STANDARD").upper()
                ),
                auto_suspend_seconds=_to_int(row.get("AUTO_SUSPEND")),
                auto_resume=_to_bool(row.get("AUTO_RESUME")) if row.get("AUTO_RESUME") is not None else None,
                owner=_str(row.get("OWNER")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED_ON")),
                resumed_at=_to_datetime(row.get("RESUMED_ON")),
            )

    # ------------------------------------------------------------------ databases / schemas

    def iter_databases(self) -> Iterator[Database]:
        for row in self._rows("SHOW DATABASES"):
            kind = (_str(row.get("KIND")) or "").upper()
            origin = (_str(row.get("ORIGIN")) or "").strip()
            yield Database(
                name=_str(row.get("NAME")) or "<unknown>",
                owner=_str(row.get("OWNER")),
                is_transient=kind == "TRANSIENT",
                is_share=bool(origin),
                retention_time_days=_to_int(row.get("RETENTION_TIME")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED_ON")),
            )

    def iter_schemas(self, database: str) -> Iterator[Schema]:
        # ``SHOW SCHEMAS IN DATABASE <name>`` requires the database to exist.
        for row in self._rows(f'SHOW SCHEMAS IN DATABASE "{database}"'):
            options = (_str(row.get("OPTIONS")) or "").upper()
            yield Schema(
                database_name=database,
                name=_str(row.get("NAME")) or "<unknown>",
                owner=_str(row.get("OWNER")),
                is_managed_access="MANAGED ACCESS" in options,
                is_transient="TRANSIENT" in options,
                retention_time_days=_to_int(row.get("RETENTION_TIME")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED_ON")),
            )

    # ------------------------------------------------------------------ tables / views

    def iter_tables(self, database: str, schema: str) -> Iterator[Table]:
        for row in self._rows(
            f'SHOW TABLES IN SCHEMA "{database}"."{schema}"',
        ):
            name = _str(row.get("NAME")) or "<unknown>"
            kind = self._classify_table_kind(row)
            support, note = classify_table(kind)
            yield Table(
                database_name=database,
                schema_name=schema,
                name=name,
                full_name=f"{database}.{schema}.{name}",
                kind=kind,
                is_transient=kind == "TRANSIENT" or _to_bool(row.get("IS_TRANSIENT")),
                is_temporary=kind == "TEMPORARY",
                row_count=_to_int(row.get("ROWS")),
                bytes=_to_int(row.get("BYTES")),
                cluster_by=_str(row.get("CLUSTER_BY")),
                retention_time_days=_to_int(row.get("RETENTION_TIME")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED_ON")),
                support=support,
                notes=[note] if note else [],
            )

    def iter_views(self, database: str, schema: str) -> Iterator[Table]:
        for row in self._rows(
            f'SHOW VIEWS IN SCHEMA "{database}"."{schema}"',
        ):
            name = _str(row.get("NAME")) or "<unknown>"
            is_materialized = _to_bool(row.get("IS_MATERIALIZED"))
            kind = "MATERIALIZED_VIEW" if is_materialized else "VIEW"
            support, note = classify_table(kind)
            yield Table(
                database_name=database,
                schema_name=schema,
                name=name,
                full_name=f"{database}.{schema}.{name}",
                kind=kind,
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED_ON")),
                support=support,
                notes=[note] if note else [],
            )

    @staticmethod
    def _classify_table_kind(row: dict[str, Any]) -> str:
        """Best-effort classification of a ``SHOW TABLES`` row."""
        kind = (_str(row.get("KIND")) or "").upper()
        if kind == "EXTERNAL TABLE":
            return "EXTERNAL_TABLE"
        if kind == "DYNAMIC TABLE":
            return "DYNAMIC_TABLE"
        if kind == "ICEBERG TABLE" or _to_bool(row.get("IS_ICEBERG")):
            return "ICEBERG_TABLE"
        if kind == "TEMPORARY":
            return "TEMPORARY"
        if kind == "TRANSIENT" or _to_bool(row.get("IS_TRANSIENT")):
            return "TRANSIENT"
        if kind == "TABLE" or not kind:
            return "TABLE"
        return "UNKNOWN"

    # ------------------------------------------------------------------ routines

    def iter_functions(self, database: str, schema: str) -> Iterator[Routine]:
        for row in self._rows(
            f'SHOW USER FUNCTIONS IN SCHEMA "{database}"."{schema}"',
        ):
            yield self._routine_from_row(database, schema, row, kind="FUNCTION")

    def iter_procedures(self, database: str, schema: str) -> Iterator[Routine]:
        for row in self._rows(
            f'SHOW PROCEDURES IN SCHEMA "{database}"."{schema}"',
        ):
            yield self._routine_from_row(database, schema, row, kind="PROCEDURE")

    def _routine_from_row(
        self, database: str, schema: str, row: dict[str, Any], *, kind: str,
    ) -> Routine:
        name = _str(row.get("NAME")) or "<unknown>"
        language = (_str(row.get("LANGUAGE")) or "SQL").upper()
        arguments = _str(row.get("ARGUMENTS")) or ""
        support, note = classify_routine(kind, language)
        return Routine(
            database_name=database,
            schema_name=schema,
            name=name,
            full_name=f"{database}.{schema}.{arguments or name}",
            routine_kind=kind,
            language=language if language in ("SQL", "JAVASCRIPT", "PYTHON", "JAVA", "SCALA") else "UNKNOWN",
            argument_count=arguments.count(",") + 1 if arguments and arguments != "()" else 0,
            is_secure=_to_bool(row.get("IS_SECURE")),
            owner=_str(row.get("OWNER")),
            comment=_str(row.get("COMMENT") or row.get("DESCRIPTION")),
            created_at=_to_datetime(row.get("CREATED_ON")),
            support=support,
            notes=[note] if note else [],
        )

    # ------------------------------------------------------------------ stages / streams / tasks / pipes

    def iter_stages(self, database: str, schema: str) -> Iterator[Stage]:
        for row in self._rows(
            f'SHOW STAGES IN SCHEMA "{database}"."{schema}"',
        ):
            name = _str(row.get("NAME")) or "<unknown>"
            support, note = classify_object("STAGE")
            yield Stage(
                database_name=database,
                schema_name=schema,
                name=name,
                full_name=f"{database}.{schema}.{name}",
                stage_type=_str(row.get("TYPE")),
                cloud=_str(row.get("CLOUD")),
                url=_str(row.get("URL")),
                storage_integration=_str(row.get("STORAGE_INTEGRATION")),
                owner=_str(row.get("OWNER")),
                created_at=_to_datetime(row.get("CREATED_ON")),
                support=support,
                notes=[note] if note else [],
            )

    def iter_streams(self, database: str, schema: str) -> Iterator[Stream]:
        for row in self._rows(
            f'SHOW STREAMS IN SCHEMA "{database}"."{schema}"',
        ):
            name = _str(row.get("NAME")) or "<unknown>"
            mode = (_str(row.get("MODE")) or "DEFAULT").upper()
            if mode not in ("DEFAULT", "APPEND_ONLY", "INSERT_ONLY"):
                mode = "DEFAULT"
            support, note = classify_object("STREAM")
            yield Stream(
                database_name=database,
                schema_name=schema,
                name=name,
                full_name=f"{database}.{schema}.{name}",
                source_full_name=_str(row.get("TABLE_NAME")),
                mode=mode,  # type: ignore[arg-type]
                stale=_to_bool(row.get("STALE")) if row.get("STALE") is not None else None,
                stale_after=_to_datetime(row.get("STALE_AFTER")),
                owner=_str(row.get("OWNER")),
                created_at=_to_datetime(row.get("CREATED_ON")),
                support=support,
                notes=[note] if note else [],
            )

    def iter_tasks(self, database: str, schema: str) -> Iterator[Task]:
        for row in self._rows(
            f'SHOW TASKS IN SCHEMA "{database}"."{schema}"',
        ):
            name = _str(row.get("NAME")) or "<unknown>"
            state = (_str(row.get("STATE")) or "UNKNOWN").upper()
            if state not in ("STARTED", "SUSPENDED"):
                state = "UNKNOWN"
            preds_raw = row.get("PREDECESSORS")
            preds: list[str] = []
            if isinstance(preds_raw, (list, tuple)):
                preds = [str(p) for p in preds_raw if p]
            elif isinstance(preds_raw, str):
                # Snowflake returns ``["DB.SCHEMA.PRED1","DB.SCHEMA.PRED2"]`` as a JSON-ish string.
                cleaned = preds_raw.strip().strip("[]")
                if cleaned:
                    preds = [p.strip().strip('"') for p in cleaned.split(",") if p.strip()]
            support, note = classify_object("TASK")
            yield Task(
                database_name=database,
                schema_name=schema,
                name=name,
                full_name=f"{database}.{schema}.{name}",
                state=state,  # type: ignore[arg-type]
                warehouse=_str(row.get("WAREHOUSE")),
                schedule=_str(row.get("SCHEDULE")),
                predecessors=preds,
                condition=_str(row.get("CONDITION")),
                owner=_str(row.get("OWNER")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED_ON")),
                support=support,
                notes=[note] if note else [],
            )

    def iter_pipes(self, database: str, schema: str) -> Iterator[Pipe]:
        for row in self._rows(
            f'SHOW PIPES IN SCHEMA "{database}"."{schema}"',
        ):
            name = _str(row.get("NAME")) or "<unknown>"
            state_raw = (_str(row.get("EXECUTION_STATE")) or _str(row.get("STATE")) or "UNKNOWN").upper()
            if state_raw not in ("RUNNING", "PAUSED", "STOPPED"):
                state_raw = "UNKNOWN"
            support, note = classify_object("PIPE")
            yield Pipe(
                database_name=database,
                schema_name=schema,
                name=name,
                full_name=f"{database}.{schema}.{name}",
                state=state_raw,  # type: ignore[arg-type]
                integration=_str(row.get("NOTIFICATION_CHANNEL") or row.get("INTEGRATION")),
                pattern=_str(row.get("PATTERN")),
                owner=_str(row.get("OWNER")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED_ON")),
                support=support,
                notes=[note] if note else [],
            )

    # ------------------------------------------------------------------ ACCOUNT_USAGE inventory fallback

    # When the analyzer role lacks per-database ``USAGE`` (a single
    # ``IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`` grant is enough),
    # the ``SHOW`` statements above silently return empty sets. The
    # ACCOUNT_USAGE views below are visible with that single grant and
    # cover the high-value object families (databases, schemas,
    # tables, views, functions, procedures) — at the cost of ~45-180
    # min freshness latency. The analyzer calls these only as a
    # fallback when SHOW DATABASES came back empty, and emits a
    # caveat to flag the latency.

    def iter_databases_from_account_usage(self) -> Iterator[Database]:
        for row in self._rows(
            "SELECT DATABASE_NAME AS NAME, DATABASE_OWNER AS OWNER, "
            "TYPE, IS_TRANSIENT, RETENTION_TIME, COMMENT, CREATED "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.DATABASES "
            "WHERE DELETED IS NULL ORDER BY DATABASE_NAME"
        ):
            type_str = (_str(row.get("TYPE")) or "").upper()
            yield Database(
                name=_str(row.get("NAME")) or "<unknown>",
                owner=_str(row.get("OWNER")),
                is_transient=_to_bool(row.get("IS_TRANSIENT")),
                is_share=type_str in ("IMPORTED DATABASE", "SHARED"),
                retention_time_days=_to_int(row.get("RETENTION_TIME")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED")),
            )

    def iter_schemas_from_account_usage(self) -> Iterator[Schema]:
        for row in self._rows(
            "SELECT CATALOG_NAME AS DATABASE_NAME, SCHEMA_NAME AS NAME, "
            "SCHEMA_OWNER AS OWNER, IS_TRANSIENT, IS_MANAGED_ACCESS, "
            "RETENTION_TIME, COMMENT, CREATED "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.SCHEMATA "
            "WHERE DELETED IS NULL "
            "AND SCHEMA_NAME <> 'INFORMATION_SCHEMA' "
            "ORDER BY CATALOG_NAME, SCHEMA_NAME"
        ):
            yield Schema(
                database_name=_str(row.get("DATABASE_NAME")) or "<unknown>",
                name=_str(row.get("NAME")) or "<unknown>",
                owner=_str(row.get("OWNER")),
                is_managed_access=_to_bool(row.get("IS_MANAGED_ACCESS")),
                is_transient=_to_bool(row.get("IS_TRANSIENT")),
                retention_time_days=_to_int(row.get("RETENTION_TIME")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED")),
            )

    def iter_tables_from_account_usage(self) -> Iterator[Table]:
        for row in self._rows(
            "SELECT TABLE_CATALOG AS DATABASE_NAME, TABLE_SCHEMA AS SCHEMA_NAME, "
            "TABLE_NAME AS NAME, TABLE_TYPE, IS_TRANSIENT, "
            "CLUSTERING_KEY, ROW_COUNT, BYTES, RETENTION_TIME, COMMENT, CREATED "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES "
            "WHERE DELETED IS NULL "
            "AND TABLE_SCHEMA <> 'INFORMATION_SCHEMA' "
            "ORDER BY TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME"
        ):
            db = _str(row.get("DATABASE_NAME")) or "<unknown>"
            sc = _str(row.get("SCHEMA_NAME")) or "<unknown>"
            name = _str(row.get("NAME")) or "<unknown>"
            kind = _classify_account_usage_table_kind(row)
            support, note = classify_table(kind)
            yield Table(
                database_name=db,
                schema_name=sc,
                name=name,
                full_name=f"{db}.{sc}.{name}",
                kind=kind,
                is_transient=kind == "TRANSIENT" or _to_bool(row.get("IS_TRANSIENT")),
                is_temporary=kind == "TEMPORARY",
                row_count=_to_int(row.get("ROW_COUNT")),
                bytes=_to_int(row.get("BYTES")),
                cluster_by=_str(row.get("CLUSTERING_KEY")),
                retention_time_days=_to_int(row.get("RETENTION_TIME")),
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED")),
                support=support,
                notes=[note] if note else [],
            )

    def iter_views_from_account_usage(self) -> Iterator[Table]:
        for row in self._rows(
            "SELECT TABLE_CATALOG AS DATABASE_NAME, TABLE_SCHEMA AS SCHEMA_NAME, "
            "TABLE_NAME AS NAME, COMMENT, CREATED "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.VIEWS "
            "WHERE DELETED IS NULL "
            "AND TABLE_SCHEMA <> 'INFORMATION_SCHEMA' "
            "ORDER BY TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME"
        ):
            db = _str(row.get("DATABASE_NAME")) or "<unknown>"
            sc = _str(row.get("SCHEMA_NAME")) or "<unknown>"
            name = _str(row.get("NAME")) or "<unknown>"
            support, note = classify_table("VIEW")
            yield Table(
                database_name=db,
                schema_name=sc,
                name=name,
                full_name=f"{db}.{sc}.{name}",
                kind="VIEW",
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED")),
                support=support,
                notes=[note] if note else [],
            )

    def iter_functions_from_account_usage(self) -> Iterator[Routine]:
        for row in self._rows(
            "SELECT FUNCTION_CATALOG AS DATABASE_NAME, "
            "FUNCTION_SCHEMA AS SCHEMA_NAME, FUNCTION_NAME AS NAME, "
            "FUNCTION_LANGUAGE AS LANGUAGE, COMMENT, CREATED "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.FUNCTIONS "
            "WHERE DELETED IS NULL "
            "AND FUNCTION_SCHEMA <> 'INFORMATION_SCHEMA' "
            "ORDER BY FUNCTION_CATALOG, FUNCTION_SCHEMA, FUNCTION_NAME"
        ):
            db = _str(row.get("DATABASE_NAME")) or "<unknown>"
            sc = _str(row.get("SCHEMA_NAME")) or "<unknown>"
            name = _str(row.get("NAME")) or "<unknown>"
            lang = _normalise_routine_language(row.get("LANGUAGE"))
            support, note = classify_routine("FUNCTION", lang)
            yield Routine(
                database_name=db,
                schema_name=sc,
                name=name,
                full_name=f"{db}.{sc}.{name}",
                routine_kind="FUNCTION",
                language=lang,
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED")),
                support=support,
                notes=[note] if note else [],
            )

    def iter_procedures_from_account_usage(self) -> Iterator[Routine]:
        for row in self._rows(
            "SELECT PROCEDURE_CATALOG AS DATABASE_NAME, "
            "PROCEDURE_SCHEMA AS SCHEMA_NAME, PROCEDURE_NAME AS NAME, "
            "PROCEDURE_LANGUAGE AS LANGUAGE, COMMENT, CREATED "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.PROCEDURES "
            "WHERE DELETED IS NULL "
            "AND PROCEDURE_SCHEMA <> 'INFORMATION_SCHEMA' "
            "ORDER BY PROCEDURE_CATALOG, PROCEDURE_SCHEMA, PROCEDURE_NAME"
        ):
            db = _str(row.get("DATABASE_NAME")) or "<unknown>"
            sc = _str(row.get("SCHEMA_NAME")) or "<unknown>"
            name = _str(row.get("NAME")) or "<unknown>"
            lang = _normalise_routine_language(row.get("LANGUAGE"))
            support, note = classify_routine("PROCEDURE", lang)
            yield Routine(
                database_name=db,
                schema_name=sc,
                name=name,
                full_name=f"{db}.{sc}.{name}",
                routine_kind="PROCEDURE",
                language=lang,
                comment=_str(row.get("COMMENT")),
                created_at=_to_datetime(row.get("CREATED")),
                support=support,
                notes=[note] if note else [],
            )

    # ------------------------------------------------------------------ jobs

    def iter_jobs(
        self,
        *,
        lookback_days: int = 28,
        limit: int = 50_000,
    ) -> Iterator[SnowflakeJob]:
        """Pull recent queries from ``SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY``."""
        if lookback_days < 1:
            lookback_days = 1
        if limit < 1:
            limit = 1
        sql = (
            "SELECT QUERY_ID, WAREHOUSE_NAME, WAREHOUSE_SIZE, USER_NAME, ROLE_NAME, "
            "DATABASE_NAME, SCHEMA_NAME, QUERY_TYPE, EXECUTION_STATUS, "
            "START_TIME, END_TIME, TOTAL_ELAPSED_TIME, QUEUED_OVERLOAD_TIME, "
            "COMPILATION_TIME, EXECUTION_TIME, BYTES_SCANNED, BYTES_WRITTEN, "
            "ROWS_PRODUCED, PARTITIONS_SCANNED, PARTITIONS_TOTAL, "
            "CREDITS_USED_CLOUD_SERVICES, ERROR_CODE, ERROR_MESSAGE "
            "FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY "
            f"WHERE START_TIME >= DATEADD(day, -{int(lookback_days)}, CURRENT_TIMESTAMP()) "
            "ORDER BY START_TIME DESC "
            f"LIMIT {int(limit)}"
        )
        for row in self._rows(sql):
            start = _to_datetime(row.get("START_TIME"))
            end = _to_datetime(row.get("END_TIME"))
            duration = None
            if start and end:
                duration = max((end - start).total_seconds(), 0.0)
            status = _str(row.get("EXECUTION_STATUS"))
            yield SnowflakeJob(
                query_id=_str(row.get("QUERY_ID")) or "<unknown>",
                warehouse_name=_str(row.get("WAREHOUSE_NAME")),
                warehouse_size=(
                    _normalise_size(row.get("WAREHOUSE_SIZE"))
                    if row.get("WAREHOUSE_SIZE") else None
                ),
                user_name=_str(row.get("USER_NAME")),
                role_name=_str(row.get("ROLE_NAME")),
                database_name=_str(row.get("DATABASE_NAME")),
                schema_name=_str(row.get("SCHEMA_NAME")),
                query_type=_str(row.get("QUERY_TYPE")),
                execution_status=status,
                outcome=_outcome(status),  # type: ignore[arg-type]
                start_time=start,
                end_time=end,
                duration_seconds=duration,
                total_elapsed_ms=_to_int(row.get("TOTAL_ELAPSED_TIME")),
                queued_overload_ms=_to_int(row.get("QUEUED_OVERLOAD_TIME")),
                compilation_ms=_to_int(row.get("COMPILATION_TIME")),
                execution_ms=_to_int(row.get("EXECUTION_TIME")),
                bytes_scanned=_to_int(row.get("BYTES_SCANNED")),
                bytes_written=_to_int(row.get("BYTES_WRITTEN")),
                rows_produced=_to_int(row.get("ROWS_PRODUCED")),
                partitions_scanned=_to_int(row.get("PARTITIONS_SCANNED")),
                partitions_total=_to_int(row.get("PARTITIONS_TOTAL")),
                credits_used_cloud_services=_to_float(row.get("CREDITS_USED_CLOUD_SERVICES")),
                error_code=_str(row.get("ERROR_CODE")),
                error_message=_str(row.get("ERROR_MESSAGE")),
            )

    # ------------------------------------------------------------------ top-active tables

    def iter_table_usage_from_access_history(
        self,
        *,
        lookback_days: int = 28,
        limit: int = 500,
    ) -> Iterator[SnowflakeTableUsage]:
        """Top-active tables from ``ACCOUNT_USAGE.ACCESS_HISTORY``.

        ACCESS_HISTORY is Enterprise+ only — callers should be ready to
        catch and fall back to a ``QUERY_HISTORY``-based proxy. Counts
        distinct queries (``query_id``) per ``(objectDomain, objectName)``
        flattened from ``BASE_OBJECTS_ACCESSED``. We join ``QUERY_HISTORY``
        to roll up bytes / rows / execution_ms because ACCESS_HISTORY
        does not carry resource fields itself.
        """
        if lookback_days < 1:
            lookback_days = 1
        if limit < 1:
            limit = 1
        sql = (
            "WITH access AS ("
            "  SELECT ah.QUERY_ID, "
            "         obj.value:objectDomain::string AS OBJECT_DOMAIN, "
            "         obj.value:objectName::string   AS OBJECT_NAME "
            "  FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah, "
            "       LATERAL FLATTEN(input => ah.BASE_OBJECTS_ACCESSED) obj "
            f"  WHERE ah.QUERY_START_TIME >= DATEADD(day, -{int(lookback_days)}, CURRENT_TIMESTAMP()) "
            "    AND obj.value:objectDomain::string IN ('Table','View','Materialized view','External table','Iceberg table','Dynamic table') "
            "), qh AS ("
            "  SELECT QUERY_ID, BYTES_SCANNED, ROWS_PRODUCED, EXECUTION_TIME, START_TIME "
            "  FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY "
            f"  WHERE START_TIME >= DATEADD(day, -{int(lookback_days)}, CURRENT_TIMESTAMP()) "
            ") "
            "SELECT a.OBJECT_DOMAIN, a.OBJECT_NAME, "
            "       COUNT(DISTINCT a.QUERY_ID)        AS USAGE_COUNT, "
            "       SUM(COALESCE(q.BYTES_SCANNED, 0)) AS TOTAL_BYTES_SCANNED, "
            "       SUM(COALESCE(q.ROWS_PRODUCED, 0)) AS TOTAL_ROWS_PRODUCED, "
            "       SUM(COALESCE(q.EXECUTION_TIME, 0)) AS TOTAL_EXECUTION_MS, "
            "       MAX(q.START_TIME)                  AS LAST_SEEN "
            "FROM access a LEFT JOIN qh q ON a.QUERY_ID = q.QUERY_ID "
            "GROUP BY 1, 2 "
            "ORDER BY USAGE_COUNT DESC "
            f"LIMIT {int(limit)}"
        )
        for row in self._rows(sql):
            obj_name = _str(row.get("OBJECT_NAME")) or ""
            parts = obj_name.split(".")
            # Snowflake returns ``DB.SCHEMA.OBJECT`` (objectName is
            # fully-qualified); guard against shorter strings just in case.
            if len(parts) == 3:
                db, sc, name = parts
            elif len(parts) == 2:
                db, sc, name = "<unknown>", parts[0], parts[1]
            else:
                db, sc, name = "<unknown>", "<unknown>", obj_name or "<unknown>"
            domain = (_str(row.get("OBJECT_DOMAIN")) or "TABLE").upper().replace(" ", "_")
            yield SnowflakeTableUsage(
                full_name=obj_name or f"{db}.{sc}.{name}",
                database_name=db,
                schema_name=sc,
                object_name=name,
                object_domain=domain,
                usage_count=int(_to_int(row.get("USAGE_COUNT")) or 0),
                total_bytes_scanned=int(_to_int(row.get("TOTAL_BYTES_SCANNED")) or 0),
                total_rows_produced=int(_to_int(row.get("TOTAL_ROWS_PRODUCED")) or 0),
                total_execution_ms=int(_to_int(row.get("TOTAL_EXECUTION_MS")) or 0),
                last_seen=_to_datetime(row.get("LAST_SEEN")),
                source="access_history",
            )


__all__ = ["SnowflakeWorkloadsCollector"]
