from __future__ import annotations

import json
from pathlib import Path

from ..modules.dedicated_pools.models import WorkspaceAnalysis


def write_json(result: WorkspaceAnalysis, out_dir: Path) -> Path:
    path = out_dir / "dedicated_pools.json"
    path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    return path
