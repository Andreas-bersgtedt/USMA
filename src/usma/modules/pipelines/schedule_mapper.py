"""Translate Synapse trigger schedule descriptors into Fabric pipeline schedules.

The Synapse Artifacts trigger payload ships a `recurrence` block (interval / frequency
/ start / end / schedule). Fabric's pipeline schedule UI accepts the same primitives but
in a slightly different shape, plus tumbling-window triggers translate differently.

We do **not** call Fabric here — this module emits an advisory descriptor that the
fabric_mapping report can include. Out-of-band the user must still create the schedule
in Fabric.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

ScheduleKind = Literal["recurring", "tumbling", "event", "manual", "unknown"]


@dataclass(frozen=True)
class FabricSchedule:
    kind: ScheduleKind
    every_n: int | None       # e.g. 1
    interval: str | None       # "Minute" | "Hour" | "Day" | "Week" | "Month"
    start_time_utc: datetime | None
    end_time_utc: datetime | None
    days_of_week: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def map_trigger(trigger: dict[str, Any]) -> FabricSchedule:
    t_type = (trigger.get("type") or "").lower()
    if t_type in ("scheduletrigger", "schedule"):
        return _from_schedule(trigger)
    if t_type in ("tumblingwindowtrigger", "tumblingwindow"):
        return _from_tumbling(trigger)
    if t_type in ("blobeventstrigger", "customeventstrigger", "event"):
        return FabricSchedule(
            kind="event", every_n=None, interval=None,
            start_time_utc=None, end_time_utc=None,
            notes=("Event-based trigger; configure as a Fabric Eventstream → pipeline binding.",),
        )
    return FabricSchedule(
        kind="unknown", every_n=None, interval=None,
        start_time_utc=None, end_time_utc=None,
        notes=(f"Unrecognized trigger type {trigger.get('type')!r}.",),
    )


def _from_schedule(trigger: dict[str, Any]) -> FabricSchedule:
    # Trigger dicts come from one of three shapes:
    #   - Synapse Artifacts SDK ``ScheduleTrigger.as_dict()`` flattens
    #     ``recurrence`` onto the top level (so ``trigger['recurrence']``).
    #   - Our :class:`~..models.Trigger` model dumps the raw payload under
    #     ``type_properties`` (Phase 2.5 ADF + Synapse path).
    #   - Legacy ADF JSON exports nest it under ``typeProperties``.
    rec = (
        trigger.get("type_properties")
        or trigger.get("typeProperties")
        or trigger
    ).get("recurrence") or {}
    freq = (rec.get("frequency") or "").capitalize() or None
    interval = rec.get("interval")
    days_of_week: tuple[str, ...] = ()
    sched = rec.get("schedule") or {}
    if "weekDays" in sched:
        days_of_week = tuple(d.capitalize() for d in sched["weekDays"])
    notes: list[str] = []
    if sched.get("monthDays") or sched.get("monthlyOccurrences"):
        notes.append("Day-of-month / weekly-occurrence patterns require manual setup in Fabric.")
    return FabricSchedule(
        kind="recurring",
        every_n=int(interval) if isinstance(interval, int) else None,
        interval=freq,
        start_time_utc=_parse_dt(rec.get("startTime")),
        end_time_utc=_parse_dt(rec.get("endTime")),
        days_of_week=days_of_week,
        notes=tuple(notes),
    )


def _from_tumbling(trigger: dict[str, Any]) -> FabricSchedule:
    tp = trigger.get("type_properties") or trigger.get("typeProperties") or trigger
    return FabricSchedule(
        kind="tumbling",
        every_n=int(tp.get("interval")) if isinstance(tp.get("interval"), int) else None,
        interval=(tp.get("frequency") or "").capitalize() or None,
        start_time_utc=_parse_dt(tp.get("startTime")),
        end_time_utc=_parse_dt(tp.get("endTime")),
        notes=(
            "Fabric does not yet have a 1:1 tumbling-window equivalent; "
            "the closest pattern is a recurring schedule + WindowStart / WindowEnd parameters.",
        ),
    )


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        # Synapse returns ISO 8601 with optional 'Z'
        s = v.rstrip("Z") + ("+00:00" if str(v).endswith("Z") else "")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def render_human(s: FabricSchedule) -> str:
    """One-line human-readable summary for inclusion in a Markdown report."""
    if s.kind == "manual":
        return "Manual / no schedule"
    if s.kind == "event":
        return "Event-driven (configure via Fabric Eventstream)"
    if s.kind == "tumbling":
        every = f"every {s.every_n} {s.interval or '?'}".strip()
        return f"Tumbling-window {every} (manual mapping required)"
    if s.kind == "recurring":
        every = f"every {s.every_n} {s.interval or '?'}".strip()
        days = f" on {', '.join(s.days_of_week)}" if s.days_of_week else ""
        return f"Recurring {every}{days}"
    return "Unknown schedule"
