"""Orchestrator for the security module (mid-term v0)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from . import rules as _rules
from .arm_client import SecurityArmClient
from .models import SecurityAnalysis

log = logging.getLogger(__name__)


class SecurityAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._cfg = cfg
        self._client = SecurityArmClient(cfg.azure)
        self._progress = progress or NullProgress()

    def run(self) -> SecurityAnalysis:
        result = SecurityAnalysis(
            workspace_name=self._cfg.azure.workspace_name,
            subscription_id=self._cfg.azure.subscription_id,
            resource_group=self._cfg.azure.resource_group,
            generated_at=datetime.now(timezone.utc),
        )

        # 6 sub-tasks: workspace_settings, firewall_rules, aad_admins,
        # pool_tde, credentials, rules.
        self._progress.start(6, label="security discovery")

        try:
            result.workspace_settings = self._client.fetch_workspace_settings()
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch_workspace_settings failed: %s", exc)
            result.errors.append(format_error("workspace_settings", exc))
        self._progress.step(label="workspace_settings")

        try:
            result.firewall_rules = self._client.list_firewall_rules()
        except Exception as exc:  # noqa: BLE001
            log.warning("list_firewall_rules failed: %s", exc)
            result.errors.append(format_error("firewall_rules", exc))
        self._progress.step(label="firewall_rules")

        try:
            result.aad_admins = self._client.fetch_aad_admins()
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch_aad_admins failed: %s", exc)
            result.errors.append(format_error("aad_admins", exc))
        self._progress.step(label="aad_admins")

        try:
            result.pool_tde_status = self._client.list_pool_tde_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("list_pool_tde_status failed: %s", exc)
            result.errors.append(format_error("pool_tde", exc))
        self._progress.step(label="pool_tde")

        # Credentials inventory: pull linked-service payloads live from the
        # Synapse Artifacts plane, then run the pure classifier.
        try:
            ls_payloads = self._client.iter_linked_service_payloads()
            result.credentials = self._client.list_credentials_inventory(
                linked_services=ls_payloads
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("list_credentials_inventory failed: %s", exc)
            result.errors.append(format_error("credentials", exc))
        self._progress.step(label="credentials")

        try:
            result.findings = _rules.evaluate(result)
        except Exception as exc:  # noqa: BLE001
            log.warning("rules.evaluate failed: %s", exc)
            result.errors.append(format_error("rules", exc))
        self._progress.step(label="rules")

        return result
