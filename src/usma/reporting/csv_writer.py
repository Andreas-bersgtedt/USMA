from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ..modules.dedicated_pools.models import WorkspaceAnalysis


def _write(path: Path, rows: Iterable[BaseModel]) -> Path | None:
    rows = list(rows)
    if not rows:
        return None
    fieldnames = list(rows[0].model_dump(mode="json").keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r.model_dump(mode="json"))
    return path


def write_csv(result: WorkspaceAnalysis, out_dir: Path) -> list[Path]:
    paths: list[Path] = []
    inv_path = out_dir / "pools_inventory.csv"
    p = _write(inv_path, [p.inventory for p in result.pools])
    if p:
        paths.append(p)

    per_entity: dict[str, list[BaseModel]] = {
        "schemas.csv": [],
        "tables.csv": [],
        "indexes.csv": [],
        "usage.csv": [],
        "security.csv": [],
        "workload_groups.csv": [],
        # v2 entities — emitted only when the corresponding collector returned rows.
        "column_collations.csv": [],
        "materialized_views.csv": [],
        "statistics.csv": [],
        "column_stats.csv": [],
        "distribution_candidates.csv": [],
        "tsql_surface_gaps.csv": [],
    }
    for pool in result.pools:
        per_entity["schemas.csv"].extend(pool.schemas)
        per_entity["tables.csv"].extend(pool.tables)
        per_entity["indexes.csv"].extend(pool.indexes)
        per_entity["usage.csv"].extend(pool.usage)
        per_entity["security.csv"].extend(pool.security)
        per_entity["workload_groups.csv"].extend(pool.workload_groups)
        per_entity["column_collations.csv"].extend(pool.column_collations)
        per_entity["materialized_views.csv"].extend(pool.materialized_views)
        per_entity["statistics.csv"].extend(pool.statistics)
        per_entity["column_stats.csv"].extend(pool.column_stats)
        per_entity["distribution_candidates.csv"].extend(pool.distribution_candidates)
        per_entity["tsql_surface_gaps.csv"].extend(pool.tsql_surface_gaps)

    for filename, rows in per_entity.items():
        p = _write(out_dir / filename, rows)
        if p:
            paths.append(p)
    return paths
