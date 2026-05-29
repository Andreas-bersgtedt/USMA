"""GCP / BigQuery cost client — reads the BigQuery billing-export tables.

Mirrors :mod:`cost_client` (which targets Azure Cost Management) so the
``CostAnalyzer`` can dispatch on scope type and re-use the existing
aggregation, rules, and Fabric-comparison machinery.

Wiring
------
GCP customers enable a billing export to BigQuery which lands daily
rows in a table named ``gcp_billing_export_v1_<BILLING_ACCOUNT_ID>``
(or ``..._resource_v1_...`` for the resource-grained export). The
table layout is documented at
https://cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage.

We only need the standard export (per-SKU rollup is enough for monthly
attribution + Fabric comparison) so the SQL stays simple:

.. code-block:: sql

    SELECT
      FORMAT_TIMESTAMP('%Y-%m', usage_start_time) AS month,
      service.description                          AS service,
      sku.description                              AS sku,
      project.id                                   AS project_id,
      currency,
      SUM(cost)                                    AS cost,
      SUM(usage.amount)                            AS usage_quantity,
      ANY_VALUE(usage.unit)                        AS usage_unit
    FROM `<billing_dataset>.<billing_table>`
    WHERE project.id = @project_id
      AND usage_start_time >= @window_start
      AND usage_start_time <  @window_end
    GROUP BY month, service, sku, project_id, currency

Required env vars (all read at call time):
    * ``SMA_GCP_BILLING_DATASET`` — fully-qualified ``project.dataset``
      that hosts the billing export. May omit ``project.`` if the
      hosting project equals ``SMA_GCP_PROJECT_ID``.
    * ``SMA_GCP_BILLING_TABLE`` — table name. Defaults to
      ``gcp_billing_export_v1_<SMA_GCP_BILLING_ACCOUNT>`` when
      ``SMA_GCP_BILLING_ACCOUNT`` is set (hyphens replaced with
      underscores per Google's naming).

Honors ``SMA_COST_DISABLE_LIVE`` (same env var as the Azure client) so
offline test runs never hit BigQuery.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from ...sources import SourceDescriptor
from .models import MonthlyCostRow

log = logging.getLogger(__name__)


def _live_disabled() -> bool:
    return os.getenv("SMA_COST_DISABLE_LIVE", "").strip() in {"1", "true", "yes"}


def _resolve_billing_table(project_id: str) -> tuple[str, str] | None:
    """Return ``(dataset_fqn, table_name)`` or ``None`` if not configured.

    ``dataset_fqn`` is ``<billing_project>.<dataset>``; ``table_name``
    is just the table (BigQuery's ``query`` API takes the fully-
    qualified ``project.dataset.table`` in the SQL itself).
    """
    dataset = (os.getenv("SMA_GCP_BILLING_DATASET") or "").strip()
    if not dataset:
        return None
    if "." not in dataset:
        dataset = f"{project_id}.{dataset}"

    table = (os.getenv("SMA_GCP_BILLING_TABLE") or "").strip()
    if not table:
        account = (os.getenv("SMA_GCP_BILLING_ACCOUNT") or "").strip()
        if not account:
            return None
        table = f"gcp_billing_export_v1_{account.replace('-', '_').upper()}"
    return dataset, table


class GcpCostClient:
    """Thin wrapper around ``bigquery.Client.query`` for billing-export rows."""

    def __init__(self, descriptor: SourceDescriptor) -> None:
        if not descriptor.id:
            raise ValueError("GcpCostClient requires a descriptor with a project id")
        self._project_id = descriptor.id
        self._descriptor = descriptor

    def fetch_monthly_breakdown(
        self,
        start: datetime,
        end: datetime,
    ) -> list[MonthlyCostRow]:
        rows, _ = self.fetch_monthly_breakdown_with_status(start, end)
        return rows

    def fetch_monthly_breakdown_with_status(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[MonthlyCostRow], str]:
        """Return ``(rows, status)``.

        Status values mirror the Azure client's vocabulary:
        ``ok`` | ``sdk_missing`` | ``live_disabled`` | ``missing_config``
        | ``empty_window`` | ``error``.
        """
        if _live_disabled():
            return [], "live_disabled"

        table_ref = _resolve_billing_table(self._project_id)
        if table_ref is None:
            log.info(
                "GCP billing export not configured "
                "(SMA_GCP_BILLING_DATASET / SMA_GCP_BILLING_TABLE / "
                "SMA_GCP_BILLING_ACCOUNT missing); skipping cost fetch"
            )
            return [], "missing_config"

        try:  # pragma: no cover - exercised only when SDK is installed
            from google.cloud import bigquery
        except ImportError:
            log.debug("google-cloud-bigquery not installed; skipping cost collect")
            return [], "sdk_missing"

        dataset_fqn, table_name = table_ref
        billing_project, dataset_id = dataset_fqn.split(".", 1)
        sql = f"""
            SELECT
              FORMAT_TIMESTAMP('%Y-%m', usage_start_time) AS month,
              service.description AS service,
              sku.description AS sku,
              project.id AS project_id,
              currency,
              SUM(cost) AS cost,
              SUM(usage.amount) AS usage_quantity,
              ANY_VALUE(usage.unit) AS usage_unit
            FROM `{billing_project}.{dataset_id}.{table_name}`
            WHERE project.id = @project_id
              AND usage_start_time >= @window_start
              AND usage_start_time <  @window_end
            GROUP BY month, service, sku, project_id, currency
            ORDER BY month, service, sku
        """.strip()

        client = bigquery.Client(project=billing_project)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("project_id", "STRING", self._project_id),
                bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", start),
                bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", end),
            ]
        )
        try:
            iterator = client.query(sql, job_config=job_config).result()
        except Exception as exc:  # noqa: BLE001
            log.warning("BigQuery billing-export query failed: %s", exc)
            raise

        rows = _parse_billing_rows(iterator)
        return rows, ("ok" if rows else "empty_window")


def _parse_billing_rows(iterator: Any) -> list[MonthlyCostRow]:
    """Convert a ``RowIterator`` (or any iterable of mapping-like rows) to ours."""
    out: list[MonthlyCostRow] = []
    for row in iterator:
        record = _row_to_dict(row)
        cost = float(record.get("cost") or 0.0)
        usage = float(record.get("usage_quantity") or 0.0)
        currency = str(record.get("currency") or "USD")
        month = str(record.get("month") or "")
        service = (record.get("service") or "").strip() or None
        sku = (record.get("sku") or "").strip() or None
        project_id = (record.get("project_id") or "").strip() or None
        kind = _classify_service(service)
        out.append(
            MonthlyCostRow(
                month=month,
                resource_kind=kind,
                resource_name=project_id,
                sku=sku or service,
                cost=cost,
                currency=currency,
                usage_quantity=usage,
                usage_unit=record.get("usage_unit"),
            )
        )
    return out


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Best-effort: support ``bigquery.Row`` (``.items()``), dict, and tuples."""
    if isinstance(row, dict):
        return row
    items = getattr(row, "items", None)
    if callable(items):
        try:
            return dict(items())
        except Exception:  # noqa: BLE001
            pass
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            return {k: row[k] for k in keys()}  # type: ignore[index]
        except Exception:  # noqa: BLE001
            pass
    return {}


def _classify_service(service: str | None) -> str:
    """Map a GCP service.description to one of our normalized ``resource_kind`` buckets."""
    s = (service or "").lower()
    if "bigquery" in s:
        return "bigquery_project"
    if "dataflow" in s:
        return "dataflow_job"
    if "dataproc" in s:
        return "dataproc_cluster"
    if "cloud storage" in s or s == "storage":
        return "storage"
    if "compute engine" in s:
        return "compute"
    return "other"
