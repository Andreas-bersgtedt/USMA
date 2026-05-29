"""``PipelineCollector`` Protocol — uniform per-source contract.

Each concrete collector wraps the source-specific SDK and emits the
same set of typed iterators that
:class:`usma.modules.pipelines.analyzer.PipelinesAnalyzer`
consumes. The analyzer therefore stays source-agnostic; adding a new
source requires writing one collector and registering it here.
"""
from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from ..models import (
    Activity,
    Dataset,
    IntegrationRuntime,
    LinkedService,
    Pipeline,
    Trigger,
)


@runtime_checkable
class PipelineCollector(Protocol):
    """Uniform per-source contract.

    Implementations:

    - :class:`usma.modules.pipelines.sources.synapse_collector.SynapseCollector`
    - :class:`usma.modules.pipelines.sources.adf_collector.AdfCollector`

    All methods return *iterators* of typed model objects (defined in
    :mod:`..models`) so the analyzer can stream-process large factories
    without loading every pipeline into memory at once.
    """

    @property
    def source_label(self) -> str:
        """Human-readable label for log lines and progress UIs.

        Typically the workspace or factory name. The analyzer uses this
        verbatim in progress messages; the manifest derives ``scope_id``
        from the :class:`SourceDescriptor` instead, not this property.
        """
        ...

    def iter_pipelines_with_activities(
        self,
    ) -> Iterator[tuple[Pipeline, list[Activity]]]: ...

    def list_linked_services(self) -> Iterator[LinkedService]: ...

    def list_datasets(self) -> Iterator[Dataset]: ...

    def list_triggers(self) -> Iterator[Trigger]: ...

    def list_integration_runtimes(self) -> Iterator[IntegrationRuntime]: ...


__all__ = ["PipelineCollector"]
