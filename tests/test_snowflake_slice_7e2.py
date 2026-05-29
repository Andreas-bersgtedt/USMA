"""Tests for Slice 7-E.2 — browser-based Snowflake OAuth sign-in.

The flow opens a real browser and a localhost HTTP listener, so we
mock both ``webbrowser.open`` and the one-shot HTTP listener
end-to-end via the module-level helpers (``_run_flow``,
``_exchange_code_for_refresh_token``). We focus on:

* state machine transitions (``idle`` → ``pending`` → ``ready`` /
  ``error``) under the module's ``_lock``;
* single-flight semantics (a second POST while pending is a no-op,
  not a second thread);
* missing env vars produce a 400 with the list of absent keys before
  any thread is spawned;
* the refresh token is persisted into the configured ``.env`` via
  ``set_key`` so the next ``read_config`` collapses it to ``set``;
* reset endpoint refuses while pending and clears state otherwise.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from usma.web.api import snowflake_auth as sf_auth_module
from usma.web.app import create_app


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    sf_auth_module._status = sf_auth_module._Status()
    yield
    sf_auth_module._status = sf_auth_module._Status()


def _client(tmp_path: Path, *, seed_oauth: bool = True) -> tuple[TestClient, Path]:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    env = tmp_path / ".env"
    if seed_oauth:
        env.write_text(
            "\n".join([
                "SMA_SOURCE_TYPE=snowflake",
                "SNOWFLAKE_ACCOUNT=acme-prod",
                "SNOWFLAKE_USER=svc_usma",
                "SNOWFLAKE_ROLE=USMA_ROLE",
                "SNOWFLAKE_WAREHOUSE=USMA_WH",
                "SNOWFLAKE_OAUTH_CLIENT_ID=cid-123",
                "SNOWFLAKE_OAUTH_CLIENT_SECRET=secret-xyz",
                "SNOWFLAKE_OAUTH_REDIRECT_URI=http://localhost:47123/",
            ]) + "\n",
            encoding="utf-8",
        )
    else:
        env.write_text("", encoding="utf-8")
    return TestClient(create_app(runs_dir=runs_dir, env_file=env)), env


def _wait_for_state(target: str, *, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with sf_auth_module._lock:
            current = sf_auth_module._status.state
        if current == target:
            return current
        time.sleep(0.02)
    raise AssertionError(f"timeout waiting for state {target!r}; last={current!r}")


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


def test_status_starts_idle(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/api/auth/snowflake/status")
    assert r.status_code == 200
    assert r.json() == {"state": "idle", "account": None, "error": None, "env_file": None}


# ---------------------------------------------------------------------------
# Login validation (no thread spawned)
# ---------------------------------------------------------------------------


def test_login_rejects_when_env_missing(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, seed_oauth=False)
    r = client.post("/api/auth/snowflake/login", headers={"X-SMA-API": "1"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "SNOWFLAKE_ACCOUNT" in detail
    assert "SNOWFLAKE_OAUTH_CLIENT_ID" in detail
    assert "SNOWFLAKE_OAUTH_CLIENT_SECRET" in detail


def test_login_rejects_when_only_account_set(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    env = tmp_path / ".env"
    env.write_text("SNOWFLAKE_ACCOUNT=acme-prod\n", encoding="utf-8")
    client = TestClient(create_app(runs_dir=runs_dir, env_file=env))
    r = client.post("/api/auth/snowflake/login", headers={"X-SMA-API": "1"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "SNOWFLAKE_ACCOUNT" not in detail
    assert "SNOWFLAKE_OAUTH_CLIENT_ID" in detail
    assert "SNOWFLAKE_OAUTH_CLIENT_SECRET" in detail


# ---------------------------------------------------------------------------
# Successful login flow
# ---------------------------------------------------------------------------


def test_login_success_persists_refresh_token_to_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, env_file = _client(tmp_path)

    # Replace the heavyweight pieces of the flow with deterministic
    # stubs: ``webbrowser.open`` is a no-op, ``HTTPServer`` is replaced
    # by a stub whose ``handle_request`` synthesises a callback into
    # the ``_CallbackResult`` slot, and the token exchange returns a
    # canned refresh token. The port comes from the
    # ``SNOWFLAKE_OAUTH_REDIRECT_URI`` seeded into the test ``.env``.
    monkeypatch.setattr(sf_auth_module.webbrowser, "open", lambda *a, **kw: True)

    captured: dict = {}

    class _FakeServer:
        def __init__(self, addr, handler_cls):  # noqa: ANN001
            captured["addr"] = addr
            self._handler_cls = handler_cls
            self.timeout = 0.0

        def handle_request(self) -> None:
            # The real flow sets ``result.event`` from inside the
            # handler. We mimic that by reaching into the closure of
            # ``_make_handler`` via the captured ``result`` argument.
            res = captured["result"]
            res.code = "fake-auth-code"
            res.event.set()

        def server_close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(sf_auth_module.http.server, "HTTPServer", _FakeServer)

    real_make_handler = sf_auth_module._make_handler

    def _spy_make_handler(result):  # noqa: ANN001
        captured["result"] = result
        return real_make_handler(result)

    monkeypatch.setattr(sf_auth_module, "_make_handler", _spy_make_handler)

    def _fake_exchange(**kwargs):  # noqa: ANN003
        captured["exchange_kwargs"] = kwargs
        return {"refresh_token": "shiny-new-refresh", "access_token": "short-lived"}

    monkeypatch.setattr(sf_auth_module, "_exchange_code_for_refresh_token", _fake_exchange)

    r = client.post("/api/auth/snowflake/login", headers={"X-SMA-API": "1"})
    assert r.status_code == 200
    assert r.json()["state"] == "pending"

    _wait_for_state("ready", timeout=5.0)
    status = client.get("/api/auth/snowflake/status").json()
    assert status["state"] == "ready"
    assert status["account"] == "acme-prod"
    assert status["error"] is None

    # The refresh token must have been written into the .env via set_key.
    env_text = env_file.read_text(encoding="utf-8")
    assert "SNOWFLAKE_OAUTH_REFRESH_TOKEN=shiny-new-refresh" in env_text

    # The token-exchange call received the right parameters.
    assert captured["exchange_kwargs"]["account"] == "acme-prod"
    assert captured["exchange_kwargs"]["client_id"] == "cid-123"
    assert captured["exchange_kwargs"]["client_secret"] == "secret-xyz"
    assert captured["exchange_kwargs"]["code"] == "fake-auth-code"
    assert captured["exchange_kwargs"]["redirect_uri"] == "http://localhost:47123/"
    assert captured["closed"] is True


def test_login_single_flight_returns_pending_for_second_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(tmp_path)

    # Hold the flow indefinitely so the first call stays ``pending``.
    block = __import__("threading").Event()

    def _hang(**kwargs):  # noqa: ANN003, ARG001
        block.wait(timeout=2.0)

    monkeypatch.setattr(sf_auth_module, "_run_flow", _hang)

    r1 = client.post("/api/auth/snowflake/login", headers={"X-SMA-API": "1"})
    assert r1.json()["state"] == "pending"
    r2 = client.post("/api/auth/snowflake/login", headers={"X-SMA-API": "1"})
    assert r2.json()["state"] == "pending"
    assert "already in progress" in r2.json()["message"]
    block.set()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_run_flow_records_error_when_exchange_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(sf_auth_module.webbrowser, "open", lambda *a, **kw: True)

    captured: dict = {}

    class _FakeServer:
        def __init__(self, addr, handler_cls):  # noqa: ANN001, ARG002
            self.timeout = 0.0

        def handle_request(self) -> None:
            res = captured["result"]
            res.code = "code-A"
            res.event.set()

        def server_close(self) -> None:
            pass

    monkeypatch.setattr(sf_auth_module.http.server, "HTTPServer", _FakeServer)
    real_make_handler = sf_auth_module._make_handler
    monkeypatch.setattr(
        sf_auth_module, "_make_handler",
        lambda res: captured.setdefault("result", res) or real_make_handler(res),
    )

    def _boom(**kwargs):  # noqa: ANN003, ARG001
        raise RuntimeError("token endpoint refused")

    monkeypatch.setattr(sf_auth_module, "_exchange_code_for_refresh_token", _boom)

    sf_auth_module._run_flow(
        env_file=env_file,
        account="acme-prod",
        client_id="cid",
        client_secret="secret",
        role=None,
        redirect_uri="http://localhost:47124/",
    )
    assert sf_auth_module._status.state == "error"
    assert "token endpoint refused" in (sf_auth_module._status.error or "")


def test_run_flow_records_error_on_callback_error_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(sf_auth_module.webbrowser, "open", lambda *a, **kw: True)

    captured: dict = {}

    class _FakeServer:
        def __init__(self, addr, handler_cls):  # noqa: ANN001, ARG002
            self.timeout = 0.0

        def handle_request(self) -> None:
            res = captured["result"]
            res.error = "user denied consent"
            res.event.set()

        def server_close(self) -> None:
            pass

    monkeypatch.setattr(sf_auth_module.http.server, "HTTPServer", _FakeServer)
    real_make_handler = sf_auth_module._make_handler
    monkeypatch.setattr(
        sf_auth_module, "_make_handler",
        lambda res: captured.setdefault("result", res) or real_make_handler(res),
    )

    # If the flow short-circuits on the error callback, the exchange
    # must not be called.
    exchange_called = mock.MagicMock(return_value={"refresh_token": "x"})
    monkeypatch.setattr(sf_auth_module, "_exchange_code_for_refresh_token", exchange_called)

    sf_auth_module._run_flow(
        env_file=env_file, account="acme-prod",
        client_id="cid", client_secret="secret", role=None,
        redirect_uri="http://localhost:47125/",
    )
    assert sf_auth_module._status.state == "error"
    assert "user denied consent" in (sf_auth_module._status.error or "")
    exchange_called.assert_not_called()


# ---------------------------------------------------------------------------
# Authorize URL + token exchange unit tests
# ---------------------------------------------------------------------------


def test_build_authorize_url_includes_role_scope() -> None:
    url = sf_auth_module._build_authorize_url(
        account="acme-prod", client_id="abc/xyz=",
        redirect_uri="http://localhost:1234/", role="USMA_ROLE",
    )
    assert url.startswith("https://acme-prod.snowflakecomputing.com/oauth/authorize?")
    assert "response_type=code" in url
    # client_id with /, = must be URL-encoded.
    assert "client_id=abc%2Fxyz%3D" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A1234%2F" in url
    assert "scope=refresh_token+session%3Arole%3AUSMA_ROLE" in url


def test_build_authorize_url_without_role() -> None:
    url = sf_auth_module._build_authorize_url(
        account="acme-prod", client_id="cid",
        redirect_uri="http://localhost:1234/", role=None,
    )
    assert "scope=refresh_token" in url
    assert "session" not in url


def test_exchange_code_for_refresh_token_posts_and_returns_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json as _json

    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):  # noqa: ANN002
            return None

        def read(self) -> bytes:
            return _json.dumps({
                "refresh_token": "RT",
                "access_token": "AT",
                "expires_in": 600,
            }).encode("utf-8")

    def fake_urlopen(req, timeout=0.0):  # noqa: ANN001, ARG001
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8")
        return _Resp()

    monkeypatch.setattr(sf_auth_module.urllib.request, "urlopen", fake_urlopen)
    payload = sf_auth_module._exchange_code_for_refresh_token(
        account="acme-prod", client_id="cid", client_secret="csec",
        code="the-code", redirect_uri="http://localhost:1234/",
    )
    assert payload["refresh_token"] == "RT"
    assert captured["url"] == "https://acme-prod.snowflakecomputing.com/oauth/token-request"
    assert "grant_type=authorization_code" in captured["body"]
    assert "code=the-code" in captured["body"]
    assert "client_id=cid" in captured["body"]
    assert "client_secret=csec" in captured["body"]


def test_exchange_code_for_refresh_token_missing_refresh_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):  # noqa: ANN002
            return None

        def read(self) -> bytes:
            return b'{"error": "invalid_grant"}'

    monkeypatch.setattr(sf_auth_module.urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp())  # noqa: ARG005
    with pytest.raises(RuntimeError, match="no refresh_token"):
        sf_auth_module._exchange_code_for_refresh_token(
            account="acme-prod", client_id="cid", client_secret="csec",
            code="the-code", redirect_uri="http://localhost:1234/",
        )


# ---------------------------------------------------------------------------
# Reset endpoint
# ---------------------------------------------------------------------------


def test_reset_clears_state_when_not_pending(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    sf_auth_module._status = sf_auth_module._Status(state="ready", account="acme-prod")
    r = client.post("/api/auth/snowflake/reset", headers={"X-SMA-API": "1"})
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_reset_refuses_while_pending(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    sf_auth_module._status = sf_auth_module._Status(state="pending", account="acme-prod")
    r = client.post("/api/auth/snowflake/reset", headers={"X-SMA-API": "1"})
    assert r.status_code == 409
