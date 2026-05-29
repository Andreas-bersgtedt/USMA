"""Mount the prebuilt SPA at ``/`` (production single-binary mode)."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Scope

log = logging.getLogger(__name__)


class _SafeStaticFiles(StaticFiles):
    """StaticFiles with HTML5 history fallback + safe error handling.

    1. SPA fallback: any request that would 404 *and* looks like a client-side
       route (no file extension, not under ``/api/``) is served the bundled
       ``index.html`` so React Router can take over. This is what makes deep
       links such as ``/code-objects`` and ``/runs`` work after a refresh.

    2. Browsers occasionally probe odd URLs (e.g. ``/:undefined`` from
       devtools, source-map lookups, or stale extensions). On Windows those
       paths contain characters that are illegal on NTFS (``:``), which
       makes ``Path.resolve()`` raise ``OSError [WinError 123]`` and pollutes
       the server log. Treat any path lookup failure as a 404.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._index_path = Path(self.directory) / "index.html"  # type: ignore[arg-type]

    def _is_spa_route(self, path: str) -> bool:
        """Return True if ``path`` should fall back to ``index.html`` on 404."""
        # On Windows StaticFiles normalises with ``os.path.normpath`` which
        # turns separators into backslashes; convert back so our heuristics
        # work cross-platform.
        p = path.replace("\\", "/").strip("/")
        if not p:
            return True  # bare "/" — already handled by html=True, but be safe
        # Never override API or asset paths.
        if p.startswith("api/") or p == "api":
            return False
        # Files have an extension (``foo.json``, ``assets/main.js``); routes
        # don't (``code-objects``, ``runs/abc``).
        last_segment = p.rsplit("/", 1)[-1]
        return "." not in last_segment

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # StaticFiles raises HTTPException(404) on missing files; convert
            # to a Response so we can apply the SPA fallback uniformly.
            if exc.status_code != 404:
                raise
            response = Response(status_code=404)
        except (OSError, ValueError) as exc:  # noqa: BLE001
            log.debug("static lookup failed for %r: %s", path, exc)
            response = Response(status_code=404)
        if response.status_code == 404 and self._is_spa_route(path):
            if self._index_path.exists():
                return FileResponse(self._index_path, media_type="text/html")
        return response


def mount_static(app: FastAPI, static_dir: Path) -> None:
    """Mount ``static_dir`` at ``/``.

    StaticFiles already rejects path-traversal attempts; we additionally
    validate the dir before mounting so a missing ``web/dist/`` causes a
    clear startup error rather than 404 storm.
    """
    static_dir = static_dir.resolve()
    if not static_dir.is_dir():
        raise FileNotFoundError(
            f"--static-dir {static_dir} does not exist. Build the SPA "
            "with `cd web && npm install && npm run build` first."
        )
    if not (static_dir / "index.html").exists():
        raise FileNotFoundError(
            f"--static-dir {static_dir} has no index.html. Pass the dist/ "
            "directory produced by `npm run build`, not the source dir."
        )
    app.mount("/", _SafeStaticFiles(directory=str(static_dir), html=True), name="spa")
