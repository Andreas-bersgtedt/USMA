"""Estate Overview endpoints \u2014 cross-workspace, cross-time aggregation.

These routes never execute analyzer code; they only summarise existing
``runs/`` artefacts via :class:`..estate.EstateIndex`.
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..deps import AppState, get_state
from ..schemas import EstateReport, EstateWorkspace

router = APIRouter()


@router.get("", response_model=EstateReport)
def get_estate(
    refresh: int = Query(0, ge=0, le=1),
    state: AppState = Depends(get_state),
) -> EstateReport:
    if refresh:
        state.estate.invalidate()
    return state.estate.build()


@router.get("/workspaces/{key}", response_model=EstateWorkspace)
def get_estate_workspace(
    key: str,
    state: AppState = Depends(get_state),
) -> EstateWorkspace:
    report = state.estate.build()
    for ws in report.workspaces:
        if ws.key == key:
            return ws
    raise HTTPException(status_code=404, detail=f"workspace {key!r} not found")


@router.get("/export.csv")
def export_estate_csv(state: AppState = Depends(get_state)) -> StreamingResponse:
    report = state.estate.build()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "tenant_id",
        "subscription_id",
        "resource_group",
        "workspace_name",
        "source_type",
        "run_count",
        "latest_run_id",
        "latest_status",
        "latest_finished_at",
        "readiness_score",
        "readiness_bucket",
        "blocker_count",
        "warning_count",
        "info_count",
        "tsql_compatibility_pct",
        "projected_fabric_cu",
        "recommended_fabric_sku",
        "actual_monthly_cost",
        "actual_currency",
        "fabric_estimated_monthly_cost",
        "fabric_cost_delta_abs",
        "fabric_cost_delta_pct",
        "effort_hours_p50",
        "effort_hours_p90",
        "effort_days_p50",
        "effort_days_p90",
    ])
    for ws in report.workspaces:
        w.writerow([
            ws.tenant_id or "",
            ws.subscription_id or "",
            ws.resource_group or "",
            ws.workspace_name,
            ws.source_type or "",
            ws.run_count,
            ws.latest_run_id,
            ws.latest_status,
            ws.latest_finished_at.isoformat(),
            "" if ws.readiness_score is None else ws.readiness_score,
            ws.readiness_bucket or "",
            ws.blocker_count,
            ws.warning_count,
            ws.info_count,
            "" if ws.tsql_compatibility_pct is None else ws.tsql_compatibility_pct,
            "" if ws.projected_fabric_cu is None else ws.projected_fabric_cu,
            ws.recommended_fabric_sku or "",
            "" if ws.actual_monthly_cost is None else ws.actual_monthly_cost,
            ws.actual_currency or "",
            "" if ws.fabric_estimated_monthly_cost is None else ws.fabric_estimated_monthly_cost,
            "" if ws.fabric_cost_delta_abs is None else ws.fabric_cost_delta_abs,
            "" if ws.fabric_cost_delta_pct is None else ws.fabric_cost_delta_pct,
            "" if ws.effort_hours_p50 is None else ws.effort_hours_p50,
            "" if ws.effort_hours_p90 is None else ws.effort_hours_p90,
            "" if ws.effort_days_p50 is None else ws.effort_days_p50,
            "" if ws.effort_days_p90 is None else ws.effort_days_p90,
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=estate.csv"},
    )
