"""Tests for the runs export/import archive feature."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from usma.web import create_app
from usma.web import archive as runs_archive


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


def _make_run(runs_dir: Path, run_id: str, *, status: str = "ok") -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "id": run_id,
                "status": status,
                "started_at": "2026-05-13T08:00:00Z",
                "finished_at": "2026-05-13T08:05:00Z",
                "modules": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "dedicated_pools.json").write_text(
        json.dumps({"pools": []}), encoding="utf-8"
    )
    (run_dir / "events.ndjson").write_text(
        '{"t": "started"}\n{"t": "finished"}\n', encoding="utf-8"
    )
    return run_dir


def _client(tmp_path: Path) -> TestClient:
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    env = tmp_path / ".env"
    return TestClient(create_app(runs_dir=runs, env_file=env))


# ---------------------------------------------------------------------
# build_runs_archive_bytes
# ---------------------------------------------------------------------


def test_export_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "src" / "runs"
    src.mkdir(parents=True)
    _make_run(src, "20260513T080000Z-aaaa1111")
    _make_run(src, "20260513T081000Z-bbbb2222")

    data = runs_archive.build_runs_archive_bytes(src)

    dst = tmp_path / "dst" / "runs"
    result = runs_archive.extract_runs_archive(data, dst, mode="overwrite")
    assert sorted(result.imported) == [
        "20260513T080000Z-aaaa1111",
        "20260513T081000Z-bbbb2222",
    ]
    assert (dst / "20260513T080000Z-aaaa1111" / "run.json").is_file()
    assert (dst / "20260513T080000Z-aaaa1111" / "events.ndjson").is_file()


def test_export_excludes_dotfiles(tmp_path: Path) -> None:
    src = tmp_path / "runs"
    src.mkdir()
    rd = _make_run(src, "20260513T080000Z-aaaa1111")
    # Defence-in-depth: even if a dotfile lands inside a run dir it is dropped.
    (rd / ".env").write_text("SECRET=hunter2", encoding="utf-8")

    data = runs_archive.build_runs_archive_bytes(src)
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        names = zf.namelist()
    assert not any(".env" in n for n in names)


def test_export_excludes_events_when_disabled(tmp_path: Path) -> None:
    src = tmp_path / "runs"
    src.mkdir()
    _make_run(src, "20260513T080000Z-aaaa1111")
    data = runs_archive.build_runs_archive_bytes(src, include_events=False)
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        names = zf.namelist()
    assert not any(n.endswith("events.ndjson") for n in names)


def test_export_rejects_unknown_ids(tmp_path: Path) -> None:
    src = tmp_path / "runs"
    src.mkdir()
    with pytest.raises(ValueError):
        runs_archive.build_runs_archive_bytes(src, ids=["not-a-real-id"])


def test_export_rejects_unknown_run(tmp_path: Path) -> None:
    src = tmp_path / "runs"
    src.mkdir()
    with pytest.raises(ValueError):
        runs_archive.build_runs_archive_bytes(
            src, ids=["20260513T080000Z-aaaa1111"]
        )


# ---------------------------------------------------------------------
# Validation / security
# ---------------------------------------------------------------------


def _make_zip(entries: dict[str, bytes], manifest: dict | None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if manifest is not None:
            zf.writestr(
                runs_archive.MANIFEST_NAME, json.dumps(manifest).encode()
            )
    return buf.getvalue()


def test_import_rejects_path_traversal(tmp_path: Path) -> None:
    data = _make_zip(
        {"../etc/passwd": b"x"},
        manifest={"schema_version": 1, "run_ids": []},
    )
    with pytest.raises(ValueError, match="unsafe"):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_absolute_path(tmp_path: Path) -> None:
    data = _make_zip(
        {"/etc/passwd": b"x"},
        manifest={"schema_version": 1, "run_ids": []},
    )
    with pytest.raises(ValueError):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_backslash_path(tmp_path: Path) -> None:
    data = _make_zip(
        {"runs\\evil\\f": b"x"},
        manifest={"schema_version": 1, "run_ids": []},
    )
    with pytest.raises(ValueError):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_bad_schema_version(tmp_path: Path) -> None:
    data = _make_zip({}, manifest={"schema_version": 99, "run_ids": []})
    with pytest.raises(ValueError, match="schema_version"):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_missing_manifest(tmp_path: Path) -> None:
    data = _make_zip(
        {"runs/20260513T080000Z-aaaa1111/run.json": b"{}"},
        manifest=None,
    )
    with pytest.raises(ValueError, match="manifest"):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_entry_outside_runs(tmp_path: Path) -> None:
    data = _make_zip(
        {"other/file.txt": b"x"},
        manifest={"schema_version": 1, "run_ids": []},
    )
    with pytest.raises(ValueError, match="outside"):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_undeclared_run(tmp_path: Path) -> None:
    data = _make_zip(
        {"runs/20260513T080000Z-aaaa1111/run.json": b"{}"},
        manifest={"schema_version": 1, "run_ids": []},
    )
    with pytest.raises(ValueError, match="not declared"):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_invalid_run_id(tmp_path: Path) -> None:
    data = _make_zip(
        {"runs/badid/run.json": b"{}"},
        manifest={"schema_version": 1, "run_ids": ["badid"]},
    )
    with pytest.raises(ValueError):
        runs_archive.extract_runs_archive(data, tmp_path / "runs")


def test_import_rejects_total_size_cap(tmp_path: Path) -> None:
    blob = b"x" * 1024
    data = _make_zip(
        {"runs/20260513T080000Z-aaaa1111/run.json": blob},
        manifest={"schema_version": 1, "run_ids": ["20260513T080000Z-aaaa1111"]},
    )
    with pytest.raises(ValueError, match="size"):
        runs_archive.extract_runs_archive(
            data, tmp_path / "runs", total_cap=100
        )


# ---------------------------------------------------------------------
# Import modes
# ---------------------------------------------------------------------


def _round_trip_data(tmp_path: Path, run_id: str = "20260513T080000Z-aaaa1111") -> bytes:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    _make_run(src, run_id)
    return runs_archive.build_runs_archive_bytes(src)


def test_import_skip_existing(tmp_path: Path) -> None:
    rid = "20260513T080000Z-aaaa1111"
    data = _round_trip_data(tmp_path / "a", run_id=rid)
    dst = tmp_path / "runs"
    dst.mkdir()
    _make_run(dst, rid)
    (dst / rid / "marker.txt").write_text("keep me", encoding="utf-8")

    result = runs_archive.extract_runs_archive(data, dst, mode="skip_existing")
    assert result.skipped == [rid]
    assert result.imported == []
    assert (dst / rid / "marker.txt").read_text() == "keep me"


def test_import_overwrite(tmp_path: Path) -> None:
    rid = "20260513T080000Z-aaaa1111"
    data = _round_trip_data(tmp_path / "a", run_id=rid)
    dst = tmp_path / "runs"
    dst.mkdir()
    _make_run(dst, rid)
    (dst / rid / "marker.txt").write_text("old", encoding="utf-8")

    result = runs_archive.extract_runs_archive(data, dst, mode="overwrite")
    assert result.imported == [rid]
    assert not (dst / rid / "marker.txt").exists()


def test_import_rename(tmp_path: Path) -> None:
    rid = "20260513T080000Z-aaaa1111"
    data = _round_trip_data(tmp_path / "a", run_id=rid)
    dst = tmp_path / "runs"
    dst.mkdir()
    _make_run(dst, rid)

    result = runs_archive.extract_runs_archive(data, dst, mode="rename")
    assert rid in result.renamed
    new_id = result.renamed[rid]
    assert new_id != rid
    assert (dst / new_id / "run.json").is_file()
    body = json.loads((dst / new_id / "run.json").read_text())
    assert body["id"] == new_id


# ---------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------


def test_http_export_then_import(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        # Seed two runs on disk under the app's runs_dir.
        runs_dir = tmp_path / "runs"
        _make_run(runs_dir, "20260513T080000Z-aaaa1111")
        _make_run(runs_dir, "20260513T081000Z-bbbb2222")

        r = c.get("/api/runs-archive/export")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/zip")
        assert "attachment" in r.headers["content-disposition"]
        data = r.content
        assert data.startswith(b"PK")

    # Fresh app pointing at a clean runs_dir.
    target = tmp_path / "target"
    target.mkdir()
    env2 = target / ".env"
    runs2 = target / "runs"
    from usma.web import create_app as _ca

    with TestClient(_ca(runs_dir=runs2, env_file=env2)) as c:
        r = c.post(
            "/api/runs-archive/import",
            headers={"X-SMA-API": "1"},
            files={"file": ("export.zip", data, "application/zip")},
            data={"mode": "overwrite"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(body["imported"]) == [
            "20260513T080000Z-aaaa1111",
            "20260513T081000Z-bbbb2222",
        ]
        assert (runs2 / "20260513T080000Z-aaaa1111" / "run.json").is_file()


def test_http_import_requires_csrf_header(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.post(
            "/api/runs-archive/import",
            files={"file": ("x.zip", b"PK\x03\x04", "application/zip")},
        )
        assert r.status_code == 400
        assert "X-SMA-API" in r.json()["detail"]


def test_http_import_rejects_non_zip(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.post(
            "/api/runs-archive/import",
            headers={"X-SMA-API": "1"},
            files={"file": ("x.zip", b"not a zip", "application/zip")},
        )
        assert r.status_code == 400
        assert "zip" in r.json()["detail"].lower()
