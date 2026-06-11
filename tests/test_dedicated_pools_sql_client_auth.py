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


def test_odbc_quote_escapes_closing_brace():
    # SP secrets occasionally contain ``}``; ODBC brace-quoted values
    # must double that to ``}}`` or the driver rejects the string as
    # ``Invalid connection string attribute (0)``.
    assert mod._odbc_quote("plain") == "{plain}"
    assert mod._odbc_quote("with}brace") == "{with}}brace}"
    assert mod._odbc_quote("two}}braces}") == "{two}}}}braces}}}"


def test_sp_direct_connection_string_quotes_secret_with_braces(monkeypatch):
    cfg = _make_cfg()
    # Replace just the secret to include a ``}`` — re-use dataclasses.replace
    # via the constructor for the AzureConfig.
    from dataclasses import replace as _dc_replace
    cfg = _dc_replace(cfg, azure=_dc_replace(cfg.azure, client_secret="abc}def"))
    client = DedicatedPoolSqlClient(cfg, "demoserver.database.windows.net", "pool1")
    monkeypatch.setattr(mod, "resolve_odbc_driver", lambda _d: "ODBC Driver 18 for SQL Server")

    conn_str = client._sp_direct_connection_string()
    # The secret's ``}`` must be doubled inside the braces.
    assert "PWD={abc}}def}" in conn_str
    # And the UID (no special chars) stays braced unchanged.
    assert "UID={client-uuid}" in conn_str


def test_open_raises_aggregated_error_when_both_attempts_fail(monkeypatch, caplog):
    """When the SP-direct fallback also fails, the user sees both errors
    labelled, plus the AAD-admin hint when both return 'user '''."""
    cfg = _make_cfg()
    client = DedicatedPoolSqlClient(cfg, "demoserver.database.windows.net", "pool1")
    monkeypatch.setattr(mod, "get_sql_access_token", lambda _azure: "fake-token")
    monkeypatch.setattr(mod, "resolve_odbc_driver", lambda _d: "ODBC Driver 18 for SQL Server")

    first = _make_token_rejected_error()
    # SP-direct also fails with "Login failed for user ''" — typical signature
    # when the SQL server has no AAD admin set.
    second = pyodbc.Error(
        "28000",
        "[28000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
        "Login failed for user ''. (18456); "
        "[28000] [Microsoft][ODBC Driver 18 for SQL Server]"
        "Invalid connection string attribute (0)",
    )

    side_effects = [first, second]
    def _connect_side_effect(*_a, **_kw):
        raise side_effects.pop(0)

    with patch.object(mod.pyodbc, "connect", side_effect=_connect_side_effect):
        with pytest.raises(mod._DedicatedPoolAuthError) as ei:
            client._open()

    msg = str(ei.value)
    # Both attempts labelled, in the order they were tried.
    assert "[token-struct (SQL_COPT_SS_ACCESS_TOKEN)]" in msg
    assert "[Authentication=ActiveDirectoryServicePrincipal]" in msg
    # The AAD-admin hint fires when every attempt returned ``user ''``.
    assert "no Azure AD admin set" in msg
    assert "user-guide §24" in msg


def test_open_aggregated_error_omits_aad_admin_hint_when_one_attempt_is_different(monkeypatch):
    """The AAD-admin hint should only appear when *both* attempts return
    the empty-user pattern; otherwise we'd mislead the user."""
    cfg = _make_cfg()
    client = DedicatedPoolSqlClient(cfg, "demoserver.database.windows.net", "pool1")
    monkeypatch.setattr(mod, "get_sql_access_token", lambda _azure: "fake-token")
    monkeypatch.setattr(mod, "resolve_odbc_driver", lambda _d: "ODBC Driver 18 for SQL Server")

    first = _make_token_rejected_error()
    # SP-direct fails with a different error (e.g. wrong secret).
    second = pyodbc.Error(
        "28000",
        "[28000] Login failed for user 'app://client-uuid@tenant'.",
    )
    side_effects = [first, second]
    with patch.object(mod.pyodbc, "connect", side_effect=lambda *_a, **_kw: (_ for _ in ()).throw(side_effects.pop(0))):
        with pytest.raises(mod._DedicatedPoolAuthError) as ei:
            client._open()
    msg = str(ei.value)
    assert "no Azure AD admin set" not in msg
    assert "[Authentication=ActiveDirectoryServicePrincipal]" in msg


def _make_token_principal_unmapped_error() -> pyodbc.Error:
    # The signature seen when the AAD admin is set on the server but the
    # SP has no contained user in the target database. Token *is*
    # accepted by the gateway — the database itself rejects the
    # principal.
    return pyodbc.Error(
        "28000",
        "[28000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
        "Login failed for user '<token-identified principal>'. (18456) "
        "(SQLDriverConnect); [28000] [Microsoft][ODBC Driver 18 for SQL Server]"
        "[SQL Server]Login failed for user '<token-identified principal>'. (18456)",
    )


def test_is_token_principal_unmapped_recognises_signature():
    exc = _make_token_principal_unmapped_error()
    assert mod._is_token_principal_unmapped(exc) is True


def test_is_token_principal_unmapped_rejects_other_28000():
    # Empty-user signature is a different problem (no AAD admin set);
    # must not be classified as the unmapped-user case.
    exc = pyodbc.Error("28000", "[28000] Login failed for user ''. (18456)")
    assert mod._is_token_principal_unmapped(exc) is False


def test_open_does_not_retry_on_token_principal_unmapped(monkeypatch):
    """The SP-direct fallback is pointless for token-identified-principal
    rejections — switching auth method produces an identical error. The
    code must surface the targeted hint immediately."""
    cfg = _make_cfg()
    client = DedicatedPoolSqlClient(cfg, "demoserver.database.windows.net", "exampledwpool")
    monkeypatch.setattr(mod, "get_sql_access_token", lambda _azure: "fake-token")
    monkeypatch.setattr(mod, "resolve_odbc_driver", lambda _d: "ODBC Driver 18 for SQL Server")

    err = _make_token_principal_unmapped_error()
    with patch.object(mod.pyodbc, "connect", side_effect=err) as connect:
        with pytest.raises(mod._DedicatedPoolAuthError) as ei:
            client._open()

    # Only one connection attempt — no retry.
    assert connect.call_count == 1

    msg = str(ei.value)
    assert "no contained AAD user in database 'exampledwpool'" in msg
    assert "CREATE USER" in msg
    assert "FROM EXTERNAL PROVIDER" in msg
    assert "db_datareader" in msg
    assert "VIEW DATABASE STATE" in msg
    # Confirm the misleading empty-user hint is NOT emitted for this signature.
    assert "no Azure AD admin set" not in msg
    # Original ODBC error text should still be present so the user can grep for it.
    assert "<token-identified principal>" in msg
