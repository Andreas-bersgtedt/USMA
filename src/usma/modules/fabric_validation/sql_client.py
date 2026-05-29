"""Best-effort SQL probes against a Fabric Warehouse endpoint.

The target endpoint string and credentials come from environment variables so
no secrets need to live in code:

- ``SMA_FABRIC_SQL_SERVER``  — Fabric SQL endpoint FQDN
- ``SMA_FABRIC_DATABASE``    — Fabric Warehouse / Lakehouse SQL endpoint name
- ``SMA_FABRIC_AUTH``        — ``aad`` (default) or ``sql``
- ``SMA_FABRIC_SQL_USER`` / ``SMA_FABRIC_SQL_PASSWORD`` — when ``SMA_FABRIC_AUTH=sql``

When the driver is missing or the connection fails the helpers return an empty
result and the analyzer records the error.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

from ..._odbc import resolve_odbc_driver

log = logging.getLogger(__name__)

# Conservative SQL identifier whitelist: letters / digits / underscore / dot,
# 1-128 chars (the SQL Server limit). Anything else is rejected before being
# bracketed for use in COUNT_BIG probe.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _quote_ident(name: str) -> str:
    """Bracket-quote ``name`` after validating it against ``_IDENT_RE``.

    Raises :class:`ValueError` for inputs that don't look like a plain SQL
    identifier — protects ``fetch_row_count`` against caller-supplied schema /
    table names that could otherwise break out of ``[...]`` and inject T-SQL.
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    # Defence-in-depth: also escape any literal ``]``, even though the regex
    # already rejects it.
    return "[" + name.replace("]", "]]") + "]"


_OBJECT_COUNT_QUERY = """
SELECT s.name AS schema_name, o.type_desc AS type_desc, COUNT(*) AS cnt
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('U', 'V', 'P', 'FN', 'IF', 'TF')
GROUP BY s.name, o.type_desc
"""

_COLLATION_QUERY = """
SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name,
       c.collation_name AS collation
FROM sys.columns c
JOIN sys.tables  t ON t.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE c.collation_name IS NOT NULL
"""


class FabricSqlClient:
    def __init__(self) -> None:
        self._server = os.getenv("SMA_FABRIC_SQL_SERVER")
        self._database = os.getenv("SMA_FABRIC_DATABASE")
        self._auth = (os.getenv("SMA_FABRIC_AUTH") or "aad").lower()

    def is_configured(self) -> bool:
        return bool(self._server and self._database)

    def fetch_object_counts(self) -> list[dict]:
        return list(self._query(_OBJECT_COUNT_QUERY))

    def fetch_collations(self) -> list[dict]:
        return list(self._query(_COLLATION_QUERY))

    def fetch_row_count(self, schema: str, table: str) -> int | None:
        # Use COUNT_BIG to avoid INT overflow on large tables. Both names are
        # validated + bracketed by ``_quote_ident`` so caller-supplied input
        # cannot escape into T-SQL.
        try:
            qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        except ValueError as exc:
            log.warning("refusing fetch_row_count for unsafe identifier: %s", exc)
            return None
        sql = f"SELECT COUNT_BIG(*) AS rows FROM {qualified}"
        rows = list(self._query(sql))
        if not rows:
            return None
        try:
            return int(rows[0]["rows"])
        except (KeyError, TypeError, ValueError):
            return None

    # -- internals ------------------------------------------------------------

    def _query(self, sql: str) -> Iterable[dict]:
        if not self.is_configured():
            return []
        try:  # pragma: no cover - exercised only when pyodbc is installed and Fabric is reachable
            import pyodbc
        except ImportError:
            log.debug("pyodbc not available; Fabric validation skipped")
            return []
        try:
            conn_str = self._build_connection_string()
        except RuntimeError:
            return []
        try:
            with pyodbc.connect(conn_str, timeout=30) as cn:
                cn.timeout = 60
                cur = cn.cursor()
                cur.execute(sql)
                cols = [d[0] for d in cur.description or []]
                return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            log.warning("Fabric SQL query failed: %s", exc)
            return []

    def _build_connection_string(self) -> str:
        # Resolve the actual installed driver instead of hard-coding v18 — this
        # mirrors what ``DedicatedPoolSqlClient`` already does and gives a clean
        # error message on hosts where v18 is missing but v17 is present.
        try:
            driver = resolve_odbc_driver(
                os.getenv("SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"),
            )
        except RuntimeError as exc:  # no driver installed
            log.debug("Fabric validation skipped: %s", exc)
            raise
        parts = [
            f"Driver={{{driver}}}",
            f"Server=tcp:{self._server},1433",
            f"Database={self._database}",
            "Encrypt=yes",
            "TrustServerCertificate=no",
            "Connection Timeout=30",
        ]
        if self._auth == "sql":
            parts.append(f"UID={os.getenv('SMA_FABRIC_SQL_USER', '')}")
            parts.append(f"PWD={os.getenv('SMA_FABRIC_SQL_PASSWORD', '')}")
        else:
            parts.append("Authentication=ActiveDirectoryServicePrincipal")
        return ";".join(parts)
