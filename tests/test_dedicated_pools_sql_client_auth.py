"""Tests for :class:`DedicatedPoolSqlClient` auth fallback.

When the AAD ``SQL_COPT_SS_ACCESS_TOKEN`` handshake is rejected by the
SQL gateway (canonical "Login failed for user ''" + "Invalid connection
string attribute" pair seen on standalone Dedicated SQL pools via
``*.database.windows.net``), the client must retry with the documented
Microsoft fallback: ``Authentication=ActiveDirectoryServicePrincipal``
with ``UID``/``PWD`` set from the configured service principal. See
ADR-0009 — Slice E live-smoke fix.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pyodbc
import pytest

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules.dedicated_pools import sql_client as mod
from usma.modules.dedicated_pools.sql_client import DedicatedPoolSqlClient


def _make_cfg() -> AppConfig:
    azure = AzureConfig(
        tenant_id="t", client_id="client-uuid",
        client_secret="secret-value",
        subscription_id="sub-x",
        resource_group="rg-1",
        workspace_name="demoserver",
    )
    return AppConfig(azure=azure, sql=SqlConfig(), output_dir=Path("."))


def test_open_uses_token_struct_on_first_attempt(monkeypatch):
    cfg = _make_cfg()
    client = DedicatedPoolSqlClient(cfg, "demoserver.database.windows.net", "pool1")

    monkeypatch.setattr(mod, "get_sql_access_token", lambda _azure: "fake-token")
    monkeypatch.setattr(mod, "resolve_odbc_driver", lambda _d: "ODBC Driver 18 for SQL Server")

    fake_conn = MagicMock(spec=pyodbc.Connection)
    with patch.object(mod.pyodbc, "connect", return_value=fake_conn) as connect:
        result = client._open()

    assert result is fake_conn
    connect.assert_called_once()
    args, kwargs = connect.call_args
    conn_str = args[0]
    assert "Server=tcp:demoserver.database.windows.net,1433" in conn_str
    assert "Database=pool1" in conn_str
    # Token-struct path: attrs_before is set, Authentication= NOT in string.
    assert "attrs_before" in kwargs
    assert 1256 in kwargs["attrs_before"]
    assert "Authentication=" not in conn_str


def _make_token_rejected_error() -> pyodbc.Error:
    # pyodbc.Error.args is (SQLState, message). Use the canonical signature
    # from the customer's run.
    return pyodbc.Error(
        "28000",
        "[28000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
        "Login failed for user ''. (18456) (SQLDriverConnect); "
        "[28000] [Microsoft][ODBC Driver 18 for SQL Server]"
        "Invalid connection string attribute (0)",
    )


def test_open_falls_back_to_sp_direct_when_token_struct_rejected(monkeypatch, caplog):
    cfg = _make_cfg()
    client = DedicatedPoolSqlClient(cfg, "demoserver.database.windows.net", "pool1")

    monkeypatch.setattr(mod, "get_sql_access_token", lambda _azure: "fake-token")
    monkeypatch.setattr(mod, "resolve_odbc_driver", lambda _d: "ODBC Driver 18 for SQL Server")

    fake_conn = MagicMock(spec=pyodbc.Connection)
    side_effects = [_make_token_rejected_error(), fake_conn]

    def _connect_side_effect(*_a, **_kw):
        v = side_effects.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    with patch.object(mod.pyodbc, "connect", side_effect=_connect_side_effect) as connect:
        with caplog.at_level("WARNING", logger=mod.log.name):
            result = client._open()

    assert result is fake_conn
    assert connect.call_count == 2
    # Second call: SP-direct path — no attrs_before, Authentication=... included,
    # UID set to the client_id, PWD to the client_secret.
    second_args, second_kwargs = connect.call_args_list[1]
    conn_str = second_args[0]
    assert "attrs_before" not in second_kwargs
    assert "Authentication=ActiveDirectoryServicePrincipal" in conn_str
    assert "UID={client-uuid}" in conn_str
    assert "PWD={secret-value}" in conn_str
    # User-visible warning explains the fallback.
    assert any("retrying with Authentication=ActiveDirectoryServicePrincipal" in r.message
               for r in caplog.records)


def test_open_does_not_fall_back_on_unrelated_error(monkeypatch):
    cfg = _make_cfg()
    client = DedicatedPoolSqlClient(cfg, "demoserver.database.windows.net", "pool1")

    monkeypatch.setattr(mod, "get_sql_access_token", lambda _azure: "fake-token")
    monkeypatch.setattr(mod, "resolve_odbc_driver", lambda _d: "ODBC Driver 18 for SQL Server")

    # Network / DNS failure — must propagate, no retry.
    timeout_err = pyodbc.Error("HYT00", "[HYT00] [Microsoft][ODBC Driver 18]Login timeout expired")

    with patch.object(mod.pyodbc, "connect", side_effect=timeout_err) as connect:
        with pytest.raises(pyodbc.Error):
            client._open()
    assert connect.call_count == 1


def test_is_aad_token_rejected_recognises_canonical_signature():
    exc = _make_token_rejected_error()
    assert mod._is_aad_token_rejected(exc) is True


def test_is_aad_token_rejected_rejects_other_28000_errors():
    # Plain "Login failed" with an actual user name — that's a real
    # permissions issue, not the token-attribute bug.
    exc = pyodbc.Error("28000", "[28000] Login failed for user 'someone@example.com'.")
    assert mod._is_aad_token_rejected(exc) is False
