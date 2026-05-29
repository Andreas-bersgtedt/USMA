"""Runs archive endpoints: export a zip of all run data / import one back.

The export streams a zip containing every run under the configured
``runs_dir`` along with a ``manifest.json``. The import accepts that
zip via a multipart upload and writes each run atomically. Secrets in
``.env`` are not reachable because the archiver only walks ``runs_dir``.

The ``X-SMA-API: 1`` CSRF marker is enforced by the global middleware
for the ``POST`` endpoint.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from .. import archive as runs_archive
from ..deps import AppState, get_state

log = logging.getLogger(__name__)

router = APIRouter()


# Cap raw uploaded bytes (compressed) before we even try to open the zip.
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


@router.get("/export")
def export_runs_archive(
    include_events: bool = True,
    state: AppState = Depends(get_state),
) -> Response:
    """Return a zip of every run under ``runs_dir``.

    The response is a single ``application/zip`` with a
    ``Content-Disposition: attachment`` header so browsers offer a
    download dialog. ``.env`` is never included (it lives outside
    ``runs_dir``); the archiver also drops dotfiles and refuses to
    follow symlinks.
    """
    try:
        data = runs_archive.build_runs_archive_bytes(
            state.runs_dir,
            ids=None,
            include_events=include_events,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fname = runs_archive.archive_filename()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/import")
async def import_runs_archive(
    file: UploadFile = File(...),
    mode: Literal["skip_existing", "overwrite", "rename"] = Form("skip_existing"),
    state: AppState = Depends(get_state),
) -> dict:
    """Restore an exported zip into ``runs_dir``.

    ``mode`` controls behaviour when a run id already exists:
    ``skip_existing`` (default) leaves the existing run alone,
    ``overwrite`` replaces it, ``rename`` keeps both by assigning the
    incoming run a fresh id.
    """
    # Read with a hard cap so a giant upload cannot exhaust memory.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"upload exceeds {_MAX_UPLOAD_BYTES} bytes",
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    if not data.startswith(b"PK\x03\x04") and not data.startswith(b"PK\x05\x06"):
        raise HTTPException(status_code=400, detail="file is not a zip archive")

    try:
        result = runs_archive.extract_runs_archive(
            data,
            state.runs_dir,
            mode=mode,
            repo=state.repo,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Invalidate estate cache for every touched run id.
    try:
        for rid in (*result.imported, *result.renamed.values()):
            state.estate.invalidate(rid)
    except Exception:  # noqa: BLE001
        pass

    return result.to_dict()
