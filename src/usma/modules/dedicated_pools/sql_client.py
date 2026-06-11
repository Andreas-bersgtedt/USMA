"""pyodbc-based SQL client for Synapse dedicated SQL pools using AAD access tokens."""
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

# SQL_COPT_SS_ACCESS_TOKEN; required to pass an AAD token via pyodbc.
_SQL_COPT_SS_ACCESS_TOKEN = 1256

_QUERIES_DIR = Path(__file__).parent / "queries"


@lru_cache(maxsize=None)
def _load_query(name: str) -> str:
    """Read a packaged ``*.sql`` file once and cache the text in-process."""
    return (_QUERIES_DIR / f"{name}.sql").read_text(encoding="utf-8")


# SQLState 28000 + "Login failed for user ''" + "Invalid connection string
# attribute" is the canonical signature of the AAD token-struct being
# rejected by the SQL gateway (seen on standalone Dedicated SQL pools via
# ``*.database.windows.net``). When we see it, retry with the
# connection-string SP auth method which is the documented Microsoft
# fallback for ODBC Driver 17.4+/18.x.
def _is_aad_token_rejected(exc: pyodbc.Error) -> bool:
    args = getattr(exc, "args", ()) or ()
    sqlstate = str(args[0]) if args else ""
    msg = str(args[1]) if len(args) > 1 else str(exc)
    if sqlstate not in {"28000", "08001", "08S01", "HY000"}:
        return False
    lowered = msg.lower()
    return (
        "login failed for user ''" in lowered
        or "invalid connection string attribute" in lowered
    )


def _short_pyodbc_error(exc: pyodbc.Error) -> str:
    args = getattr(exc, "args", ()) or ()
    if len(args) >= 2:
        return f"{args[0]}: {str(args[1])[:160]}"
    return str(exc)[:160]


def _odbc_quote(value: str) -> str:
    """ODBC-quote a connection-string value.

    Wraps in ``{...}`` so embedded ``;``, ``=``, spaces and other
    delimiters are tolerated, and doubles any embedded ``}`` per the
    ODBC connection-string grammar (``}`` is the only character that
    needs escaping inside brace-quoted values). Without this, an SP
    secret that contains ``}`` produces a malformed string that the
    driver rejects as ``Invalid connection string attribute``.
    """
    return "{" + value.replace("}", "}}") + "}"


class _DedicatedPoolAuthError(Exception):
    """Aggregated error raised when *all* auth strategies fail.

    Surfacing every attempt in one message means the analyzer's
    per-pool ``connect: pool unreachable …`` line tells the user which
    paths were tried and why each one failed — instead of swallowing
    the first attempt and showing only the last error pattern.
    """


class DedicatedPoolSqlClient:
    """Connects to a single dedicated SQL pool using a service-principal access token.

    Each ``fetch_all`` / ``fetch_query_file`` call opens its own connection by
    default, which is convenient for one-off queries but expensive (each AAD
    handshake takes ~300-800 ms). For bulk collection (the analyzer runs ~11
    DMV queries per pool), wrap the calls in ``with client.session(): ...`` to
    reuse a single underlying ``pyodbc.Connection`` for the lifetime of the
    block. ``pyodbc.Connection`` is **not** thread-safe — keep one session per
    thread.
    """

    def __init__(self, cfg: AppConfig, server_fqdn: str, database: str) -> None:
        self._cfg = cfg
        self._server = server_fqdn
        self._database = database
        self._shared_conn: pyodbc.Connection | None = None

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

    def _sp_direct_connection_string(self) -> str:
        # ODBC Driver 17.4+ / 18.x can mint the AAD token internally
        # when given the SP credentials directly; used as a fallback when
        # the SQL_COPT_SS_ACCESS_TOKEN handshake is rejected (some
        # Azure SQL / standalone DWU gateways return "Login failed for
        # user ''" + "Invalid connection string attribute" when the token
        # struct isn't recognised — most often seen on standalone
        # Dedicated SQL pools accessed via *.database.windows.net).
        azure = self._cfg.azure
        return (
            self._connection_string()
            + "Authentication=ActiveDirectoryServicePrincipal;"
            + f"UID={_odbc_quote(azure.client_id)};"
            + f"PWD={_odbc_quote(azure.client_secret)};"
        )

    def _token_struct(self) -> bytes:
        token = get_sql_access_token(self._cfg.azure)
        encoded = token.encode("utf-16-le")
        return struct.pack("=i", len(encoded)) + encoded

    def _open(self) -> pyodbc.Connection:
        log.debug("Connecting to %s / %s", self._server, self._database)
        attempts: list[tuple[str, pyodbc.Error]] = []

        # Attempt 1 — SQL_COPT_SS_ACCESS_TOKEN with an azure-identity
        # minted AAD token. Works for Synapse-workspace pools and most
        # standalone Azure SQL deployments.
        try:
            attrs = {_SQL_COPT_SS_ACCESS_TOKEN: self._token_struct()}
            conn = pyodbc.connect(self._connection_string(), attrs_before=attrs)
            conn.timeout = self._cfg.sql.query_timeout
            return conn
        except pyodbc.Error as exc:
            if not _is_aad_token_rejected(exc):
                raise
            attempts.append(("token-struct (SQL_COPT_SS_ACCESS_TOKEN)", exc))
            log.warning(
                "AAD token-struct auth rejected for %s/%s (%s); retrying with "
                "Authentication=ActiveDirectoryServicePrincipal",
                self._server, self._database, _short_pyodbc_error(exc),
            )

        # Attempt 2 — connection-string SP auth (ODBC driver mints its
        # own token from UID/PWD). Documented Microsoft fallback for
        # ODBC Driver 17.4+/18.x. Some standalone DWU gateways accept
        # this path when they reject the token struct above.
        try:
            conn = pyodbc.connect(self._sp_direct_connection_string())
            conn.timeout = self._cfg.sql.query_timeout
            return conn
        except pyodbc.Error as exc:
            attempts.append(("Authentication=ActiveDirectoryServicePrincipal", exc))

        # Both attempts failed — raise an aggregated error so the
        # analyzer's ``connect: pool unreachable`` line tells the user
        # which paths were tried and why each one failed. "Login failed
        # for user ''" with an empty username on every attempt almost
        # always means the SQL server has no Azure AD admin configured
        # (server-level setting) — see docs/user-guide/24-standalone-dedicated-sql.md
        # → "AAD admin gap is the #1 setup failure mode".
        summary = "; ".join(
            f"[{label}] {_short_pyodbc_error(err)}" for label, err in attempts
        )
        hint = (
            " — both AAD paths returned 'Login failed for user '''; the SQL server "
            "likely has no Azure AD admin set. See user-guide §24 "
            "'AAD admin on the SQL server'."
            if all(
                "login failed for user ''" in str(err.args[1] if len(err.args) > 1 else err).lower()
                for _label, err in attempts
            )
            else ""
        )
        raise _DedicatedPoolAuthError(
            f"All AAD authentication attempts failed: {summary}{hint}"
        )

    @contextmanager
    def connect(self) -> Iterator[pyodbc.Connection]:
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def session(self) -> Iterator["DedicatedPoolSqlClient"]:
        """Open one connection and reuse it for every ``fetch_*`` inside the ``with`` block.

        Nested sessions are tolerated — only the outermost owns the connection.
        """
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
        """Load `<queries>/<name>.sql` and execute it."""
        return self.fetch_all(_load_query(name), params)
