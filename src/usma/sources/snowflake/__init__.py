"""Snowflake source provider — Phase-7 stub.

Slice 7-A pre-flight: registers the provider scaffold so the
`SourceType.SNOWFLAKE` enum value resolves to a real (NotImplementedError-
raising) provider class. Real `discover()` / `validate()` / `make_clients()`
land in Slice 7-B.
"""
from __future__ import annotations

from .. import register_provider
from .provider import SnowflakeClientBundle, SnowflakeProvider, host_to_descriptor

register_provider(SnowflakeProvider())

__all__ = ["SnowflakeProvider", "SnowflakeClientBundle", "host_to_descriptor"]
