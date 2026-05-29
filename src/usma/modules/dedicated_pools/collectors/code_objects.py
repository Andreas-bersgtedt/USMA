from __future__ import annotations

import logging

from ..models import CodeObject, CodeObjectParameter
from ..sql_client import DedicatedPoolSqlClient
from ..tsql_surface_gap import stable_code_object_id

log = logging.getLogger(__name__)


def _coerce_bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    try:
        return bool(int(v))
    except (TypeError, ValueError):
        return None


def _line_count(definition: str | None) -> int | None:
    if not definition:
        return None
    # Mirror SSMS: count "lines" as the number of CR/LF-separated rows.
    # ``splitlines`` handles \r\n / \r / \n uniformly.
    return max(1, len(definition.splitlines()))


_DEFINITION_LIMIT = 50_000


def _truncate(definition: str | None) -> tuple[str | None, bool]:
    """Return ``(text, truncated)`` capped at ``_DEFINITION_LIMIT`` chars."""
    if not definition:
        return None, False
    if len(definition) > _DEFINITION_LIMIT:
        return definition[:_DEFINITION_LIMIT], True
    return definition, False


def collect_code_objects(sql: DedicatedPoolSqlClient) -> list[CodeObject]:
    rows = sql.fetch_query_file("code_objects")

    # Best-effort parameter fetch. Collected here (not as a separate top-level
    # collector) so each CodeObject carries its own parameter list. Failures
    # are non-fatal -- objects still report inventory + T-SQL surface gaps.
    params_by_object: dict[tuple[str, str, str], list[CodeObjectParameter]] = {}
    try:
        param_rows = sql.fetch_query_file("code_object_parameters")
    except Exception:  # noqa: BLE001 - parameter fetch is non-critical
        param_rows = []
    for pr in param_rows:
        key = (pr["schema_name"], pr["object_name"], pr["object_type"])
        params_by_object.setdefault(key, []).append(CodeObjectParameter(
            schema_name=pr["schema_name"],
            object_name=pr["object_name"],
            object_type=pr["object_type"],
            parameter_name=pr.get("parameter_name") or "",
            data_type=pr.get("data_type"),
            max_length=pr.get("max_length"),
            is_output=bool(pr.get("is_output") or False),
            has_default=bool(pr.get("has_default") or False),
            ordinal=int(pr.get("ordinal") or 0),
        ))

    out: list[CodeObject] = []
    for r in rows:
        definition_raw = r.get("definition") or ""
        key = (r["schema_name"], r["object_name"], r["object_type"])
        params = params_by_object.get(key, [])
        truncated_definition, was_truncated = _truncate(definition_raw)
        out.append(CodeObject(
            schema_name=r["schema_name"],
            object_name=r["object_name"],
            object_type=r["object_type"],
            definition=truncated_definition,
            code_object_id=stable_code_object_id(
                r["schema_name"], r["object_name"], r["object_type"],
            ),
            create_date=r.get("create_date"),
            modify_date=r.get("modify_date"),
            line_count=_line_count(definition_raw),
            definition_length=int(r["definition_length"]) if r.get("definition_length") is not None else None,
            definition_truncated=was_truncated,
            parameter_count=len(params),
            parameters=params,
            uses_ansi_nulls=_coerce_bool(r.get("uses_ansi_nulls")),
            uses_quoted_identifier=_coerce_bool(r.get("uses_quoted_identifier")),
        ))
    # Surface a per-type count so missing procedures / functions are visible
    # in the run log instead of silently producing a "views-only" report.
    if out:
        counts: dict[str, int] = {}
        for o in out:
            key = (o.object_type or "").strip().upper() or "UNKNOWN"
            counts[key] = counts.get(key, 0) + 1
        log.info("code_objects collected: %s", counts)
    else:
        log.info("code_objects collected: 0 rows (no procedures, views, or functions found)")
    return out

