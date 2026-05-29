"""SAP BW source provider — Phase-5 stub."""
from __future__ import annotations

from .. import register_provider
from .provider import SapBwProvider

register_provider(SapBwProvider())

__all__ = ["SapBwProvider"]
