"""Tests for ``modules.bigquery_workloads`` (Phase 5 Slice 5-B)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from usma.modules import MODULE_REGISTRY
from usma.modules.bigquery_workloads.analyzer import (
    BigQueryWorkloadsAnalyzer,
)
from usma.modules.bigquery_workloads.collector import (
    AUDIT_LOG_FILTER_TEMPLATE,
    BigQueryWorkloadsCollector,
    _extract_job_from_entry,
)
from usma.modules.bigquery_workloads.fabric_compat import (
    classify_routine,
    classify_table,
    job_type_bucket,
)
from usma.modules.bigquery_workloads.models import (
    BigQueryWorkloadsAnalysis,
    Dataset,
)
from usma.modules.bigquery_workloads.reporting import (
    write_reports,
)
from usma.modules.spec import MODULE_SPECS
from usma.sources import SourceType


PROJECT = "demo-project-123"


# ---------------------------------------------------------------------------
# Fixtures — duck-typed fakes for bq.Client / logging_v2.Client / DTS client
# ---------------------------------------------------------------------------


def _ds_ref(dataset_id: str) -> SimpleNamespace:
    # ``DatasetListItem`` in the real SDK exposes both ``.dataset_id`` and
    # a ``.reference`` ``DatasetReference`` with ``.dataset_id`` /
    # ``.datasetId`` plus the ``.path`` builder that ``Client.get_dataset``
    # actually consumes. Mirror enough of that here so the collector's
    # ``ref.reference`` round-trip works on the fakes too.
    reference = SimpleNamespace(dataset_id=dataset_id, datasetId=dataset_id)
    return SimpleNamespace(dataset_id=dataset_id, reference=reference)


def _ds_full(dataset_id: str, *, location: str = "us") -> SimpleNamespace:
    return SimpleNamespace(
        dataset_id=dataset_id,
        location=location,
        friendly_name=f"{dataset_id}-friendly",
        description="test ds",
        default_table_expiration_ms=None,
        default_partition_expiration_ms=None,
        labels={"env": "dev"},
        created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        modified=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )


def _table_ref(table_id: str) -> SimpleNamespace:
    reference = SimpleNamespace(table_id=table_id, tableId=table_id)
    return SimpleNamespace(table_id=table_id, reference=reference)


def _table_full(
    table_id: str,
    *,
    table_type: str = "TABLE",
    num_rows: int = 100,
    num_bytes: int = 1024,
    partition_field: str | None = None,
    clustering: list[str] | None = None,
) -> SimpleNamespace:
    partition = (
        SimpleNamespace(field=partition_field, type_=SimpleNamespace(value="DAY"))
        if partition_field
        else None
    )
    return SimpleNamespace(
        table_id=table_id,
        table_type=SimpleNamespace(value=table_type),
        time_partitioning=partition,
        range_partitioning=None,
        require_partition_filter=bool(partition_field),
        clustering_fields=clustering or [],
        num_rows=num_rows,
        num_bytes=num_bytes,
        created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        modified=datetime(2024, 6, 1, tzinfo=timezone.utc),
        expires=None,
        labels={},
    )


def _routine_ref(routine_id: str) -> SimpleNamespace:
    reference = SimpleNamespace(routine_id=routine_id, routineId=routine_id)
    return SimpleNamespace(routine_id=routine_id, reference=reference)


def _routine_full(
    routine_id: str,
    *,
    routine_type: str = "SCALAR_FUNCTION",
    language: str = "SQL",
    arguments: int = 2,
) -> SimpleNamespace:
    return SimpleNamespace(
        routine_id=routine_id,
        type_=SimpleNamespace(value=routine_type),
        language=SimpleNamespace(value=language),
        arguments=[SimpleNamespace(name=f"a{i}") for i in range(arguments)],
        created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        modified=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )


def _fake_bq_client() -> MagicMock:
    """A BigQuery client mock with two datasets, mixed tables, one routine."""
    client = MagicMock()
    client.list_datasets.return_value = [_ds_ref("sales"), _ds_ref("staging")]
    client.get_dataset.side_effect = lambda ref: _ds_full(
        ref.dataset_id, location="us" if ref.dataset_id == "sales" else "eu",
    )

    def _list_tables(dataset_id: str) -> list[SimpleNamespace]:
        if dataset_id == "sales":
            return [_table_ref("orders"), _table_ref("orders_v"), _table_ref("ext_logs")]
        if dataset_id == "staging":
            return [_table_ref("snap_2024_06_01")]
        return []

    def _get_table(ref: SimpleNamespace) -> SimpleNamespace:
        mapping = {
            "orders": _table_full(
                "orders", table_type="TABLE", num_rows=1_000_000,
                partition_field="order_date", clustering=["region"],
            ),
            "orders_v": _table_full("orders_v", table_type="VIEW"),
            "ext_logs": _table_full("ext_logs", table_type="EXTERNAL"),
            "snap_2024_06_01": _table_full("snap_2024_06_01", table_type="SNAPSHOT"),
        }
        return mapping[ref.table_id]

    client.list_tables.side_effect = _list_tables
    client.get_table.side_effect = _get_table

    def _list_routines(dataset_id: str) -> list[SimpleNamespace]:
        if dataset_id == "sales":
            return [_routine_ref("fn_revenue")]
        return []

    def _get_routine(ref: SimpleNamespace) -> SimpleNamespace:
        return _routine_full(
            ref.routine_id, routine_type="SCALAR_FUNCTION", language="SQL",
        )

    client.list_routines.side_effect = _list_routines
    client.get_routine.side_effect = _get_routine
    return client


def _v2_query_entry() -> dict:
    """A v2-shape audit-log entry for a successful query job."""
    return {
        "principal_email": "alice@contoso",
        "payload": {
            "authenticationInfo": {"principalEmail": "alice@contoso"},
            "metadata": {
                "jobChange": {
                    "job": {
                        "jobName": f"projects/{PROJECT}/jobs/job-v2-001",
                        "jobReference": {"jobId": "job-v2-001", "location": "us"},
                        "jobConfig": {"type": "QUERY"},
                        "jobStats": {
                            "startTime": "2026-05-01T10:00:00Z",
                            "endTime": "2026-05-01T10:00:05Z",
                            "queryStats": {
                                "totalSlotMs": 60000,
                                "totalBilledBytes": 10485760,
                                "totalProcessedBytes": 10485760,
                                "statementType": "SELECT",
                                "referencedTables": [
                                    {"projectId": PROJECT, "datasetId": "sales", "tableId": "orders"},
                                ],
                                "cacheHit": False,
                            },
                        },
                        "jobStatus": {"jobState": "DONE"},
                    }
                }
            },
        },
    }


def _v1_load_entry() -> dict:
    """A v1-shape audit-log entry for a successful LOAD job."""
    return {
        "payload": {
            "authenticationInfo": {"principalEmail": "bob@contoso"},
            "serviceData": {
                "jobCompletedEvent": {
                    "job": {
                        "jobName": {"jobId": "job-v1-001", "location": "eu"},
                        "jobConfiguration": {"load": {"sourceUris": ["gs://b/f.csv"]}},
                        "jobStatistics": {
                            "startTime": "2026-05-02T11:00:00Z",
                            "endTime": "2026-05-02T11:00:30Z",
                            "totalSlotMs": 900000,
                            "totalProcessedBytes": 5242880,
                        },
                        "jobStatus": {"state": "DONE"},
                    }
                }
            },
        },
    }


def _noise_entry() -> dict:
    """An audit-log entry that is NOT a job (should be filtered out)."""
    return {
        "payload": {
            "metadata": {
                "tableDataChange": {"tableName": f"projects/{PROJECT}/datasets/sales/tables/orders"},
            },
        },
    }


def _v2_dml_merge_entry() -> dict:
    """A v2 MERGE (DML) job — the kind of user-execution we must surface."""
    return {
        "payload": {
            "authenticationInfo": {"principalEmail": "carol@contoso"},
            "metadata": {
                "jobChange": {
                    "after": "DONE",
                    "job": {
                        "jobReference": {"jobId": "job-dml-merge-001", "location": "us"},
                        "jobConfig": {"type": "QUERY"},
                        "jobStats": {
                            "startTime": "2026-05-04T09:00:00Z",
                            "endTime": "2026-05-04T09:00:12Z",
                            "queryStats": {
                                "totalSlotMs": 240000,
                                "totalBilledBytes": 52428800,
                                "totalProcessedBytes": 52428800,
                                "statementType": "MERGE",
                                "referencedTables": [
                                    {"projectId": PROJECT, "datasetId": "sales", "tableId": "orders"},
                                ],
                            },
                        },
                        "jobStatus": {"jobState": "DONE"},
                    },
                },
            },
        },
    }


def _v2_extract_entry() -> dict:
    """A v2 EXTRACT (export to GCS) job."""
    return {
        "payload": {
            "authenticationInfo": {"principalEmail": "dave@contoso"},
            "metadata": {
                "jobChange": {
                    "after": "DONE",
                    "job": {
                        "jobReference": {"jobId": "job-extract-001", "location": "us"},
                        "jobConfig": {"type": "EXTRACT"},
                        "jobStats": {
                            "startTime": "2026-05-04T10:00:00Z",
                            "endTime": "2026-05-04T10:00:20Z",
                        },
                        "jobStatus": {"jobState": "DONE"},
                    },
                },
            },
        },
    }


def _v2_copy_entry() -> dict:
    """A v2 COPY (table-copy) job."""
    return {
        "payload": {
            "authenticationInfo": {"principalEmail": "eve@contoso"},
            "metadata": {
                "jobChange": {
                    "after": "DONE",
                    "job": {
                        "jobReference": {"jobId": "job-copy-001", "location": "us"},
                        "jobConfig": {"type": "COPY"},
                        "jobStats": {
                            "startTime": "2026-05-04T11:00:00Z",
                            "endTime": "2026-05-04T11:00:03Z",
                        },
                        "jobStatus": {"jobState": "DONE"},
                    },
                },
            },
        },
    }


def test_extractor_covers_user_execution_surface_dml_load_extract_copy():
    """Lock the four user-facing job kinds the GCP_logs reference targets:
    DML queries, data-import (LOAD), data-export (EXTRACT), and table COPY.
    Every one must round-trip through ``_extract_job_from_entry`` with the
    correct ``job_type`` / ``statement_type`` and a recognisable user email."""
    rows = [
        _extract_job_from_entry(_v2_dml_merge_entry(), project_id=PROJECT),
        _extract_job_from_entry(_v1_load_entry(), project_id=PROJECT),
        _extract_job_from_entry(_v2_extract_entry(), project_id=PROJECT),
        _extract_job_from_entry(_v2_copy_entry(), project_id=PROJECT),
    ]
    assert all(r is not None for r in rows)
    dml, load, extract, copy = rows  # type: ignore[misc]
    assert (dml.job_type, dml.statement_type, dml.user_email) == ("QUERY", "MERGE", "carol@contoso")
    assert (load.job_type, load.user_email) == ("LOAD", "bob@contoso")
    assert (extract.job_type, extract.user_email) == ("EXTRACT", "dave@contoso")
    assert (copy.job_type, copy.user_email) == ("COPY", "eve@contoso")
    # All must be marked succeeded.
    assert {r.outcome for r in rows} == {"succeeded"}


def _failed_v2_entry() -> dict:
    return {
        "payload": {
            "metadata": {
                "jobChange": {
                    "job": {
                        "jobReference": {"jobId": "job-v2-002", "location": "us"},
                        "jobConfig": {"type": "QUERY"},
                        "jobStats": {
                            "startTime": "2026-05-03T11:00:00Z",
                            "endTime": "2026-05-03T11:00:10Z",
                            "queryStats": {"totalSlotMs": 1000, "totalBilledBytes": 0},
                        },
                        "jobStatus": {
                            "jobState": "DONE",
                            "errorResult": {"reason": "invalidQuery", "message": "bad SQL"},
                        },
                    }
                }
            }
        }
    }


def _fake_log_client(entries: list[dict]) -> MagicMock:
    log = MagicMock()
    log.list_entries.return_value = iter(entries)
    return log


def _dts_scheduled_query_config(name: str = "sq-1") -> SimpleNamespace:
    return SimpleNamespace(
        name=f"projects/{PROJECT}/locations/us/transferConfigs/{name}",
        display_name=name,
        data_source_id="scheduled_query",
        destination_dataset_id="analytics",
        schedule="every 24 hours",
        state=SimpleNamespace(value="ENABLED"),
        next_run_time=datetime(2026, 5, 22, tzinfo=timezone.utc),
        update_time=datetime(2026, 5, 21, tzinfo=timezone.utc),
        user_id="0",
        params={"query": "SELECT 1"},
    )


def _dts_non_query_config() -> SimpleNamespace:
    return SimpleNamespace(
        name=f"projects/{PROJECT}/locations/us/transferConfigs/dts-2",
        display_name="dts-2",
        data_source_id="google_cloud_storage",  # not a scheduled query
        destination_dataset_id="raw",
        schedule="every 1 hours",
        state=SimpleNamespace(value="ENABLED"),
        next_run_time=None,
        update_time=None,
        user_id="0",
        params={},
    )


def _fake_dts_client() -> MagicMock:
    dts = MagicMock()

    def _list_configs(parent: str):
        if parent.endswith("/locations/us"):
            return iter([
                _dts_scheduled_query_config(),
                _dts_non_query_config(),
            ])
        return iter([])

    dts.list_transfer_configs.side_effect = _list_configs
    return dts


# ---------------------------------------------------------------------------
# Unit tests — fabric_compat
# ---------------------------------------------------------------------------


def test_classify_table_supported():
    label, note = classify_table("TABLE")
    assert label == "supported"
    assert "Fabric" in note


def test_classify_table_unsupported_snapshot():
    label, _ = classify_table("SNAPSHOT")
    assert label == "unsupported"


def test_classify_table_unknown_kind():
    label, note = classify_table("WEIRD_NEW_TYPE")
    assert label == "unknown"
    assert "WEIRD_NEW_TYPE" in note


def test_classify_table_none():
    label, _ = classify_table(None)
    assert label == "unknown"


def test_classify_routine_aggregate_unsupported():
    label, _ = classify_routine("AGGREGATE_FUNCTION")
    assert label == "unsupported"


def test_classify_routine_procedure_partial():
    label, _ = classify_routine("PROCEDURE")
    assert label == "partial"


def test_job_type_bucket():
    assert job_type_bucket("QUERY") == "query"
    assert job_type_bucket("LOAD") == "ingest"
    assert job_type_bucket("EXTRACT") == "egress"
    assert job_type_bucket("COPY") == "copy"
    assert job_type_bucket("UNKNOWN_KIND") == "unknown"
    assert job_type_bucket(None) == "unknown"


# ---------------------------------------------------------------------------
# Unit tests — _extract_job_from_entry
# ---------------------------------------------------------------------------


def test_extract_v2_query_entry():
    job = _extract_job_from_entry(_v2_query_entry(), project_id=PROJECT)
    assert job is not None
    assert job.job_id == "job-v2-001"
    assert job.project_id == PROJECT
    assert job.location == "us"
    assert job.job_type == "QUERY"
    assert job.statement_type == "SELECT"
    assert job.outcome == "succeeded"
    assert job.total_slot_ms == 60000
    assert job.total_billed_bytes == 10485760
    assert job.duration_seconds == 5.0
    assert job.referenced_table_count == 1
    assert job.user_email == "alice@contoso"


def test_extract_v1_load_entry():
    job = _extract_job_from_entry(_v1_load_entry(), project_id=PROJECT)
    assert job is not None
    assert job.job_id == "job-v1-001"
    assert job.location == "eu"
    assert job.job_type == "LOAD"
    assert job.outcome == "succeeded"
    assert job.total_slot_ms == 900000
    assert job.duration_seconds == 30.0


def test_extract_noise_entry_returns_none():
    assert _extract_job_from_entry(_noise_entry(), project_id=PROJECT) is None


def test_extract_failed_v2_entry_marked_failed():
    job = _extract_job_from_entry(_failed_v2_entry(), project_id=PROJECT)
    assert job is not None
    assert job.outcome == "failed"
    assert job.error_result is not None and "bad SQL" in job.error_result


# ---------------------------------------------------------------------------
# Collector tests
# ---------------------------------------------------------------------------


def test_collector_iter_datasets_and_tables():
    bq = _fake_bq_client()
    log = _fake_log_client([])
    c = BigQueryWorkloadsCollector(bq, log, project_id=PROJECT)

    datasets = list(c.iter_datasets())
    assert {d.dataset_id for d in datasets} == {"sales", "staging"}
    assert datasets[0].location == "us"
    assert datasets[0].labels == {"env": "dev"}

    sales_tables = list(c.iter_tables("sales"))
    assert {t.table_id for t in sales_tables} == {"orders", "orders_v", "ext_logs"}
    by_id = {t.table_id: t for t in sales_tables}
    assert by_id["orders"].table_type == "TABLE"
    assert by_id["orders"].partition_field == "order_date"
    assert by_id["orders"].clustering_fields == ["region"]
    assert by_id["orders"].support == "supported"
    assert by_id["orders_v"].table_type == "VIEW"
    assert by_id["orders_v"].support == "partial"
    assert by_id["ext_logs"].table_type == "EXTERNAL"
    assert by_id["ext_logs"].support == "partial"

    staging_tables = list(c.iter_tables("staging"))
    assert staging_tables[0].table_type == "SNAPSHOT"
    assert staging_tables[0].support == "unsupported"


def test_collector_iter_routines():
    bq = _fake_bq_client()
    log = _fake_log_client([])
    c = BigQueryWorkloadsCollector(bq, log, project_id=PROJECT)
    routines = list(c.iter_routines("sales"))
    assert len(routines) == 1
    assert routines[0].routine_id == "fn_revenue"
    assert routines[0].routine_type == "SCALAR_FUNCTION"
    assert routines[0].language == "SQL"
    assert routines[0].arguments_count == 2
    assert routines[0].support == "partial"
    # Datasets without routines yield empty.
    assert list(c.iter_routines("staging")) == []


def test_collector_iter_jobs_passes_filter_and_extracts():
    bq = _fake_bq_client()
    log = _fake_log_client([_v2_query_entry(), _noise_entry(), _v1_load_entry()])
    c = BigQueryWorkloadsCollector(bq, log, project_id=PROJECT)
    jobs = list(c.iter_jobs(lookback_days=7))
    assert {j.job_id for j in jobs} == {"job-v2-001", "job-v1-001"}
    # The filter expression carries the bigquery_resource clause.
    assert "bigquery_resource" in AUDIT_LOG_FILTER_TEMPLATE
    args, kwargs = log.list_entries.call_args
    assert "bigquery_resource" in kwargs["filter_"]
    assert kwargs["resource_names"] == [f"projects/{PROJECT}"]


def test_audit_log_filter_clamps_to_job_completions():
    """The filter must clamp Cloud Logging to job-completion events only,
    catching both v1 ``jobservice.jobcompleted`` and v2
    ``jobChange.after="DONE"`` shapes — so DML, load (data import),
    extract, and copy completions are all collected without dragging in
    tableservice / IAM / start-only noise."""
    f = AUDIT_LOG_FILTER_TEMPLATE.format(start_iso="2026-05-01T00:00:00")
    assert 'protoPayload.methodName="jobservice.jobcompleted"' in f
    assert 'protoPayload.metadata.jobChange.after="DONE"' in f
    assert 'protoPayload.serviceName="bigquery.googleapis.com"' in f
    # Severity clamp is gone — DONE entries are INFO but we don't need
    # to assert that server-side; the methodName/after predicate is tighter.
    assert "severity" not in f


def test_collector_iter_scheduled_queries_filters_non_query():
    bq = _fake_bq_client()
    log = _fake_log_client([])
    dts = _fake_dts_client()
    c = BigQueryWorkloadsCollector(
        bq, log, project_id=PROJECT, dts_client=dts, locations=("us",),
    )
    sqs = list(c.iter_scheduled_queries())
    assert len(sqs) == 1
    assert sqs[0].display_name == "sq-1"
    assert sqs[0].state == "ENABLED"
    assert sqs[0].destination_dataset_id == "analytics"
    assert sqs[0].params_query == "SELECT 1"


def test_collector_scheduled_queries_empty_when_no_dts_client():
    c = BigQueryWorkloadsCollector(
        _fake_bq_client(), _fake_log_client([]), project_id=PROJECT,
    )
    assert list(c.iter_scheduled_queries()) == []


# ---------------------------------------------------------------------------
# Analyzer tests
# ---------------------------------------------------------------------------


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.azure.workspace_name = "ignored"
    return cfg


def test_analyzer_run_end_to_end():
    bq = _fake_bq_client()
    log = _fake_log_client([_v2_query_entry(), _v1_load_entry()])
    dts = _fake_dts_client()
    collector = BigQueryWorkloadsCollector(
        bq, log, project_id=PROJECT, dts_client=dts, locations=("us",),
    )
    analyzer = BigQueryWorkloadsAnalyzer(
        _cfg(),
        collector=collector,
        project_id=PROJECT,
        project_number="123456789012",
        location="us",
        # This end-to-end exercise drives the audit-log extractor with
        # canned v1+v2 entries — keep the source pinned so the new
        # INFORMATION_SCHEMA default doesn't bypass it.
        jobs_source="audit_log",
    )
    result = analyzer.run()
    assert isinstance(result, BigQueryWorkloadsAnalysis)
    assert result.project_id == PROJECT
    assert result.dataset_count == 2
    assert result.table_count == 1  # only the TABLE — VIEW/EXTERNAL/SNAPSHOT are counted separately
    assert result.view_count == 1
    assert result.external_table_count == 1
    assert result.routine_count == 1
    assert result.scheduled_query_count == 1
    assert result.job_count == 2
    assert result.unsupported_table_count == 1  # SNAPSHOT
    assert result.partial_table_count == 2  # VIEW + EXTERNAL
    # Per-dataset table_count rollup.
    by_id = {d.dataset_id: d for d in result.datasets}
    assert by_id["sales"].table_count == 3
    assert by_id["staging"].table_count == 1
    assert result.errors == []


def test_analyzer_adds_caveat_when_dts_missing():
    bq = _fake_bq_client()
    log = _fake_log_client([])
    collector = BigQueryWorkloadsCollector(bq, log, project_id=PROJECT)
    analyzer = BigQueryWorkloadsAnalyzer(
        _cfg(), collector=collector, project_id=PROJECT, location="us",
    )
    result = analyzer.run()
    assert result.scheduled_query_count == 0
    assert any("Scheduled-query enumeration skipped" in c for c in result.caveats)


def test_analyzer_warns_about_disabled_data_access_logs_when_no_jobs():
    """When the audit-log pull returns zero jobs, the analyzer must
    surface a caveat pointing at the most common root cause —
    BigQuery Data Access audit logs being disabled. Without this hint
    users see an empty workloads page on a busy project and assume
    the tool is broken."""
    bq = _fake_bq_client()
    log = _fake_log_client([])  # zero audit entries
    collector = BigQueryWorkloadsCollector(bq, log, project_id=PROJECT)
    analyzer = BigQueryWorkloadsAnalyzer(
        _cfg(), collector=collector, project_id=PROJECT, location="us",
        job_lookback_days=28, jobs_source="audit_log",
    )
    result = analyzer.run()
    assert result.job_count == 0
    assert any(
        "Data Access audit logs are disabled" in c
        and "logging.logEntries.list" in c
        for c in result.caveats
    )


def test_analyzer_for_descriptor_rejects_non_bigquery():
    from usma.sources import SourceDescriptor

    bad = SourceDescriptor(
        type=SourceType.DATABRICKS, id="x", display_name="x",
    )
    with pytest.raises(ValueError, match="BigQuery"):
        BigQueryWorkloadsAnalyzer.for_descriptor(_cfg(), bad, None)


def test_analyzer_requires_collector():
    with pytest.raises(RuntimeError, match="requires a collector"):
        BigQueryWorkloadsAnalyzer(_cfg()).run()


# ---------------------------------------------------------------------------
# Reporting tests
# ---------------------------------------------------------------------------


def _minimal_result() -> BigQueryWorkloadsAnalysis:
    """Cheap fixture without going through the analyzer (keeps tests fast)."""
    return BigQueryWorkloadsAnalysis(
        project_id=PROJECT,
        project_number="123",
        location="us",
        generated_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
        datasets=[Dataset(project_id=PROJECT, dataset_id="d1", location="us", table_count=0)],
        dataset_count=1,
    )


def test_write_reports_all_formats(tmp_path: Path):
    result = _minimal_result()
    paths = write_reports(result, tmp_path, ["json", "csv", "markdown"])
    names = {p.name for p in paths}
    assert "bigquery_workloads.json" in names
    assert "bigquery_datasets.csv" in names
    assert "bigquery_workloads.md" in names
    md = (tmp_path / "bigquery_workloads.md").read_text(encoding="utf-8")
    assert "BigQuery workloads" in md
    assert PROJECT in md


def test_write_reports_only_json(tmp_path: Path):
    result = _minimal_result()
    paths = write_reports(result, tmp_path, ["json"])
    assert [p.name for p in paths] == ["bigquery_workloads.json"]


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_bigquery_workloads_in_module_registry():
    assert "bigquery_workloads" in MODULE_REGISTRY
    spec = MODULE_SPECS["bigquery_workloads"]
    assert spec.supports == frozenset({SourceType.BIGQUERY})
    assert "BigQuery" in spec.description


# ---------------------------------------------------------------------------
# INFORMATION_SCHEMA.JOBS_BY_PROJECT path (Slice 5-J)
# ---------------------------------------------------------------------------


def _info_schema_row(**overrides) -> SimpleNamespace:
    """Mimic a ``bigquery.table.Row`` — supports both attr and item access.

    The real client returns ``Row`` objects that respond to ``row["x"]``
    *and* ``row.x``; our collector reads via ``_get`` which tries attr
    first then dict. ``SimpleNamespace`` covers the attr branch and is
    enough for the unit tests.
    """
    defaults = dict(
        job_id="job-abc-001",
        user_email="alice@contoso",
        job_type="QUERY",
        statement_type="SELECT",
        creation_time=datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc),
        start_time=datetime(2026, 5, 1, 10, 0, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 1, 10, 0, 6, tzinfo=timezone.utc),
        total_slot_ms=60000,
        total_bytes_billed=10485760,
        total_bytes_processed=10485760,
        cache_hit=False,
        state="DONE",
        error_result=None,
        reservation_id=None,
        referenced_tables=[
            SimpleNamespace(project_id=PROJECT, dataset_id="sales", table_id="orders"),
        ],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_iter_jobs_information_schema_parses_rows_and_referenced_tables():
    """The new collector path must emit BigQueryJob objects from
    INFORMATION_SCHEMA rows and populate ``referenced_tables`` as
    fully-qualified ``project.dataset.table`` strings."""
    bq = MagicMock()
    bq.query.return_value.result.return_value = iter([
        _info_schema_row(),
        _info_schema_row(
            job_id="job-abc-002",
            statement_type="INSERT",
            referenced_tables=[
                SimpleNamespace(project_id=PROJECT, dataset_id="sales", table_id="orders"),
                SimpleNamespace(project_id=PROJECT, dataset_id="sales", table_id="orders_audit"),
            ],
        ),
    ])
    collector = BigQueryWorkloadsCollector(
        bq, MagicMock(), project_id=PROJECT, locations=("us",),
    )

    jobs = list(collector.iter_jobs_information_schema(lookback_days=28))

    assert len(jobs) == 2
    sent_sql = bq.query.call_args.args[0]
    assert "INFORMATION_SCHEMA.JOBS_BY_PROJECT" in sent_sql
    assert "region-us" in sent_sql  # region pulled from the descriptor location
    assert "state = 'DONE'" in sent_sql
    j1, j2 = jobs
    assert j1.job_id == "job-abc-001"
    assert j1.outcome == "succeeded"
    assert j1.referenced_tables == [f"{PROJECT}.sales.orders"]
    assert j1.referenced_table_count == 1
    assert j1.statement_type == "SELECT"
    assert j2.referenced_tables == [
        f"{PROJECT}.sales.orders",
        f"{PROJECT}.sales.orders_audit",
    ]


def test_iter_jobs_information_schema_swallows_query_errors():
    """A failed INFORMATION_SCHEMA query (permission denied, region typo,
    etc.) must not crash the run — the collector logs and yields zero
    rows so the analyzer can append the usual 0-jobs caveat."""
    bq = MagicMock()
    bq.query.side_effect = RuntimeError("403 Permission 'bigquery.jobs.listAll' denied")
    collector = BigQueryWorkloadsCollector(
        bq, MagicMock(), project_id=PROJECT, locations=("us",),
    )

    assert list(collector.iter_jobs_information_schema()) == []


def test_iter_jobs_information_schema_renders_error_result_struct():
    """``error_result`` in the view is a STRUCT with .message — render it
    to a short string so the JSON artefact doesn't dump a raw repr."""
    bq = MagicMock()
    bq.query.return_value.result.return_value = iter([
        _info_schema_row(
            state="DONE",
            error_result=SimpleNamespace(
                reason="invalidQuery",
                message="Syntax error: Unexpected identifier 'FORM'",
            ),
        ),
    ])
    collector = BigQueryWorkloadsCollector(
        bq, MagicMock(), project_id=PROJECT, locations=("us",),
    )

    jobs = list(collector.iter_jobs_information_schema())

    assert len(jobs) == 1
    assert jobs[0].outcome == "failed"
    assert "Syntax error" in (jobs[0].error_result or "")


def test_analyzer_uses_information_schema_by_default_and_caveat_text():
    """Default ``jobs_source='information_schema'`` must route the job
    pull through ``iter_jobs_information_schema`` and, when zero rows
    come back, emit the INFORMATION_SCHEMA-specific caveat (no
    audit-log advice)."""
    bq = _fake_bq_client()
    bq.query.return_value.result.return_value = iter([])  # no jobs
    log = _fake_log_client([_v2_query_entry()])  # would yield 1 if hit
    collector = BigQueryWorkloadsCollector(bq, log, project_id=PROJECT)
    analyzer = BigQueryWorkloadsAnalyzer(
        _cfg(), collector=collector, project_id=PROJECT, location="us",
        job_lookback_days=28,
    )
    result = analyzer.run()
    assert result.job_count == 0
    text = " ".join(result.caveats)
    assert "INFORMATION_SCHEMA.JOBS_BY_PROJECT" in text
    assert "Data Access audit logs are disabled" not in text  # don't mix advice
    assert "bigquery.jobs.listAll" in text


def test_analyzer_falls_back_to_audit_log_when_both_requested():
    """``jobs_source='both'`` must run INFORMATION_SCHEMA first and,
    only when it yields zero rows, fall back to the audit-log path so
    central-logging orgs still get coverage."""
    bq = _fake_bq_client()
    bq.query.return_value.result.return_value = iter([])  # info_schema empty
    log = _fake_log_client([_v2_query_entry()])  # audit-log has 1 job
    collector = BigQueryWorkloadsCollector(bq, log, project_id=PROJECT)
    analyzer = BigQueryWorkloadsAnalyzer(
        _cfg(), collector=collector, project_id=PROJECT, location="us",
        job_lookback_days=28, jobs_source="both",
    )
    result = analyzer.run()
    assert result.job_count == 1  # came from audit-log fallback


def test_fqn_table_refs_handles_v1_and_v2_shapes():
    """The fqn renderer must accept v2 ``projectId``/``datasetId``/``tableId``
    structs, v1 snake_case from INFORMATION_SCHEMA, and the resource-URI
    fallback used by some legacy v1 audit entries — and dedupe."""
    from usma.modules.bigquery_workloads.collector import (
        _fqn_table_refs,
    )

    items = [
        # v2 audit log shape
        {"projectId": "p", "datasetId": "d", "tableId": "t"},
        # INFORMATION_SCHEMA shape
        SimpleNamespace(project_id="p", dataset_id="d", table_id="t2"),
        # Resource URI shape (v1 legacy)
        {"name": "//bigquery.googleapis.com/projects/p/datasets/d/tables/t3"},
        # Duplicate — must be dropped
        {"projectId": "p", "datasetId": "d", "tableId": "t"},
    ]

    assert _fqn_table_refs(items) == ["p.d.t", "p.d.t2", "p.d.t3"]


def test_audit_log_extractor_now_emits_referenced_tables_list():
    """The v2 audit-log parser must populate the new
    ``referenced_tables: list[str]`` field, not just the count."""
    entry = _v2_query_entry()
    job = _extract_job_from_entry(entry, project_id=PROJECT)
    assert job is not None
    assert job.referenced_tables == [f"{PROJECT}.sales.orders"]
    assert job.referenced_table_count == 1

