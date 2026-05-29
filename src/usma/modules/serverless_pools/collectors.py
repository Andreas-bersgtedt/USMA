"""Collectors for serverless SQL pool analysis."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .models import (
    ExternalDataSource,
    ExternalTable,
    ExternalTableColumn,
    ServerlessCostEstimate,
    ServerlessDailyUsage,
    ServerlessDatabase,
    ServerlessHourlyUsage,
    ServerlessTopQuery,
    ServerlessUsageStat,
)
from .sql_client import ServerlessSqlClient

# List price (USD per TB processed) for serverless SQL pool.
# Override via SMA_SERVERLESS_PRICE_PER_TB env var if your contract differs.
DEFAULT_PRICE_PER_TB_USD = 5.0

log = logging.getLogger(__name__)


def _db_concurrency() -> int:
    """How many user databases to query in parallel inside a serverless collector."""
    try:
        return max(1, int(os.getenv("SMA_SERVERLESS_DB_CONCURRENCY", "4")))
    except ValueError:
        return 4


def _per_db_parallel(
    sql: ServerlessSqlClient,
    databases: list[ServerlessDatabase],
    query_name: str,
    label: str,
) -> list[tuple[str, list[dict]]]:
    """Run ``query_name`` across each non-master DB; return [(db_name, rows), ...].

    Each worker opens its own pyodbc session against ``master\u2192db`` so we get
    one login per database in parallel rather than serial logins.
    """
    targets = [db.name for db in databases if db.name != "master"]
    if not targets:
        return []
    max_workers = min(len(targets), _db_concurrency())

    def _one(db_name: str) -> tuple[str, list[dict]]:
        try:
            client = sql.for_database(db_name)
            with client.session():
                return db_name, client.fetch_query_file(query_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed for %s: %s", label, db_name, exc)
            return db_name, []

    if max_workers <= 1:
        return [_one(db) for db in targets]
    with ThreadPoolExecutor(max_workers=max_workers,
                            thread_name_prefix="sma-srv-db") as ex:
        return list(ex.map(_one, targets))


def collect_databases(sql: ServerlessSqlClient) -> list[ServerlessDatabase]:
    rows = sql.fetch_query_file("databases")
    return [
        ServerlessDatabase(
            name=r["name"],
            collation=r.get("collation_name"),
            create_date=r.get("create_date"),
        )
        for r in rows
    ]


def collect_external_data_sources(sql: ServerlessSqlClient, databases: list[ServerlessDatabase]) -> list[ExternalDataSource]:
    out: list[ExternalDataSource] = []
    for db_name, rows in _per_db_parallel(sql, databases, "external_data_sources", "external_data_sources"):
        for r in rows:
            out.append(ExternalDataSource(database=db_name, name=r["name"],
                                          location=r.get("location"), type=r.get("type_desc")))
    return out


def collect_external_tables(sql: ServerlessSqlClient, databases: list[ServerlessDatabase]) -> list[ExternalTable]:
    out: list[ExternalTable] = []
    for db_name, rows in _per_db_parallel(sql, databases, "external_tables", "external_tables"):
        for r in rows:
            out.append(ExternalTable(
                database=db_name,
                schema_name=r["schema_name"],
                table_name=r["table_name"],
                data_source=r.get("data_source"),
                file_format=r.get("file_format"),
                location=r.get("location"),
            ))
    return out


def collect_usage(sql: ServerlessSqlClient) -> list[ServerlessUsageStat]:
    rows = sql.fetch_query_file("usage")
    return [
        ServerlessUsageStat(
            metric=r["metric"],
            value=r.get("value"),
            unit=r.get("unit"),
            captured_at=r.get("captured_at") or datetime.now(timezone.utc),
        )
        for r in rows
    ]


def collect_top_queries(sql: ServerlessSqlClient) -> list[ServerlessTopQuery]:
    rows = sql.fetch_query_file("top_queries")
    out: list[ServerlessTopQuery] = []
    for r in rows:
        out.append(ServerlessTopQuery(
            request_id=str(r["request_id"]) if r.get("request_id") is not None else None,
            login_name=r.get("login_name"),
            start_time=r.get("start_time"),
            end_time=r.get("end_time"),
            duration_seconds=r.get("duration_seconds"),
            status=r.get("status"),
            error_code=str(r["error_code"]) if r.get("error_code") is not None else None,
            data_processed_mb=int(r["data_processed_mb"]) if r.get("data_processed_mb") is not None else None,
            command_text=(r.get("command_text") or "")[:4000] or None,
        ))
    return out


def collect_daily_usage(sql: ServerlessSqlClient) -> list[ServerlessDailyUsage]:
    rows = sql.fetch_query_file("data_processed")
    out: list[ServerlessDailyUsage] = []
    for r in rows:
        day = r.get("day")
        out.append(ServerlessDailyUsage(
            day=day.isoformat() if hasattr(day, "isoformat") else str(day),
            request_count=int(r.get("request_count") or 0),
            data_processed_mb=int(r.get("data_processed_mb") or 0),
            duration_seconds=int(r.get("duration_seconds") or 0),
            mb_seconds=int(r.get("mb_seconds") or 0),
        ))
    return out


def collect_hourly_usage(sql: ServerlessSqlClient) -> list[ServerlessHourlyUsage]:
    """Per-UTC-hour usage rollup over the trailing 24 hours.

    Mirrors :func:`collect_daily_usage` but at hourly granularity. The
    query returns at most 24 rows so the chart can pre-seed empty bins
    on the frontend without overloading the SQL endpoint.
    """
    rows = sql.fetch_query_file("data_processed_hourly")
    out: list[ServerlessHourlyUsage] = []
    for r in rows:
        hour = r.get("hour")
        if hasattr(hour, "isoformat"):
            try:
                hour_utc = (
                    hour.replace(tzinfo=timezone.utc)
                    if hour.tzinfo is None
                    else hour.astimezone(timezone.utc)
                )
                hour_iso = hour_utc.isoformat().replace("+00:00", "Z")
            except Exception:  # noqa: BLE001
                hour_iso = str(hour)
        else:
            hour_iso = str(hour)
        out.append(ServerlessHourlyUsage(
            hour=hour_iso,
            request_count=int(r.get("request_count") or 0),
            data_processed_mb=int(r.get("data_processed_mb") or 0),
            duration_seconds=int(r.get("duration_seconds") or 0),
            mb_seconds=int(r.get("mb_seconds") or 0),
        ))
    # Sort chronologically so the chart reads left-to-right without a
    # frontend resort step.
    out.sort(key=lambda u: u.hour)
    return out


def estimate_cost(daily: list[ServerlessDailyUsage], price_per_tb_usd: float = DEFAULT_PRICE_PER_TB_USD) -> ServerlessCostEstimate:
    total_mb = sum(d.data_processed_mb for d in daily)
    total_tb = total_mb / 1_048_576  # 1024^2 MB per TB
    return ServerlessCostEstimate(
        window_days=len(daily),
        total_data_processed_tb=round(total_tb, 4),
        list_price_usd_per_tb=price_per_tb_usd,
        estimated_cost_usd=round(total_tb * price_per_tb_usd, 2),
        notes="Estimate based on sys.dm_exec_requests_history; check Azure Pricing for your region.",
    )


# --- v2 -----------------------------------------------------------------------

def collect_external_table_columns(
    sql: ServerlessSqlClient, databases: list[ServerlessDatabase]
) -> list[ExternalTableColumn]:
    """Collect column projections for external tables across user databases.

    Wrapped per-database in try/except — older serverless preview versions don't
    expose `sys.external_tables` columns the same way and that's fine.
    """
    out: list[ExternalTableColumn] = []
    for db_name, rows in _per_db_parallel(sql, databases, "external_table_columns", "external_table_columns"):
        for r in rows:
            out.append(ExternalTableColumn(
                database=r.get("database_name") or db_name,
                schema_name=r["schema_name"],
                table_name=r["table_name"],
                column_name=r["column_name"],
                data_type=r.get("data_type"),
                max_length=r.get("max_length"),
                precision=r.get("precision"),
                scale=r.get("scale"),
                is_nullable=bool(r.get("is_nullable")) if r.get("is_nullable") is not None else True,
                column_id=r.get("column_id"),
            ))
    return out
