"""Phase 2.5 — tests for the ADF source-type plumbing.

Covers the four end-to-end touchpoints that let a user start an ADF
inventory from the web UI without any CLI flags:

1. ``read_config`` / ``write_config`` round-trip ``SMA_SOURCE_TYPE``.
2. ``load_config`` materialises the right :class:`SourceDescriptor`
   on ``cfg.scopes`` when ``SMA_SOURCE_TYPE=adf``.
3. The pipelines module factory routes ADF scopes through
   ``PipelinesAnalyzer.for_descriptor`` (so the right collector is wired).
4. ``validate_config_live`` skips the Synapse data-plane gauntlet and
   delegates to ``AdfProvider.validate`` when source_type=adf.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock


from usma.config import load_config
from usma.sources import SourceType
from usma.web.config_io import (
    read_config,
    validate_config_live,
    write_config,
)
from usma.web.schemas import (
    AppConfigUpdate,
    AzureConfigUpdate,
)


def _seed_env(env_file: Path, *, source_type: str | None = None) -> None:
    """Write a minimally valid .env so load_config / validators don't bail."""
    lines = [
        "AZURE_TENANT_ID=11111111-1111-1111-1111-111111111111",
        "AZURE_CLIENT_ID=22222222-2222-2222-2222-222222222222",
        "AZURE_CLIENT_SECRET=secret",
        "AZURE_SUBSCRIPTION_ID=33333333-3333-3333-3333-333333333333",
        "SYNAPSE_RESOURCE_GROUP=rg-data",
        "SYNAPSE_WORKSPACE_NAME=my-thing",
    ]
    if source_type is not None:
        lines.append(f"SMA_SOURCE_TYPE={source_type}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_config_defaults_source_type_to_synapse(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env)
    cfg = read_config(env)
    assert cfg.azure.source_type == "synapse_workspace"


def test_read_config_returns_adf_when_env_says_adf(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env, source_type="adf")
    cfg = read_config(env)
    assert cfg.azure.source_type == "adf"


def test_read_config_falls_back_for_unknown_source_type(tmp_path: Path) -> None:
    """Bad SMA_SOURCE_TYPE values silently degrade to synapse_workspace
    so a typo in .env can't brick the Configuration page."""
    env = tmp_path / ".env"
    _seed_env(env, source_type="not_a_real_source")
    cfg = read_config(env)
    assert cfg.azure.source_type == "synapse_workspace"


def test_write_config_persists_source_type(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env)
    update = AppConfigUpdate(azure=AzureConfigUpdate(source_type="adf"))
    write_config(env, update)
    contents = env.read_text(encoding="utf-8")
    assert "SMA_SOURCE_TYPE=adf" in contents
    # Subsequent read sees the new value.
    assert read_config(env).azure.source_type == "adf"


def test_load_config_builds_adf_scope_when_source_type_is_adf(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env, source_type="adf")
    with mock.patch.dict(os.environ, {}, clear=False):
        # load_config relies on os.environ via dotenv override=True;
        # clear any inherited keys that would mask the .env file.
        for k in (
            "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
            "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP",
            "SYNAPSE_WORKSPACE_NAME", "SMA_SOURCE_TYPE",
        ):
            os.environ.pop(k, None)
        cfg = load_config(env)
    assert cfg.scopes, "load_config must materialise an implicit scope"
    primary = cfg.primary_scope()
    assert primary is not None
    assert primary.type is SourceType.ADF
    assert "Microsoft.DataFactory/factories/my-thing" in primary.id
    assert primary.resource_group == "rg-data"


def test_load_config_default_scope_is_synapse(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _seed_env(env)  # no SMA_SOURCE_TYPE
    with mock.patch.dict(os.environ, {}, clear=False):
        for k in (
            "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
            "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP",
            "SYNAPSE_WORKSPACE_NAME", "SMA_SOURCE_TYPE",
        ):
            os.environ.pop(k, None)
        cfg = load_config(env)
    primary = cfg.primary_scope()
    assert primary is not None
    assert primary.type is SourceType.SYNAPSE_WORKSPACE


def test_load_config_builds_databricks_scope_with_url_from_env(tmp_path: Path) -> None:
    """SMA_SOURCE_TYPE=databricks + DATABRICKS_WORKSPACE_URL → Databricks scope
    with workspace_url stamped into extras (no ARM call)."""
    env = tmp_path / ".env"
    _seed_env(env, source_type="databricks")
    env.write_text(
        env.read_text(encoding="utf-8")
        + "DATABRICKS_WORKSPACE_URL=https://adb-1234.5.azuredatabricks.net\n",
        encoding="utf-8",
    )
    with mock.patch.dict(os.environ, {}, clear=False):
        for k in (
            "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
            "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP",
            "SYNAPSE_WORKSPACE_NAME", "SMA_SOURCE_TYPE",
            "DATABRICKS_WORKSPACE_URL",
        ):
            os.environ.pop(k, None)
        cfg = load_config(env)
    primary = cfg.primary_scope()
    assert primary is not None
    assert primary.type is SourceType.DATABRICKS
    assert "Microsoft.Databricks/workspaces/my-thing" in primary.id
    assert primary.extras.get("workspace_url") == "https://adb-1234.5.azuredatabricks.net"


def test_load_config_databricks_scope_without_url_falls_back_silently(
    tmp_path: Path, monkeypatch
) -> None:
    """When DATABRICKS_WORKSPACE_URL is missing and the live ARM probe
    fails (e.g. offline), load_config must still return a Databricks
    descriptor — just with an empty extras dict."""
    env = tmp_path / ".env"
    _seed_env(env, source_type="databricks")
    for k in (
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP",
        "SYNAPSE_WORKSPACE_NAME", "SMA_SOURCE_TYPE",
        "DATABRICKS_WORKSPACE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    # Force ARM probe to fail.
    monkeypatch.setattr(
        "azure.mgmt.databricks.AzureDatabricksManagementClient",
        mock.Mock(side_effect=RuntimeError("offline")),
    )
    cfg = load_config(env)
    primary = cfg.primary_scope()
    assert primary is not None
    assert primary.type is SourceType.DATABRICKS
    assert primary.extras.get("workspace_url") is None


def test_load_config_bigquery_pins_workspace_to_project_id_ignoring_stale_synapse_vars(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: when SMA_SOURCE_TYPE=bigquery, ``azure.workspace_name``
    and ``azure.resource_group`` MUST be derived from the GCP project id
    — never from ``SYNAPSE_WORKSPACE_NAME`` / ``SYNAPSE_RESOURCE_GROUP``
    left over from a prior Synapse / ADF / Databricks session. Without
    this clamp the estate page renders rows like
    ``Dag2ADF | My First Project | Bigquery`` (stale RG + stale workspace
    display name leaking into the run manifest)."""
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "SMA_SOURCE_TYPE=bigquery",
            "SMA_GCP_PROJECT_ID=my-first-project-123456",
            # Stale Synapse / ADF leftovers — must NOT leak.
            "SYNAPSE_RESOURCE_GROUP=Dag2ADF",
            "SYNAPSE_WORKSPACE_NAME=My First Project",
            "AZURE_SUBSCRIPTION_ID=68e360fa-2a87-4ea1-96e3-f4101bea3709",
        ]) + "\n",
        encoding="utf-8",
    )
    for k in (
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID", "SYNAPSE_RESOURCE_GROUP",
        "SYNAPSE_WORKSPACE_NAME", "SMA_SOURCE_TYPE", "SMA_GCP_PROJECT_ID",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(env)
    assert cfg.azure.workspace_name == "my-first-project-123456"
    assert cfg.azure.resource_group == ""
    assert cfg.azure.gcp_project_id == "my-first-project-123456"


def test_pipelines_factory_dispatches_to_for_descriptor_for_adf() -> None:
    """The ``_pipelines`` factory routes ADF scopes through
    ``PipelinesAnalyzer.for_descriptor`` instead of the default ctor.

    We patch both code paths and confirm only ``for_descriptor`` is
    called when ``cfg.primary_scope.type is ADF``. The analyzer + its
    ``.run()`` return value are stubbed so no real Azure calls happen.
    """
    from usma.config import AppConfig, AzureConfig, SqlConfig
    from usma.modules import MODULE_REGISTRY
    from usma.progress import NullProgress
    from usma.sources import SourceDescriptor

    cfg = AppConfig(
        azure=AzureConfig(
            tenant_id="t", client_id="c", client_secret="s",
            subscription_id="sub", resource_group="rg", workspace_name="adf-x",
        ),
        sql=SqlConfig(),
        output_dir=Path("./output"),
        scopes=(SourceDescriptor(
            type=SourceType.ADF,
            id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.DataFactory/factories/adf-x",
            display_name="adf-x",
            subscription_id="sub",
            resource_group="rg",
        ),),
    )

    class _Stub:
        def run(self) -> str:
            return "RESULT"

    with mock.patch(
        "usma.modules.pipelines.analyzer.PipelinesAnalyzer.for_descriptor",
        return_value=_Stub(),
    ) as for_desc, mock.patch(
        "usma.modules.pipelines.analyzer.PipelinesAnalyzer.__init__",
        return_value=None,
    ) as ctor:
        result, _writer = MODULE_REGISTRY["pipelines"](cfg, NullProgress())
    assert result == "RESULT"
    assert for_desc.called, "ADF scope must go through for_descriptor"
    assert not ctor.called, "default ctor must not be invoked for ADF scopes"


def test_validate_config_live_uses_adf_provider(tmp_path: Path) -> None:
    """When SMA_SOURCE_TYPE=adf, validate_config_live must call
    AdfProvider.validate and *not* attempt the Synapse SQL probes."""
    from usma.sources import ConfigCheck as ProviderConfigCheck

    env = tmp_path / ".env"
    _seed_env(env, source_type="adf")

    fake_check = ProviderConfigCheck(
        name="ADF stub", ok=True, detail="stub", category="Control plane",
    )
    with mock.patch(
        "usma.sources.adf.provider.AdfProvider.validate",
        return_value=[fake_check],
    ) as adf_validate, mock.patch(
        "usma.sources.adf.provider.AdfProvider.discover",
        return_value=[],
    ):
        checks, workspaces = validate_config_live(env)
    assert adf_validate.called
    assert workspaces == []
    # The stub check must round-trip into the API-side Pydantic ConfigCheck.
    assert any(c.name == "ADF stub" and c.ok for c in checks)
    # The Synapse data-plane categories should NOT appear in the ADF path.
    names = [c.name for c in checks]
    assert not any("Serverless SQL" in n for n in names)
    assert not any("Spark Livy" in n for n in names)
