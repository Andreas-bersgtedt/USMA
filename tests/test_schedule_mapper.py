"""Tests for trigger → Fabric schedule mapping."""
from usma.modules.pipelines import schedule_mapper


def test_schedule_trigger_weekly():
    trig = {
        "type": "ScheduleTrigger",
        "typeProperties": {
            "recurrence": {
                "frequency": "week", "interval": 1,
                "startTime": "2025-01-01T00:00:00Z",
                "schedule": {"weekDays": ["monday", "wednesday"]},
            }
        },
    }
    s = schedule_mapper.map_trigger(trig)
    assert s.kind == "recurring"
    assert s.every_n == 1
    assert s.interval == "Week"
    assert "Monday" in s.days_of_week and "Wednesday" in s.days_of_week


def test_tumbling_window_trigger():
    trig = {
        "type": "TumblingWindowTrigger",
        "typeProperties": {"frequency": "hour", "interval": 4},
    }
    s = schedule_mapper.map_trigger(trig)
    assert s.kind == "tumbling"
    assert s.every_n == 4


def test_event_trigger():
    s = schedule_mapper.map_trigger({"type": "BlobEventsTrigger"})
    assert s.kind == "event"


def test_render_human_returns_string():
    s = schedule_mapper.map_trigger({"type": "BlobEventsTrigger"})
    assert isinstance(schedule_mapper.render_human(s), str)


def test_schedule_trigger_via_type_properties_key():
    """Phase 2.5 — collectors dump props.as_dict() under
    ``type_properties`` (snake_case from the Pydantic model). The
    mapper must resolve cadence via that key, not just ``typeProperties``.
    Without this the SPA renders ``Recurring every None ?`` for every
    ScheduleTrigger emitted by the new ADF collector.
    """
    trig = {
        "type": "ScheduleTrigger",
        "type_properties": {
            "recurrence": {
                "frequency": "day", "interval": 1,
                "startTime": "2026-01-01T00:00:00Z",
            }
        },
    }
    s = schedule_mapper.map_trigger(trig)
    assert s.kind == "recurring"
    assert s.every_n == 1
    assert s.interval == "Day"
    assert "every 1 Day" in schedule_mapper.render_human(s)


def test_tumbling_window_via_type_properties_key():
    trig = {
        "type": "TumblingWindowTrigger",
        "type_properties": {"frequency": "hour", "interval": 6},
    }
    s = schedule_mapper.map_trigger(trig)
    assert s.kind == "tumbling"
    assert s.every_n == 6
    assert s.interval == "Hour"
