"""Tests for run-manifest schema v2 (Phase 1)."""
from __future__ import annotations

import json
from pathlib import Path

from usma.reporting.run_manifest import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    ScopeRecord,
    build_manifest,
    load_manifest,
    save_manifest,
)


def _write_artifact(p: Path, payload: dict) -> None:
    p.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# v2 baseline
# ---------------------------------------------------------------------------


def test_schema_version_is_two() -> None:
    assert MANIFEST_SCHEMA_VERSION == 2


def test_build_manifest_stamps_v2_fields(tmp_path: Path) -> None:
    _write_artifact(tmp_path / "pipelines.json", {"v": 1})
    m = build_manifest(
        tmp_path,
        workspace_name="ws-a",
        subscription_id="sub-x",
        resource_group="rg-1",
        sma_version="1.2.3",
        file_names=["pipelines.json"],
    )
    assert m.schema_version == 2
    assert len(m.scopes) == 1
    sc = m.scopes[0]
    assert sc.source_type == "synapse_workspace"
    assert sc.scope_id == "ws-a"
    assert sc.display_name == "ws-a"
    assert sc.subscription_id == "sub-x"
    assert sc.resource_group == "rg-1"
    # Per-artifact provenance
    assert all(a.source_type == "synapse_workspace" for a in m.artifacts)
    assert all(a.scope_id == "ws-a" for a in m.artifacts)


def test_build_manifest_accepts_explicit_scopes(tmp_path: Path) -> None:
    _write_artifact(tmp_path / "pipelines.json", {"v": 1})
    scope = ScopeRecord(
        source_type="adf",
        scope_id="factory-a",
        display_name="factory-a",
        subscription_id="sub-x",
        resource_group="rg-2",
    )
    m = build_manifest(
        tmp_path,
        workspace_name="ws-a",  # legacy fields still populated
        subscription_id="sub-x",
        resource_group="rg-1",
        sma_version="1.2.3",
        file_names=["pipelines.json"],
        scopes=[scope],
    )
    assert m.scopes == [scope]
    # Default stamping uses the first scope when caller didn't override.
    assert m.artifacts[0].source_type == "adf"
    assert m.artifacts[0].scope_id == "factory-a"


# ---------------------------------------------------------------------------
# v1 -> v2 read-only upgrade
# ---------------------------------------------------------------------------


def _v1_payload() -> dict:
    return {
        "workspace_name": "ws-legacy",
        "subscription_id": "sub-legacy",
        "resource_group": "rg-legacy",
        "generated_at": "2025-01-01T00:00:00+00:00",
        "sma_version": "0.9.0",
        "artifacts": [
            {
                "name": "pipelines",
                "file_name": "pipelines.json",
                "sha256": "deadbeef" * 8,
                "size_bytes": 10,
                "generated_at": "2025-01-01T00:00:00+00:00",
                "record_count": 3,
            },
        ],
        # NB: no schema_version, no scopes, no source_type/scope_id on artifact
    }


def test_load_manifest_auto_upgrades_v1(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps(_v1_payload()), encoding="utf-8",
    )
    m = load_manifest(tmp_path)
    assert m is not None
    assert m.schema_version == 2
    assert len(m.scopes) == 1
    sc = m.scopes[0]
    assert sc.source_type == "synapse_workspace"
    assert sc.scope_id == "ws-legacy"
    assert sc.subscription_id == "sub-legacy"
    assert sc.resource_group == "rg-legacy"
    # Each artifact got stamped with synthetic provenance.
    assert m.artifacts[0].source_type == "synapse_workspace"
    assert m.artifacts[0].scope_id == "ws-legacy"


def test_load_manifest_v1_upgrade_is_read_only(tmp_path: Path) -> None:
    """v1 file on disk must not be rewritten when read."""
    path = tmp_path / MANIFEST_FILENAME
    original = json.dumps(_v1_payload())
    path.write_text(original, encoding="utf-8")
    load_manifest(tmp_path)
    on_disk = path.read_text(encoding="utf-8")
    assert on_disk == original  # untouched
    parsed = json.loads(on_disk)
    assert "schema_version" not in parsed
    assert "scopes" not in parsed
    assert "source_type" not in parsed["artifacts"][0]


def test_load_manifest_v2_passthrough(tmp_path: Path) -> None:
    _write_artifact(tmp_path / "pipelines.json", {"v": 1})
    built = build_manifest(
        tmp_path,
        workspace_name="ws-a", subscription_id="sub", resource_group="rg",
        sma_version="1.0.0", file_names=["pipelines.json"],
    )
    save_manifest(built, tmp_path)
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded.schema_version == 2
    assert loaded.scopes[0].scope_id == "ws-a"
    assert loaded.artifacts[0].source_type == "synapse_workspace"


def test_load_manifest_explicit_v1_field_upgrades(tmp_path: Path) -> None:
    payload = _v1_payload()
    payload["schema_version"] = 1
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8",
    )
    m = load_manifest(tmp_path)
    assert m is not None
    assert m.schema_version == 2
    assert m.scopes[0].scope_id == "ws-legacy"


# ---------------------------------------------------------------------------
# Phase 4.7 — platform discriminator on ScopeRecord
# ---------------------------------------------------------------------------


def test_v1_upgrade_leaves_platform_none(tmp_path: Path) -> None:
    """Pre-4.7 manifests have no platform field; upgrader must not invent one."""
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps(_v1_payload()), encoding="utf-8",
    )
    m = load_manifest(tmp_path)
    assert m is not None
    assert m.scopes[0].platform is None


def test_synthetic_synapse_scope_has_no_platform(tmp_path: Path) -> None:
    """build_manifest's synthetic Synapse fallback omits platform."""
    _write_artifact(tmp_path / "pipelines.json", {"v": 1})
    m = build_manifest(
        tmp_path,
        workspace_name="ws-a",
        subscription_id="sub-x",
        resource_group="rg-1",
        sma_version="1.2.3",
        file_names=["pipelines.json"],
    )
    assert m.scopes[0].platform is None


def test_explicit_aws_databricks_scope_round_trips(tmp_path: Path) -> None:
    """AWS Databricks scope serialises through save -> load with platform intact."""
    _write_artifact(tmp_path / "databricks_workflows.json", {"v": 1})
    aws_scope = ScopeRecord(
        source_type="databricks",
        scope_id="acme-prod",
        display_name="acme-prod",
        platform="aws",
    )
    built = build_manifest(
        tmp_path,
        workspace_name="acme-prod",
        subscription_id="",
        resource_group="",
        sma_version="1.2.3",
        file_names=["databricks_workflows.json"],
        scopes=[aws_scope],
    )
    save_manifest(built, tmp_path)
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded.scopes[0].platform == "aws"
    assert loaded.artifacts[0].source_type == "databricks"
    assert loaded.artifacts[0].scope_id == "acme-prod"


def test_azure_databricks_scope_can_be_explicit(tmp_path: Path) -> None:
    """Multi-cloud-aware writers may stamp platform='azure' explicitly."""
    _write_artifact(tmp_path / "databricks_workflows.json", {"v": 1})
    azure_scope = ScopeRecord(
        source_type="databricks",
        scope_id="contoso-prod",
        display_name="contoso-prod",
        subscription_id="sub-x",
        resource_group="rg-prod",
        platform="azure",
    )
    built = build_manifest(
        tmp_path,
        workspace_name="contoso-prod",
        subscription_id="sub-x",
        resource_group="rg-prod",
        sma_version="1.2.3",
        file_names=["databricks_workflows.json"],
        scopes=[azure_scope],
    )
    save_manifest(built, tmp_path)
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded.scopes[0].platform == "azure"
