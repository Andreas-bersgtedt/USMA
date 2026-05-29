"""Browser-based OAuth sign-in for Snowflake (Slice 7-E.2).

Mirrors the GCP ADC sign-in shipped in Slice 5-I but targets Snowflake's
built-in OAuth integration. Snowflake built-in OAuth only supports
``authorization_code`` + ``refresh_token`` grants — there is no
``client_credentials`` path — so we run the authorisation-code flow
once via a browser hop and persist the resulting refresh token to the
project's ``.env`` file. The analyzer then trades the refresh token
for short-lived (~10 min TTL) access tokens at run time via the
mechanism added in Slice 7-E.1.

Flow:

1. ``POST /api/auth/snowflake/login`` reads
   ``SNOWFLAKE_ACCOUNT`` / ``SNOWFLAKE_OAUTH_CLIENT_ID`` /
   ``SNOWFLAKE_OAUTH_CLIENT_SECRET`` from the configured ``.env``.
   Optionally honours ``SNOWFLAKE_ROLE``.
2. We pick a free localhost port, open the user's default browser at
   ``https://<account>.snowflakecomputing.com/oauth/authorize?...``,
   and stand up a one-shot stdlib ``HTTPServer`` listening on that
   port for the OAuth callback.
3. On callback we extract ``?code=...``, POST it to
   ``https://<account>.snowflakecomputing.com/oauth/token-request``
   with ``grant_type=authorization_code``, and receive a JSON payload
   with ``refresh_token`` + (short-lived) ``access_token``.
4. The refresh token is written to the ``.env`` as
   ``SNOWFLAKE_OAUTH_REFRESH_TOKEN`` via python-dotenv's ``set_key``;
   the access token is *not* persisted (it would be stale within
   minutes; the analyzer mints fresh ones on demand from the refresh
   token).
5. The SPA polls ``GET /api/auth/snowflake/status`` until state
   transitions from ``pending`` → ``ready`` (or ``error``).

**Localhost-only constraint.** The OAuth callback redirects to
``http://localhost:<port>``, which means the browser must run on the
same machine as the SMA backend. The Snowflake security integration's
``OAUTH_REDIRECT_URI`` must match the redirect URI used here
*exactly* (Snowflake does not support wildcard host or port). By
default we use ``http://localhost:53682/``; override via
``SNOWFLAKE_OAUTH_REDIRECT_URI`` in ``.env`` (and re-register the
matching URI on the security integration).
"""
from __future__ import annotations

import http.server
import json
import logging
import threading
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import set_key
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import AppState, get_state

log = logging.getLogger(__name__)


# Local-listener wait: how long the background thread blocks waiting
# for the user to complete the consent screen before giving up. Five
# minutes matches GCP's default and is long enough for a 2FA prompt.
_CALLBACK_WAIT_SECS = 5 * 60.0

# Token-exchange timeout for the authorization_code → refresh_token
# round-trip (same value as the refresh-token exchange in
# ``sources/snowflake/provider.py``).
_TOKEN_EXCHANGE_TIMEOUT_SECS = 30.0

# Default redirect URI used when ``SNOWFLAKE_OAUTH_REDIRECT_URI`` is
# not set. **Must** match the ``OAUTH_REDIRECT_URI`` registered on the
# Snowflake security integration exactly (Snowflake does an exact
# string compare — no wildcard host or port). 53682 is the same port
# the Google ``InstalledAppFlow`` uses by convention, picked because
# it's high, fixed, and unlikely to collide with a dev server.
_DEFAULT_REDIRECT_URI = "http://localhost:53682/"


State = Literal["idle", "pending", "ready", "error"]


@dataclass
class _Status:
    state: State = "idle"
    account: str | None = None
    error: str | None = None
    # ``ready`` carries the (redacted) env file path where the refresh
    # token was persisted, so the SPA can render a confirmation hint.
    env_file: str | None = None


# Single-flight state. The flow holds an HTTP listener on a localhost
# port so concurrent flows would compete for the OAuth callback.
_lock = threading.Lock()
_status = _Status()


# ---------------------------------------------------------------------------
# Helpers (kept module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------


def _port_from_redirect_uri(redirect_uri: str) -> int:
    """Parse the port out of a redirect URI; raise ``RuntimeError`` if absent."""
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError(
            f"redirect_uri must be http://localhost:<port>/ (got {redirect_uri!r})"
        )
    if parsed.port is None:
        raise RuntimeError(
            "redirect_uri must include an explicit port (e.g. "
            f"http://localhost:53682/); got {redirect_uri!r}"
        )
    return parsed.port


def _build_authorize_url(*, account: str, client_id: str, redirect_uri: str, role: str | None) -> str:
    scope = "refresh_token"
    if role:
        scope = f"refresh_token session:role:{role}"
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
    })
    return f"https://{account}.snowflakecomputing.com/oauth/authorize?{params}"


def _exchange_code_for_refresh_token(
    *,
    account: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, object]:
    """POST the authorisation code to Snowflake's token endpoint.

    Returns the decoded JSON payload (caller pulls ``refresh_token`` /
    ``access_token`` out of it). Raises ``RuntimeError`` on every
    failure mode with the response body included for diagnostics.
    """
    endpoint = f"https://{account}.snowflakecomputing.com/oauth/token-request"
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 (https-only, fixed scheme)
        endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TOKEN_EXCHANGE_TIMEOUT_SECS) as resp:  # noqa: S310
            payload_bytes = resp.read()
    except OSError as exc:
        raise RuntimeError(f"Snowflake OAuth token-request failed at {endpoint}: {exc}") from exc
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except ValueError as exc:
        raise RuntimeError(
            f"Snowflake OAuth token-request returned non-JSON payload: {payload_bytes!r}"
        ) from exc
    if not isinstance(payload, dict) or not payload.get("refresh_token"):
        raise RuntimeError(
            f"Snowflake OAuth token-request returned no refresh_token; payload={payload!r}"
        )
    return payload


class _CallbackResult:
    """Thread-safe slot the HTTP handler writes the OAuth code into."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.code: str | None = None
        self.error: str | None = None


def _make_handler(result: _CallbackResult) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a BaseHTTPRequestHandler that captures ``?code=...`` once."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API)
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            code = query.get("code", [None])[0]
            err = query.get("error", [None])[0]
            err_desc = query.get("error_description", [None])[0]
            if err:
                result.error = err_desc or err
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    f"<h1>Sign-in failed</h1><p>{err}</p>"
                    f"<p>You may close this tab.</p>".encode("utf-8")
                )
            elif code:
                result.code = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Sign-in complete</h1>"
                    b"<p>You may close this tab and return to USMA.</p>"
                )
            else:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"missing 'code' query parameter")
                return
            result.event.set()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
            # Suppress stderr access-log spam from the one-shot server.
            return

    return _Handler


# ---------------------------------------------------------------------------
# Flow driver
# ---------------------------------------------------------------------------


def _run_flow(
    *,
    env_file: Path,
    account: str,
    client_id: str,
    client_secret: str,
    role: str | None,
    redirect_uri: str,
) -> None:
    """Drive the Snowflake authorisation-code flow on a background thread.

    Any exception is captured into ``_status.error`` so a failed flow
    doesn't leave the state stuck on ``pending``.
    """
    global _status
    try:
        port = _port_from_redirect_uri(redirect_uri)
        result = _CallbackResult()
        handler_cls = _make_handler(result)
        # ``HTTPServer`` blocks on ``handle_request()``; we run it in
        # the current (background) thread because the SPA polls via
        # ``GET /status`` rather than blocking on the POST response.
        server = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
        server.timeout = _CALLBACK_WAIT_SECS

        authorize_url = _build_authorize_url(
            account=account, client_id=client_id,
            redirect_uri=redirect_uri, role=role,
        )
        try:
            webbrowser.open(authorize_url, new=1, autoraise=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("snowflake_auth: webbrowser.open failed: %s", exc)

        server.handle_request()
        server.server_close()

        if not result.event.is_set():
            with _lock:
                _status = _Status(
                    state="error", account=account,
                    error="Timed out waiting for the browser callback.",
                )
            return
        if result.error or not result.code:
            with _lock:
                _status = _Status(
                    state="error", account=account,
                    error=result.error or "Snowflake did not return an authorisation code.",
                )
            return

        payload = _exchange_code_for_refresh_token(
            account=account, client_id=client_id, client_secret=client_secret,
            code=result.code, redirect_uri=redirect_uri,
        )
        refresh_token = str(payload["refresh_token"])

        try:
            set_key(str(env_file), "SNOWFLAKE_OAUTH_REFRESH_TOKEN",
                    refresh_token, quote_mode="never")
        except OSError as exc:
            with _lock:
                _status = _Status(
                    state="error", account=account,
                    error=f"failed to write refresh token to {env_file}: {exc}",
                )
            log.warning("snowflake_auth: env write failed: %s", exc)
            return

        with _lock:
            _status = _Status(state="ready", account=account, env_file=str(env_file))
        log.info("snowflake_auth: refresh token persisted for account=%s", account)
    except RuntimeError as exc:
        with _lock:
            _status = _Status(state="error", account=account, error=str(exc))
        log.warning("snowflake_auth: flow failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _status = _Status(state="error", account=account, error=f"unexpected error: {exc}")
        log.exception("snowflake_auth: unexpected error")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


router = APIRouter()


class LoginResponse(BaseModel):
    ok: bool
    state: State
    message: str


class StatusResponse(BaseModel):
    state: State
    account: str | None = None
    error: str | None = None
    env_file: str | None = None


def _read_oauth_env(env_file: Path) -> tuple[str, str, str, str | None, str]:
    """Return (account, client_id, client_secret, role, redirect_uri) or raise 400."""
    from dotenv import dotenv_values

    raw = dotenv_values(str(env_file)) if env_file.exists() else {}
    account = (raw.get("SNOWFLAKE_ACCOUNT") or "").strip()
    client_id = (raw.get("SNOWFLAKE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (raw.get("SNOWFLAKE_OAUTH_CLIENT_SECRET") or "").strip()
    role = (raw.get("SNOWFLAKE_ROLE") or "").strip() or None
    redirect_uri = (raw.get("SNOWFLAKE_OAUTH_REDIRECT_URI") or "").strip() or _DEFAULT_REDIRECT_URI
    missing = [name for name, value in (
        ("SNOWFLAKE_ACCOUNT", account),
        ("SNOWFLAKE_OAUTH_CLIENT_ID", client_id),
        ("SNOWFLAKE_OAUTH_CLIENT_SECRET", client_secret),
    ) if not value]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "Save the Snowflake account + OAuth client id + OAuth "
                "client secret first; missing: " + ", ".join(missing)
            ),
        )
    return account, client_id, client_secret, role, redirect_uri


@router.post("/snowflake/login", response_model=LoginResponse)
def start_login(state: AppState = Depends(get_state)) -> LoginResponse:
    """Start an authorization-code OAuth flow against Snowflake.

    Idempotent for an already-pending flow: returns ``state="pending"``
    without starting a second one. Resets ``error``/``ready`` state on
    a fresh start.
    """
    global _status
    account, client_id, client_secret, role, redirect_uri = _read_oauth_env(state.env_file)

    with _lock:
        if _status.state == "pending":
            return LoginResponse(
                ok=True,
                state="pending",
                message="A sign-in is already in progress; complete it in your browser.",
            )
        _status = _Status(state="pending", account=account)

    thread = threading.Thread(
        target=_run_flow,
        kwargs={
            "env_file": state.env_file,
            "account": account,
            "client_id": client_id,
            "client_secret": client_secret,
            "role": role,
            "redirect_uri": redirect_uri,
        },
        name="sma-snowflake-oauth",
        daemon=True,
    )
    thread.start()
    return LoginResponse(
        ok=True,
        state="pending",
        message=(
            f"Browser opened for Snowflake sign-in against {account}. "
            "Complete the consent screen, then poll "
            "/api/auth/snowflake/status."
        ),
    )


@router.get("/snowflake/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    with _lock:
        snap = _status
    return StatusResponse(
        state=snap.state,
        account=snap.account,
        error=snap.error,
        env_file=snap.env_file,
    )


@router.post("/snowflake/reset", response_model=StatusResponse)
def reset_status() -> StatusResponse:
    """Clear the last login result so the UI can dismiss banners.

    Refuses to reset a ``pending`` flow (the background thread still
    owns the localhost listener).
    """
    global _status
    with _lock:
        if _status.state == "pending":
            raise HTTPException(
                status_code=409,
                detail="A sign-in is in progress; finish or cancel the browser flow first.",
            )
        _status = _Status()
        snap = _status
    return StatusResponse(state=snap.state)
