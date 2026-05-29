"""Azure Data Factory source provider — Phase 2."""
from __future__ import annotations

from .. import register_provider
from .provider import AdfClientBundle, AdfProvider

register_provider(AdfProvider())

__all__ = ["AdfProvider", "AdfClientBundle"]
