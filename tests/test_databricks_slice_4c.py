"""Tests for Slice 4-C widening of cost + fabric_mapping to DATABRICKS."""
from __future__ import annotations

from usma.modules.cost.cost_client import _classify_resource_id
from usma.modules.fabric_mapping.rules import (
    rules_for_databricks_workflows,
)
from usma.modules.spec import MODULE_SPECS
from usma.sources import SourceType


# ---------------------------------------------------------------------------
# MODULE_SPECS
# ---------------------------------------------------------------------------


def test_cost_supports_databricks():
    assert SourceType.DATABRICKS in MODULE_SPECS["cost"].supports


def test_fabric_mapping_supports_databricks():
    assert SourceType.DATABRICKS in MODULE_SPECS["fabric_mapping"].supports


# ---------------------------------------------------------------------------
# Cost ARM-id classification
# ---------------------------------------------------------------------------


def test_classify_databricks_workspace():
    arm = (
        "/subscriptions/00000000-0000-0000-0000-000000000000"
        "/resourceGroups/rg-data/providers/Microsoft.Databricks"
        "/workspaces/dbx-prod"
    )
    kind, name = _classify_resource_id(arm)
    assert kind == "databricks_workspace"
    assert name == "dbx-prod"


def test_classify_adf_factory():
    arm = (
        "/subscriptions/00000000-0000-0000-0000-000000000000"
        "/resourceGroups/rg-data/providers/Microsoft.DataFactory"
        "/factories/main-factory"
    )
    kind, name = _classify_resource_id(arm)
    assert kind == "adf_factory"
    assert name == "main-factory"


# ---------------------------------------------------------------------------
# rules_for_databricks_workflows
# ---------------------------------------------------------------------------


def _payload(**overrides) -> dict:
    base = {
        "workflows": [
            {"job_id": 1, "name": "etl-daily", "task_count": 3},
        ],
        "tasks": [
            {
                "job_id": 1, "job_name": "etl-daily", "task_key": "bronze",
                "task_type": "notebook_task", "support": "supported",
            },
            {
                "job_id": 1, "job_name": "etl-daily", "task_key": "dlt",
                "task_type": "pipeline_task", "support": "partial",
                "dlt_pipeline_id": "dlt-pipe-1",
            },
            {
                "job_id": 1, "job_name": "etl-daily", "task_key": "weird",
                "task_type": "weird_task", "support": "unknown",
            },
            {
                "job_id": 1, "job_name": "etl-daily", "task_key": "kill",
                "task_type": "spark_submit_legacy",
                "support": "unsupported",
            },
        ],
        "job_clusters": [
            {
                "job_id": 1, "job_name": "etl-daily",
                "job_cluster_key": "old", "spark_version": "10.4.x-scala2.12",
            },
            {
                "job_id": 1, "job_name": "etl-daily",
                "job_cluster_key": "new", "spark_version": "13.3.x-scala2.12",
            },
        ],
        "interactive_clusters": [
            {"cluster_id": "c-1", "cluster_name": "shared"},
        ],
    }
    base.update(overrides)
    return base


def test_databricks_rules_emit_inventory():
    recs = rules_for_databricks_workflows(_payload())
    inv = [r for r in recs if r.id == "db.workflows_inventory"]
    assert len(inv) == 1
    assert "1 Databricks workflow(s)" in inv[0].title


def test_databricks_rules_flag_unsupported_partial_unknown():
    recs = rules_for_databricks_workflows(_payload())
    ids = {r.id for r in recs}
    # unsupported task type
    assert "db.unsupported.spark_submit_legacy" in ids
    # partial includes the pipeline_task DLT one
    assert "db.partial.pipeline_task" in ids
    # unknown rolled into a single rec
    assert "db.unknown_tasks" in ids
    # DLT pipeline tasks separately surfaced
    assert "db.dlt_pipelines" in ids


def test_databricks_rules_runtime_drift():
    recs = rules_for_databricks_workflows(_payload())
    runtime = [r for r in recs if r.id == "db.cluster_runtime"]
    assert len(runtime) == 1
    # Only the 10.4 cluster is flagged; the 13.3 one is fine.
    assert "1 job cluster(s)" in runtime[0].title


def test_databricks_rules_interactive_cluster_signal():
    recs = rules_for_databricks_workflows(_payload())
    sprawl = [r for r in recs if r.id == "db.interactive_clusters"]
    assert len(sprawl) == 1
    assert sprawl[0].severity == "info"


def test_databricks_rules_empty_payload_emits_nothing():
    assert rules_for_databricks_workflows({}) == []
    assert rules_for_databricks_workflows({"workflows": []}) == []


def test_databricks_rules_no_outdated_clusters_skips_runtime_rec():
    payload = _payload(job_clusters=[
        {
            "job_id": 1, "job_name": "etl-daily",
            "job_cluster_key": "new", "spark_version": "14.3.x-scala2.12",
        },
    ])
    recs = rules_for_databricks_workflows(payload)
    assert not any(r.id == "db.cluster_runtime" for r in recs)


# ---------------------------------------------------------------------------
# Analyzer + runbook wiring
# ---------------------------------------------------------------------------


def test_databricks_workflows_in_fabric_mapping_inputs():
    from usma.modules.fabric_mapping import analyzer

    assert "databricks_workflows" in analyzer._INPUT_FILES
    assert analyzer._INPUT_FILES["databricks_workflows"] == "databricks_workflows.json"
    assert "databricks_workflows" in analyzer._RULES


def test_runbook_phase_mapping_for_databricks_areas():
    from usma.modules.fabric_mapping.runbook import _AREA_TO_PHASE

    assert _AREA_TO_PHASE["databricks_workflows"] == "orchestration_migration"
    assert _AREA_TO_PHASE["databricks_workflows.tasks"] == "orchestration_migration"
    assert _AREA_TO_PHASE["databricks_workflows.clusters"] == "compute_migration"


def test_runbook_rollback_hint_for_databricks_workflows():
    from usma.modules.fabric_mapping.models import Recommendation
    from usma.modules.fabric_mapping.runbook import (
        _rollback_hint_for,
    )

    rec = Recommendation(
        id="db.workflows_inventory",
        area="databricks_workflows",
        title="x", detail="x", severity="info", effort="high",
    )
    hint = _rollback_hint_for(rec)
    assert hint is not None
    assert "paused" in hint.lower()

    rec_cluster = Recommendation(
        id="db.cluster_runtime",
        area="databricks_workflows.clusters",
        title="x", detail="x", severity="warning", effort="medium",
    )
    cluster_hint = _rollback_hint_for(rec_cluster)
    assert cluster_hint is not None
    assert "spark runtime parity" in cluster_hint.lower()
