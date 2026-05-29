"""Per-source pipeline collectors — Phase 2.

This sub-package introduces the :class:`PipelineCollector` protocol so
that :mod:`usma.modules.pipelines.analyzer` can
dispatch between Synapse and ADF without per-source ``if`` branches.

Phase-2 status: the protocol and both implementations
(:mod:`synapse_collector`, :mod:`adf_collector`) are wired in but the
analyzer itself still calls the legacy
:class:`usma.modules.pipelines.artifacts_client.ArtifactsApiClient`
directly (see ADR-0001's "move, don't rewrite" rule). The dispatch
swap lands in a follow-up PR once an ADF integration fixture is
available — at that point the analyzer's ``__init__`` will accept a
``SourceDescriptor`` and pick the collector via this registry.
"""
from __future__ import annotations

from .protocol import PipelineCollector

__all__ = ["PipelineCollector"]
