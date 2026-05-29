"""Synapse-workspace ``PipelineCollector`` adapter.

Thin wrapper around the existing
:class:`usma.modules.pipelines.artifacts_client.ArtifactsApiClient`,
re-exposed under the :class:`PipelineCollector` Protocol so the analyzer
can swap between Synapse and ADF without per-source branches.

The wrapping is intentionally a one-liner per method: this collector
exists so the analyzer's *call sites* can become source-agnostic, not
to rewrite the Synapse data-plane code. The underlying SDK calls and
model construction are unchanged from Phase 1 — that move-don't-rewrite
property is what makes Phase 2's regression risk low.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from ..models import (
    Activity,
    Dataset,
    IntegrationRuntime,
    LinkedService,
    Pipeline,
    Trigger,
)

if TYPE_CHECKING:
    # ``ArtifactsApiClient`` pulls in ``azure-synapse-artifacts`` at
    # import time. Keep the import behind ``TYPE_CHECKING`` so this
    # module stays importable in environments without that SDK (e.g.
    # ADF-only deployments).
    from ..artifacts_client import ArtifactsApiClient


class SynapseCollector:
    """Adapt :class:`ArtifactsApiClient` to the
    :class:`PipelineCollector` Protocol."""

    def __init__(self, client: "ArtifactsApiClient", *, workspace_name: str) -> None:
        self._client = client
        self._workspace_name = workspace_name

    @property
    def source_label(self) -> str:
        return self._workspace_name

    def iter_pipelines_with_activities(
        self,
    ) -> Iterator[tuple[Pipeline, list[Activity]]]:
        return self._client.iter_pipelines_with_activities()

    def list_linked_services(self) -> Iterator[LinkedService]:
        return self._client.list_linked_services()

    def list_datasets(self) -> Iterator[Dataset]:
        return self._client.list_datasets()

    def list_triggers(self) -> Iterator[Trigger]:
        return self._client.list_triggers()

    def list_integration_runtimes(self) -> Iterator[IntegrationRuntime]:
        return self._client.list_integration_runtimes()


__all__ = ["SynapseCollector"]
