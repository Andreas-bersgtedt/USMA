"""Report writers."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..modules.dedicated_pools.models import WorkspaceAnalysis
from .csv_writer import write_csv
from .html_report import write_html
from .json_writer import write_json
from .markdown_report import write_markdown


def write_reports(result: WorkspaceAnalysis, out_dir: Path, formats: Iterable[str]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    fmts = {f.lower() for f in formats}
    if "json" in fmts:
        written.append(write_json(result, out_dir))
    if "csv" in fmts:
        written.extend(write_csv(result, out_dir))
    if "markdown" in fmts:
        written.append(write_markdown(result, out_dir))
    if "html" in fmts:
        written.append(write_html(result, out_dir))
    return written
