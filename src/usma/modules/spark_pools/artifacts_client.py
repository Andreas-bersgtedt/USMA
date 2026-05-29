"""Synapse Artifacts API client for Spark assets (notebooks, Spark job definitions)."""
from __future__ import annotations

import logging
from typing import Any, Iterator

from azure.synapse.artifacts import ArtifactsClient

from ...auth import get_credential
from ...config import AzureConfig
from .models import Notebook, SparkJobDefinition

log = logging.getLogger(__name__)


class SparkArtifactsClient:
    """Wraps `azure-synapse-artifacts` for the spark_pools module."""

    def __init__(self, azure: AzureConfig) -> None:
        self._azure = azure
        self._endpoint = f"https://{azure.workspace_name}.dev.azuresynapse.net"
        self._artifacts = ArtifactsClient(endpoint=self._endpoint, credential=get_credential(azure))

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def list_notebooks(self) -> Iterator[Notebook]:
        for nb in self._artifacts.notebook.get_notebooks_by_workspace():
            props = getattr(nb, "properties", None)
            md = getattr(props, "metadata", None) if props else None
            kernel = None
            language = None
            if md is not None:
                k = getattr(md, "kernelspec", None)
                if k is not None:
                    kernel = getattr(k, "display_name", None) or getattr(k, "name", None)
                lang_info = getattr(md, "language_info", None)
                if lang_info is not None:
                    language = getattr(lang_info, "name", None)

            attached_pool = None
            big_data_pool = getattr(props, "big_data_pool", None) if props else None
            if big_data_pool is not None:
                attached_pool = getattr(big_data_pool, "reference_name", None)

            cells = list(getattr(props, "cells", None) or []) if props else []
            cell_count = len(cells)
            sources = []
            for c in cells:
                src = getattr(c, "source", None)
                if isinstance(src, list):
                    sources.extend(s for s in src if isinstance(s, str))
                elif isinstance(src, str):
                    sources.append(src)
            source_text = "\n".join(sources)

            yield Notebook(
                name=nb.name,
                folder=getattr(getattr(props, "folder", None), "name", None) if props else None,
                language=language,
                kernel=kernel,
                attached_spark_pool=attached_pool,
                cell_count=cell_count,
                source_size_chars=len(source_text),
                imports=_extract_imports(source_text, language),
                annotations=[str(a) for a in (getattr(props, "additional_properties", {}).get("annotations", []) or [])] if props else [],
            )

    def list_spark_job_definitions(self) -> Iterator[SparkJobDefinition]:
        for sjd in self._artifacts.spark_job_definition.get_spark_job_definitions_by_workspace():
            props = getattr(sjd, "properties", None)
            target_pool = None
            if props is not None:
                tbdpr = getattr(props, "target_big_data_pool", None)
                if tbdpr is not None:
                    target_pool = getattr(tbdpr, "reference_name", None)
            jp = getattr(props, "job_properties", None) if props else None
            yield SparkJobDefinition(
                name=sjd.name,
                folder=getattr(getattr(props, "folder", None), "name", None) if props else None,
                language=getattr(props, "language", None) if props else None,
                target_spark_pool=target_pool,
                main_definition_file=getattr(jp, "file", None) if jp else None,
                class_name=getattr(jp, "class_name", None) if jp else None,
                conf=_safe_dict(getattr(jp, "conf", None)) if jp else {},
                args=list(getattr(jp, "args", None) or []) if jp else [],
            )


def _safe_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except Exception:  # noqa: BLE001
        return {}


def _extract_imports(source: str, language: str | None) -> list[str]:
    """Cheap heuristic — pulls out `import …` / `from … import …` lines (Python/Scala)."""
    if not source:
        return []
    imports: list[str] = []
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith("import ") or line.startswith("from "):
            imports.append(line[:200])
    # de-dup, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for i in imports:
        if i not in seen:
            seen.add(i)
            deduped.append(i)
    return deduped[:50]
