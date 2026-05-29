from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ...reporting.generic import write_csv_rows, write_json_model, write_markdown
from .models import FabricValidationAnalysis


class _ObjRow(BaseModel):
    schema_name: str
    object_kind: str
    expected: int
    actual: int
    delta: int
    status: str


class _RowRow(BaseModel):
    schema_name: str
    table_name: str
    expected_rows: int
    actual_rows: int | None
    delta: int | None
    status: str


class _ColRow(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    expected_collation: str
    actual_collation: str | None
    status: str


class _SurfaceRow(BaseModel):
    code_object_id: str
    schema_name: str | None
    object_name: str | None
    finding_id: str
    expected_resolution: str
    status: str


def write_reports(
    result: FabricValidationAnalysis,
    out_dir: Path,
    formats: Iterable[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {f.lower() for f in formats}
    written: list[Path] = []

    if "json" in fmts:
        written.append(write_json_model(result, out_dir / "fabric_validation.json"))

    if "csv" in fmts:
        p = write_csv_rows(out_dir / "fabric_validation_object_counts.csv",
                           [_ObjRow(**c.model_dump()) for c in result.object_count_checks])
        if p:
            written.append(p)
        p = write_csv_rows(out_dir / "fabric_validation_row_counts.csv",
                           [_RowRow(**c.model_dump()) for c in result.row_count_checks])
        if p:
            written.append(p)
        p = write_csv_rows(out_dir / "fabric_validation_collation.csv",
                           [_ColRow(**c.model_dump()) for c in result.collation_checks])
        if p:
            written.append(p)
        p = write_csv_rows(out_dir / "fabric_validation_tsql_surface.csv",
                           [_SurfaceRow(**c.model_dump()) for c in result.tsql_surface_checks])
        if p:
            written.append(p)

    if "markdown" in fmts:
        lines = [
            "# Unified Solution Migration Analyzer - Fabric Validation",
            "",
            f"- Target workspace: `{result.target_workspace or '(not configured)'}`",
            f"- Target warehouse: `{result.target_warehouse or '(not configured)'}`",
            f"- Object count checks: {len(result.object_count_checks)}",
            f"- Row count checks: {len(result.row_count_checks)}",
            f"- Collation checks: {len(result.collation_checks)}",
            f"- T-SQL surface checks: {len(result.tsql_surface_checks)}",
            "",
        ]
        if result.summary:
            lines.append("## Summary")
            for status in sorted(result.summary):
                lines.append(f"- **{status}**: {result.summary[status]}")
            lines.append("")
        if result.errors:
            lines.append("## Collection errors")
            for e in result.errors:
                lines.append(f"- {e}")
            lines.append("")
        written.append(write_markdown(out_dir / "fabric_validation.md", lines))

    return written
