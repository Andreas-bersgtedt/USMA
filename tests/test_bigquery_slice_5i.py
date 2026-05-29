"""Tests for Slice 5-I — browser-based GCP ADC sign-in.

The flow opens a real browser and a localhost HTTP listener, so we
mock ``google_auth_oauthlib.flow.InstalledAppFlow`` end-to-end. We
focus on:

* state machine transitions (``idle`` → ``pending`` → ``ready`` /
  ``error``) under the module's ``_lock``;
* single-flight semantics (a second POST while pending is a no-op,
  not a second thread);
* ADC file is written to the canonical gcloud path with the right
  JSON shape (``authorized_user`` + refresh token);
* reset endpoint refuses while pending and clears state otherwise.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from usma.web.api import gcp_auth as gcp_auth_module
from usma.web.app import create_app


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Reset the module-level state between tests."""
    gcp_auth_module._status = gcp_auth_module._Status()
    yield
    gcp_auth_module._status = gcp_auth_module._Status()


def _client(tmp_path: Path) -> TestClient:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    env = tmp_path / ".env"
    env.write_text("")
    return TestClient(create_app(runs_dir=runs_dir, env_file=env))


class _FakeCredentials:
    """Stand-in for ``google.oauth2.credentials.Credentials``."""

    def __init__(self, refresh: str = "fake-refresh-token", email: str | None = "user@example.com") -> None:
        self.refresh_token = refresh
        if email is not None:
            # Construct a minimal JWT with a base64 payload that holds
            # ``email``. ``InstalledAppFlow.run_local_server`` exposes
            # ``id_token`` as a raw string on the credentials.
            import base64

            payload = base64.urlsafe_b64encode(
                json.dumps({"email": email}).encode("ascii")
            ).rstrip(b"=").decode("ascii")
            self.id_token = f"header.{payload}.signature"
        else:
            self.id_token = None


class _FakeFlow:
    """Drop-in for ``InstalledAppFlow`` returned by ``from_client_config``."""

    last_kwargs: dict = {}

    def __init__(self, *, raises: Exception | None = None, refresh: str = "fake-refresh-token") -> None:
        self._raises = raises
        self._refresh = refresh

    def run_local_server(self, **kwargs):  # type: ignore[no-untyped-def]
        _FakeFlow.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return _FakeCredentials(refresh=self._refresh)


def _install_flow(monkeypatch: pytest.MonkeyPatch, flow: _FakeFlow) -> None:
    """Patch ``InstalledAppFlow.from_client_config`` to yield ``flow``."""
    import google_auth_oauthlib.flow as _real

    monkeypatch.setattr(
        _real.InstalledAppFlow,
        "from_client_config",
        classmethod(lambda cls, *_a, **_kw: flow),
    )


def _wait_for_state(target: str, *, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with gcp_auth_module._lock:
            current = gcp_auth_module._status.state
        if current == target:
            return current
        time.sleep(0.02)
    raise AssertionError(f"timeout waiting for state {target!r}; last={current!r}")


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


def test_status_starts_idle(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/api/auth/gcp/status")
    assert r.status_code == 200
    assert r.json() == {"state": "idle", "email": None, "error": None, "adc_path": None}


# ---------------------------------------------------------------------------
# Successful login flow
# ---------------------------------------------------------------------------


def test_login_success_writes_adc_and_extracts_email(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Redirect the ADC write to a tmp dir so we don't clobber the dev
    # machine's real gcloud credentials.
    fake_adc = tmp_path / "adc" / "application_default_credentials.json"
    monkeypatch.setattr(gcp_auth_module, "_adc_path", lambda: fake_adc)
    _install_flow(monkeypatch, _FakeFlow(refresh="ya29-refresh"))

    client = _client(tmp_path)
    r = client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    assert r.status_code == 200
    assert r.json()["state"] == "pending"

    _wait_for_state("ready")

    status = client.get("/api/auth/gcp/status").json()
    assert status["state"] == "ready"
    assert status["email"] == "user@example.com"
    assert status["adc_path"] == str(fake_adc)

    # ADC file shape — gcloud-compatible authorized_user payload.
    on_disk = json.loads(fake_adc.read_text())
    assert on_disk["type"] == "authorized_user"
    assert on_disk["refresh_token"] == "ya29-refresh"
    assert on_disk["client_id"].endswith(".apps.googleusercontent.com")
    assert on_disk["client_secret"]

    # The flow should have requested offline access so a refresh token
    # is returned (not just an access token).
    assert _FakeFlow.last_kwargs.get("access_type") == "offline"
    assert _FakeFlow.last_kwargs.get("prompt") == "consent"
    assert _FakeFlow.last_kwargs.get("port") == 0


# ---------------------------------------------------------------------------
# Single-flight semantics
# ---------------------------------------------------------------------------


def test_concurrent_login_returns_pending_without_starting_second_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_adc = tmp_path / "adc" / "application_default_credentials.json"
    monkeypatch.setattr(gcp_auth_module, "_adc_path", lambda: fake_adc)

    # Block the flow on a gate so we can observe ``pending`` and fire
    # a second POST while the first is still running.
    gate = threading.Event()

    class _BlockingFlow(_FakeFlow):
        def run_local_server(self, **kwargs):  # type: ignore[no-untyped-def]
            gate.wait(timeout=5.0)
            return _FakeCredentials()

    _install_flow(monkeypatch, _BlockingFlow())

    client = _client(tmp_path)
    r1 = client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    assert r1.status_code == 200
    _wait_for_state("pending")

    r2 = client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["state"] == "pending"
    assert "already in progress" in body["message"].lower()

    gate.set()
    _wait_for_state("ready")


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_login_flow_exception_records_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_flow(monkeypatch, _FakeFlow(raises=RuntimeError("user denied consent")))

    client = _client(tmp_path)
    client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    _wait_for_state("error")

    status = client.get("/api/auth/gcp/status").json()
    assert status["state"] == "error"
    assert "user denied consent" in (status["error"] or "")
    assert status["adc_path"] is None


def test_login_without_refresh_token_records_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Credentials with refresh_token=None should be rejected, not
    # silently written (would leave ADC unusable for offline calls).
    class _NoRefreshFlow(_FakeFlow):
        def run_local_server(self, **kwargs):  # type: ignore[no-untyped-def]
            return _FakeCredentials(refresh="", email=None)

    _install_flow(monkeypatch, _NoRefreshFlow())

    client = _client(tmp_path)
    client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    _wait_for_state("error")

    status = client.get("/api/auth/gcp/status").json()
    assert status["state"] == "error"
    assert "refresh token" in (status["error"] or "").lower()


def test_missing_oauthlib_dependency_records_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate the ``[bigquery]`` extra not installed by making the
    # import inside ``_run_flow`` fail.
    import builtins

    real_import = builtins.__import__

    def _raise(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "google_auth_oauthlib.flow":
            raise ImportError("No module named 'google_auth_oauthlib'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raise)

    client = _client(tmp_path)
    client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    _wait_for_state("error")

    status = client.get("/api/auth/gcp/status").json()
    assert status["state"] == "error"
    assert "google-auth-oauthlib" in (status["error"] or "")


# ---------------------------------------------------------------------------
# Reset endpoint
# ---------------------------------------------------------------------------


def test_reset_after_ready_clears_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_adc = tmp_path / "adc" / "application_default_credentials.json"
    monkeypatch.setattr(gcp_auth_module, "_adc_path", lambda: fake_adc)
    _install_flow(monkeypatch, _FakeFlow())

    client = _client(tmp_path)
    client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    _wait_for_state("ready")

    r = client.post("/api/auth/gcp/reset", headers={"X-SMA-API": "1"})
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_reset_while_pending_returns_409(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = threading.Event()

    class _BlockingFlow(_FakeFlow):
        def run_local_server(self, **kwargs):  # type: ignore[no-untyped-def]
            gate.wait(timeout=5.0)
            return _FakeCredentials()

    _install_flow(monkeypatch, _BlockingFlow())

    client = _client(tmp_path)
    client.post("/api/auth/gcp/login", headers={"X-SMA-API": "1"})
    _wait_for_state("pending")

    r = client.post("/api/auth/gcp/reset", headers={"X-SMA-API": "1"})
    assert r.status_code == 409

    gate.set()
    _wait_for_state("ready")


# ---------------------------------------------------------------------------
# Header / CSRF guard
# ---------------------------------------------------------------------------


def test_login_requires_api_marker(tmp_path: Path) -> None:
    r = _client(tmp_path).post("/api/auth/gcp/login")
    # The defence-in-depth middleware should reject without the header.
    assert r.status_code == 400
