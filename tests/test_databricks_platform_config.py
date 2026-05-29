"""Phase 4.7-D — Databricks platform SPA wiring (read/write round-trip).

Covers the backend half of the Configuration page's new Azure | AWS
sub-radio: the ``databricks_platform`` field on
``AzureConfigPublic`` / ``AzureConfigUpdate`` must round-trip cleanly
to the ``SMA_DATABRICKS_PLATFORM`` env var without disturbing other
keys, and ``read_config`` must normalise unknown / absent values to
``None`` so the SPA can render the inferred Azure default explicitly.
"""
from __future__ import annotations

from pathlib import Path

from usma.web.config_io import read_config, write_config
from usma.web.schemas import AppConfigUpdate, AzureConfigUpdate


def test_read_config_databricks_platform_absent_is_none(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SMA_SOURCE_TYPE=databricks\n", encoding="utf-8")
    cfg = read_config(env)
    assert cfg.azure.source_type == "databricks"
    assert cfg.azure.databricks_platform is None


def test_read_config_databricks_platform_aws_is_aws(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "SMA_SOURCE_TYPE=databricks\nSMA_DATABRICKS_PLATFORM=aws\n",
        encoding="utf-8",
    )
    cfg = read_config(env)
    assert cfg.azure.databricks_platform == "aws"


def test_read_config_databricks_platform_azure_is_azure(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "SMA_SOURCE_TYPE=databricks\nSMA_DATABRICKS_PLATFORM=azure\n",
        encoding="utf-8",
    )
    cfg = read_config(env)
    assert cfg.azure.databricks_platform == "azure"


def test_read_config_databricks_platform_unknown_normalises_to_none(
    tmp_path: Path,
) -> None:
    """Garbage values fall back to ``None`` (the SPA infers Azure)."""
    env = tmp_path / ".env"
    env.write_text(
        "SMA_SOURCE_TYPE=databricks\nSMA_DATABRICKS_PLATFORM=gcp\n",
        encoding="utf-8",
    )
    cfg = read_config(env)
    assert cfg.azure.databricks_platform is None


def test_write_config_persists_databricks_platform_aws(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(
            source_type="databricks",
            databricks_platform="aws",
        ),
    )
    write_config(env, update)
    contents = env.read_text(encoding="utf-8")
    assert "SMA_DATABRICKS_PLATFORM=aws" in contents
    cfg = read_config(env)
    assert cfg.azure.databricks_platform == "aws"


def test_write_config_empty_string_clears_databricks_platform(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "SMA_SOURCE_TYPE=databricks\nSMA_DATABRICKS_PLATFORM=aws\n",
        encoding="utf-8",
    )
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(databricks_platform=""),
    )
    write_config(env, update)
    contents = env.read_text(encoding="utf-8")
    assert "SMA_DATABRICKS_PLATFORM" not in contents
    cfg = read_config(env)
    assert cfg.azure.databricks_platform is None


def test_write_config_leaves_databricks_platform_untouched_when_none(
    tmp_path: Path,
) -> None:
    """``databricks_platform=None`` in the update means 'leave as-is'."""
    env = tmp_path / ".env"
    env.write_text(
        "SMA_SOURCE_TYPE=databricks\nSMA_DATABRICKS_PLATFORM=aws\n",
        encoding="utf-8",
    )
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(source_type="databricks"),
    )
    write_config(env, update)
    cfg = read_config(env)
    assert cfg.azure.databricks_platform == "aws"


def test_write_config_round_trips_platform_alongside_other_azure_fields(
    tmp_path: Path,
) -> None:
    """Switching to AWS Databricks at the same time as filling AAD env
    must persist both — they share the same Azure update payload."""
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    update = AppConfigUpdate(
        azure=AzureConfigUpdate(
            source_type="databricks",
            databricks_platform="aws",
            tenant_id="tid",
            client_id="cid",
            subscription_id="sub",
            resource_group="rg",
            workspace_name="ws",
        ),
    )
    write_config(env, update)
    cfg = read_config(env)
    assert cfg.azure.source_type == "databricks"
    assert cfg.azure.databricks_platform == "aws"
    assert cfg.azure.tenant_id == "tid"
    assert cfg.azure.subscription_id == "sub"
