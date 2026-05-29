"""Shared helpers for resolving the Microsoft ODBC Driver for SQL Server.

Both :mod:`modules.dedicated_pools.sql_client` and
:mod:`modules.serverless_pools.sql_client` need to pick a usable ODBC driver
at connect-time. Centralising the logic here means a missing driver produces
the same actionable message everywhere instead of a raw IM002.
"""
from __future__ import annotations

import logging
import re

import pyodbc

log = logging.getLogger(__name__)

_DRIVER_VERSION_RE = re.compile(r"ODBC Driver (\d+) for SQL Server", re.IGNORECASE)


def installed_sql_server_drivers() -> list[str]:
    """Return installed ``ODBC Driver NN for SQL Server`` entries, newest first."""
    drivers = [d for d in pyodbc.drivers() if _DRIVER_VERSION_RE.search(d)]
    drivers.sort(
        key=lambda d: int(_DRIVER_VERSION_RE.search(d).group(1)),  # type: ignore[union-attr]
        reverse=True,
    )
    return drivers


def resolve_odbc_driver(configured: str) -> str:
    """Pick a usable ODBC driver, falling back to the highest installed version.

    Raises :class:`RuntimeError` with an actionable message when no SQL Server
    ODBC driver is installed on the host (the IM002 case).
    """
    installed = installed_sql_server_drivers()
    if not installed:
        raise RuntimeError(
            "No 'ODBC Driver NN for SQL Server' is installed on this host. "
            "Install Microsoft ODBC Driver 18 for SQL Server "
            "(https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) "
            "and re-run, or run `sma doctor` to diagnose."
        )
    for d in installed:
        if d.lower() == configured.lower():
            return d
    fallback = installed[0]
    log.warning(
        "Configured SQL_ODBC_DRIVER=%r not installed; falling back to %r. "
        "Installed drivers: %s",
        configured, fallback, ", ".join(installed),
    )
    return fallback
