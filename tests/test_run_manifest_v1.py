"""Tests for run_manifest v1 additions: peek_record_count, summary,
HTML/JSON delta reports, size + record-count deltas."""
from __future__ import annotations

import json
from pathlib import Path

from usma.reporting.run_manifest import (
    build_manifest,
    diff_manifests,
    diff_summary,
    peek_record_count,
    write_delta_html,
    write_delta_json,
    write_delta_report,
)


def _build(tmp_path: Path, file_names: list[str], version: str = "1.1.0"):
    return build_manifest(
        tmp_path,
        workspace_name="ws",
        subscription_id="sub-1",
        resource_group="rg",
        sma_version=version,
        file_names=file_names,
    )


def test_peek_record_count_findings(tmp_path: Path) -> None:
    p = tmp_path / "security.json"
    p.write_text(json.dumps({
        "workspace_name": "ws",
        "findings": [{"rule_id": "x"}, {"rule_id": "y"}, {"rule_id": "z"}],
    }), encoding="utf-8")
    assert peek_record_count(p) == 3


def test_peek_record_count_falls_back_to_rows(tmp_path: Path) -> None:
    p = tmp_path / "cost.json"
    p.write_text(json.dumps({
        "rows": [{"month": "2026-01"}, {"month": "2026-02"}],
    }), encoding="utf-8")
    assert peek_record_count(p) == 2


def test_peek_record_count_handles_malformed(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("not json", encoding="utf-8")
    assert peek_record_count(p) is None


def test_peek_record_count_returns_none_when_no_known_key(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    assert peek_record_count(p) is None


def test_build_manifest_populates_record_count(tmp_path: Path) -> None:
    (tmp_path / "security.json").write_text(json.dumps({
        "findings": [{"rule_id": "a"}, {"rule_id": "b"}],
    }), encoding="utf-8")
    manifest = _build(tmp_path, ["security.json"])
    assert len(manifest.artifacts) == 1
    assert manifest.artifacts[0].record_count == 2


def test_diff_summary_counts_buckets(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({"findings": [{"x": 1}]}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"findings": []}), encoding="utf-8")
    prev = _build(tmp_path, ["a.json", "b.json"])
    # mutate a, drop b, add c
    (tmp_path / "a.json").write_text(json.dumps({"findings": [{"x": 1}, {"x": 2}]}), encoding="utf-8")
    (tmp_path / "b.json").unlink()
    (tmp_path / "c.json").write_text(json.dumps({"findings": []}), encoding="utf-8")
    curr = _build(tmp_path, ["a.json", "b.json", "c.json"])
    diff = diff_manifests(prev, curr)
    summary = diff_summary(diff)
    assert summary["changed"] == 1
    assert summary["added"] == 1
    assert summary["removed"] == 1
    assert summary["unchanged"] == 0
    assert summary["total"] == 3


def test_diff_entry_carries_record_and_size_deltas(tmp_path: Path) -> None:
    (tmp_path / "security.json").write_text(json.dumps({
        "findings": [{"rule_id": "a"}],
    }), encoding="utf-8")
    prev = _build(tmp_path, ["security.json"])
    (tmp_path / "security.json").write_text(json.dumps({
        "findings": [{"rule_id": "a"}, {"rule_id": "b"}, {"rule_id": "c"}],
    }), encoding="utf-8")
    curr = _build(tmp_path, ["security.json"])
    diff = diff_manifests(prev, curr)
    [entry] = diff
    assert entry.status == "changed"
    assert entry.record_count_delta == 2
    assert entry.size_delta is not None and entry.size_delta > 0


def test_write_delta_html_round_trip(tmp_path: Path) -> None:
    (tmp_path / "security.json").write_text(json.dumps({
        "findings": [{"rule_id": "a"}],
    }), encoding="utf-8")
    prev = _build(tmp_path, ["security.json"], version="1.1.0")
    (tmp_path / "security.json").write_text(json.dumps({
        "findings": [{"rule_id": "a"}, {"rule_id": "b"}],
    }), encoding="utf-8")
    curr = _build(tmp_path, ["security.json"], version="1.1.1")
    diff = diff_manifests(prev, curr)
    out = write_delta_html(diff, tmp_path, prev_manifest=prev, curr_manifest=curr)
    text = out.read_text(encoding="utf-8")
    assert "Run delta" in text
    assert "security" in text
    assert "changed" in text
    assert "v1.1.1" in text


def test_write_delta_json_payload(tmp_path: Path) -> None:
    (tmp_path / "security.json").write_text(json.dumps({
        "findings": [{"rule_id": "a"}],
    }), encoding="utf-8")
    curr = _build(tmp_path, ["security.json"])
    diff = diff_manifests(None, curr)
    out = write_delta_json(diff, tmp_path, prev_manifest=None, curr_manifest=curr)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["previous"] is None
    assert payload["current"]["workspace_name"] == "ws"
    assert payload["summary"]["added"] == 1
    assert payload["entries"][0]["status"] == "added"
    assert payload["entries"][0]["curr_record_count"] == 1


def test_write_delta_md_includes_summary_line(tmp_path: Path) -> None:
    (tmp_path / "security.json").write_text(json.dumps({"findings": []}), encoding="utf-8")
    curr = _build(tmp_path, ["security.json"])
    diff = diff_manifests(None, curr)
    out = write_delta_report(diff, tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "added: 1" in text
    assert "Records (prev -> curr)" in text
