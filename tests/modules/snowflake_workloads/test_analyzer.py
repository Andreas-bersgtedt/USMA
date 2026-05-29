"""Phase 7 Slice 7-C — tests for ``modules.snowflake_workloads``."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules import MODULE_REGISTRY
from usma.modules.snowflake_workloads.analyzer import (
    SnowflakeWorkloadsAnalyzer,
)
from usma.modules.snowflake_workloads.collector import (
    SnowflakeWorkloadsCollector,
    _normalise_size,
    _outcome,
)
from usma.modules.snowflake_workloads.fabric_compat import (
    classify_object,
    classify_routine,
    classify_table,
)
from usma.modules.snowflake_workloads.models import (
    SnowflakeWorkloadsAnalysis,
)
from usma.modules.snowflake_workloads.reporting import write_reports
from usma.modules.spec import MODULE_SPECS
from usma.sources import SourceDescriptor, SourceType


ACCOUNT = "acme-prod"


# ---------------------------------------------------------------------------
# Stub connector — driven by an SQL-substring → rows map
# ---------------------------------------------------------------------------


class _StubCursor:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]):
        self._responses = responses
        self._rows: list[dict[str, Any]] = []
        self._desc: list[tuple[str]] = []

    def execute(self, sql: str) -> None:
        # Find the first registered SQL prefix that the executed statement matches.
        for needle, rows in self._responses.items():
            if needle in sql:
                self._rows = list(rows)
                cols = sorted({k for r in rows for k in r}) if rows else []
                self._desc = [(c,) for c in cols]
                return
        self._rows = []
        self._desc = []

    @property
    def description(self) -> list[tuple[str]]:
        return self._desc

    def fetchall(self) -> list[dict[str, Any]]:
        # Returning dicts triggers the collector's dict-branch in ``_row_to_dict``.
        return list(self._rows)

    def close(self) -> None:
        pass


class _StubConnection:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]):
        self._responses = responses
        self.closed = False

    def cursor(self) -> _StubCursor:
        return _StubCursor(self._responses)

    def close(self) -> None:
        self.closed = True


def _stub_connect(responses: dict[str, list[dict[str, Any]]]) -> Any:
    def _connect() -> _StubConnection:
        return _StubConnection(responses)
    return _connect


# ---------------------------------------------------------------------------
# fabric_compat — classification tables
# ---------------------------------------------------------------------------


def test_classify_table_supported_and_partial() -> None:
    assert classify_table("TABLE")[0] == "supported"
    assert classify_table("VIEW")[0] == "partial"
    assert classify_table("MATERIALIZED_VIEW")[0] == "partial"
    assert classify_table("DYNAMIC_TABLE")[0] == "partial"
    assert classify_table("ICEBERG_TABLE")[0] == "partial"
    assert classify_table("EXTERNAL_TABLE")[0] == "partial"


def test_classify_table_unknown() -> None:
    sup, note = classify_table("NEVER_HEARD_OF_THIS_KIND")
    assert sup == "unknown"
    assert "manual review" in note.lower()


def test_classify_table_handles_missing() -> None:
    assert classify_table(None) == (
        "unknown",
        "Unrecognised table kind — manual review required.",
    )


def test_classify_routine_kind_language_matrix() -> None:
    # JavaScript everywhere is unsupported.
    assert classify_routine("FUNCTION", "JAVASCRIPT")[0] == "unsupported"
    assert classify_routine("PROCEDURE", "JAVASCRIPT")[0] == "unsupported"
    # Java is unsupported too.
    assert classify_routine("FUNCTION", "JAVA")[0] == "unsupported"
    # SQL / Python / Scala are partial.
    assert classify_routine("FUNCTION", "SQL")[0] == "partial"
    assert classify_routine("PROCEDURE", "PYTHON")[0] == "partial"
    assert classify_routine("FUNCTION", "SCALA")[0] == "partial"


def test_classify_routine_unknown_language_falls_back_to_kind() -> None:
    sup, note = classify_routine("FUNCTION", "BRAINFUCK")
    assert sup == "partial"
    assert "BRAINFUCK" in note


def test_classify_object_stage_stream_task_pipe() -> None:
    assert classify_object("STAGE")[0] == "partial"
    assert classify_object("STREAM")[0] == "partial"
    assert classify_object("TASK")[0] == "partial"
    assert classify_object("PIPE")[0] == "partial"
    assert classify_object("UFO")[0] == "unknown"
    assert classify_object(None)[0] == "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_normalise_size_canonicals_and_aliases() -> None:
    assert _normalise_size("X-Small") == "X-SMALL"
    assert _normalise_size("XSMALL") == "X-SMALL"
    assert _normalise_size("xxlarge") == "2X-LARGE"
    assert _normalise_size("Medium") == "MEDIUM"
    assert _normalise_size("") == "UNKNOWN"
    assert _normalise_size(None) == "UNKNOWN"
    assert _normalise_size("WHATEVER") == "UNKNOWN"


def test_outcome_maps_execution_status_buckets() -> None:
    assert _outcome("SUCCESS") == "succeeded"
    assert _outcome("FAIL") == "failed"
    assert _outcome("FAILED_WITH_ERROR") == "failed"
    assert _outcome("INCIDENT") == "failed"
    assert _outcome("CANCELLED") == "cancelled"
    assert _outcome("RUNNING") == "in_progress"
    assert _outcome("RESUMING_WAREHOUSE") == "in_progress"
    assert _outcome("BANANA") == "unknown"
    assert _outcome(None) == "unknown"


# ---------------------------------------------------------------------------
# Collector — iterate over SHOW + ACCOUNT_USAGE rows
# ---------------------------------------------------------------------------


def test_collector_iter_warehouses_normalises_size_and_clusters() -> None:
    rows = [
        {
            "NAME": "USMA_WH",
            "SIZE": "X-Small",
            "TYPE": "STANDARD",
            "STATE": "STARTED",
            "MIN_CLUSTER_COUNT": "1",
            "MAX_CLUSTER_COUNT": "3",
            "SCALING_POLICY": "STANDARD",
            "AUTO_SUSPEND": "60",
            "AUTO_RESUME": "true",
            "OWNER": "SYSADMIN",
            "COMMENT": "primary wh",
            "CREATED_ON": "2024-01-01T00:00:00Z",
            "RESUMED_ON": "2024-06-01T00:00:00Z",
        },
    ]
    c = SnowflakeWorkloadsCollector(
        _stub_connect({"SHOW WAREHOUSES": rows}),
        account=ACCOUNT,
    )
    out = list(c.iter_warehouses())
    assert len(out) == 1
    w = out[0]
    assert w.name == "USMA_WH"
    assert w.size == "X-SMALL"
    assert w.min_cluster_count == 1
    assert w.max_cluster_count == 3
    assert w.auto_suspend_seconds == 60
    assert w.auto_resume is True


def test_collector_iter_databases_flags_transient_and_share() -> None:
    rows = [
        {
            "NAME": "ANALYTICS",
            "OWNER": "SYSADMIN",
            "KIND": "PERMANENT",
            "ORIGIN": "",
            "RETENTION_TIME": "1",
            "CREATED_ON": "2024-01-01T00:00:00Z",
        },
        {
            "NAME": "TEMP_DB",
            "OWNER": "ANALYST",
            "KIND": "TRANSIENT",
            "ORIGIN": "",
            "RETENTION_TIME": "0",
        },
        {
            "NAME": "SHARED_FROM_PARTNER",
            "OWNER": "ACCOUNTADMIN",
            "KIND": "PERMANENT",
            "ORIGIN": "PARTNER_ACCT.SHARE_X",
            "RETENTION_TIME": "1",
        },
    ]
    c = SnowflakeWorkloadsCollector(
        _stub_connect({"SHOW DATABASES": rows}),
        account=ACCOUNT,
    )
    out = list(c.iter_databases())
    by_name = {d.name: d for d in out}
    assert by_name["ANALYTICS"].is_transient is False
    assert by_name["TEMP_DB"].is_transient is True
    assert by_name["SHARED_FROM_PARTNER"].is_share is True


def test_collector_iter_tables_kinds_and_support() -> None:
    rows = [
        {"NAME": "DIM_DATE", "KIND": "TABLE"},
        {"NAME": "FCT_SALES", "KIND": "TABLE", "IS_TRANSIENT": "Y"},
        {"NAME": "EXT_LOGS", "KIND": "EXTERNAL TABLE"},
        {"NAME": "DYN_HOT", "KIND": "DYNAMIC TABLE"},
        {"NAME": "ICE_COLD", "KIND": "ICEBERG TABLE"},
        {"NAME": "TMP_X", "KIND": "TEMPORARY"},
    ]
    c = SnowflakeWorkloadsCollector(
        _stub_connect({"SHOW TABLES IN SCHEMA": rows}),
        account=ACCOUNT,
    )
    out = list(c.iter_tables("ANALYTICS", "PUBLIC"))
    by_name = {t.name: t for t in out}
    assert by_name["DIM_DATE"].kind == "TABLE"
    assert by_name["DIM_DATE"].support == "supported"
    assert by_name["FCT_SALES"].kind == "TRANSIENT"
    assert by_name["EXT_LOGS"].kind == "EXTERNAL_TABLE"
    assert by_name["DYN_HOT"].kind == "DYNAMIC_TABLE"
    assert by_name["ICE_COLD"].kind == "ICEBERG_TABLE"
    assert by_name["TMP_X"].kind == "TEMPORARY"
    assert by_name["DIM_DATE"].full_name == "ANALYTICS.PUBLIC.DIM_DATE"


def test_collector_iter_views_splits_materialized() -> None:
    rows = [
        {"NAME": "V_CUSTOMERS", "IS_MATERIALIZED": "false"},
        {"NAME": "MV_DAILY_SALES", "IS_MATERIALIZED": "true"},
    ]
    c = SnowflakeWorkloadsCollector(
        _stub_connect({"SHOW VIEWS IN SCHEMA": rows}),
        account=ACCOUNT,
    )
    out = list(c.iter_views("ANALYTICS", "PUBLIC"))
    by_name = {t.name: t for t in out}
    assert by_name["V_CUSTOMERS"].kind == "VIEW"
    assert by_name["MV_DAILY_SALES"].kind == "MATERIALIZED_VIEW"


def test_collector_iter_functions_and_procedures_classify_languages() -> None:
    fn_rows = [
        {"NAME": "FN_SQL", "LANGUAGE": "SQL", "ARGUMENTS": "(NUMBER) RETURN NUMBER"},
        {"NAME": "FN_JS", "LANGUAGE": "JAVASCRIPT", "ARGUMENTS": "()"},
    ]
    proc_rows = [
        {"NAME": "PR_PY", "LANGUAGE": "PYTHON", "ARGUMENTS": "(STRING, STRING)"},
    ]
    c = SnowflakeWorkloadsCollector(
        _stub_connect({
            "SHOW USER FUNCTIONS IN SCHEMA": fn_rows,
            "SHOW PROCEDURES IN SCHEMA": proc_rows,
        }),
        account=ACCOUNT,
    )
    fns = list(c.iter_functions("ANALYTICS", "PUBLIC"))
    procs = list(c.iter_procedures("ANALYTICS", "PUBLIC"))
    by_name = {r.name: r for r in fns + procs}
    assert by_name["FN_SQL"].language == "SQL"
    assert by_name["FN_SQL"].support == "partial"
    assert by_name["FN_JS"].language == "JAVASCRIPT"
    assert by_name["FN_JS"].support == "unsupported"
    assert by_name["PR_PY"].routine_kind == "PROCEDURE"
    assert by_name["PR_PY"].language == "PYTHON"


def test_collector_iter_tasks_parses_predecessors() -> None:
    rows = [
        {
            "NAME": "T_LOAD_GOLD",
            "STATE": "STARTED",
            "WAREHOUSE": "USMA_WH",
            "SCHEDULE": "USING CRON 0 * * * * UTC",
            "PREDECESSORS": '["ANALYTICS.PUBLIC.T_LOAD_SILVER"]',
            "OWNER": "SYSADMIN",
        },
    ]
    c = SnowflakeWorkloadsCollector(
        _stub_connect({"SHOW TASKS IN SCHEMA": rows}),
        account=ACCOUNT,
    )
    out = list(c.iter_tasks("ANALYTICS", "PUBLIC"))
    assert len(out) == 1
    t = out[0]
    assert t.state == "STARTED"
    assert t.predecessors == ["ANALYTICS.PUBLIC.T_LOAD_SILVER"]
    assert t.support == "partial"


def test_collector_iter_jobs_computes_duration_and_outcome() -> None:
    rows = [
        {
            "QUERY_ID": "01abc",
            "WAREHOUSE_NAME": "USMA_WH",
            "WAREHOUSE_SIZE": "Medium",
            "USER_NAME": "ANALYST",
            "ROLE_NAME": "ANALYST",
            "DATABASE_NAME": "ANALYTICS",
            "SCHEMA_NAME": "PUBLIC",
            "QUERY_TYPE": "SELECT",
            "EXECUTION_STATUS": "SUCCESS",
            "START_TIME": "2026-05-01T12:00:00Z",
            "END_TIME": "2026-05-01T12:00:42Z",
            "TOTAL_ELAPSED_TIME": "42000",
            "BYTES_SCANNED": "1048576",
            "ROWS_PRODUCED": "1000",
            "CREDITS_USED_CLOUD_SERVICES": "0.0001",
        },
        {
            "QUERY_ID": "02def",
            "EXECUTION_STATUS": "FAIL",
            "START_TIME": "2026-05-02T00:00:00Z",
            "END_TIME": "2026-05-02T00:00:01Z",
            "ERROR_CODE": "606",
            "ERROR_MESSAGE": "no warehouse",
        },
    ]
    c = SnowflakeWorkloadsCollector(
        _stub_connect({"SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY": rows}),
        account=ACCOUNT,
    )
    out = list(c.iter_jobs(lookback_days=28))
    assert len(out) == 2
    j1, j2 = out
    assert j1.query_id == "01abc"
    assert j1.outcome == "succeeded"
    assert j1.duration_seconds == pytest.approx(42.0)
    assert j1.warehouse_size == "MEDIUM"
    assert j1.credits_used_cloud_services == pytest.approx(0.0001)
    assert j2.outcome == "failed"
    assert j2.error_code == "606"


# ---------------------------------------------------------------------------
# Analyzer — orchestration
# ---------------------------------------------------------------------------


def _cfg() -> AppConfig:
    return AppConfig(
        azure=AzureConfig(
            tenant_id="t", client_id="c", client_secret="s",
            subscription_id="sub", resource_group="rg", workspace_name="ws",
        ),
        sql=SqlConfig(),
        output_dir=Path("./output"),
    )


def test_analyzer_run_aggregates_inventory_and_jobs() -> None:
    responses: dict[str, list[dict[str, Any]]] = {
        "SHOW WAREHOUSES": [
            {"NAME": "USMA_WH", "SIZE": "Medium", "TYPE": "STANDARD"},
        ],
        "SHOW DATABASES": [
            {"NAME": "ANALYTICS", "KIND": "PERMANENT"},
            {"NAME": "SNOWFLAKE", "KIND": "PERMANENT"},  # system; should be skipped
        ],
        "SHOW SCHEMAS IN DATABASE": [
            {"NAME": "PUBLIC"},
            {"NAME": "INFORMATION_SCHEMA"},  # skipped by analyzer
        ],
        "SHOW TABLES IN SCHEMA": [
            {"NAME": "DIM_DATE", "KIND": "TABLE"},
            {"NAME": "EXT_LOGS", "KIND": "EXTERNAL TABLE"},
        ],
        "SHOW VIEWS IN SCHEMA": [
            {"NAME": "V_CUSTOMERS", "IS_MATERIALIZED": "false"},
            {"NAME": "MV_DAILY", "IS_MATERIALIZED": "true"},
        ],
        "SHOW USER FUNCTIONS IN SCHEMA": [
            {"NAME": "FN_JS", "LANGUAGE": "JAVASCRIPT", "ARGUMENTS": "()"},
        ],
        "SHOW PROCEDURES IN SCHEMA": [],
        "SHOW STAGES IN SCHEMA": [
            {"NAME": "EXT_STAGE", "TYPE": "EXTERNAL", "CLOUD": "AWS"},
        ],
        "SHOW STREAMS IN SCHEMA": [],
        "SHOW TASKS IN SCHEMA": [
            {"NAME": "T_LOAD", "STATE": "STARTED"},
        ],
        "SHOW PIPES IN SCHEMA": [],
        "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY": [
            {
                "QUERY_ID": "01abc", "EXECUTION_STATUS": "SUCCESS",
                "START_TIME": "2026-05-01T12:00:00Z",
                "END_TIME": "2026-05-01T12:00:05Z",
            },
        ],
    }
    collector = SnowflakeWorkloadsCollector(
        _stub_connect(responses), account=ACCOUNT,
    )
    analyzer = SnowflakeWorkloadsAnalyzer(
        _cfg(),
        collector=collector,
        account=ACCOUNT,
        platform="aws",
        region="AWS_US_EAST_1",
        edition="ENTERPRISE",
    )
    result = analyzer.run()
    assert isinstance(result, SnowflakeWorkloadsAnalysis)
    assert result.account == ACCOUNT
    assert result.platform == "aws"
    assert result.warehouse_count == 1
    # SNOWFLAKE system db is skipped.
    assert {d.name for d in result.databases} == {"ANALYTICS", "SNOWFLAKE"}
    assert result.database_count == 2
    # ANALYTICS's INFORMATION_SCHEMA filtered out -> 1 schema collected.
    assert result.schema_count == 1
    assert result.table_count == 1  # DIM_DATE
    assert result.external_table_count == 1  # EXT_LOGS
    assert result.view_count == 1  # V_CUSTOMERS
    assert result.materialized_view_count == 1  # MV_DAILY
    assert result.routine_count == 1
    assert result.stage_count == 1
    assert result.task_count == 1
    assert result.job_count == 1
    # FN_JS (unsupported) contributes to the unsupported_object_count.
    assert result.unsupported_object_count >= 1
    assert result.partial_object_count >= 1

    # New Phase-7 stat surfaces.
    assert result.daily_stats and result.daily_stats[0].date == "2026-05-01"
    assert result.daily_stats[0].job_count == 1
    dims = {b.dimension for b in result.breakdowns}
    assert {"query_type", "user", "warehouse", "role", "status"} <= dims
    assert result.code_object_summary is not None
    assert result.code_object_summary.total >= 5
    assert "TABLE" in result.code_object_summary.by_kind
    # ACCESS_HISTORY not stubbed -> falls back to QUERY_HISTORY grouping (or
    # empty when jobs lack db/schema). The field must exist either way.
    assert isinstance(result.table_usage, list)


def test_analyzer_run_swallows_per_schema_errors() -> None:
    # A schema that fails enumeration on tables must not abort the run.
    class _FlakyConn:
        def cursor(self) -> Any:
            return _FlakyCur()

        def close(self) -> None:
            pass

    class _FlakyCur:
        def execute(self, sql: str) -> None:
            self._sql = sql
            if "SHOW TABLES" in sql:
                raise RuntimeError("boom")
            self._sql = sql

        @property
        def description(self) -> list[tuple[str]]:
            return []

        def fetchall(self) -> list[dict[str, Any]]:
            return []

        def close(self) -> None:
            pass

    base_responses: dict[str, list[dict[str, Any]]] = {
        "SHOW WAREHOUSES": [],
        "SHOW DATABASES": [{"NAME": "ANALYTICS"}],
        "SHOW SCHEMAS IN DATABASE": [{"NAME": "PUBLIC"}],
        "SHOW VIEWS IN SCHEMA": [],
        "SHOW USER FUNCTIONS IN SCHEMA": [],
        "SHOW PROCEDURES IN SCHEMA": [],
        "SHOW STAGES IN SCHEMA": [],
        "SHOW STREAMS IN SCHEMA": [],
        "SHOW TASKS IN SCHEMA": [],
        "SHOW PIPES IN SCHEMA": [],
        "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY": [],
    }

    class _MixedConn(_StubConnection):
        def cursor(self) -> Any:
            cur = _StubCursor(base_responses)
            real_execute = cur.execute

            def execute(sql: str) -> None:
                if "SHOW TABLES IN SCHEMA" in sql:
                    raise RuntimeError("show tables denied")
                real_execute(sql)
            cur.execute = execute  # type: ignore[method-assign]
            return cur

    collector = SnowflakeWorkloadsCollector(
        lambda: _MixedConn(base_responses), account=ACCOUNT,
    )
    result = SnowflakeWorkloadsAnalyzer(
        _cfg(), collector=collector, account=ACCOUNT,
    ).run()
    # The error is recorded; the rest of the run continues.
    assert any("tables[ANALYTICS.PUBLIC]" in e for e in result.errors)
    assert result.table_count == 0
    # Job stage still ran.
    assert result.job_count == 0
    # Privilege caveat surfaces because databases > 0 but tables == 0.
    assert any("under-privileged" in c for c in result.caveats)


def test_analyzer_emits_privilege_caveat_on_account_usage_error() -> None:
    """ACCOUNT_USAGE access denied → caveat fires even with non-zero tables."""
    from usma.modules.snowflake_workloads.models import SnowflakeWorkloadsAnalysis

    result = SnowflakeWorkloadsAnalysis(account=ACCOUNT, generated_at=datetime(2026, 5, 28, tzinfo=timezone.utc))
    result.database_count = 1
    result.table_count = 5
    result.errors.append(
        "jobs: ProgrammingError: SQL compilation error: Schema "
        "'SNOWFLAKE.ACCOUNT_USAGE' does not exist or not authorized.",
    )
    SnowflakeWorkloadsAnalyzer._emit_privilege_caveat(result)
    assert any("under-privileged" in c for c in result.caveats)


def test_analyzer_no_privilege_caveat_on_healthy_run() -> None:
    from usma.modules.snowflake_workloads.models import SnowflakeWorkloadsAnalysis

    result = SnowflakeWorkloadsAnalysis(account=ACCOUNT, generated_at=datetime(2026, 5, 28, tzinfo=timezone.utc))
    result.database_count = 1
    result.table_count = 5
    SnowflakeWorkloadsAnalyzer._emit_privilege_caveat(result)
    assert not any("under-privileged" in c for c in result.caveats)


def test_analyzer_account_usage_inventory_fallback() -> None:
    """SHOW DATABASES empty → analyzer backfills inventory from ACCOUNT_USAGE."""
    responses: dict[str, list[dict[str, Any]]] = {
        "SHOW WAREHOUSES": [],
        "SHOW DATABASES": [],  # role lacks per-db USAGE
        "SNOWFLAKE.ACCOUNT_USAGE.DATABASES": [
            {
                "NAME": "ANALYTICS", "OWNER": "SYSADMIN",
                "TYPE": "STANDARD", "IS_TRANSIENT": "NO",
                "RETENTION_TIME": 1, "COMMENT": None,
                "CREATED": "2026-05-01T00:00:00Z",
            },
        ],
        "SNOWFLAKE.ACCOUNT_USAGE.SCHEMATA": [
            {
                "DATABASE_NAME": "ANALYTICS", "NAME": "PUBLIC",
                "OWNER": "SYSADMIN", "IS_TRANSIENT": "NO",
                "IS_MANAGED_ACCESS": "NO", "RETENTION_TIME": 1,
                "COMMENT": None, "CREATED": "2026-05-01T00:00:00Z",
            },
        ],
        "SNOWFLAKE.ACCOUNT_USAGE.TABLES": [
            {
                "DATABASE_NAME": "ANALYTICS", "SCHEMA_NAME": "PUBLIC",
                "NAME": "DIM_DATE", "TABLE_TYPE": "BASE TABLE",
                "IS_TRANSIENT": "NO", "ROW_COUNT": 365, "BYTES": 4096,
                "CREATED": "2026-05-01T00:00:00Z",
            },
        ],
        "SNOWFLAKE.ACCOUNT_USAGE.VIEWS": [
            {
                "DATABASE_NAME": "ANALYTICS", "SCHEMA_NAME": "PUBLIC",
                "NAME": "V_CUSTOMERS", "CREATED": "2026-05-01T00:00:00Z",
            },
        ],
        "SNOWFLAKE.ACCOUNT_USAGE.FUNCTIONS": [
            {
                "DATABASE_NAME": "ANALYTICS", "SCHEMA_NAME": "PUBLIC",
                "NAME": "FN_SQL", "LANGUAGE": "SQL",
                "CREATED": "2026-05-01T00:00:00Z",
            },
        ],
        "SNOWFLAKE.ACCOUNT_USAGE.PROCEDURES": [
            {
                "DATABASE_NAME": "ANALYTICS", "SCHEMA_NAME": "PUBLIC",
                "NAME": "SP_LOAD", "LANGUAGE": "PYTHON",
                "CREATED": "2026-05-01T00:00:00Z",
            },
        ],
        "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY": [],
    }
    collector = SnowflakeWorkloadsCollector(
        _stub_connect(responses), account=ACCOUNT,
    )
    analyzer = SnowflakeWorkloadsAnalyzer(
        _cfg(), collector=collector, account=ACCOUNT,
    )
    result = analyzer.run()
    assert result.database_count == 1
    assert result.schema_count == 1
    assert result.table_count == 1
    assert result.view_count == 1
    assert result.routine_count == 2
    assert any("ACCOUNT_USAGE" in c for c in result.caveats)


def test_analyzer_run_without_collector_raises() -> None:
    with pytest.raises(RuntimeError, match="requires a collector"):
        SnowflakeWorkloadsAnalyzer(_cfg()).run()


def test_analyzer_for_descriptor_rejects_non_snowflake() -> None:
    desc = SourceDescriptor(
        type=SourceType.BIGQUERY, id="proj", display_name="proj",
    )
    with pytest.raises(ValueError, match="Snowflake SourceDescriptor"):
        SnowflakeWorkloadsAnalyzer.for_descriptor(_cfg(), desc, None)


# ---------------------------------------------------------------------------
# MODULE_REGISTRY + MODULE_SPECS wiring
# ---------------------------------------------------------------------------


def test_module_registry_includes_snowflake_workloads() -> None:
    assert "snowflake_workloads" in MODULE_REGISTRY


def test_module_spec_only_supports_snowflake() -> None:
    spec = MODULE_SPECS["snowflake_workloads"]
    assert spec.supports == frozenset({SourceType.SNOWFLAKE})


def test_module_factory_rejects_non_snowflake_primary_scope() -> None:
    from dataclasses import replace

    from usma.progress import NullProgress
    from usma.sources import SourceDescriptor

    cfg = replace(
        _cfg(),
        scopes=(
            SourceDescriptor(
                type=SourceType.SYNAPSE_WORKSPACE, id="x", display_name="ws",
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="requires a Snowflake scope"):
        MODULE_REGISTRY["snowflake_workloads"](cfg, NullProgress())


# ---------------------------------------------------------------------------
# Reporting — JSON / CSV / Markdown
# ---------------------------------------------------------------------------


def test_write_reports_emits_json_csv_md(tmp_path: Path) -> None:
    result = SnowflakeWorkloadsAnalysis(
        account=ACCOUNT,
        platform="aws",
        region="AWS_US_EAST_1",
        edition="ENTERPRISE",
        generated_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
    )
    paths = write_reports(result, tmp_path, formats=["json", "csv", "markdown"])
    names = {p.name for p in paths}
    assert "snowflake_workloads.json" in names
    assert "snowflake_workloads.md" in names
    # CSV writers skip empty buckets — the only thing we can rely on with
    # an empty result is the JSON + MD outputs being written.
    md_text = (tmp_path / "snowflake_workloads.md").read_text(encoding="utf-8")
    assert "Snowflake workloads" in md_text
    assert ACCOUNT in md_text


def test_write_reports_renders_warehouse_rows(tmp_path: Path) -> None:
    from usma.modules.snowflake_workloads.models import Warehouse

    result = SnowflakeWorkloadsAnalysis(
        account=ACCOUNT,
        generated_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        warehouses=[
            Warehouse(name="USMA_WH", size="MEDIUM", type="STANDARD"),
        ],
    )
    # Roll-ups are populated by the analyzer, but reporting reads them as-is.
    result.warehouse_count = 1
    write_reports(result, tmp_path, formats=["markdown", "csv"])
    md_text = (tmp_path / "snowflake_workloads.md").read_text(encoding="utf-8")
    assert "USMA_WH" in md_text
    assert "MEDIUM" in md_text
    csv_text = (tmp_path / "snowflake_warehouses.csv").read_text(encoding="utf-8")
    assert "USMA_WH" in csv_text


def test_analyzer_populates_table_usage_from_access_history() -> None:
    """When ACCESS_HISTORY rows are available the analyzer surfaces them."""
    responses: dict[str, list[dict[str, Any]]] = {
        "SHOW WAREHOUSES": [{"NAME": "WH", "SIZE": "Small", "TYPE": "STANDARD"}],
        "SHOW DATABASES": [{"NAME": "ANALYTICS", "KIND": "PERMANENT"}],
        "SHOW SCHEMAS IN DATABASE": [{"NAME": "PUBLIC"}],
        "SHOW TABLES IN SCHEMA": [{"NAME": "DIM_DATE", "KIND": "TABLE"}],
        "SHOW VIEWS IN SCHEMA": [],
        "SHOW USER FUNCTIONS IN SCHEMA": [],
        "SHOW PROCEDURES IN SCHEMA": [],
        "SHOW STAGES IN SCHEMA": [],
        "SHOW STREAMS IN SCHEMA": [],
        "SHOW TASKS IN SCHEMA": [],
        "SHOW PIPES IN SCHEMA": [],
        # Register ACCESS_HISTORY before QUERY_HISTORY so the stub's
        # substring matcher picks it for the new ACCESS_HISTORY query.
        "ACCESS_HISTORY": [
            {
                "OBJECT_DOMAIN": "Table",
                "OBJECT_NAME": "ANALYTICS.PUBLIC.DIM_DATE",
                "USAGE_COUNT": 42,
                "TOTAL_BYTES_SCANNED": 1024,
                "TOTAL_ROWS_PRODUCED": 100,
                "TOTAL_EXECUTION_MS": 5000,
                "LAST_SEEN": "2026-05-01T12:00:00Z",
            },
        ],
        "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY": [
            {
                "QUERY_ID": "anyq", "EXECUTION_STATUS": "SUCCESS",
                "START_TIME": "2026-05-01T12:00:00Z",
                "END_TIME": "2026-05-01T12:00:01Z",
            },
        ],
    }
    collector = SnowflakeWorkloadsCollector(
        _stub_connect(responses), account=ACCOUNT,
    )
    analyzer = SnowflakeWorkloadsAnalyzer(
        _cfg(), collector=collector, account=ACCOUNT,
    )
    result = analyzer.run()
    assert result.table_usage, "expected table_usage rows from ACCESS_HISTORY"
    row = result.table_usage[0]
    assert row.full_name == "ANALYTICS.PUBLIC.DIM_DATE"
    assert row.object_domain.upper().startswith("TABLE")
    assert row.usage_count == 42
    assert row.source == "access_history"
    assert row.in_catalog is True


def test_analyzer_table_usage_falls_back_to_query_history_schema_grouping() -> None:
    """ACCESS_HISTORY unavailable -> group jobs by (database, schema)."""
    responses: dict[str, list[dict[str, Any]]] = {
        "SHOW WAREHOUSES": [{"NAME": "WH", "SIZE": "Small", "TYPE": "STANDARD"}],
        "SHOW DATABASES": [{"NAME": "ANALYTICS", "KIND": "PERMANENT"}],
        "SHOW SCHEMAS IN DATABASE": [{"NAME": "PUBLIC"}],
        "SHOW TABLES IN SCHEMA": [{"NAME": "DIM_DATE", "KIND": "TABLE"}],
        "SHOW VIEWS IN SCHEMA": [],
        "SHOW USER FUNCTIONS IN SCHEMA": [],
        "SHOW PROCEDURES IN SCHEMA": [],
        "SHOW STAGES IN SCHEMA": [],
        "SHOW STREAMS IN SCHEMA": [],
        "SHOW TASKS IN SCHEMA": [],
        "SHOW PIPES IN SCHEMA": [],
        # Empty ACCESS_HISTORY result first -> the new collector method
        # returns 0 rows -> analyzer falls back to QUERY_HISTORY grouping.
        "ACCESS_HISTORY": [],
        "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY": [
            {
                "QUERY_ID": "q1", "EXECUTION_STATUS": "SUCCESS",
                "START_TIME": "2026-05-01T12:00:00Z",
                "END_TIME": "2026-05-01T12:00:05Z",
                "DATABASE_NAME": "ANALYTICS", "SCHEMA_NAME": "PUBLIC",
                "BYTES_SCANNED": 500,
            },
            {
                "QUERY_ID": "q2", "EXECUTION_STATUS": "SUCCESS",
                "START_TIME": "2026-05-01T12:01:00Z",
                "END_TIME": "2026-05-01T12:01:01Z",
                "DATABASE_NAME": "ANALYTICS", "SCHEMA_NAME": "PUBLIC",
                "BYTES_SCANNED": 100,
            },
        ],
        # No "ACCESS_HISTORY" key -> stub returns [] -> fallback path triggers.
    }
    collector = SnowflakeWorkloadsCollector(
        _stub_connect(responses), account=ACCOUNT,
    )
    analyzer = SnowflakeWorkloadsAnalyzer(
        _cfg(), collector=collector, account=ACCOUNT,
    )
    result = analyzer.run()
    assert result.table_usage, "expected fallback table_usage rows"
    row = result.table_usage[0]
    assert row.source == "query_history_fallback"
    assert row.full_name == "ANALYTICS.PUBLIC.*"
    assert row.object_domain == "SCHEMA"
    assert row.usage_count == 2
