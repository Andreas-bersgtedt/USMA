"""pyodbc client targeting the workspace's built-in serverless SQL endpoint."""
from __future__ import annotations

import logging
import struct
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import pyodbc

from ..._odbc import resolve_odbc_driver
from ...auth import get_sql_access_token
from ...config import AppConfig

log = logging.getLogger(__name__)

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_QUERIES_DIR = Path(__file__).parent / "queries"


@lru_cache(maxsize=None)
def _load_query(name: str) -> str:
    return (_QUERIES_DIR / f"{name}.sql").read_text(encoding="utf-8")


class ServerlessSqlClient:
    """Connects to <workspace>-ondemand.sql.azuresynapse.net.

    Supports an optional ``with client.session(): ...`` context that opens one
    underlying ``pyodbc.Connection`` and reuses it for every ``fetch_*`` call
    inside the block, avoiding the per-query AAD/TLS handshake. Connections
    are not thread-safe — open one session per worker thread.
    """

    def __init__(self, cfg: AppConfig, server_fqdn: str, database: str = "master") -> None:
        self._cfg = cfg
        self._server = server_fqdn
        self._database = database
        self._shared_conn: pyodbc.Connection | None = None

    @staticmethod
    def endpoint_fqdn(workspace_name: str) -> str:
        return f"{workspace_name}-ondemand.sql.azuresynapse.net"

    def _connection_string(self) -> str:
        sql = self._cfg.sql
        driver = resolve_odbc_driver(sql.odbc_driver)
        return (
            f"Driver={{{driver}}};"
            f"Server=tcp:{self._server},1433;"
            f"Database={self._database};"
            f"Encrypt=yes;TrustServerCertificate=no;"
            f"Connection Timeout={sql.login_timeout};"
        )

    def _token_struct(self) -> bytes:
        token = get_sql_access_token(self._cfg.azure)
        encoded = token.encode("utf-16-le")
        return struct.pack("=i", len(encoded)) + encoded

    def _open(self) -> pyodbc.Connection:
        log.debug("Connecting to serverless %s / %s", self._server, self._database)
        attrs = {_SQL_COPT_SS_ACCESS_TOKEN: self._token_struct()}
        conn = pyodbc.connect(self._connection_string(), attrs_before=attrs)
        conn.timeout = self._cfg.sql.query_timeout
        return conn

    @contextmanager
    def connect(self) -> Iterator[pyodbc.Connection]:
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def session(self) -> Iterator["ServerlessSqlClient"]:
        if self._shared_conn is not None:
            yield self
            return
        self._shared_conn = self._open()
        try:
            yield self
        finally:
            try:
                self._shared_conn.close()
            finally:
                self._shared_conn = None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self._shared_conn is not None:
            cur = self._shared_conn.cursor()
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def fetch_query_file(self, name: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return self.fetch_all(_load_query(name), params)

    def for_database(self, database: str) -> "ServerlessSqlClient":
        return ServerlessSqlClient(self._cfg, self._server, database)
