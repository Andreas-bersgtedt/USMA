"""Tests for ``fabric_mapping.rules.rules_for_bigquery_workloads`` (Slice 5-C)."""
from __future__ import annotations

from usma.modules.fabric_mapping import rules


def _payload(**overrides):
    base = {
        "datasets": [{"dataset_id": "sales", "table_count": 2}],
        "tables": [],
        "routines": [],
        "scheduled_queries": [],
        "jobs": [],
        "caveats": [],
        "table_count": 2,
    }
    base.update(overrides)
    return base


def test_empty_payload_yields_no_recs():
    out = rules.rules_for_bigquery_workloads({})
    assert out == []


def test_inventory_rec_emitted_when_datasets_present():
    out = rules.rules_for_bigquery_workloads(_payload())
    ids = [r.id for r in out]
    assert "bq.inventory" in ids
    inv = next(r for r in out if r.id == "bq.inventory")
    assert "BigQuery" in inv.title
    assert "1 BigQuery dataset" in inv.title
    assert "2 table" in inv.title


def test_unsupported_and_partial_table_rollups():
    payload = _payload(tables=[
        {"table_type": "SNAPSHOT", "support": "unsupported"},
        {"table_type": "SNAPSHOT", "support": "unsupported"},
        {"table_type": "VIEW", "support": "partial"},
        {"table_type": "EXTERNAL", "support": "partial"},
        {"table_type": "TABLE", "support": "supported"},
    ])
    out = rules.rules_for_bigquery_workloads(payload)
    ids = {r.id for r in out}
    assert "bq.unsupported_table.snapshot" in ids
    assert "bq.partial_table.view" in ids
    assert "bq.partial_table.external" in ids
    # No partial rec for supported tables.
    assert not any(r.id.startswith("bq.partial_table.table") for r in out)
    snap = next(r for r in out if r.id == "bq.unsupported_table.snapshot")
    assert "(2)" in snap.title
    assert snap.severity == "warning"


def test_routines_rec_summarises_kinds():
    payload = _payload(routines=[
        {"routine_id": "f1", "routine_type": "SCALAR_FUNCTION"},
        {"routine_id": "f2", "routine_type": "SCALAR_FUNCTION"},
        {"routine_id": "p1", "routine_type": "PROCEDURE"},
    ])
    out = rules.rules_for_bigquery_workloads(payload)
    rec = next(r for r in out if r.id == "bq.routines")
    assert "3 BigQuery routine" in rec.title
    assert "SCALAR_FUNCTION=2" in rec.title
    assert "PROCEDURE=1" in rec.title


def test_scheduled_queries_rec():
    payload = _payload(scheduled_queries=[
        {"name": "x", "display_name": "daily-rollup"},
    ])
    out = rules.rules_for_bigquery_workloads(payload)
    rec = next(r for r in out if r.id == "bq.scheduled_queries")
    assert "1 scheduled BigQuery" in rec.title


def test_job_bucket_recs():
    payload = _payload(jobs=[
        {"job_type": "QUERY", "outcome": "succeeded"},
        {"job_type": "QUERY", "outcome": "succeeded"},
        {"job_type": "LOAD", "outcome": "succeeded"},
        {"job_type": "EXTRACT", "outcome": "succeeded"},
    ])
    out = rules.rules_for_bigquery_workloads(payload)
    ids = {r.id for r in out}
    assert "bq.jobs.query" in ids
    assert "bq.jobs.load" in ids
    assert "bq.jobs.extract" in ids
    assert "bq.jobs.copy" not in ids


def test_failed_jobs_warning_above_threshold():
    payload = _payload(jobs=[
        {"job_type": "QUERY", "outcome": "succeeded"},
        {"job_type": "QUERY", "outcome": "succeeded"},
        {"job_type": "QUERY", "outcome": "succeeded"},
        {"job_type": "QUERY", "outcome": "failed"},
        {"job_type": "QUERY", "outcome": "failed"},
    ])
    out = rules.rules_for_bigquery_workloads(payload)
    rec = next(r for r in out if r.id == "bq.failed_jobs")
    assert rec.severity == "warning"
    assert "40.0%" in rec.title


def test_failed_jobs_warning_not_emitted_below_threshold():
    payload = _payload(jobs=[{"job_type": "QUERY", "outcome": "succeeded"}] * 20 + [
        {"job_type": "QUERY", "outcome": "failed"},
    ])
    out = rules.rules_for_bigquery_workloads(payload)
    assert not any(r.id == "bq.failed_jobs" for r in out)


def test_slot_caveat_bubbled_up_when_present():
    payload = _payload(caveats=[
        "BigQuery slot-hours converted to Fabric Spark CU-hours using 0.25 ratio.",
    ])
    out = rules.rules_for_bigquery_workloads(payload)
    rec = next(r for r in out if r.id == "bq.slot_hour_caveat")
    assert rec.severity == "info"


def test_source_type_kwarg_accepted_for_symmetry():
    # Should not raise — mirrors rules_for_pipelines signature.
    out = rules.rules_for_bigquery_workloads(_payload(), source_type="bigquery")
    assert len(out) >= 1
