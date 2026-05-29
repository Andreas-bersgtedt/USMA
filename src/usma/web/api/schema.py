"""Pydantic JSON Schema for each module's top-level model.

Re-uses the registry already maintained by ``cli.py`` so the schema
endpoint and the ``sma export-schema`` CLI subcommand stay in sync.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException

from ... import __version__
from ...cli import _schema_registry

router = APIRouter()


@lru_cache(maxsize=1)
def _registry() -> dict[str, type]:
    return _schema_registry()


@router.get("")
def list_schemas() -> dict[str, list[str]]:
    return {"modules": sorted(_registry().keys())}


@router.get("/{module}")
def get_schema(module: str) -> dict[str, Any]:
    reg = _registry()
    if module not in reg:
        raise HTTPException(
            status_code=404,
            detail=f"unknown module '{module}'. Known: {sorted(reg)}",
        )
    schema = reg[module].model_json_schema()
    schema["x-sma-version"] = __version__
    schema["x-sma-module"] = module
    return schema
