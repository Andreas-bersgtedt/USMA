"""Generic helpers shared by per-module reporters."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel


def write_json_model(model: BaseModel, path: Path) -> Path:
    path.write_text(json.dumps(model.model_dump(mode="json"), indent=2, default=str), encoding="utf-8")
    return path


def write_csv_rows(path: Path, rows: Iterable[BaseModel]) -> Path | None:
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


def write_markdown(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
