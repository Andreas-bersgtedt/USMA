from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ObjectCountCheck(BaseModel):
    """Compare object counts (tables/views/procs) per schema."""
    schema_name: str
    object_kind: str  # "TABLE" | "VIEW" | "PROCEDURE" | "FUNCTION"
    expected: int
    actual: int
    delta: int  # actual - expected
    status: str  # "match" | "missing" | "extra"


class RowCountCheck(BaseModel):
    schema_name: str
    table_name: str
    expected_rows: int
    actual_rows: int | None
    delta: int | None
    status: str  # "match" | "mismatch" | "missing" | "error"
    error: str | None = None


class CollationCheck(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    expected_collation: str
    actual_collation: str | None
    status: str  # "match" | "mismatch" | "missing"


class TsqlSurfaceCheck(BaseModel):
    """A T-SQL surface finding that was supposed to be resolved by migration."""
    code_object_id: str
    schema_name: str | None
    object_name: str | None
    finding_id: str
    expected_resolution: str  # "removed" | "rewritten" | "noop"
    status: str  # "resolved" | "still_present" | "object_missing"


class FabricValidationAnalysis(BaseModel):
    target_workspace: str | None  # Fabric workspace name/id
    target_warehouse: str | None
    generated_at: datetime
    expected_source: str | None  # The source dedicated_pools.json path used as truth.
    object_count_checks: list[ObjectCountCheck] = Field(default_factory=list)
    row_count_checks: list[RowCountCheck] = Field(default_factory=list)
    collation_checks: list[CollationCheck] = Field(default_factory=list)
    tsql_surface_checks: list[TsqlSurfaceCheck] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)  # status -> count
    errors: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
