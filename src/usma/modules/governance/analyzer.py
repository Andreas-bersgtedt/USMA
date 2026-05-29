"""Orchestrator for the governance module (mid-term v0)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from .arm_client import GovernanceArmClient
from .models import GovernanceAnalysis
from .rules import evaluate as evaluate_findings

log = logging.getLogger(__name__)


class GovernanceAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._cfg = cfg
        self._client = GovernanceArmClient(cfg.azure)
        self._progress = progress or NullProgress()

    def run(self) -> GovernanceAnalysis:
        result = GovernanceAnalysis(
            workspace_name=self._cfg.azure.workspace_name,
            subscription_id=self._cfg.azure.subscription_id,
            resource_group=self._cfg.azure.resource_group,
            generated_at=datetime.now(timezone.utc),
            purview_account=os.getenv("SMA_PURVIEW_ACCOUNT") or None,
        )

        # Scope set: subscription, RG, workspace.
        sub = f"/subscriptions/{self._cfg.azure.subscription_id}"
        rg = f"{sub}/resourceGroups/{self._cfg.azure.resource_group}"
        ws = (
            f"{rg}/providers/Microsoft.Synapse/workspaces/"
            f"{self._cfg.azure.workspace_name}"
        )
        scopes = [sub, rg, ws]

        # 5 sub-tasks: rbac, mpe, cmk, purview, rules.
        self._progress.start(5, label="governance discovery")

        try:
            result.role_assignments = self._client.list_role_assignments(scopes=scopes)
        except Exception as exc:  # noqa: BLE001
            log.warning("list_role_assignments failed: %s", exc)
            result.errors.append(format_error("rbac", exc))
        self._progress.step(label="rbac")

        try:
            result.managed_private_endpoints = self._client.list_managed_private_endpoints()
        except Exception as exc:  # noqa: BLE001
            log.warning("list_managed_private_endpoints failed: %s", exc)
            result.errors.append(format_error("managed_private_endpoints", exc))
        self._progress.step(label="managed_private_endpoints")

        try:
            result.customer_managed_keys = self._client.collect_customer_managed_keys()
        except Exception as exc:  # noqa: BLE001
            log.warning("collect_customer_managed_keys failed: %s", exc)
            result.errors.append(format_error("cmk", exc))
        self._progress.step(label="customer_managed_keys")

        try:
            result.purview_lineage = self._client.collect_purview_lineage(
                result.purview_account
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("collect_purview_lineage failed: %s", exc)
            result.errors.append(format_error("purview", exc))
        self._progress.step(label="purview")

        # Pure-Python rules engine — never raises against a partial result.
        try:
            result.findings = evaluate_findings(result)
        except Exception as exc:  # noqa: BLE001
            log.warning("governance rules evaluation failed: %s", exc)
            result.errors.append(format_error("rules", exc))
        self._progress.step(label="rules")

        return result
