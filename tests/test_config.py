
import pytest

from usma.config import load_config


def test_load_config_missing_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
              "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP", "SYNAPSE_WORKSPACE_NAME"):
        monkeypatch.delenv(k, raising=False)
    # Use an empty .env file so dotenv doesn't load the repo's real one.
    empty = tmp_path / ".env"
    empty.write_text("")
    with pytest.raises(RuntimeError, match="Missing required environment variables"):
        load_config(empty)


def test_load_config_ok(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub")
    monkeypatch.setenv("SYNAPSE_RESOURCE_GROUP", "rg")
    monkeypatch.setenv("SYNAPSE_WORKSPACE_NAME", "ws")
    monkeypatch.setenv("SMA_OUTPUT_DIR", str(tmp_path / "out"))
    cfg = load_config(tmp_path / ".env_does_not_exist")
    assert cfg.azure.workspace_name == "ws"
    assert cfg.output_dir.exists()


# ---------------------------------------------------------------------------
# Phase 4.7 — Databricks-on-AWS legacy single-scope path
# ---------------------------------------------------------------------------


def _clear_aad_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP",
        "SYNAPSE_WORKSPACE_NAME", "DATABRICKS_HOST",
        "DATABRICKS_ACCOUNT_ID", "DATABRICKS_WORKSPACE_URL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_load_config_aws_databricks_explicit_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """``SMA_DATABRICKS_PLATFORM=aws`` + ``DATABRICKS_HOST`` builds a single
    AWS Databricks scope and bypasses the AAD env-var gate."""
    _clear_aad_env(monkeypatch)
    monkeypatch.setenv("SMA_SOURCE_TYPE", "databricks")
    monkeypatch.setenv("SMA_DATABRICKS_PLATFORM", "aws")
    monkeypatch.setenv("DATABRICKS_HOST", "acme-prod.cloud.databricks.com")
    monkeypatch.setenv("SMA_OUTPUT_DIR", str(tmp_path / "out"))

    cfg = load_config(tmp_path / ".env_does_not_exist")

    assert len(cfg.scopes) == 1
    from usma.sources import SourceType
    s = cfg.scopes[0]
    assert s.type is SourceType.DATABRICKS
    assert s.extras.get("platform") == "aws"
    assert s.subscription_id is None
    # AAD fields are empty strings, not raising.
    assert cfg.azure.tenant_id == ""


def test_load_config_aws_databricks_account_id_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Account-API mode: only ``DATABRICKS_ACCOUNT_ID`` set (no host).
    The legacy single-scope returns an account-id placeholder; the
    provider's ``discover()`` does the real enumeration."""
    _clear_aad_env(monkeypatch)
    monkeypatch.setenv("SMA_SOURCE_TYPE", "databricks")
    monkeypatch.setenv("SMA_DATABRICKS_PLATFORM", "aws")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_ID", "acct-1234")
    monkeypatch.setenv("SMA_OUTPUT_DIR", str(tmp_path / "out"))

    cfg = load_config(tmp_path / ".env_does_not_exist")

    assert len(cfg.scopes) == 1
    s = cfg.scopes[0]
    assert s.extras.get("platform") == "aws"
    assert s.extras.get("account_id") == "acct-1234"


def test_load_config_aws_databricks_requires_host_or_account_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Neither ``DATABRICKS_HOST`` nor ``DATABRICKS_ACCOUNT_ID`` set is an
    error — the AWS provider has no other discovery surface."""
    _clear_aad_env(monkeypatch)
    monkeypatch.setenv("SMA_SOURCE_TYPE", "databricks")
    monkeypatch.setenv("SMA_DATABRICKS_PLATFORM", "aws")
    monkeypatch.setenv("SMA_OUTPUT_DIR", str(tmp_path / "out"))
    empty = tmp_path / ".env"
    empty.write_text("")
    with pytest.raises(RuntimeError, match="DATABRICKS_HOST"):
        load_config(empty)


def test_load_config_azure_databricks_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """``SMA_DATABRICKS_PLATFORM`` defaulting to ``azure`` keeps the legacy
    Azure single-scope path: AAD vars are still required."""
    _clear_aad_env(monkeypatch)
    monkeypatch.setenv("SMA_SOURCE_TYPE", "databricks")
    monkeypatch.setenv("SMA_OUTPUT_DIR", str(tmp_path / "out"))
    empty = tmp_path / ".env"
    empty.write_text("")
    with pytest.raises(RuntimeError, match="Missing required environment variables"):
        load_config(empty)
