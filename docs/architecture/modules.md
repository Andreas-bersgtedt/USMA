# Modules + source support

> Phase-1 — `MODULE_SPECS` populated and `plan_run()` helper live;
> per-module collectors still consume `AppConfig` directly (migration
> per source lands in Phases 2/4/5).

Each analysis module is described by a `ModuleSpec`
([`src/usma/modules/spec.py`](../../src/usma/modules/spec.py))
declaring which `SourceType` values it supports.

## Module-by-source support matrix (target state)

| Module | synapse_workspace | adf | databricks | sap_bw | sql_server |
|---|---|---|---|---|---|
| dedicated_pools | ✓ | — | — | — | — |
| serverless_pools | ✓ | — | — | — | — |
| spark_pools | ✓ | — | — | — | — |
| pipelines | ✓ | ✓ (Phase 2) | ✓* (Phase 4) | ✓* (Phase 5) | — |
| monitoring | ✓ | ✓ (Phase 2) | ✓ (Phase 4) | — | — |
| storage | ✓ | — | ✓ (Phase 4) | — | — |
| governance | ✓ | — | — | — | — |
| security | ✓ | — | ✓ (Phase 4) | ✓ (Phase 5) | — |
| cost | ✓ | ✓ (Phase 2) | ✓ (Phase 4) | — | — |
| fabric_validation | ✓ | — | — | — | — |
| fabric_mapping | ✓ | ✓ (Phase 2) | ✓ (Phase 4) | ✓ (Phase 5) | — |

`*` "pipelines" is generalized to orchestration / jobs / workflows for
non-Synapse sources (see ADR-0004 D5 candidate).

## ModuleSpec

```python
@dataclass(frozen=True)
class ModuleSpec:
    name: str
    factory: ModuleFactory
    supports: frozenset[SourceType]
    description: str
```

Phase-0 default: every module starts with
`supports = {SourceType.SYNAPSE_WORKSPACE}`. Subsequent phases widen
`supports` on each module as the relevant collector lands.

## Run planning

The real implementation lives in
[`modules/run_plan.py`](../../src/usma/modules/run_plan.py):

```python
from usma.modules.run_plan import plan_run

plan = plan_run(
    modules=["pipelines", "cost", "fabric_mapping"],
    scopes=[synapse_ws_a, adf_factory_b],
)
for task in plan.tasks:
    print(task.scope.display_name, task.module.name)
for sk in plan.skipped:
    print("skipped:", sk.scope.display_name, sk.module_name, sk.reason)
for unknown in plan.unknown_modules:
    print("unknown:", unknown)
```

The planner:

- Filters unknown module names into `unknown_modules` (callers decide
  whether to error or warn).
- Emits `(scope, module)` tasks where `module.supports` includes
  `scope.type`; everything else lands in `skipped` with a reason.
- Orders tasks `(scope-input-order, MODULE_SPECS-canonical-order)` for
  deterministic execution and diff stability.

`RunPlan`, `PlannedTask`, and `SkippedTask` are all frozen dataclasses
with no I/O — safe to construct and pass around from any layer.

## Manifest provenance (schema v2)

When the run executes, the manifest emitted by
[`reporting/run_manifest.build_manifest`](../../src/usma/reporting/run_manifest.py)
carries:

- top-level `scopes: list[ScopeRecord]` — one per scope that
  participated in the run.
- per-artifact `source_type` + `scope_id` — which scope produced this
  file.

v1 manifests (no `schema_version`, no `scopes` array) are
**auto-upgraded on read** with a synthetic single-scope record derived
from the legacy top-level `workspace_name` / `resource_group` /
`subscription_id` fields. v1 files on disk are never rewritten.
