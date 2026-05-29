"""Orchestrator for the serverless SQL pool module."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from ...config import AppConfig
from ...progress import NullProgress, ProgressReporter
from . import collectors as col
from . import cost_attribution
from .models import ServerlessAnalysis, StorageAccountUsage
from .sql_client import ServerlessSqlClient

log = logging.getLogger(__name__)


class ServerlessPoolsAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._cfg = cfg
        self._progress = progress or NullProgress()

    def run(self) -> ServerlessAnalysis:
        endpoint = ServerlessSqlClient.endpoint_fqdn(self._cfg.azure.workspace_name)
        result = ServerlessAnalysis(
            workspace_name=self._cfg.azure.workspace_name,
            subscription_id=self._cfg.azure.subscription_id,
            resource_group=self._cfg.azure.resource_group,
            endpoint_fqdn=endpoint,
            generated_at=datetime.now(timezone.utc),
        )
        sql = ServerlessSqlClient(self._cfg, endpoint, "master")

        # 7 master-scoped sub-tasks: databases, external_data_sources,
        # external_tables, external_table_columns, usage, top_queries,
        # daily_usage; plus 1 cost attribution rollup.
        self._progress.start(8, label="discovering serverless databases")

        # All master-scoped queries (`databases`, `usage`, `top_queries`,
        # `data_processed`) reuse one connection. Per-database collectors below
        # open their own connections in parallel via `for_database(...).session()`.
        with sql.session():
            try:
                result.databases = col.collect_databases(sql)
            except Exception as exc:  # noqa: BLE001
                log.warning("databases failed: %s", exc)
                result.errors.append(f"databases: {exc}")
            self._progress.step(label="databases")

            if result.databases:
                try:
                    result.external_data_sources = col.collect_external_data_sources(sql, result.databases)
                except Exception as exc:  # noqa: BLE001
                    log.warning("external_data_sources failed: %s", exc)
                    result.errors.append(f"external_data_sources: {exc}")
                self._progress.step(label="external_data_sources")

                try:
                    result.external_tables = col.collect_external_tables(sql, result.databases)
                except Exception as exc:  # noqa: BLE001
                    log.warning("external_tables failed: %s", exc)
                    result.errors.append(f"external_tables: {exc}")
                self._progress.step(label="external_tables")

                # v2 — column projections for external tables.
                try:
                    result.external_table_columns = col.collect_external_table_columns(sql, result.databases)
                except Exception as exc:  # noqa: BLE001
                    log.warning("external_table_columns failed: %s", exc)
                    result.errors.append(f"external_table_columns: {exc}")
                self._progress.step(label="external_table_columns")
            else:
                # No databases — still consume the 3 budgeted sub-steps so
                # totals reflect reality.
                self._progress.step(3, label="no user databases")

            try:
                result.usage = col.collect_usage(sql)
            except Exception as exc:  # noqa: BLE001
                log.warning("usage failed: %s", exc)
                result.errors.append(f"usage: {exc}")
            self._progress.step(label="usage")

            try:
                result.top_queries = col.collect_top_queries(sql)
            except Exception as exc:  # noqa: BLE001
                log.warning("top_queries failed: %s", exc)
                result.errors.append(f"top_queries: {exc}")
            self._progress.step(label="top_queries")

            try:
                result.daily_usage = col.collect_daily_usage(sql)
                price = float(os.getenv("SMA_SERVERLESS_PRICE_PER_TB", str(col.DEFAULT_PRICE_PER_TB_USD)))
                result.cost_estimate = col.estimate_cost(result.daily_usage, price_per_tb_usd=price)
            except Exception as exc:  # noqa: BLE001
                log.warning("daily_usage / cost estimate failed: %s", exc)
                result.errors.append(f"daily_usage: {exc}")
            self._progress.step(label="daily_usage")

            try:
                result.hourly_usage = col.collect_hourly_usage(sql)
            except Exception as exc:  # noqa: BLE001
                log.warning("hourly_usage failed: %s", exc)
                result.errors.append(f"hourly_usage: {exc}")
            self._progress.step(label="hourly_usage")

        # v2 — per-storage-account attribution. Pure-python over what we already collected.
        try:
            price = float(os.getenv("SMA_SERVERLESS_PRICE_PER_TB", str(col.DEFAULT_PRICE_PER_TB_USD)))
            attributions = cost_attribution.attribute(
                top_queries=[q.model_dump() for q in result.top_queries],
                external_data_sources=[e.model_dump() for e in result.external_data_sources],
                list_price_usd_per_tb=price,
            )
            result.storage_account_usage = [
                StorageAccountUsage(
                    storage_account=a.storage_account,
                    query_count=a.query_count,
                    data_processed_mb=a.data_processed_mb,
                    estimated_cost_usd=a.estimated_cost_usd,
                )
                for a in attributions
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("storage_account_usage failed: %s", exc)
            result.errors.append(f"storage_account_usage: {exc}")
        self._progress.step(label="storage_account_usage")

        return result
