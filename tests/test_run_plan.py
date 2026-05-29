"""Tests for plan_run / RunPlan (Phase 1)."""
from __future__ import annotations

from usma.modules.run_plan import (
    RunPlan,
    SkippedTask,
    plan_run,
)
from usma.modules.spec import MODULE_SPECS
from usma.sources import SourceDescriptor, SourceType


def _scope(name: str, st: SourceType = SourceType.SYNAPSE_WORKSPACE) -> SourceDescriptor:
    return SourceDescriptor(
        type=st,
        id=f"/scopes/{name}",
        display_name=name,
        subscription_id="sub",
        resource_group="rg",
    )


def test_plan_run_single_synapse_scope_runs_every_module():
    s = _scope("ws-a")
    plan = plan_run(list(MODULE_SPECS), [s])
    # All modules except the Databricks-only ``databricks_workflows`` apply.
    synapse_modules = [n for n, sp in MODULE_SPECS.items() if SourceType.SYNAPSE_WORKSPACE in sp.supports]
    assert len(plan.tasks) == len(synapse_modules)
    assert {t.module.name for t in plan.tasks} == set(synapse_modules)
    assert all(t.scope is s for t in plan.tasks)
    assert {sk.module_name for sk in plan.skipped} == set(MODULE_SPECS) - set(synapse_modules)
    assert plan.unknown_modules == ()


def test_plan_run_skips_unsupported_source():
    # Databricks: Slice 4-C widens ``cost`` to cover Databricks, so cost
    # now runs while ``pipelines`` remains the orchestration-sibling that
    # stays Synapse/ADF-only (Databricks uses ``databricks_workflows``).
    dbx = _scope("workspace-a", SourceType.DATABRICKS)
    plan = plan_run(["pipelines", "cost"], [dbx])
    assert {t.module.name for t in plan.tasks} == {"cost"}
    assert {sk.module_name for sk in plan.skipped} == {"pipelines"}
    assert all(isinstance(sk, SkippedTask) for sk in plan.skipped)


def test_plan_run_databricks_scope_runs_databricks_workflows():
    dbx = _scope("workspace-a", SourceType.DATABRICKS)
    plan = plan_run(["databricks_workflows", "pipelines"], [dbx])
    assert {t.module.name for t in plan.tasks} == {"databricks_workflows"}
    assert {sk.module_name for sk in plan.skipped} == {"pipelines"}


def test_plan_run_flags_unknown_modules():
    s = _scope("ws-a")
    plan = plan_run(["pipelines", "not_a_module", "cost"], [s])
    assert plan.unknown_modules == ("not_a_module",)
    assert {t.module.name for t in plan.tasks} == {"pipelines", "cost"}


def test_plan_run_emits_canonical_module_order():
    s = _scope("ws-a")
    # Input out of order; output should follow MODULE_SPECS canonical order.
    plan = plan_run(["cost", "pipelines", "dedicated_pools"], [s])
    order = [t.module.name for t in plan.tasks]
    canonical = [n for n in MODULE_SPECS if n in {"cost", "pipelines", "dedicated_pools"}]
    assert order == canonical


def test_plan_run_multi_scope_cartesian():
    s1 = _scope("ws-a")
    s2 = _scope("ws-b")
    plan = plan_run(["pipelines", "cost"], [s1, s2])
    # 2 scopes × 2 modules = 4 tasks; outer = scope, inner = module canonical.
    assert [(t.scope.display_name, t.module.name) for t in plan.tasks] == [
        ("ws-a", "pipelines"),
        ("ws-a", "cost"),
        ("ws-b", "pipelines"),
        ("ws-b", "cost"),
    ]


def test_plan_run_mixed_scopes_mixed_support():
    syn = _scope("ws-a")
    dbx = _scope("workspace-x", SourceType.DATABRICKS)
    plan = plan_run(["pipelines"], [syn, dbx])
    # Synapse pipelines runs; Databricks pipelines skipped (lands in Phase 4).
    assert [t.scope.display_name for t in plan.tasks] == ["ws-a"]
    assert [sk.scope.display_name for sk in plan.skipped] == ["workspace-x"]


def test_plan_run_adf_and_synapse_share_supported_modules():
    # Phase-2 regression: pipelines/cost/fabric_mapping support both.
    syn = _scope("ws-a")
    adf = _scope("factory-a", SourceType.ADF)
    plan = plan_run(["pipelines", "cost", "fabric_mapping"], [syn, adf])
    # 2 scopes × 3 modules = 6 tasks; none skipped.
    assert len(plan.tasks) == 6
    assert plan.skipped == ()
    assert plan.unknown_modules == ()
    # ADF scope skips Synapse-only modules when those are selected too.
    plan2 = plan_run(["pipelines", "dedicated_pools"], [adf])
    assert {t.module.name for t in plan2.tasks} == {"pipelines"}
    assert {sk.module_name for sk in plan2.skipped} == {"dedicated_pools"}


def test_plan_run_empty_inputs():
    assert plan_run([], []) == RunPlan(tasks=(), skipped=(), unknown_modules=())
    assert plan_run([], [_scope("ws")]).tasks == ()
    assert plan_run(["pipelines"], []).tasks == ()


def test_planned_task_and_run_plan_are_frozen():
    s = _scope("ws-a")
    plan = plan_run(["pipelines"], [s])
    task = plan.tasks[0]
    try:
        task.module = MODULE_SPECS["cost"]  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("PlannedTask must be frozen")
    assert isinstance(plan, RunPlan)


def test_module_names_and_scopes_helpers():
    s1 = _scope("ws-a")
    s2 = _scope("ws-b")
    plan = plan_run(["cost", "pipelines"], [s1, s2])
    assert plan.module_names() == ["pipelines", "cost"]  # canonical order
    assert [sc.display_name for sc in plan.scopes()] == ["ws-a", "ws-b"]
