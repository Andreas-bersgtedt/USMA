"""Unit tests for the run_manifest helpers (incremental / delta runs)."""
from __future__ import annotations

from pathlib import Path

from usma.reporting.run_manifest import (
    build_manifest,
    compute_artifact_hash,
    diff_manifests,
    load_manifest,
    save_manifest,
    write_delta_report,
)


def test_compute_artifact_hash_is_stable_across_key_order() -> None:
    a = compute_artifact_hash({"a": 1, "b": 2})
    b = compute_artifact_hash({"b": 2, "a": 1})
    assert a == b


def test_build_and_save_manifest_round_trip(tmp_path: Path) -> None:
    (tmp_path / "dedicated_pools.json").write_text('{"foo": 1}', encoding="utf-8")
    (tmp_path / "pipelines.json").write_text('{"bar": 2}', encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        workspace_name="ws",
        subscription_id="sub",
        resource_group="rg",
        sma_version="1.0.1",
        file_names=["dedicated_pools.json", "pipelines.json", "missing.json"],
    )
    assert {a.name for a in manifest.artifacts} == {"dedicated_pools", "pipelines"}
    saved = save_manifest(manifest, tmp_path)
    assert saved.exists()
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded.workspace_name == "ws"
    assert {a.name for a in loaded.artifacts} == {"dedicated_pools", "pipelines"}


def test_diff_manifests_detects_changes(tmp_path: Path) -> None:
    p = tmp_path / "dedicated_pools.json"
    p.write_text('{"v": 1}', encoding="utf-8")
    prev = build_manifest(
        tmp_path, workspace_name="ws", subscription_id="s", resource_group="rg",
        sma_version="1.0.0", file_names=["dedicated_pools.json"],
    )
    p.write_text('{"v": 2}', encoding="utf-8")
    curr = build_manifest(
        tmp_path, workspace_name="ws", subscription_id="s", resource_group="rg",
        sma_version="1.0.1", file_names=["dedicated_pools.json"],
    )
    diff = diff_manifests(prev, curr)
    assert len(diff) == 1
    assert diff[0].status == "changed"


def test_diff_manifests_added_when_no_prev(tmp_path: Path) -> None:
    (tmp_path / "pipelines.json").write_text("{}", encoding="utf-8")
    curr = build_manifest(
        tmp_path, workspace_name="ws", subscription_id="s", resource_group="rg",
        sma_version="1.0.1", file_names=["pipelines.json"],
    )
    diff = diff_manifests(None, curr)
    assert diff[0].status == "added"


def test_write_delta_report_creates_markdown(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text('{"v": 1}', encoding="utf-8")
    curr = build_manifest(
        tmp_path, workspace_name="ws", subscription_id="s", resource_group="rg",
        sma_version="1.0.1", file_names=["x.json"],
    )
    diff = diff_manifests(None, curr)
    path = write_delta_report(diff, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Run delta" in text
    assert "added" in text
