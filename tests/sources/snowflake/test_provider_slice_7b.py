"""Phase 7 Slice 7-B / 7-E.1 — SnowflakeProvider OAuth implementation.

Slice 7-B introduced the real provider with key-pair JWT auth; Slice
7-E.1 rewired the auth flow to Snowflake's built-in OAuth integration
(refresh-token grant). These tests cover the OAuth surface end-to-end:
``_resolve_connect_kwargs`` credential plumbing (refresh-token exchange
+ pre-minted-token escape hatch), ``discover``, ``validate``,
``make_clients``, and the ``host_to_descriptor`` CLI shortcut.
"""
from __future__ import annotations

import io
import json
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from usma.sources import Credentials, SourceDescriptor, SourceType
from usma.sources.snowflake import SnowflakeProvider, host_to_descriptor
from usma.sources.snowflake import provider as snowflake_provider


def _creds_with_oauth(**overrides: Any) -> Credentials:
    extras: dict[str, Any] = {
        "snowflake_account": "acme-prod",
        "snowflake_user": "USMA_SVC",
        "snowflake_oauth_client_id": "client-abc",
        "snowflake_oauth_client_secret": "secret-xyz",
        "snowflake_oauth_refresh_token": "refresh-123",
        "snowflake_warehouse": "USMA_WH",
        "snowflake_role": "USMA_ANALYZER",
    }
    extras.update(overrides)
    return Credentials(
        tenant_id="t", client_id="c", client_secret="s",
        extras=extras,
    )


def _creds_with_pre_minted_token(**overrides: Any) -> Credentials:
    extras: dict[str, Any] = {
        "snowflake_account": "acme-prod",
        "snowflake_user": "USMA_SVC",
        "snowflake_oauth_token": "pre-minted-access-token",
        "snowflake_warehouse": "USMA_WH",
        "snowflake_role": "USMA_ANALYZER",
    }
    extras.update(overrides)
    return Credentials(
        tenant_id="t", client_id="c", client_secret="s",
        extras=extras,
    )


class _StubCursor:
    """Mimics snowflake.connector cursor.fetchone behaviour."""

    def __init__(self, response_map: dict[str, Any]):
        self._responses = response_map
        self._last: Any = None

    def execute(self, sql: str) -> None:
        for needle, value in self._responses.items():
            if needle in sql:
                self._last = value
                return
        self._last = None

    def fetchone(self) -> tuple[Any] | None:
        if self._last is None:
            return None
        return (self._last,)

    def close(self) -> None:
        pass


class _StubConnection:
    def __init__(self, responses: dict[str, Any]):
        self._responses = responses
        self.closed = False

    def cursor(self) -> _StubCursor:
        return _StubCursor(self._responses)

    def close(self) -> None:
        self.closed = True


def _stub_token_exchange(monkeypatch: pytest.MonkeyPatch, *, access_token: str = "fresh-access-token") -> MagicMock:
    """Replace the refresh-token HTTP round-trip with an in-memory stub
    that returns the configured access token."""
    mock = MagicMock(return_value=access_token)
    monkeypatch.setattr(snowflake_provider, "_exchange_refresh_token", mock)
    return mock


@pytest.fixture
def stub_open(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the module-level ``_open_connection`` symbol with a mock
    that returns a configurable stub connection, and stub the
    refresh-token exchange so ``_resolve_connect_kwargs`` doesn't hit
    the network.
    """
    _stub_token_exchange(monkeypatch)
    responses: dict[str, Any] = {
        "CURRENT_REGION()": "AWS_US_EAST_1",
        "CURRENT_VERSION()": "8.42.1",
        "ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY": 1,
        "ACCOUNT_PROPERTIES": "ENTERPRISE",
    }
    conn = _StubConnection(responses)
    mock = MagicMock(return_value=conn)
    monkeypatch.setattr(snowflake_provider, "_open_connection", mock)
    return mock


# ---------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------
def test_resolve_connect_kwargs_reads_extras_and_exchanges_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _stub_token_exchange(monkeypatch, access_token="abcd-access")
    creds = _creds_with_oauth()
    kwargs = snowflake_provider._resolve_connect_kwargs(creds)
    assert kwargs["account"] == "acme-prod"
    assert kwargs["user"] == "USMA_SVC"
    assert kwargs["role"] == "USMA_ANALYZER"
    assert kwargs["warehouse"] == "USMA_WH"
    assert kwargs["authenticator"] == "oauth"
    assert kwargs["token"] == "abcd-access"
    # The refresh-token endpoint must have been called with the
    # credentials from extras (not the env vars).
    exchange.assert_called_once_with(
        account="acme-prod",
        client_id="client-abc",
        client_secret="secret-xyz",
        refresh_token="refresh-123",
    )


def test_resolve_connect_kwargs_env_var_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_token_exchange(monkeypatch, access_token="env-access")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "env-acct")
    monkeypatch.setenv("SNOWFLAKE_USER", "env-user")
    monkeypatch.setenv("SNOWFLAKE_OAUTH_CLIENT_ID", "env-cid")
    monkeypatch.setenv("SNOWFLAKE_OAUTH_CLIENT_SECRET", "env-csec")
    monkeypatch.setenv("SNOWFLAKE_OAUTH_REFRESH_TOKEN", "env-rt")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "env-wh")
    kwargs = snowflake_provider._resolve_connect_kwargs(None)
    assert kwargs["account"] == "env-acct"
    assert kwargs["role"] == "PUBLIC"  # default when neither extras nor env set
    assert kwargs["token"] == "env-access"


def test_resolve_connect_kwargs_pre_minted_token_skips_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _stub_token_exchange(monkeypatch)
    creds = _creds_with_pre_minted_token()
    kwargs = snowflake_provider._resolve_connect_kwargs(creds)
    assert kwargs["authenticator"] == "oauth"
    assert kwargs["token"] == "pre-minted-access-token"
    # Pre-minted token bypasses the refresh-token exchange entirely.
    exchange.assert_not_called()


def test_resolve_connect_kwargs_missing_oauth_fields_lists_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER",
        "SNOWFLAKE_OAUTH_CLIENT_ID", "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "SNOWFLAKE_OAUTH_REFRESH_TOKEN", "SNOWFLAKE_OAUTH_TOKEN",
        "SNOWFLAKE_WAREHOUSE",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="SNOWFLAKE_OAUTH_CLIENT_ID"):
        snowflake_provider._resolve_connect_kwargs(None)


def test_resolve_connect_kwargs_pre_minted_token_still_requires_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_OAUTH_CLIENT_ID", "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "SNOWFLAKE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SNOWFLAKE_OAUTH_TOKEN", "raw-access")
    with pytest.raises(RuntimeError, match="SNOWFLAKE_ACCOUNT"):
        snowflake_provider._resolve_connect_kwargs(None)


def test_resolve_connect_kwargs_account_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _stub_token_exchange(monkeypatch)
    creds = _creds_with_oauth()
    kwargs = snowflake_provider._resolve_connect_kwargs(creds, account_override="other-acct")
    assert kwargs["account"] == "other-acct"
    # The account override flows through to the refresh-token endpoint
    # so the right account host is hit.
    assert exchange.call_args.kwargs["account"] == "other-acct"


# ---------------------------------------------------------------------
# _exchange_refresh_token (HTTP plumbing)
# ---------------------------------------------------------------------
def test_exchange_refresh_token_posts_form_and_extracts_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return self._payload

    def fake_urlopen(req: Any, timeout: float = 0.0) -> _Resp:
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        captured["timeout"] = timeout
        return _Resp(json.dumps({
            "access_token": "ACCESS-OK",
            "token_type": "Bearer",
            "expires_in": 600,
        }).encode("utf-8"))

    monkeypatch.setattr(snowflake_provider.urllib.request, "urlopen", fake_urlopen)
    token = snowflake_provider._exchange_refresh_token(
        account="acme-prod",
        client_id="cid",
        client_secret="csec",
        refresh_token="rt",
    )
    assert token == "ACCESS-OK"
    assert captured["url"] == "https://acme-prod.snowflakecomputing.com/oauth/token-request"
    assert captured["method"] == "POST"
    body = captured["body"].decode("utf-8")
    assert "grant_type=refresh_token" in body
    assert "refresh_token=rt" in body
    assert "client_id=cid" in body
    assert "client_secret=csec" in body
    # Header keys may be normalised by urllib's Request.
    header_keys = {k.lower() for k in captured["headers"]}
    assert "content-type" in header_keys


def test_exchange_refresh_token_http_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: Any, timeout: float = 0.0) -> Any:  # noqa: ARG001
        raise OSError("connection refused")
    monkeypatch.setattr(snowflake_provider.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="token-request failed"):
        snowflake_provider._exchange_refresh_token(
            account="acme-prod", client_id="cid",
            client_secret="csec", refresh_token="rt",
        )


def test_exchange_refresh_token_missing_access_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"error": "invalid_grant"}'

    monkeypatch.setattr(
        snowflake_provider.urllib.request,
        "urlopen",
        lambda req, timeout=0: _Resp(),  # noqa: ARG005
    )
    with pytest.raises(RuntimeError, match="no access_token"):
        snowflake_provider._exchange_refresh_token(
            account="acme-prod", client_id="cid",
            client_secret="csec", refresh_token="rt",
        )


# ---------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------
def test_discover_returns_single_descriptor_with_aws_platform(stub_open: MagicMock) -> None:
    creds = _creds_with_oauth()
    provider = SnowflakeProvider()
    descriptors = provider.discover(creds)
    assert len(descriptors) == 1
    d = descriptors[0]
    assert d.type is SourceType.SNOWFLAKE
    assert d.id == "acme-prod"
    assert d.display_name == "acme-prod"
    assert d.extras["platform"] == "aws"
    assert d.extras["region"] == "AWS_US_EAST_1"
    assert d.extras["edition"] == "ENTERPRISE"


def test_discover_azure_region_maps_to_azure_platform(
    stub_open: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _StubConnection({
        "CURRENT_REGION()": "AZURE_WESTEUROPE",
        "ACCOUNT_PROPERTIES": None,
    })
    monkeypatch.setattr(snowflake_provider, "_open_connection", MagicMock(return_value=conn))
    provider = SnowflakeProvider()
    descriptors = provider.discover(_creds_with_oauth())
    assert descriptors[0].extras["platform"] == "azure"
    assert descriptors[0].extras["region"] == "AZURE_WESTEUROPE"
    assert "edition" not in descriptors[0].extras


def test_discover_gcp_region_maps_to_gcp_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_token_exchange(monkeypatch)
    conn = _StubConnection({"CURRENT_REGION()": "GCP_US_CENTRAL1"})
    monkeypatch.setattr(snowflake_provider, "_open_connection", MagicMock(return_value=conn))
    provider = SnowflakeProvider()
    descriptors = provider.discover(_creds_with_oauth())
    assert descriptors[0].extras["platform"] == "gcp"


def test_discover_unknown_region_falls_back_to_env_then_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_exchange(monkeypatch)
    conn = _StubConnection({"CURRENT_REGION()": None})
    monkeypatch.setattr(snowflake_provider, "_open_connection", MagicMock(return_value=conn))
    monkeypatch.setenv("SMA_SNOWFLAKE_PLATFORM", "azure")
    provider = SnowflakeProvider()
    descriptors = provider.discover(_creds_with_oauth())
    assert descriptors[0].extras["platform"] == "azure"

    monkeypatch.delenv("SMA_SNOWFLAKE_PLATFORM", raising=False)
    descriptors = provider.discover(_creds_with_oauth())
    assert descriptors[0].extras["platform"] == "aws"


# ---------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------
def test_validate_happy_path_emits_three_ok_checks(stub_open: MagicMock) -> None:
    provider = SnowflakeProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SNOWFLAKE, id="acme-prod", display_name="acme-prod",
    )
    checks = provider.validate(descriptor, _creds_with_oauth())
    names = [c.name for c in checks]
    assert names == [
        "Snowflake OAuth token resolution",
        "Snowflake CURRENT_VERSION()",
        "SNOWFLAKE.ACCOUNT_USAGE access",
    ]
    assert all(c.ok for c in checks)
    assert "8.42.1" in checks[1].detail


def test_validate_missing_creds_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER",
        "SNOWFLAKE_OAUTH_CLIENT_ID", "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "SNOWFLAKE_OAUTH_REFRESH_TOKEN", "SNOWFLAKE_OAUTH_TOKEN",
        "SNOWFLAKE_WAREHOUSE",
    ):
        monkeypatch.delenv(var, raising=False)
    provider = SnowflakeProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SNOWFLAKE, id="acme-prod", display_name="acme-prod",
    )
    checks = provider.validate(descriptor, None)
    assert len(checks) == 1
    assert checks[0].ok is False
    assert "SNOWFLAKE_ACCOUNT" in checks[0].detail


def test_validate_account_usage_failure_isolated_from_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_exchange(monkeypatch)

    class _PartialConn(_StubConnection):
        def cursor(self) -> Any:
            class _Cur(_StubCursor):
                def execute(self, sql: str) -> None:
                    if "ACCOUNT_USAGE" in sql:
                        raise RuntimeError("insufficient privileges")
                    super().execute(sql)
            return _Cur(self._responses)

    conn = _PartialConn({"CURRENT_VERSION()": "8.42.1"})
    monkeypatch.setattr(snowflake_provider, "_open_connection", MagicMock(return_value=conn))
    provider = SnowflakeProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SNOWFLAKE, id="acme-prod", display_name="acme-prod",
    )
    checks = provider.validate(descriptor, _creds_with_oauth())
    by_name = {c.name: c for c in checks}
    assert by_name["Snowflake CURRENT_VERSION()"].ok is True
    assert by_name["SNOWFLAKE.ACCOUNT_USAGE access"].ok is False
    assert "insufficient privileges" in by_name["SNOWFLAKE.ACCOUNT_USAGE access"].detail


# ---------------------------------------------------------------------
# make_clients()
# ---------------------------------------------------------------------
def test_make_clients_returns_bundle_with_lazy_connect(stub_open: MagicMock) -> None:
    provider = SnowflakeProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SNOWFLAKE, id="acme-prod", display_name="acme-prod",
        extras={"platform": "aws", "region": "AWS_US_EAST_1"},
    )
    bundle = provider.make_clients(descriptor, _creds_with_oauth())
    assert bundle.account == "acme-prod"
    assert bundle.user == "USMA_SVC"
    assert bundle.role == "USMA_ANALYZER"
    assert bundle.warehouse == "USMA_WH"
    assert bundle.platform == "aws"
    assert bundle.region == "AWS_US_EAST_1"
    # connect should not have been called yet during bundle construction
    stub_open.assert_not_called()
    bundle.connect()
    stub_open.assert_called_once()


def test_make_clients_rejects_missing_descriptor_id() -> None:
    provider = SnowflakeProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SNOWFLAKE, id="", display_name="empty",
    )
    with pytest.raises(ValueError, match="missing id"):
        provider.make_clients(descriptor, _creds_with_oauth())


def test_make_clients_mints_fresh_token_on_every_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _stub_token_exchange(monkeypatch, access_token="t1")
    conn = _StubConnection({})
    monkeypatch.setattr(snowflake_provider, "_open_connection", MagicMock(return_value=conn))
    provider = SnowflakeProvider()
    descriptor = SourceDescriptor(
        type=SourceType.SNOWFLAKE, id="acme-prod", display_name="acme-prod",
        extras={"platform": "aws"},
    )
    bundle = provider.make_clients(descriptor, _creds_with_oauth())
    # Bundle construction resolves once to capture identity.
    assert exchange.call_count == 1
    bundle.connect()
    bundle.connect()
    # Each ``connect()`` mints a fresh access token so analyzer passes
    # spanning longer than the ~10-minute token TTL still work.
    assert exchange.call_count == 3


# ---------------------------------------------------------------------
# host_to_descriptor()
# ---------------------------------------------------------------------
def test_host_to_descriptor_default_platform_is_aws() -> None:
    d = host_to_descriptor("acme-prod")
    assert d.id == "acme-prod"
    assert d.extras["platform"] == "aws"


def test_host_to_descriptor_explicit_platform() -> None:
    d = host_to_descriptor("acme-eu", platform="azure")
    assert d.extras["platform"] == "azure"


def test_host_to_descriptor_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="unknown"):
        host_to_descriptor("acme-prod", platform="oracle")


def test_host_to_descriptor_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMA_SNOWFLAKE_PLATFORM", "gcp")
    d = host_to_descriptor("acme-prod")
    assert d.extras["platform"] == "gcp"


# ---------------------------------------------------------------------
# Optional-extra missing path
# ---------------------------------------------------------------------
def test_open_connection_missing_extra_raises_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate ``snowflake.connector`` not being installed.
    monkeypatch.setitem(sys.modules, "snowflake", types.ModuleType("snowflake"))
    monkeypatch.setitem(sys.modules, "snowflake.connector", None)
    with pytest.raises(ImportError, match=r"\[snowflake\]"):
        snowflake_provider._open_connection({"account": "x"})
