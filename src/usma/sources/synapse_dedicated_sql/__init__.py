"""Standalone Dedicated SQL pool (formerly SQL DW) source provider.

Discovers ``Microsoft.Sql/servers/<server>/databases/<db>`` resources
with ``edition='DataWarehouse'`` — the classic standalone Azure SQL
Data Warehouse — that have no parent Synapse workspace.

See ADR-0009 and ``docs/architecture/standalone-dedicated-sql.md`` for
the design rationale.
"""
from __future__ import annotations

from .. import register_provider
from .provider import SynapseDedicatedSqlProvider

register_provider(SynapseDedicatedSqlProvider())

__all__ = ["SynapseDedicatedSqlProvider"]
