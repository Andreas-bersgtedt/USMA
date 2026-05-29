"""Slice 4-D — Databricks SPA Configuration wiring.

Covers the end-to-end backend touchpoints introduced by Slice 4-D so the
SPA Configuration page can drive a Databricks inventory without any CLI
flags:

1. ``read_config`` / ``write_config`` round-trip ``SMA_SOURCE_TYPE=databricks``.
2. ``discover_databricks_workspaces`` short-circuits when credentials are
   missing and otherwise delegates to ``DatabricksProvider.discover``.
3. ``validate_config_live`` routes Databricks scopes through the
   provider's validate hook and skips the Synapse data-plane gauntlet.
4. The new ``POST /api/config/discover-databricks-workspaces`` route is
   wired and returns the expected shape.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from usma.sources import SourceDescriptor, SourceType
from usma.web.config_io import (
    discover_databricks_workspaces,
    read_config,
    validate_config_live,
    write_config,
)
from usma.web.schemas import (
    AppConfigUpdate,
    AzureConfigUpdate,
)


def _seed_env(env_file: Path, *, source_type: str | None = None) -> None:
    lines = [
        "AZURE_TENANT_ID=11111111-1111-1111-1111-111111111111",
        "AZURE_CLIENT_ID=22222222-2222-2222-2222-222222222222",
        "AZURE_CLIENT_SECRET=secret",
        "AZURE_SUBSCRIPTION_ID=33333333-3333-3333-3333-333333333333",
        "SYNAPSE_RESOURCE_GROUP=rg-data",
        "SYNAPSE_WORKSPACE_NAME=dbx-prod",
    ]
    if source_type is not None:
        lines.append(f"SMA_SOURCE_TYPE={source_type}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# read/write round-trip
# ---------------------------------------------------------------------------


def test_read_config_returns_databricks_when_env_says_databricks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env, source_type="databricks")
    cfg = read_config(env)
    assert cfg.azure.source_type == "databricks"


def test_write_config_persists_databricks_source_type(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env)
    update = AppConfigUpdate(azure=AzureConfigUpdate(source_type="databricks"))
    write_config(env, update)
    contents = env.read_text(encoding="utf-8")
    assert "SMA_SOURCE_TYPE=databricks" in contents
    assert read_config(env).azure.source_type == "databricks"


# ---------------------------------------------------------------------------
# discover_databricks_workspaces
# ---------------------------------------------------------------------------


def test_discover_databricks_missing_creds_short_circuits(tmp_path: Path) -> None:
    env = tmp_path / ".env"  # nothing written
    checks, workspaces = discover_databricks_workspaces(env)
    assert workspaces == []
    assert len(checks) == 1
    assert not checks[0].ok
    assert "missing required fields" in (checks[0].detail or "")


def _fake_descriptor(name: str = "dbx-prod") -> SourceDescriptor:
    return SourceDescriptor(
        type=SourceType.DATABRICKS,
        id=(
            "/subscriptions/33333333-3333-3333-3333-333333333333"
            "/resourceGroups/rg-data/providers/Microsoft.Databricks"
            f"/workspaces/{name}"
        ),
        display_name=name,
        subscription_id="33333333-3333-3333-3333-333333333333",
        resource_group="rg-data",
        location="eastus",
        extras={"workspace_url": f"adb-1234.5.azuredatabricks.net"},
    )


def test_discover_databricks_returns_summaries(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env, source_type="databricks")
    with mock.patch(
        "usma.sources.databricks.provider.DatabricksProvider.discover",
        return_value=[_fake_descriptor("dbx-prod"), _fake_descriptor("dbx-dev")],
    ):
        checks, workspaces = discover_databricks_workspaces(env)
    assert [w.name for w in workspaces] == ["dbx-prod", "dbx-dev"]
    # The current workspace from SYNAPSE_WORKSPACE_NAME should be flagged.
    assert [w.is_current for w in workspaces] == [True, False]
    # workspace_url is surfaced via the sql_endpoint slot (no schema growth).
    assert workspaces[0].sql_endpoint == "adb-1234.5.azuredatabricks.net"
    assert any(c.name.startswith("Databricks workspaces visible") and c.ok for c in checks)


def test_discover_databricks_swallows_provider_errors(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env, source_type="databricks")
    with mock.patch(
        "usma.sources.databricks.provider.DatabricksProvider.discover",
        side_effect=RuntimeError("boom"),
    ):
        checks, workspaces = discover_databricks_workspaces(env)
    assert workspaces == []
    failing = [c for c in checks if not c.ok]
    assert failing and "boom" in (failing[0].detail or "")


# ---------------------------------------------------------------------------
# validate_config_live — Databricks branch
# ---------------------------------------------------------------------------


def test_validate_config_live_uses_databricks_provider(tmp_path: Path) -> None:
    """When SMA_SOURCE_TYPE=databricks, validate_config_live must call
    DatabricksProvider.validate and skip the Synapse SQL probes."""
    from usma.sources import (
        ConfigCheck as ProviderConfigCheck,
    )

    env = tmp_path / ".env"
    _seed_env(env, source_type="databricks")

    fake_check = ProviderConfigCheck(
        name="Databricks stub", ok=True, detail="stub", category="Control plane",
    )
    with mock.patch(
        "usma.sources.databricks.provider.DatabricksProvider.validate",
        return_value=[fake_check],
    ) as dbx_validate, mock.patch(
        "usma.sources.databricks.provider.DatabricksProvider.discover",
        return_value=[_fake_descriptor()],
    ):
        checks, workspaces = validate_config_live(env)
    assert dbx_validate.called
    assert [w.name for w in workspaces] == ["dbx-prod"]
    assert any(c.name == "Databricks stub" and c.ok for c in checks)
    # Synapse data-plane probes must not appear in the Databricks path.
    names = [c.name for c in checks]
    assert not any("Serverless SQL" in n for n in names)
    assert not any("Spark Livy" in n for n in names)


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


def test_discover_databricks_route(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from usma.web.app import create_app

    env = tmp_path / ".env"
    _seed_env(env, source_type="databricks")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    with mock.patch(
        "usma.sources.databricks.provider.DatabricksProvider.discover",
        return_value=[_fake_descriptor("dbx-prod")],
    ):
        app = create_app(runs_dir=runs_dir, env_file=env)
        client = TestClient(app)
        r = client.post(
            "/api/config/discover-databricks-workspaces",
            headers={"X-SMA-API": "1"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    names = [w["name"] for w in body["workspaces"]]
    assert names == ["dbx-prod"]
