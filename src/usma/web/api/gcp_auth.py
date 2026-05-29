"""Browser-based ADC sign-in for GCP (Slice 5-I).

Replicates ``gcloud auth application-default login`` without requiring
the gcloud CLI to be installed on the host. Runs Google's installed-app
OAuth 2.0 flow on a background thread:

1. ``POST /api/auth/gcp/login`` starts the flow. The backend:
   - Picks a free localhost port.
   - Stands up the one-shot HTTP listener
     (``InstalledAppFlow.run_local_server``).
   - Opens the user's default browser at the Google consent URL with
     ``redirect_uri=http://localhost:<port>/``.
2. The user authenticates in the browser. Google redirects to the
   localhost listener with an auth code.
3. The library swaps the code for a refresh token; we persist it to
   ``%APPDATA%\\gcloud\\application_default_credentials.json`` (Linux:
   ``$HOME/.config/gcloud/...``) where ``google.auth.default()`` will
   pick it up on the next API call.
4. The SPA polls ``GET /api/auth/gcp/status`` until state transitions
   from ``pending`` → ``ready`` (or ``error``), then re-triggers
   project discovery.

**Localhost-only constraint.** The OAuth callback redirects to
``http://localhost``, which means the browser must run on the same
machine as the SMA backend. For server deployments (browser on a
laptop, ``sma serve`` on a remote VM), customers should use Workload
Identity Federation instead — see ``docs/user-guide/22-bigquery.md``.

The public OAuth client credentials below are the same ones gcloud
ships in its source. Google's installed-app OAuth model treats them as
identifying-not-authenticating; they cannot be used to impersonate the
end user without their explicit browser consent.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Public OAuth client used by ``gcloud auth application-default login``.
# Documented at https://github.com/GoogleCloudPlatform/gcloud-cli (and
# baked into the gcloud binaries). Safe to embed because Google's
# installed-app flow validates the user's consent at the browser, not
# at the client-secret level.
_GCLOUD_ADC_CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
_GCLOUD_ADC_CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"  # noqa: S105 (public OAuth client)

_ADC_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/sqlservice.login",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


State = Literal["idle", "pending", "ready", "error"]


@dataclass
class _Status:
    state: State = "idle"
    email: str | None = None
    error: str | None = None
    adc_path: str | None = None


# Single-flight state. The flow holds an HTTP listener on a localhost
# port so concurrent flows would compete for the OAuth callback.
_lock = threading.Lock()
_status = _Status()


def _adc_path() -> Path:
    """Return the canonical ADC user-credentials file path.

    Matches gcloud's hard-coded default so subsequent
    ``google.auth.default()`` calls find the token without any env var.
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "gcloud" / "application_default_credentials.json"
    return Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


def _write_adc(refresh_token: str) -> Path:
    path = _adc_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "authorized_user",
        "client_id": _GCLOUD_ADC_CLIENT_ID,
        "client_secret": _GCLOUD_ADC_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _extract_email(credentials: object) -> str | None:
    """Best-effort: pull the user email out of the ID token if present."""
    id_token = getattr(credentials, "id_token", None)
    if not id_token:
        return None
    try:
        # ID token is a JWT — base64-decode the middle segment.
        import base64

        parts = id_token.split(".")
        if len(parts) < 2:
            return None
        # Pad to a multiple of 4.
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        body = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return body.get("email")
    except Exception:  # noqa: BLE001
        return None


def _run_flow() -> None:
    """Drive the InstalledAppFlow on a background thread.

    Updates the module-level ``_status`` so the SPA polling endpoint
    can report progress. Any exception is captured into
    ``_status.error`` so a failed flow doesn't leave the state stuck on
    ``pending``.
    """
    global _status
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        with _lock:
            _status = _Status(
                state="error",
                error=(
                    "google-auth-oauthlib is not installed. Run "
                    "`pip install synapse-migration-analyzer[bigquery]`."
                ),
            )
        log.warning("gcp_auth: google-auth-oauthlib missing: %s", exc)
        return

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": _GCLOUD_ADC_CLIENT_ID,
                "client_secret": _GCLOUD_ADC_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=_ADC_SCOPES,
    )
    try:
        credentials = flow.run_local_server(
            port=0,
            open_browser=True,
            prompt="consent",
            access_type="offline",
        )
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _status = _Status(state="error", error=str(exc))
        log.warning("gcp_auth: OAuth flow failed: %s", exc)
        return

    refresh = getattr(credentials, "refresh_token", None)
    if not refresh:
        with _lock:
            _status = _Status(
                state="error",
                error=(
                    "Google did not return a refresh token. Revoke the "
                    "previous grant at "
                    "https://myaccount.google.com/permissions and retry."
                ),
            )
        return

    try:
        path = _write_adc(refresh)
    except OSError as exc:
        with _lock:
            _status = _Status(state="error", error=f"failed to write ADC file: {exc}")
        log.warning("gcp_auth: failed to write ADC: %s", exc)
        return

    email = _extract_email(credentials)
    with _lock:
        _status = _Status(state="ready", email=email, adc_path=str(path))
    log.info("gcp_auth: ADC written for %s -> %s", email or "<unknown>", path)


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
    email: str | None = None
    error: str | None = None
    adc_path: str | None = None


@router.post("/gcp/login", response_model=LoginResponse)
def start_login() -> LoginResponse:
    """Start an installed-app OAuth flow against Google.

    Idempotent for an already-pending flow: returns ``state="pending"``
    without starting a second one. Resets ``error``/``ready`` state on
    a fresh start.
    """
    global _status
    with _lock:
        if _status.state == "pending":
            return LoginResponse(
                ok=True,
                state="pending",
                message="A sign-in is already in progress; complete it in your browser.",
            )
        _status = _Status(state="pending")

    thread = threading.Thread(target=_run_flow, name="sma-gcp-oauth", daemon=True)
    thread.start()
    return LoginResponse(
        ok=True,
        state="pending",
        message="Browser opened for Google sign-in. Poll /api/auth/gcp/status for completion.",
    )


@router.get("/gcp/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    with _lock:
        snap = _status
    return StatusResponse(
        state=snap.state,
        email=snap.email,
        error=snap.error,
        adc_path=snap.adc_path,
    )


@router.post("/gcp/reset", response_model=StatusResponse)
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
