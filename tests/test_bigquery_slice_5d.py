"""Slice 5-D — BigQuery SPA Configuration wiring.

Covers the end-to-end backend touchpoints introduced by Slice 5-D so the
SPA Configuration page can drive a BigQuery inventory without any CLI
flags:

1. ``read_config`` / ``write_config`` round-trip ``SMA_SOURCE_TYPE=bigquery``
   and ``SMA_GCP_PROJECT_ID``.
2. ``discover_bigquery_projects`` uses ADC (no Azure SP creds required)
   and delegates to ``BigQueryProvider.discover``.
3. ``validate_config_live`` routes BigQuery scopes through the provider's
   validate hook and skips the Azure/Synapse data-plane gauntlet, even
   when no AZURE_* env vars are set.
4. The new ``POST /api/config/discover-bigquery-projects`` route is
   wired and returns the expected shape.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from usma.sources import SourceDescriptor, SourceType
from usma.web.config_io import (
    discover_bigquery_projects,
    read_config,
    validate_config_live,
    write_config,
)
from usma.web.schemas import (
    AppConfigUpdate,
    AzureConfigUpdate,
)


def _seed_bq_env(env_file: Path, *, project_id: str | None = "my-gcp-proj") -> None:
    """Seed a .env with only the BigQuery-relevant fields (no AZURE_*)."""
    lines = ["SMA_SOURCE_TYPE=bigquery"]
    if project_id is not None:
        lines.append(f"SMA_GCP_PROJECT_ID={project_id}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fake_bq_descriptor(
    project_id: str = "my-gcp-proj",
    project_number: int = 123456789,
) -> SourceDescriptor:
    return SourceDescriptor(
        type=SourceType.BIGQUERY,
        id=project_id,
        display_name=project_id,
        extras={"project_number": project_number},
    )


# ---------------------------------------------------------------------------
# read/write round-trip
# ---------------------------------------------------------------------------


def test_read_config_returns_bigquery_when_env_says_bigquery(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_bq_env(env, project_id="my-gcp-proj")
    cfg = read_config(env)
    assert cfg.azure.source_type == "bigquery"
    assert cfg.azure.gcp_project_id == "my-gcp-proj"


def test_write_config_persists_bigquery_source_type_and_project(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(
            source_type="bigquery",
            gcp_project_id="other-proj",
        ),
    )
    write_config(env, update)
    contents = env.read_text(encoding="utf-8")
    assert "SMA_SOURCE_TYPE=bigquery" in contents
    assert "SMA_GCP_PROJECT_ID=other-proj" in contents
    cfg = read_config(env)
    assert cfg.azure.source_type == "bigquery"
    assert cfg.azure.gcp_project_id == "other-proj"


# ---------------------------------------------------------------------------
# discover_bigquery_projects
# ---------------------------------------------------------------------------


def test_discover_bigquery_returns_summaries(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_bq_env(env, project_id="my-gcp-proj")
    with mock.patch(
        "usma.sources.bigquery.provider.BigQueryProvider.discover",
        return_value=[
            _fake_bq_descriptor("my-gcp-proj", 111),
            _fake_bq_descriptor("other-proj", 222),
        ],
    ):
        checks, workspaces = discover_bigquery_projects(env)
    assert [w.name for w in workspaces] == ["my-gcp-proj", "other-proj"]
    # The current project from SMA_GCP_PROJECT_ID should be flagged.
    assert [w.is_current for w in workspaces] == [True, False]
    # project_number is surfaced via the resource_group slot.
    assert workspaces[0].resource_group == "111"
    assert any(
        c.name.startswith("BigQuery projects visible") and c.ok for c in checks
    )


def test_discover_bigquery_uses_project_id_not_display_name(tmp_path: Path) -> None:
    """Regression: the SPA must receive the project **id** (e.g.
    ``my-first-project-123456``), not the friendly display name (e.g.
    ``My First Project``) — Google APIs reject the latter with
    ``Invalid project ID``. Display name surfaces via ``location`` so
    the dropdown can still render it."""
    env = tmp_path / ".env"
    _seed_bq_env(env)
    descriptor = SourceDescriptor(
        type=SourceType.BIGQUERY,
        id="my-first-project-123456",
        display_name="My First Project",
        extras={"project_number": 42},
    )
    with mock.patch(
        "usma.sources.bigquery.provider.BigQueryProvider.discover",
        return_value=[descriptor],
    ):
        _, workspaces = discover_bigquery_projects(env)
    assert workspaces[0].name == "my-first-project-123456"
    assert workspaces[0].location == "My First Project"


def test_discover_bigquery_swallows_provider_errors(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_bq_env(env)
    with mock.patch(
        "usma.sources.bigquery.provider.BigQueryProvider.discover",
        side_effect=RuntimeError("ADC missing"),
    ):
        checks, workspaces = discover_bigquery_projects(env)
    assert workspaces == []
    failing = [c for c in checks if not c.ok]
    assert failing and "ADC missing" in (failing[0].detail or "")


def test_discover_bigquery_does_not_require_azure_creds(tmp_path: Path) -> None:
    """ADC-only: a .env with zero AZURE_* fields must still work."""
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")  # completely empty
    with mock.patch(
        "usma.sources.bigquery.provider.BigQueryProvider.discover",
        return_value=[_fake_bq_descriptor()],
    ):
        checks, workspaces = discover_bigquery_projects(env)
    assert [w.name for w in workspaces] == ["my-gcp-proj"]
    assert all(c.ok for c in checks)


# ---------------------------------------------------------------------------
# validate_config_live — BigQuery branch
# ---------------------------------------------------------------------------


def test_validate_config_live_uses_bigquery_provider(tmp_path: Path) -> None:
    """When SMA_SOURCE_TYPE=bigquery, validate_config_live must call
    BigQueryProvider.validate and skip the Synapse SQL probes — even
    with no AZURE_* env vars set."""
    from usma.sources import (
        ConfigCheck as ProviderConfigCheck,
    )

    env = tmp_path / ".env"
    _seed_bq_env(env, project_id="my-gcp-proj")

    fake_check = ProviderConfigCheck(
        name="BigQuery stub", ok=True, detail="stub", category="Control plane",
    )
    with mock.patch(
        "usma.sources.bigquery.provider.BigQueryProvider.validate",
        return_value=[fake_check],
    ) as bq_validate, mock.patch(
        "usma.sources.bigquery.provider.BigQueryProvider.discover",
        return_value=[_fake_bq_descriptor()],
    ):
        checks, workspaces = validate_config_live(env)
    assert bq_validate.called
    # Descriptor passed to validate must be a BigQuery one with id=project_id.
    descriptor = bq_validate.call_args[0][0]
    assert descriptor.type is SourceType.BIGQUERY
    assert descriptor.id == "my-gcp-proj"
    assert [w.name for w in workspaces] == ["my-gcp-proj"]
    assert any(c.name == "BigQuery stub" and c.ok for c in checks)
    # Synapse data-plane probes must not appear in the BigQuery path.
    names = [c.name for c in checks]
    assert not any("Serverless SQL" in n for n in names)
    assert not any("Spark Livy" in n for n in names)
    # The Azure-required-fields gate must not have short-circuited.
    assert not any(
        n == "Live connectivity" and "skipped" in (c.detail or "")
        for n, c in zip(names, checks)
    )


def test_validate_config_live_bigquery_missing_project_short_circuits(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    _seed_bq_env(env, project_id=None)
    checks, workspaces = validate_config_live(env)
    assert workspaces == []
    failing = [c for c in checks if not c.ok]
    assert any("BigQuery project configured" == c.name for c in failing)


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


def test_discover_bigquery_route(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from usma.web.app import create_app

    env = tmp_path / ".env"
    _seed_bq_env(env, project_id="my-gcp-proj")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    with mock.patch(
        "usma.sources.bigquery.provider.BigQueryProvider.discover",
        return_value=[_fake_bq_descriptor("my-gcp-proj", 111)],
    ):
        app = create_app(runs_dir=runs_dir, env_file=env)
        client = TestClient(app)
        r = client.post(
            "/api/config/discover-bigquery-projects",
            headers={"X-SMA-API": "1"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    names = [w["name"] for w in body["workspaces"]]
    assert names == ["my-gcp-proj"]
