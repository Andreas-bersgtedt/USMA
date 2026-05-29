"""Synapse Artifacts run-history client (paginated pipeline + activity runs)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterator

from azure.synapse.artifacts import ArtifactsClient
from azure.synapse.artifacts.models import (
    RunFilterParameters,
    RunQueryFilter,
    RunQueryFilterOperand,
    RunQueryFilterOperator,
    RunQueryOrder,
    RunQueryOrderBy,
    RunQueryOrderByField,
)

from ...auth import get_credential
from ...config import AzureConfig

log = logging.getLogger(__name__)

# Hard upper bound on total runs / activity runs we'll buffer in memory, even
# when the env-var override is large. Protects against accidental DoS on huge
# workspaces. (Each run is small; ~50k * ~1KB = ~50MB.)
_HARD_RUN_LIMIT = 100_000


class RunHistoryClient:
    """Wrap the Artifacts ``pipeline_run`` operations with continuation paging."""

    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        endpoint = f"https://{azure.workspace_name}.dev.azuresynapse.net"
        cred = get_credential(azure)
        self._artifacts = ArtifactsClient(endpoint=endpoint, credential=cred)

    def iter_pipeline_runs(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        pipeline_names: list[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield one dict per pipeline run between ``start`` and ``end``.

        Stops after ``limit`` runs (set ``RunHistoryResult.truncated``).
        Continuation tokens are followed transparently. Results are ordered
        by ``RunStart`` descending so that, when ``limit`` is hit, the
        oldest runs are the ones dropped.

        ``pipeline_names`` optionally restricts the query server-side via a
        ``PipelineName In (...)`` filter — used for per-pipeline backfill
        when the global pull hits the ``limit`` cap and starves
        low-frequency pipelines.
        """
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
        while True:
            params = RunFilterParameters(
                last_updated_after=start,
                last_updated_before=end,
                continuation_token=token,
                filters=filters,
                order_by=order_by,
            )
            resp = self._artifacts.pipeline_run.query_pipeline_runs_by_workspace(params)
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
        """Return the count of pipeline runs in [start, end) with ``status``.

        Streams pages with a server-side ``Status Equals <status>`` filter
        but only counts rows — does not buffer them. Designed for the daily
        status rollup: status buckets are usually small (especially failed /
        cancelled), so each call is cheap.
        """
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
        while True:
            params = RunFilterParameters(
                last_updated_after=start,
                last_updated_before=end,
                continuation_token=token,
                filters=filters,
                order_by=order_by,
            )
            resp = self._artifacts.pipeline_run.query_pipeline_runs_by_workspace(params)
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
        """Yield activity-run dicts for a single pipeline run."""
        emitted = 0
        cap = min(limit, _HARD_RUN_LIMIT)
        token: str | None = None
        while True:
            params = RunFilterParameters(
                last_updated_after=start,
                last_updated_before=end,
                continuation_token=token,
            )
            resp = self._artifacts.pipeline_run.query_activity_runs(
                pipeline_name, run_id, params,
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
        # Some SDK versions return msrest models or raw bytes.
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
