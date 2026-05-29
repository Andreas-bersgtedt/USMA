"""Tests for the access manifest + `sma access-report` CLI command."""
from __future__ import annotations

from click.testing import CliRunner

from usma import access_manifest
from usma.cli import cli


def test_manifest_covers_every_skippable_module() -> None:
    """Every module the CLI lets users include must appear in the manifest."""
    from usma.cli import _SKIPPABLE

    documented = {m.module for m in access_manifest.MANIFEST}
    missing = set(_SKIPPABLE) - documented
    assert not missing, f"Modules missing from access manifest: {sorted(missing)}"


def test_render_markdown_contains_required_sections() -> None:
    body = access_manifest.render_markdown("9.9.9")
    assert "analyzer version **9.9.9**" in body
    assert "## Per-module access matrix" in body
    assert "## What gets written" in body
    assert "## What ends up in output" in body
    assert "## What does NOT leave the host" in body
    # Every module should appear under its own H3.
    for m in access_manifest.MANIFEST:
        assert f"### `{m.module}`" in body


def test_access_report_command_writes_file(tmp_path) -> None:
    out = tmp_path / "access-report.md"
    result = CliRunner().invoke(cli, ["access-report", "--out", str(out)])
    assert result.exit_code == 0, result.output
    body = out.read_text(encoding="utf-8")
    assert "# Unified Solution Migration Analyzer" in body
    assert "## Per-module access matrix" in body


def test_access_report_command_stdout() -> None:
    result = CliRunner().invoke(cli, ["access-report"])
    assert result.exit_code == 0, result.output
    assert "Per-module access matrix" in result.output
