"""Synapse Workspace source provider.

Phase-0 stub. Phase 1 will populate this with the discovery + validation
logic currently living in ``src/usma/web/config_io.py``.
"""
from __future__ import annotations

from .. import register_provider
from .provider import SynapseWorkspaceProvider

register_provider(SynapseWorkspaceProvider())

__all__ = ["SynapseWorkspaceProvider"]
