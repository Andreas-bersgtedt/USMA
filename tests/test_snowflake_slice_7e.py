"""Slice 7-E / 7-E.1 — Snowflake SPA Configuration wiring.

Mirrors :mod:`tests.test_bigquery_slice_5d` for the Snowflake source.
Covers the end-to-end backend touchpoints introduced by Slice 7-E (and
rewired for OAuth in 7-E.1) so the SPA Configuration page can drive a
Snowflake inventory without any CLI flags:

1. ``read_config`` / ``write_config`` round-trip ``SMA_SOURCE_TYPE=snowflake``
   and the ``SNOWFLAKE_*`` env vars (plain + OAuth secrets + UI-only
   platform hint).
2. ``discover_snowflake_databases`` uses Snowflake OAuth (no Azure SP
   creds required) and delegates to :meth:`SnowflakeProvider.discover`.
3. ``validate_config_live`` routes Snowflake scopes through the provider's
   validate hook and skips the Azure/Synapse data-plane gauntlet, even when
   no AZURE_* env vars are set.
4. The new ``POST /api/config/discover-snowflake-databases`` route is
   wired and returns the expected shape.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from usma.sources import SourceDescriptor, SourceType
from usma.web.config_io import (
    discover_snowflake_databases,
    read_config,
    validate_config_live,
    write_config,
)
from usma.web.schemas import (
    AppConfigUpdate,
    AzureConfigUpdate,
)


def _seed_sf_env(env_file: Path, *, account: str | None = "myorg-myacct") -> None:
    """Seed a .env with only the Snowflake-relevant fields (no AZURE_*)."""
    lines = ["SMA_SOURCE_TYPE=snowflake"]
    if account is not None:
        lines.append(f"SNOWFLAKE_ACCOUNT={account}")
        lines.append("SNOWFLAKE_USER=svc_usma")
        lines.append("SNOWFLAKE_ROLE=USMA_ROLE")
        lines.append("SNOWFLAKE_WAREHOUSE=USMA_WH")
        lines.append("SNOWFLAKE_OAUTH_CLIENT_ID=seed-cid")
        lines.append("SNOWFLAKE_OAUTH_CLIENT_SECRET=seed-csec")
        lines.append("SNOWFLAKE_OAUTH_REFRESH_TOKEN=seed-rt")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fake_sf_descriptor(
    account: str = "myorg-myacct",
    platform: str = "aws",
    region: str = "us-east-1",
) -> SourceDescriptor:
    return SourceDescriptor(
        type=SourceType.SNOWFLAKE,
        id=account,
        display_name=account,
        extras={"platform": platform, "region": region, "edition": "ENTERPRISE"},
    )


# ---------------------------------------------------------------------------
# read/write round-trip
# ---------------------------------------------------------------------------


def test_read_config_returns_snowflake_when_env_says_snowflake(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_sf_env(env, account="myorg-myacct")
    cfg = read_config(env)
    assert cfg.azure.source_type == "snowflake"
    assert cfg.azure.snowflake_account == "myorg-myacct"
    assert cfg.azure.snowflake_user == "svc_usma"
    assert cfg.azure.snowflake_role == "USMA_ROLE"
    assert cfg.azure.snowflake_warehouse == "USMA_WH"
    assert cfg.azure.snowflake_oauth_client_id == "seed-cid"
    # Secrets must collapse to ``set``/``unset``.
    assert cfg.azure.snowflake_oauth_client_secret == "set"
    assert cfg.azure.snowflake_oauth_refresh_token == "set"
    assert cfg.azure.snowflake_oauth_token == "unset"


def test_write_config_persists_snowflake_fields(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(
            source_type="snowflake",
            snowflake_account="myorg-myacct",
            snowflake_user="svc_usma",
            snowflake_role="USMA_ROLE",
            snowflake_warehouse="USMA_WH",
            snowflake_oauth_client_id="new-cid",
            snowflake_oauth_client_secret="new-csec",
            snowflake_oauth_refresh_token="new-rt",
            snowflake_oauth_token="new-pre-minted",
            snowflake_platform="aws",
        ),
    )
    warnings = write_config(env, update)
    contents = env.read_text(encoding="utf-8")
    assert "SMA_SOURCE_TYPE=snowflake" in contents
    assert "SNOWFLAKE_ACCOUNT=myorg-myacct" in contents
    assert "SNOWFLAKE_USER=svc_usma" in contents
    assert "SNOWFLAKE_ROLE=USMA_ROLE" in contents
    assert "SNOWFLAKE_WAREHOUSE=USMA_WH" in contents
    assert "SNOWFLAKE_OAUTH_CLIENT_ID=new-cid" in contents
    assert "SNOWFLAKE_OAUTH_CLIENT_SECRET=new-csec" in contents
    assert "SNOWFLAKE_OAUTH_REFRESH_TOKEN=new-rt" in contents
    assert "SNOWFLAKE_OAUTH_TOKEN=new-pre-minted" in contents
    assert "SMA_SNOWFLAKE_PLATFORM=aws" in contents
    # All three OAuth secret writes must warn that a secret was written.
    assert any("SNOWFLAKE_OAUTH_CLIENT_SECRET" in w for w in warnings)
    assert any("SNOWFLAKE_OAUTH_REFRESH_TOKEN" in w for w in warnings)
    assert any("SNOWFLAKE_OAUTH_TOKEN" in w for w in warnings)
    # Round-trip: reads collapse the secrets back to ``set``.
    cfg = read_config(env)
    assert cfg.azure.snowflake_oauth_client_secret == "set"
    assert cfg.azure.snowflake_oauth_refresh_token == "set"
    assert cfg.azure.snowflake_oauth_token == "set"
    assert cfg.azure.snowflake_platform == "aws"


def test_write_config_clears_snowflake_secret_when_blank(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_sf_env(env)
    # Sanity: secret is set after seed.
    assert read_config(env).azure.snowflake_oauth_refresh_token == "set"
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(snowflake_oauth_refresh_token=""),
    )
    write_config(env, update)
    assert read_config(env).azure.snowflake_oauth_refresh_token == "unset"


# ---------------------------------------------------------------------------
# discover_snowflake_databases
# ---------------------------------------------------------------------------


def test_discover_snowflake_returns_account_summary(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_sf_env(env, account="myorg-myacct")
    with mock.patch(
        "usma.sources.snowflake.provider.SnowflakeProvider.discover",
        return_value=[_fake_sf_descriptor("myorg-myacct", "aws", "us-east-1")],
    ):
        checks, workspaces = discover_snowflake_databases(env)
    assert [w.name for w in workspaces] == ["myorg-myacct"]
    # The configured account from SNOWFLAKE_ACCOUNT should be flagged.
    assert workspaces[0].is_current is True
    # Platform + region surface via ``resource_group`` + ``location``.
    assert workspaces[0].resource_group == "aws"
    assert "us-east-1" in (workspaces[0].location or "")
    assert any(c.name == "Snowflake account reachable" and c.ok for c in checks)


def test_discover_snowflake_swallows_provider_errors(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_sf_env(env)
    with mock.patch(
        "usma.sources.snowflake.provider.SnowflakeProvider.discover",
        side_effect=RuntimeError("OAuth token-request failed"),
    ):
        checks, workspaces = discover_snowflake_databases(env)
    assert workspaces == []
    failing = [c for c in checks if not c.ok]
    assert failing and "OAuth token-request failed" in (failing[0].detail or "")


def test_discover_snowflake_does_not_require_azure_creds(tmp_path: Path) -> None:
    """Key-pair JWT only: a .env with zero AZURE_* fields must still work."""
    env = tmp_path / ".env"
    env.write_text("SMA_SOURCE_TYPE=snowflake\n", encoding="utf-8")
    with mock.patch(
        "usma.sources.snowflake.provider.SnowflakeProvider.discover",
        return_value=[_fake_sf_descriptor()],
    ):
        checks, workspaces = discover_snowflake_databases(env)
    assert [w.name for w in workspaces] == ["myorg-myacct"]
    assert all(c.ok for c in checks)


# ---------------------------------------------------------------------------
# validate_config_live — Snowflake branch
# ---------------------------------------------------------------------------


def test_validate_config_live_uses_snowflake_provider(tmp_path: Path) -> None:
    """When SMA_SOURCE_TYPE=snowflake, validate_config_live must call
    SnowflakeProvider.validate and skip the Synapse SQL probes — even
    with no AZURE_* env vars set."""
    from usma.sources import (
        ConfigCheck as ProviderConfigCheck,
    )

    env = tmp_path / ".env"
    _seed_sf_env(env, account="myorg-myacct")

    fake_check = ProviderConfigCheck(
        name="Snowflake stub", ok=True, detail="stub", category="Control plane",
    )
    with mock.patch(
        "usma.sources.snowflake.provider.SnowflakeProvider.validate",
        return_value=[fake_check],
    ) as sf_validate, mock.patch(
        "usma.sources.snowflake.provider.SnowflakeProvider.discover",
        return_value=[_fake_sf_descriptor()],
    ):
        checks, workspaces = validate_config_live(env)
    assert sf_validate.called
    descriptor = sf_validate.call_args[0][0]
    assert descriptor.type is SourceType.SNOWFLAKE
    assert descriptor.id == "myorg-myacct"
    assert [w.name for w in workspaces] == ["myorg-myacct"]
    assert any(c.name == "Snowflake stub" and c.ok for c in checks)
    # Synapse data-plane probes must not appear in the Snowflake path.
    names = [c.name for c in checks]
    assert not any("Serverless SQL" in n for n in names)
    assert not any("Spark Livy" in n for n in names)
    # The Azure-required-fields gate must not have short-circuited.
    assert not any(
        n == "Live connectivity" and "skipped" in (c.detail or "")
        for n, c in zip(names, checks)
    )


def test_validate_config_live_snowflake_missing_account_short_circuits(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    _seed_sf_env(env, account=None)
    checks, workspaces = validate_config_live(env)
    assert workspaces == []
    failing = [c for c in checks if not c.ok]
    assert any(c.name == "Snowflake account configured" for c in failing)


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


def test_discover_snowflake_route(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from usma.web.app import create_app

    env = tmp_path / ".env"
    _seed_sf_env(env, account="myorg-myacct")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    with mock.patch(
        "usma.sources.snowflake.provider.SnowflakeProvider.discover",
        return_value=[_fake_sf_descriptor("myorg-myacct", "aws", "us-east-1")],
    ):
        app = create_app(runs_dir=runs_dir, env_file=env)
        client = TestClient(app)
        r = client.post(
            "/api/config/discover-snowflake-databases",
            headers={"X-SMA-API": "1"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    names = [w["name"] for w in body["workspaces"]]
    assert names == ["myorg-myacct"]
