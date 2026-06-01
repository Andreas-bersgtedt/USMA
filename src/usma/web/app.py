"""FastAPI application factory.

Kept intentionally small. Every endpoint is in ``api/`` and is a thin
wrapper over an existing analyzer / CLI function. The factory is reused
by the CLI's ``serve --with-api`` command and by the tests.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from .api import config as config_api
from .api import diff as diff_api
from .api import effort_card as effort_card_api
from .api import estate as estate_api
from .api import events as events_api
from .api import gcp_auth as gcp_auth_api
from .api import snowflake_auth as snowflake_auth_api
from .api import healthz as healthz_api
from .api import logs as logs_api
from .api import migrations as migrations_api
from .api import modules as modules_api
from .api import runs as runs_api
from .api import runs_archive as runs_archive_api
from .api import schema as schema_api
from .deps import AppState, get_state
from .log_buffer import attach_to_root

log = logging.getLogger(__name__)


def create_app(
    *,
    runs_dir: Path,
    env_file: Path | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Parameters
    ----------
    runs_dir:
        Directory used as the filesystem-backed run repository.
    env_file:
        ``.env`` file the config endpoints read/write through. Defaults
        to ``./.env`` relative to the current working dir.
    static_dir:
        Optional directory containing the SPA bundle to serve at ``/``.
        When ``None`` only the ``/api/*`` routes are mounted.
    """
    runs_dir = runs_dir.resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)
    env_file = (env_file or Path(".env")).resolve()

    # Capture WARNING+ records from every module into a bounded
    # in-memory deque so the SPA's Diagnostics panel can surface them
    # without disk I/O. Idempotent across repeated create_app() calls
    # (tests build many apps per process).
    attach_to_root()

    state = AppState(runs_dir=runs_dir, env_file=env_file)

    app = FastAPI(
        title="Unified Solution Migration Analyzer (local)",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    # Local mode: same-origin only. CORS is intentionally NOT enabled.
    # If a future hosted variant needs it, it can be added behind a flag.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Defence-in-depth header marker for state-changing endpoints.
    @app.middleware("http")
    async def require_api_marker(request: Request, call_next):  # type: ignore[no-untyped-def]
        if (
            request.method in ("POST", "PUT", "DELETE", "PATCH")
            and request.url.path.startswith("/api/")
            and request.headers.get("x-sma-api") != "1"
        ):
            return JSONResponse(
                status_code=400,
                content={"detail": "missing X-SMA-API: 1 header"},
            )
        return await call_next(request)

    # Make the shared state available to dependency-injected routes.
    app.dependency_overrides[get_state] = lambda: state

    # Routers.
    app.include_router(healthz_api.router, prefix="/api")
    app.include_router(logs_api.router, prefix="/api/logs")
    app.include_router(schema_api.router, prefix="/api/schema")
    app.include_router(config_api.router, prefix="/api/config")
    app.include_router(effort_card_api.router, prefix="/api/effort-card")
    app.include_router(runs_api.router, prefix="/api/runs")
    app.include_router(runs_archive_api.router, prefix="/api/runs-archive")
    app.include_router(events_api.router, prefix="/api/runs")
    app.include_router(modules_api.router, prefix="/api/runs")
    app.include_router(diff_api.router, prefix="/api/runs")
    app.include_router(estate_api.router, prefix="/api/estate")
    app.include_router(migrations_api.router, prefix="/api/migrations")
    # Slice 5-I — browser-based GCP ADC sign-in (POST /api/auth/gcp/login
    # + GET /api/auth/gcp/status). Drives `InstalledAppFlow` on a
    # background thread so customers can produce ADC user-credentials
    # without installing gcloud CLI.
    app.include_router(gcp_auth_api.router, prefix="/api/auth")
    # Slice 7-E.2 — browser-based Snowflake OAuth sign-in
    # (authorization-code grant). Drives a one-shot localhost HTTP
    # listener so customers can mint a long-lived refresh token from
    # their Snowflake custom security integration without leaving the
    # SPA. See ``api/snowflake_auth.py`` for the flow contract.
    app.include_router(snowflake_auth_api.router, prefix="/api/auth")

    # JSON error envelope so the SPA gets a predictable shape.
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    if static_dir is not None and static_dir.exists():
        from .static import mount_static

        mount_static(app, static_dir)

    log.info("FastAPI app ready: runs_dir=%s env_file=%s", runs_dir, env_file)
    return app
