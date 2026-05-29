"""Export / import all run data as a single zip archive.

The export streams a zip containing every run under ``runs_dir`` with a
``manifest.json`` at the root. The import accepts such a zip and writes
each run atomically into ``runs_dir``, with a choice of conflict policy
(``skip_existing`` / ``overwrite`` / ``rename``).

Secrets in ``.env`` live **outside** ``runs_dir`` and are therefore not
reachable. As defence-in-depth the archiver also drops any dotfile and
refuses to follow symlinks, and the importer rejects path traversal,
symlink entries and zip-bomb compression ratios.
"""
from __future__ import annotations

import io
import json
import logging
import re
import secrets
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Literal

from .. import __version__
from .storage import FilesystemRunRepo

log = logging.getLogger(__name__)

# Same id grammar as ``FilesystemRunRepo`` — kept in sync.
_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")

# Caps. These intentionally fit a couple of hundred runs of typical
# analyser output (~1 MB / run) with comfortable headroom while keeping
# a single import bounded.
DEFAULT_TOTAL_CAP = 500 * 1024 * 1024          # 500 MB uncompressed total
DEFAULT_PER_FILE_CAP = 50 * 1024 * 1024        # 50 MB per file
DEFAULT_RATIO_CAP = 200                         # uncompressed / compressed
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

ImportMode = Literal["skip_existing", "overwrite", "rename"]


# ---------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------


@dataclass
class RunsImportResult:
    imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "imported": list(self.imported),
            "skipped": list(self.skipped),
            "renamed": dict(self.renamed),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------


def _iter_run_files(run_dir: Path, include_events: bool) -> Iterator[Path]:
    """Yield concrete files under ``run_dir``, skipping dotfiles / symlinks."""
    if not run_dir.is_dir():
        return
    for p in sorted(run_dir.rglob("*")):
        if p.is_symlink():
            continue
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(run_dir).parts):
            continue
        if not include_events and p.name == "events.ndjson":
            continue
        yield p


def list_exportable_runs(runs_dir: Path) -> list[str]:
    """Return every well-formed run id present under ``runs_dir`` (sorted)."""
    runs_dir = runs_dir.resolve()
    if not runs_dir.is_dir():
        return []
    out: list[str] = []
    for p in sorted(runs_dir.iterdir()):
        if p.is_dir() and _ID_RE.match(p.name):
            out.append(p.name)
    return out


def build_runs_archive_bytes(
    runs_dir: Path,
    ids: Iterable[str] | None = None,
    *,
    include_events: bool = True,
    app_version: str | None = None,
    size_cap_bytes: int = DEFAULT_TOTAL_CAP,
) -> bytes:
    """Build a zip in memory and return its bytes.

    Raises ``ValueError`` on unknown run ids or when total uncompressed
    size would exceed ``size_cap_bytes``.
    """
    runs_dir = runs_dir.resolve()
    available = set(list_exportable_runs(runs_dir))
    if ids is None:
        selected = sorted(available)
    else:
        selected = []
        for rid in ids:
            if not _ID_RE.match(rid):
                raise ValueError(f"invalid run id: {rid!r}")
            if rid not in available:
                raise ValueError(f"unknown run id: {rid!r}")
            selected.append(rid)
        selected.sort()

    buf = io.BytesIO()
    total_bytes = 0
    file_count = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rid in selected:
            run_dir = (runs_dir / rid).resolve()
            if not run_dir.is_relative_to(runs_dir):
                raise ValueError(f"path traversal blocked: {rid!r}")
            for fp in _iter_run_files(run_dir, include_events=include_events):
                rel = fp.relative_to(runs_dir)  # runs/<id>/...
                arc = PurePosixPath("runs") / PurePosixPath(*rel.parts)
                size = fp.stat().st_size
                total_bytes += size
                if total_bytes > size_cap_bytes:
                    raise ValueError(
                        f"archive exceeds size cap of {size_cap_bytes} bytes"
                    )
                zf.write(fp, arcname=str(arc))
                file_count += 1

        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "exported_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "app_version": app_version or __version__,
            "run_ids": selected,
            "counts": {"runs": len(selected), "files": file_count},
            "include_events": bool(include_events),
            "excluded": [".env", "dotfiles", "symlinks"],
        }
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(manifest, indent=2, sort_keys=True),
        )

    return buf.getvalue()


def archive_filename(prefix: str = "sma-runs") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{ts}.zip"


# ---------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------


def _normalise_entry(name: str) -> PurePosixPath | None:
    """Return a safe ``PurePosixPath`` for ``name`` or ``None`` if rejected.

    Rejects: absolute paths, backslashes, ``..`` segments, empty names,
    and anything that does not normalise to a forward-slash relative
    path. The returned path uses POSIX semantics for portability across
    OSes.
    """
    if not name or name.endswith("/"):
        # Directories are implicit — drop them; files only.
        return None
    if "\\" in name:
        return None
    if name.startswith("/"):
        return None
    parts = PurePosixPath(name).parts
    if any(p in ("..", "") for p in parts):
        return None
    return PurePosixPath(*parts)


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    # External attr top 16 bits hold the POSIX mode for Unix-created zips.
    mode = (info.external_attr >> 16) & 0xFFFF
    return bool(mode) and stat.S_ISLNK(mode)


def _validate_archive(
    zf: zipfile.ZipFile,
    *,
    total_cap: int,
    per_file_cap: int,
    ratio_cap: int,
) -> tuple[dict, dict[str, list[tuple[zipfile.ZipInfo, PurePosixPath]]]]:
    """Parse + validate the zip; return ``(manifest, files_by_run_id)``."""
    manifest: dict | None = None
    files_by_run: dict[str, list[tuple[zipfile.ZipInfo, PurePosixPath]]] = {}
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        if _is_symlink_entry(info):
            raise ValueError("archive contains a symlink entry")
        if info.file_size > per_file_cap:
            raise ValueError(
                f"entry {info.filename!r} exceeds per-file cap "
                f"({info.file_size} > {per_file_cap})"
            )
        if info.compress_size > 0 and info.file_size // max(info.compress_size, 1) > ratio_cap:
            raise ValueError(
                f"entry {info.filename!r} exceeds compression ratio cap"
            )
        total += info.file_size
        if total > total_cap:
            raise ValueError(
                f"archive uncompressed size exceeds cap of {total_cap} bytes"
            )
        safe = _normalise_entry(info.filename)
        if safe is None:
            raise ValueError(f"unsafe archive entry: {info.filename!r}")

        if str(safe) == MANIFEST_NAME:
            try:
                manifest = json.loads(zf.read(info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid manifest.json: {exc}") from exc
            continue

        if safe.parts[0] != "runs" or len(safe.parts) < 3:
            raise ValueError(f"entry outside runs/<id>/: {info.filename!r}")
        run_id = safe.parts[1]
        if not _ID_RE.match(run_id):
            raise ValueError(f"invalid run id in entry: {run_id!r}")
        files_by_run.setdefault(run_id, []).append((info, safe))

    if manifest is None:
        raise ValueError("archive is missing manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json is not an object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported manifest schema_version: {manifest.get('schema_version')!r}"
        )
    declared = manifest.get("run_ids")
    if not isinstance(declared, list) or not all(isinstance(r, str) for r in declared):
        raise ValueError("manifest.run_ids must be a list of strings")
    for rid in declared:
        if not _ID_RE.match(rid):
            raise ValueError(f"manifest declares invalid run id: {rid!r}")

    # Tolerate manifests that declare runs without files (edge cases).
    for rid in files_by_run:
        if rid not in declared:
            raise ValueError(
                f"archive contains run {rid!r} that is not declared in manifest"
            )

    return manifest, files_by_run


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(4)}"


def _extract_run(
    zf: zipfile.ZipFile,
    entries: list[tuple[zipfile.ZipInfo, PurePosixPath]],
    dest_run_dir: Path,
    *,
    target_id: str,
) -> None:
    """Extract one run's entries under ``dest_run_dir`` (which must not exist).

    The source entries are addressed as ``runs/<src_id>/...``; the
    extraction strips the first two segments so the content lands under
    ``dest_run_dir`` regardless of the original id.
    """
    dest_run_dir.mkdir(parents=True, exist_ok=False)
    for info, safe in entries:
        rel_parts = safe.parts[2:]  # strip ``runs/<id>``
        if not rel_parts:
            continue
        out_path = (dest_run_dir / Path(*rel_parts)).resolve()
        if not out_path.is_relative_to(dest_run_dir.resolve()):
            raise ValueError("path traversal blocked during extraction")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst)

    # If this run was renamed, rewrite the id inside run.json so the SPA
    # picks it up consistently.
    run_json = dest_run_dir / "run.json"
    if run_json.is_file():
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id") != target_id:
                data["id"] = target_id
                tmp = run_json.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(data, indent=2, sort_keys=False),
                    encoding="utf-8",
                )
                tmp.replace(run_json)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("rewrite run.json id failed for %s: %s", target_id, exc)


def extract_runs_archive(
    zip_bytes: bytes,
    runs_dir: Path,
    *,
    mode: ImportMode = "skip_existing",
    repo: FilesystemRunRepo | None = None,
    total_cap: int = DEFAULT_TOTAL_CAP,
    per_file_cap: int = DEFAULT_PER_FILE_CAP,
    ratio_cap: int = DEFAULT_RATIO_CAP,
) -> RunsImportResult:
    """Validate and extract a runs archive into ``runs_dir``.

    Returns a :class:`RunsImportResult` listing which runs were
    imported, skipped or renamed. The extraction is atomic per run:
    each run is written to a tempdir alongside ``runs_dir`` then
    ``os.replace``-d into place.
    """
    runs_dir = runs_dir.resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)

    if mode not in ("skip_existing", "overwrite", "rename"):
        raise ValueError(f"invalid mode: {mode!r}")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a zip file: {exc}") from exc

    result = RunsImportResult()
    with zf:
        _manifest, files_by_run = _validate_archive(
            zf,
            total_cap=total_cap,
            per_file_cap=per_file_cap,
            ratio_cap=ratio_cap,
        )

        for src_id in sorted(files_by_run):
            entries = files_by_run[src_id]
            target_dir = runs_dir / src_id
            target_id = src_id

            if target_dir.exists():
                # Block overwriting a run that is currently in-flight.
                if repo is not None:
                    try:
                        meta = repo.get(src_id)
                    except ValueError:
                        meta = None
                    if meta is not None and meta.status in ("queued", "running"):
                        raise PermissionError(
                            f"run {src_id} is {meta.status}; cancel before importing over it"
                        )

                if mode == "skip_existing":
                    result.skipped.append(src_id)
                    continue
                if mode == "rename":
                    target_id = _new_run_id()
                    while (runs_dir / target_id).exists():
                        target_id = _new_run_id()
                    target_dir = runs_dir / target_id
                    result.renamed[src_id] = target_id
                # else: overwrite — fall through

            # Extract to a tempdir under runs_dir, then atomic-rename.
            with tempfile.TemporaryDirectory(
                prefix=f".sma-import-{target_id}-", dir=str(runs_dir)
            ) as staging:
                staging_run = Path(staging) / target_id
                _extract_run(zf, entries, staging_run, target_id=target_id)
                if target_dir.exists():
                    # mode == "overwrite"
                    shutil.rmtree(target_dir)
                shutil.move(str(staging_run), str(target_dir))

            result.imported.append(target_id)

    return result
