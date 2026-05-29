"""Orchestrator for the fabric_validation module (mid-term v0).

Loads the *expected* state from existing module JSON in ``cfg.output_dir``
(produced by a prior ``analyze-all`` against the Synapse source) and compares
it to the live Fabric Warehouse / Lakehouse SQL endpoint configured via the
SMA_FABRIC_* environment variables.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ...config import AppConfig
from ...errors import format_error
from ...progress import NullProgress, ProgressReporter
from . import diff
from .models import FabricValidationAnalysis
from .sql_client import FabricSqlClient

log = logging.getLogger(__name__)


class FabricValidationAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._cfg = cfg
        self._sql = FabricSqlClient()
        self._progress = progress or NullProgress()

    def run(self) -> FabricValidationAnalysis:
        result = FabricValidationAnalysis(
            target_workspace=os.getenv("SMA_FABRIC_WORKSPACE"),
            target_warehouse=os.getenv("SMA_FABRIC_DATABASE"),
            generated_at=datetime.now(timezone.utc),
            expected_source=str(self._cfg.output_dir / "dedicated_pools.json"),
        )

        expected = _load_expected(self._cfg.output_dir)
        if expected is None:
            result.errors.append(
                "expected_state: dedicated_pools.json not found in output_dir; "
                "run analyze-dedicated-pools first to generate the expected state."
            )
            self._progress.start(0, label="missing expected state")
            return result

        # 5 sub-tasks: object_counts, row_counts, collation, tsql_surface, summary.
        self._progress.start(5, label="fabric validation")

        # Object counts.
        try:
            actual_objects = self._sql.fetch_object_counts() if self._sql.is_configured() else []
            result.object_count_checks = diff.diff_object_counts(
                expected=expected.get("object_counts", {}),
                actual=actual_objects,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("object count comparison failed: %s", exc)
            result.errors.append(format_error("object_counts", exc))
        self._progress.step(label="object_counts")

        # Row counts (only when explicitly enabled — slow on large warehouses).
        if os.getenv("SMA_FABRIC_VALIDATE_ROWS", "").strip() in {"1", "true", "yes"}:
            try:
                actual_rows: dict[tuple[str, str], int | None] = {}
                for (schema, table), _ in expected.get("row_counts", {}).items():
                    actual_rows[(schema, table)] = (
                        self._sql.fetch_row_count(schema, table)
                        if self._sql.is_configured()
                        else None
                    )
                result.row_count_checks = diff.diff_row_counts(
                    expected=expected.get("row_counts", {}),
                    actual=actual_rows,
                    tolerance_pct=float(os.getenv("SMA_FABRIC_ROW_TOLERANCE", "0") or "0"),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("row count comparison failed: %s", exc)
                result.errors.append(format_error("row_counts", exc))
        self._progress.step(label="row_counts")

        # Collation.
        try:
            actual_collation = self._sql.fetch_collations() if self._sql.is_configured() else []
            result.collation_checks = diff.diff_collation(
                expected=expected.get("collation", {}),
                actual=actual_collation,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("collation comparison failed: %s", exc)
            result.errors.append(format_error("collation", exc))
        self._progress.step(label="collation")

        # T-SQL surface resolutions.
        try:
            present_objects = {
                (row["schema_name"], _strip_objtype(row["type_desc"]))
                for row in (
                    self._sql.fetch_object_counts() if self._sql.is_configured() else []
                )
            } if False else set()  # gate: object names need a separate query
            findings = expected.get("tsql_findings", [])
            result.tsql_surface_checks = diff.evaluate_tsql_surface_resolutions(
                findings=findings,
                fabric_objects_present=present_objects,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("tsql surface comparison failed: %s", exc)
            result.errors.append(format_error("tsql_surface", exc))
        self._progress.step(label="tsql_surface")

        result.summary = diff.summarize(
            result.object_count_checks,
            result.row_count_checks,
            result.collation_checks,
            result.tsql_surface_checks,
        )
        self._progress.step(label="summary")
        return result


def _strip_objtype(_desc: str) -> str:
    return _desc  # placeholder; v1 will join sys.objects -> name


def _load_expected(out_dir: Path) -> dict | None:
    """Build the expected-state map from existing module JSON outputs."""
    path = Path(out_dir) / "dedicated_pools.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None

    object_counts: dict[tuple[str, str], int] = {}
    row_counts: dict[tuple[str, str], int] = {}
    collation: dict[tuple[str, str, str], str] = {}
    findings: list[dict] = []

    for pool in payload.get("pools", []) or []:
        for tbl in pool.get("tables", []) or []:
            sch = tbl.get("schema_name") or tbl.get("schema")
            name = tbl.get("table_name") or tbl.get("name")
            if not (sch and name):
                continue
            object_counts[(sch, "USER_TABLE")] = object_counts.get((sch, "USER_TABLE"), 0) + 1
            rc = tbl.get("row_count")
            if isinstance(rc, int):
                row_counts[(sch, name)] = rc
        for view in pool.get("views", []) or []:
            sch = view.get("schema_name")
            if sch:
                object_counts[(sch, "VIEW")] = object_counts.get((sch, "VIEW"), 0) + 1
        for col in pool.get("column_collation", []) or []:
            sch = col.get("schema_name")
            tbl = col.get("table_name")
            cn = col.get("column_name")
            cl = col.get("collation_name")
            if sch and tbl and cn and cl:
                collation[(sch, tbl, cn)] = cl
        for f in pool.get("tsql_surface_gaps", []) or []:
            findings.append(f)

    return {
        "object_counts": object_counts,
        "row_counts": row_counts,
        "collation": collation,
        "tsql_findings": findings,
    }
