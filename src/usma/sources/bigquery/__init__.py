"""Google BigQuery source provider — Phase-5 implementation."""
from __future__ import annotations

from .. import register_provider
from .provider import BigQueryProvider

register_provider(BigQueryProvider())

__all__ = ["BigQueryProvider"]
