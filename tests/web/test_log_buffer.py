"""Tests for the in-process log buffer + /api/logs endpoints."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from usma.web import create_app
from usma.web.log_buffer import attach_to_root, get_log_buffer


def _client(tmp_path: Path) -> TestClient:
    app = create_app(runs_dir=tmp_path / "runs", env_file=tmp_path / ".env")
    return TestClient(app)


def test_buffer_captures_warning_and_above(tmp_path: Path) -> None:
    buf = attach_to_root()
    buf.clear()
    logging.getLogger("usma.test").info("ignored info")
    logging.getLogger("usma.test").warning("kept warning")
    logging.getLogger("usma.test").error("kept error")

    records = buf.snapshot()
    msgs = [r["message"] for r in records]
    assert "kept warning" in msgs
    assert "kept error" in msgs
    assert "ignored info" not in msgs


def test_buffer_captures_exception_traceback(tmp_path: Path) -> None:
    buf = attach_to_root()
    buf.clear()
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("usma.test").exception("operation failed")

    rec = buf.snapshot()[-1]
    assert rec["message"] == "operation failed"
    assert rec["exc"] and "ValueError" in rec["exc"]


def test_logs_endpoint_filters_and_supports_since_seq(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        get_log_buffer().clear()
        logging.getLogger("usma.test").warning("first")
        logging.getLogger("usma.test").error("second")

        r = c.get("/api/logs")
        assert r.status_code == 200
        body = r.json()
        assert body["handler_level"] == "WARNING"
        msgs = [rec["message"] for rec in body["records"]]
        assert msgs == ["first", "second"]
        last_seq = body["records"][-1]["seq"]

        logging.getLogger("usma.test").warning("third")
        r = c.get(f"/api/logs?since_seq={last_seq}")
        body = r.json()
        msgs = [rec["message"] for rec in body["records"]]
        assert msgs == ["third"]

        # Level filter excludes warnings.
        r = c.get("/api/logs?level=ERROR")
        body = r.json()
        msgs = [rec["message"] for rec in body["records"]]
        assert "second" in msgs
        assert "first" not in msgs


def test_logs_clear_endpoint(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        get_log_buffer().clear()
        logging.getLogger("usma.test").warning("dropme")

        # State-changing endpoint requires X-SMA-API marker.
        r = c.delete("/api/logs")
        assert r.status_code == 400

        r = c.delete("/api/logs", headers={"X-SMA-API": "1"})
        assert r.status_code == 200
        assert r.json()["dropped"] >= 1

        r = c.get("/api/logs")
        assert r.json()["records"] == []
