"""SPA mount + HTML5 history fallback tests.

Regression for: deep-linking to a client-side route such as ``/code-objects``
returned 404 from ``StaticFiles`` because there is no physical
``code-objects`` file. The bundled SPA expects the server to fall back to
``index.html`` so React Router can take over.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from usma.web import create_app


def _build_fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id=root></div></body></html>",
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("// bundle", encoding="utf-8")
    return dist


def _client(tmp_path: Path) -> TestClient:
    runs = tmp_path / "runs"
    env = tmp_path / ".env"
    static_dir = _build_fake_dist(tmp_path)
    app = create_app(runs_dir=runs, env_file=env, static_dir=static_dir)
    return TestClient(app)


def test_root_serves_index_html(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "<div id=root>" in r.text


def test_real_asset_served(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/assets/main.js")
        assert r.status_code == 200
        assert "// bundle" in r.text


def test_spa_route_falls_back_to_index(tmp_path: Path) -> None:
    """Deep-link to /code-objects must return index.html (not 404)."""
    with _client(tmp_path) as c:
        for route in ("/code-objects", "/recommendations", "/runs", "/runbook"):
            r = c.get(route)
            assert r.status_code == 200, route
            assert "<div id=root>" in r.text, route


def test_missing_asset_still_404s(tmp_path: Path) -> None:
    """A missing file (looks like a real asset) must NOT fall back to index."""
    with _client(tmp_path) as c:
        r = c.get("/missing.json")
        assert r.status_code == 404
        r = c.get("/assets/does-not-exist.js")
        assert r.status_code == 404


def test_api_404_not_shadowed(tmp_path: Path) -> None:
    """/api/* 404s must remain 404 — never serve index.html."""
    with _client(tmp_path) as c:
        r = c.get("/api/does-not-exist")
        assert r.status_code == 404
        # Body should be the JSON error envelope, not HTML.
        assert "<div id=root>" not in r.text
