"""Phase 4.7 follow-up — Configuration API surface for Databricks-on-AWS.

Covers two things the SPA depends on:

1. ``GET`` / ``PUT /api/config`` round-trip the seven ``DATABRICKS_*``
   fields (host + PAT + workspace OAuth pair + account OAuth triple)
   with the same set/unset/clear semantics as ``AZURE_CLIENT_SECRET``.

2. :func:`usma.web.config_io.discover_databricks_workspaces` dispatches
   to the AWS branch when ``SMA_DATABRICKS_PLATFORM=aws`` and delegates
   workspace enumeration to :class:`DatabricksAwsProvider`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from usma.sources import SourceDescriptor, SourceType
from usma.web import create_app
from usma.web.config_io import discover_databricks_workspaces


def _client(tmp_path: Path) -> TestClient:
    runs = tmp_path / "runs"
    env = tmp_path / ".env"
    app = create_app(runs_dir=runs, env_file=env)
    return TestClient(app)


def test_aws_databricks_secrets_roundtrip(tmp_path: Path) -> None:
    """The AWS-Databricks SP secret persists as ``set``/``unset``."""
    with _client(tmp_path) as c:
        r = c.get("/api/config")
        assert r.status_code == 200
        body = r.json()
        az = body["azure"]
        # All four AWS fields start blank/unset.
        assert az["databricks_host"] is None
        assert az["databricks_client_id"] is None
        assert az["databricks_client_secret"] == "unset"
        assert az["databricks_account_id"] is None

        # Write all four fields.
        update = {
            "azure": {
                "databricks_host": "https://dbc-test.cloud.databricks.com",
                "databricks_client_id": "sp-client-id",
                "databricks_client_secret": "sp-client-secret",
                "databricks_account_id": "11111111-2222-3333-4444-555555555555",
            },
        }
        r = c.put("/api/config", json=update, headers={"X-SMA-API": "1"})
        assert r.status_code == 200, r.text

        # Read back — the secret redacted, plain fields verbatim.
        r = c.get("/api/config")
        az = r.json()["azure"]
        assert az["databricks_host"] == "https://dbc-test.cloud.databricks.com"
        assert az["databricks_client_id"] == "sp-client-id"
        assert az["databricks_client_secret"] == "set"
        assert az["databricks_account_id"] == "11111111-2222-3333-4444-555555555555"

        # Clear a plain field by sending empty string; secret by empty string too.
        update = {
            "azure": {
                "databricks_host": "",
                "databricks_client_secret": "",
            },
        }
        r = c.put("/api/config", json=update, headers={"X-SMA-API": "1"})
        assert r.status_code == 200, r.text

        r = c.get("/api/config")
        az = r.json()["azure"]
        assert az["databricks_host"] is None
        assert az["databricks_client_secret"] == "unset"
        # Unrelated fields untouched.
        assert az["databricks_client_id"] == "sp-client-id"
        assert az["databricks_account_id"] == "11111111-2222-3333-4444-555555555555"


def test_discover_databricks_aws_account_api(monkeypatch, tmp_path: Path) -> None:
    """``SMA_DATABRICKS_PLATFORM=aws`` + SP triple -> AWS branch."""
    env = tmp_path / ".env"
    env.write_text(
        "SMA_DATABRICKS_PLATFORM=aws\n"
        "DATABRICKS_ACCOUNT_ID=acct-1\n"
        "DATABRICKS_CLIENT_ID=cid\n"
        "DATABRICKS_CLIENT_SECRET=csec\n",
        encoding="utf-8",
    )

    desc = SourceDescriptor(
        type=SourceType.DATABRICKS,
        id="https://dbc-abc.cloud.databricks.com",
        display_name="prod-ws",
        location="us-east-1",
        resource_group=None,
        extras={"workspace_url": "https://dbc-abc.cloud.databricks.com"},
    )

    captured: dict[str, Any] = {}

    class _StubProvider:
        def discover(self, creds, subscription_id=None):  # noqa: D401, ANN001
            captured["extras"] = dict(creds.extras)
            captured["subscription_id"] = subscription_id
            return [desc]

    import usma.sources.databricks as dbx_mod

    monkeypatch.setattr(
        dbx_mod, "provider_for_platform", lambda p: _StubProvider(),
    )

    checks, workspaces = discover_databricks_workspaces(env)

    assert captured["subscription_id"] is None
    assert captured["extras"]["DATABRICKS_ACCOUNT_ID"] == "acct-1"
    assert captured["extras"]["DATABRICKS_CLIENT_ID"] == "cid"
    assert captured["extras"]["DATABRICKS_CLIENT_SECRET"] == "csec"

    assert len(workspaces) == 1
    assert workspaces[0].name == "prod-ws"
    assert workspaces[0].sql_endpoint == "https://dbc-abc.cloud.databricks.com"

    # PASS checks: mode line + count.
    names = [c.name for c in checks]
    assert "Databricks-on-AWS discovery" in names
    assert "Databricks-on-AWS workspaces visible" in names
    assert all(c.ok for c in checks)
    mode_check = next(c for c in checks if c.name == "Databricks-on-AWS discovery")
    assert "Databricks SP" in (mode_check.detail or "")


def test_discover_databricks_aws_missing_creds(tmp_path: Path) -> None:
    """No host, no SP triple -> single FAIL check."""
    env = tmp_path / ".env"
    env.write_text("SMA_DATABRICKS_PLATFORM=aws\n", encoding="utf-8")

    checks, workspaces = discover_databricks_workspaces(env)
    assert workspaces == []
    assert len(checks) == 1
    assert checks[0].ok is False
    assert "DATABRICKS_ACCOUNT_ID" in (checks[0].detail or "")
    assert "DATABRICKS_CLIENT_ID" in (checks[0].detail or "")
