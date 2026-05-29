"""ADF run-history client (paginated pipeline + activity runs).

Mirrors :class:`run_history_client.RunHistoryClient` for the
``DataFactoryManagementClient`` SDK so the analyzer can collect
``PipelineRunHistory`` for ADF factories identically to Synapse
workspaces. The SDK models share the same names (``RunFilterParameters``,
``RunQueryFilter``…) under ``azure.mgmt.datafactory.models``; only the
client object and the operation names differ
(``pipeline_runs.query_by_factory`` instead of
``pipeline_run.query_pipeline_runs_by_workspace`` etc.).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterator

from azure.mgmt.datafactory.models import (
    RunFilterParameters,
    RunQueryFilter,
    RunQueryFilterOperand,
    RunQueryFilterOperator,
    RunQueryOrder,
    RunQueryOrderBy,
    RunQueryOrderByField,
)

from ...sources.adf.provider import AdfClientBundle

log = logging.getLogger(__name__)

# Same hard cap as the Synapse client to keep memory bounded.
_HARD_RUN_LIMIT = 100_000


class AdfRunHistoryClient:
    """Wrap the ADF management client's run-query operations with
    continuation paging — drop-in replacement for
    :class:`RunHistoryClient` when the analyzer is wired to an ADF
    collector.
    """

    def __init__(self, bundle: AdfClientBundle) -> None:
        self._bundle = bundle
        self._mgmt: Any = None

    def _client(self) -> Any:
        if self._mgmt is None:
            from azure.mgmt.datafactory import DataFactoryManagementClient

            self._mgmt = DataFactoryManagementClient(
                self._bundle.credential, self._bundle.subscription_id,
            )
        return self._mgmt

    def _factory_args(self) -> tuple[str, str]:
        return self._bundle.resource_group, self._bundle.factory_name

    def iter_pipeline_runs(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        pipeline_names: list[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        emitted = 0
        cap = min(limit, _HARD_RUN_LIMIT)
        token: str | None = None
        order_by = [RunQueryOrderBy(
            order_by=RunQueryOrderByField.RUN_START,
            order=RunQueryOrder.DESC,
        )]
        filters: list[RunQueryFilter] | None = None
        if pipeline_names:
            filters = [RunQueryFilter(
                operand=RunQueryFilterOperand.PIPELINE_NAME,
                operator=RunQueryFilterOperator.IN,
                values=list(pipeline_names),
            )]
        rg, factory = self._factory_args()
        mgmt = self._client()
        while True:
            params = RunFilterParameters(
                last_updated_after=start,
                last_updated_before=end,
                continuation_token=token,
                filters=filters,
                order_by=order_by,
            )
            resp = mgmt.pipeline_runs.query_by_factory(rg, factory, params)
            for run in (resp.value or []):
                yield _run_to_dict(run)
                emitted += 1
                if emitted >= cap:
                    return
            token = getattr(resp, "continuation_token", None)
            if not token:
                return

    def count_pipeline_runs(
        self,
        *,
        start: datetime,
        end: datetime,
        status: str,
    ) -> int:
        total = 0
        token: str | None = None
        filters = [RunQueryFilter(
            operand=RunQueryFilterOperand.STATUS,
            operator=RunQueryFilterOperator.EQUALS,
            values=[status],
        )]
        order_by = [RunQueryOrderBy(
            order_by=RunQueryOrderByField.RUN_START,
            order=RunQueryOrder.DESC,
        )]
        rg, factory = self._factory_args()
        mgmt = self._client()
        while True:
            params = RunFilterParameters(
                last_updated_after=start,
                last_updated_before=end,
                continuation_token=token,
                filters=filters,
                order_by=order_by,
            )
            resp = mgmt.pipeline_runs.query_by_factory(rg, factory, params)
            page = resp.value or []
            total += len(page)
            token = getattr(resp, "continuation_token", None)
            if not token:
                return total
            if total >= _HARD_RUN_LIMIT:
                log.warning(
                    "count_pipeline_runs hit hard cap %d (status=%s)",
                    _HARD_RUN_LIMIT, status,
                )
                return total

    def iter_activity_runs(
        self,
        *,
        pipeline_name: str,
        run_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> Iterator[dict[str, Any]]:
        emitted = 0
        cap = min(limit, _HARD_RUN_LIMIT)
        token: str | None = None
        rg, factory = self._factory_args()
        mgmt = self._client()
        while True:
            params = RunFilterParameters(
                last_updated_after=start,
                last_updated_before=end,
                continuation_token=token,
            )
            resp = mgmt.activity_runs.query_by_pipeline_run(
                rg, factory, run_id, params,
            )
            for ar in (resp.value or []):
                yield _activity_run_to_dict(ar)
                emitted += 1
                if emitted >= cap:
                    return
            token = getattr(resp, "continuation_token", None)
            if not token:
                return


def _run_to_dict(run: Any) -> dict[str, Any]:
    return {
        "run_id": getattr(run, "run_id", None),
        "pipeline_name": getattr(run, "pipeline_name", None),
        "status": getattr(run, "status", None),
        "run_start": getattr(run, "run_start", None),
        "run_end": getattr(run, "run_end", None),
        "duration_in_ms": getattr(run, "duration_in_ms", None),
        "is_latest": getattr(run, "is_latest", None),
    }


def _activity_run_to_dict(ar: Any) -> dict[str, Any]:
    output = getattr(ar, "output", None)
    if output is not None and not isinstance(output, dict):
        as_dict = getattr(output, "as_dict", None)
        if callable(as_dict):
            try:
                output = as_dict()
            except Exception:  # noqa: BLE001
                output = None
        elif not isinstance(output, dict):
            output = None
    return {
        "activity_name": getattr(ar, "activity_name", None),
        "activity_type": getattr(ar, "activity_type", None),
        "status": getattr(ar, "status", None),
        "activity_run_id": getattr(ar, "activity_run_id", None),
        "duration_in_ms": getattr(ar, "duration_in_ms", None),
        "output": output if isinstance(output, dict) else {},
    }
