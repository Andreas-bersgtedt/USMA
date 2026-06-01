"""Migration that strips inherited Azure identity from non-Azure runs.

Covers :func:`usma.web.jobs.migrate_run_attribution` end-to-end:

* BigQuery / Snowflake-on-AWS / Databricks-on-AWS runs are rewritten.
* Azure-native runs (Synapse, ADF, Databricks-on-Azure, Snowflake-on-Azure)
  are left alone.
* ``dry_run=True`` reports without writing.
* Malformed / scopeless runs are skipped without crashing.
"""
from __future__ import annotations

import json
from pathlib import Path

from usma.web.jobs import migrate_run_attribution


def _write_run(
    runs_dir: Path,
    run_id: str,
    *,
    scope: dict,
    tenant_id: str | None = "azure-tenant",
    subscription_id: str | None = "azure-sub",
    resource_group: str | None = "azure-rg",
    workspace_name: str | None = "azure-ws",
) -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    meta = {
        "id": run_id,
        "label": None,
        "status": "ok",
        "started_at": "2025-01-01T00:00:00+00:00",
        "config_hash": "abc",
        "modules": [],
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "workspace_name": workspace_name,
        "scopes": [scope],
    }
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    return run_dir / "run.json"


def test_bigquery_run_is_rewritten(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    path = _write_run(
        runs, "20250101T000000Z-aaaaaaaa",
        scope={
            "source_type": "bigquery",
            "id": "my-gcp-project",
            "display_name": "my-gcp-project",
        },
    )
    result = migrate_run_attribution(runs)
    assert result["updated"] == ["20250101T000000Z-aaaaaaaa"]
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["tenant_id"] is None
    assert after["subscription_id"] is None
    assert after["resource_group"] is None
    assert after["workspace_name"] == "my-gcp-project"


def test_databricks_aws_run_is_rewritten(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_run(
        runs, "20250101T000000Z-bbbbbbbb",
        scope={
            "source_type": "databricks",
            "id": "acct-1",
            "display_name": "prod-ws",
            "extras": {"platform": "aws"},
        },
    )
    result = migrate_run_attribution(runs)
    assert result["updated"] == ["20250101T000000Z-bbbbbbbb"]


def test_azure_run_is_left_alone(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    path = _write_run(
        runs, "20250101T000000Z-cccccccc",
        scope={
            "source_type": "synapse_workspace",
            "id": "/subscriptions/azure-sub/resourceGroups/rg/...",
            "display_name": "azure-ws",
        },
    )
    result = migrate_run_attribution(runs)
    assert result["updated"] == []
    assert result["skipped"] == ["20250101T000000Z-cccccccc"]
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["tenant_id"] == "azure-tenant"
    assert after["subscription_id"] == "azure-sub"


def test_snowflake_on_azure_is_left_alone(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_run(
        runs, "20250101T000000Z-dddddddd",
        scope={
            "source_type": "snowflake",
            "id": "acct",
            "display_name": "acct",
            "extras": {"platform": "azure"},
        },
    )
    result = migrate_run_attribution(runs)
    assert result["skipped"] == ["20250101T000000Z-dddddddd"]


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    path = _write_run(
        runs, "20250101T000000Z-eeeeeeee",
        scope={
            "source_type": "bigquery",
            "id": "p",
            "display_name": "p",
        },
    )
    before = path.read_text(encoding="utf-8")
    result = migrate_run_attribution(runs, dry_run=True)
    assert result["updated"] == ["20250101T000000Z-eeeeeeee"]
    assert path.read_text(encoding="utf-8") == before


def test_scopeless_run_is_skipped(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    run_dir = runs / "20250101T000000Z-ffffffff"
    run_dir.mkdir()
    meta = {
        "id": "20250101T000000Z-ffffffff",
        "status": "ok",
        "started_at": "2025-01-01T00:00:00+00:00",
        "config_hash": "abc",
        "modules": [],
        "tenant_id": "t",
        "subscription_id": "s",
        "resource_group": "rg",
        "workspace_name": "ws",
        "scopes": [],
    }
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    result = migrate_run_attribution(runs)
    assert result["updated"] == []
    assert result["skipped"] == ["20250101T000000Z-ffffffff"]


def test_malformed_run_json_recorded_as_error(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    run_dir = runs / "20250101T000000Z-99999999"
    run_dir.mkdir()
    (run_dir / "run.json").write_text("{not json", encoding="utf-8")
    result = migrate_run_attribution(runs)
    assert result["errors"]
    assert "20250101T000000Z-99999999" in result["errors"][0]


def test_missing_runs_dir_is_safe(tmp_path: Path) -> None:
    result = migrate_run_attribution(tmp_path / "nope")
    assert result == {"updated": [], "skipped": [], "errors": []}
