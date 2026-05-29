"""Orchestrator for the cost module (mid-term v0)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from ...sources import SourceType
from . import cost_client as _client
from . import fabric_compare
from . import rules as _rules
from .cost_client import CostClient, default_window
from .gcp_cost_client import GcpCostClient
from .snowflake_cost_client import SnowflakeCostClient
from .models import CostAnalysis

log = logging.getLogger(__name__)


class CostAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._cfg = cfg
        scope = cfg.primary_scope()
        self._scope = scope
        # Databricks-on-AWS / Databricks-on-GCP have no Azure
        # subscription, so Azure Cost Management can't be queried for
        # them. The run still emits a cost.json/.md/.html (with an
        # empty-but-explanatory body) so downstream readers don't 404.
        # Detect via ``extras['platform']`` (set by
        # :class:`DatabricksAwsProvider` / GCP equivalent); legacy
        # Azure-Databricks descriptors carry no key and stay on the
        # Azure path.
        platform = (
            (scope.extras.get("platform") if scope is not None else None) or "azure"
        ).lower()
        self._is_non_azure_databricks = (
            scope is not None
            and scope.type == SourceType.DATABRICKS
            and platform in ("aws", "gcp")
        )
        if self._is_non_azure_databricks:
            self._cc = None  # type: ignore[assignment]
        elif scope is not None and scope.type == SourceType.BIGQUERY:
            self._cc: CostClient | GcpCostClient | SnowflakeCostClient = GcpCostClient(scope)
        elif scope is not None and scope.type == SourceType.SNOWFLAKE:
            self._cc = SnowflakeCostClient(scope)
        else:
            self._cc = CostClient(cfg.azure)
        self._progress = progress or NullProgress()

    def run(self) -> CostAnalysis:
        months = int(os.getenv("SMA_COST_MONTHS", "3"))
        start, end = default_window(months=months)

        scope = self._scope
        if scope is not None and scope.type == SourceType.BIGQUERY:
            workspace_name = scope.display_name or scope.id
            subscription_id = scope.id
            resource_group = ""
        elif scope is not None and scope.type == SourceType.SNOWFLAKE:
            workspace_name = scope.display_name or scope.id
            subscription_id = scope.id
            resource_group = ""
        elif self._is_non_azure_databricks and scope is not None:
            workspace_name = scope.display_name or scope.id
            subscription_id = ""
            resource_group = ""
        else:
            workspace_name = self._cfg.azure.workspace_name
            subscription_id = self._cfg.azure.subscription_id
            resource_group = self._cfg.azure.resource_group

        result = CostAnalysis(
            workspace_name=workspace_name,
            subscription_id=subscription_id,
            resource_group=resource_group,
            generated_at=datetime.now(timezone.utc),
            window_start=start,
            window_end=end,
            source_type=(
                scope.type.value if scope is not None and hasattr(scope.type, "value") else "azure"
            ),
        )

        # Short-circuit for non-Azure Databricks: no Azure Cost
        # Management to call, no GCP billing-export wired up. Emit an
        # explanatory "skipped" status and walk the progress bar to the
        # end so the module finishes ``ok`` with no spurious errors.
        if self._is_non_azure_databricks:
            from .models import CostFinding

            self._progress.start(4, label="cost discovery")
            result.collection_status = "skipped"
            platform_label = (
                scope.extras.get("platform", "non-azure").upper()
                if scope is not None
                else "non-azure"
            )
            result.findings.append(CostFinding(
                rule_id="cost.skipped_non_azure",
                severity="info",
                title=f"Cost collection skipped (Databricks on {platform_label})",
                detail=(
                    "Azure Cost Management is only available for Azure "
                    "subscriptions. Databricks-on-"
                    f"{platform_label} workspaces have no associated "
                    "Azure scope, and a native billing integration "
                    "(AWS Cost Explorer / GCP Billing Export) is not "
                    "yet wired up in this build. Other modules "
                    "(workflows, fabric_mapping) still run normally."
                ),
                resource=None,
            ))
            for label in ("monthly_breakdown", "aggregate", "fabric_compare", "rules"):
                self._progress.step(label=label)
            return result

        # 4 sub-tasks: fetch rows, aggregate, fabric compare, rules.
        self._progress.start(4, label="cost discovery")

        try:
            result.rows, result.collection_status = (
                self._cc.fetch_monthly_breakdown_with_status(start, end)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch_monthly_breakdown failed: %s", exc)
            result.errors.append(format_error("monthly_breakdown", exc))
            result.collection_status = "error"
        # Narrow Azure rows to this scope's own ARM id so an ADF factory
        # that shares an RG with a Synapse workspace gets *its own* cost
        # totals rather than the RG-wide rollup. BigQuery cost rows come
        # from the GCP billing-export client and are already
        # project-scoped, so skip filtering for them.
        if (
            scope is not None
            and scope.type != SourceType.BIGQUERY
            and scope.type != SourceType.SNOWFLAKE
            and getattr(scope, "id", None)
            and result.rows
        ):
            result.rows = _client.filter_rows_for_scope(result.rows, scope.id)
        self._progress.step(label="monthly_breakdown")

        try:
            result.monthly_totals, result.by_resource_kind = _client.aggregate_rows(result.rows)
            result.by_resource_name = _client.aggregate_by_resource_name(result.rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("aggregate_rows failed: %s", exc)
            result.errors.append(format_error("aggregate", exc))
        self._progress.step(label="aggregate")

        # Optional: compare against the Fabric CU projection from the
        # fabric_mapping module (loaded from fabric_mapping.json if present).
        try:
            avg = _client.latest_monthly_cost(result.monthly_totals)
            projection = _load_fabric_projection(self._cfg.output_dir)
            result.fabric_comparison = fabric_compare.compare_to_fabric(avg, projection)
        except Exception as exc:  # noqa: BLE001
            log.warning("fabric comparison failed: %s", exc)
            result.errors.append(format_error("fabric_compare", exc))
        self._progress.step(label="fabric_compare")

        try:
            result.findings = _rules.evaluate(result)
        except Exception as exc:  # noqa: BLE001
            log.warning("rules.evaluate failed: %s", exc)
            result.errors.append(format_error("rules", exc))
        self._progress.step(label="rules")

        return result


def _load_fabric_projection(out_dir: Path) -> dict | None:
    path = Path(out_dir) / "fabric_mapping.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload.get("cu_projection") or payload.get("capacity_projection")
