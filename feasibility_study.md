# Feasibility study — Unified multi-source migration analyzer

**Branch:** `unified_SMA`
**Status:** Design / planning. No production code in this branch yet beyond this document and Phase-0 stubs.
**Author:** initial draft — `2026-05-19`

> **Renamed project:** **USMA — Unified Solution Migration Analyzer** (final pick per ADR-0004, signed off 2026-05-19; supersedes the *Estate Migration Analyzer* placeholder used in earlier drafts of this study). The Python package directory has been renamed from `synapse_migration_analyzer/` to `usma/` in Phase-3 PR #1; a thin shim package at `src/synapse_migration_analyzer/` re-exports `usma` with a `DeprecationWarning` for one minor release.

---

## 1. Background

The current tool (`SynapseMigrationAnalyzer`, "SMA") was built to analyze a single Azure Synapse Analytics workspace and produce a migration readiness report toward Microsoft Fabric. As adoption broadens, customers consistently have **more than just Synapse** in their data estate. Specifically:

- Many Synapse deployments rely on **Azure Data Factory (ADF)** for pipelines and data integration that sit alongside the Synapse workspace.
- Estate-wide modernization conversations increasingly involve **Databricks**, **SAP BW**, on-prem **SQL Server / SSIS**, and **Snowflake** as adjacent or replacement sources.
- A single, unified UI that produces one estate-level readiness report is significantly more valuable than N separate tools.

This study assesses (a) the effort to add ADF and (b) the architectural changes needed so that subsequent sources (Databricks, SAP BW, …) are *plug-in* additions rather than fresh forks.

---

## 2. Goals and non-goals

### Goals
1. Support multiple source types in a single tool, run, and UI.
2. Make adding a new source type cost ~60–100 hours, not ~250+ hours per source.
3. Preserve current Synapse functionality with zero user-visible regression during the refactor.
4. Produce an estate-level Fabric mapping / readiness view that aggregates across all in-scope sources.
5. Keep deployment simple — same `pip install` and same `synapse-migration-analyzer web` entry point during transition.

### Non-goals
1. Building actual migration tooling (code conversion, data movement). The tool remains assessment-only.
2. Multi-tenant SaaS hosting. Single-user / single-org local or self-hosted use stays the model.
3. Real-time monitoring. The tool produces point-in-time runs.
4. Source-to-source migrations (e.g. SAP BW → Databricks). Target is always Fabric.

---

## 3. Current architecture — assessment

### 3.1 What's already well-aligned

| Component | Status | Why it's well-aligned |
|---|---|---|
| Module registry (`src/usma/modules/__init__.py`, `MODULE_REGISTRY`) | ✅ Good | Each capability (`pipelines`, `security`, `cost`, …) is already a discrete plugin with a factory function. |
| Run manifest (`reporting/run_manifest.py`) | ✅ Good | Generic artifact tracking by file name + SHA + record count. Naming convention can extend to `<source>__<scope>__<module>.json`. |
| Pluggable reporting (JSON/CSV/MD/HTML per module) | ✅ Good | Each module owns its own renderers. No central renderer assumes Synapse-isms. |
| Auth (`ClientSecretCredential` via `azure-identity`) | ✅ Good | Same SP can be used across ARM resource types. Per-source credential override is additive. |
| FastAPI control plane + React SPA split | ✅ Good | Frontend already calls a generic `/api/runs/{id}/modules/{module}` endpoint. |
| Run history / re-run / diff | ✅ Good | Already source-agnostic at the manifest level. |

### 3.2 What's Synapse-coupled (the seams to break)

| Component | File | Coupling |
|---|---|---|
| Config schema | `src/usma/config.py` | Hardcodes `SYNAPSE_RESOURCE_GROUP`, `SYNAPSE_WORKSPACE_NAME` as required. |
| Doctor checks | `src/usma/doctor.py` | Same. |
| Workspace discovery | `src/usma/web/config_io.py` | Uses `SynapseManagementClient.workspaces.list()` only. |
| Pipeline collector | `src/usma/modules/pipelines/artifacts_client.py` | Uses `azure.synapse.artifacts.ArtifactsClient` (Synapse-only data plane). |
| Run history | `modules/pipelines/run_history_client.py` | Hits Synapse Monitor REST endpoints. |
| Frontend Configuration page | `web/src/pages/Configuration.tsx` | Single workspace dropdown, no source-type selector. |
| Run page | `web/src/pages/Run.tsx` | `ALL_MODULES` constant; no concept of "applicable to selected source". |
| Dashboard / Recommendations | `web/src/pages/Dashboard.tsx`, `web/src/pages/Recommendations.tsx` | Implicitly assume single source. |
| Effort estimator | `src/usma/effort/estimator.py` | Counts Synapse objects only. |

**Verdict:** the foundations (module registry, manifest, reporting, FastAPI/SPA split) are reusable. The coupling is concentrated in **config, discovery, and three frontend pages** — a tractable refactor surface.

---

## 4. Target architecture

### 4.1 Two orthogonal axes

```
                  MODULES (what is analyzed)
                  ┌────────────┬──────────┬──────┬─────────┬──────────────┐
                  │ pipelines  │ security │ cost │ storage │ fabric_map   │ …
SOURCES (where ───┼────────────┼──────────┼──────┼─────────┼──────────────┤
data is collected)│            │          │      │         │              │
  synapse_ws      │     ✓      │    ✓     │  ✓   │   ✓     │      ✓       │
  adf             │     ✓      │    —     │  ✓   │   —     │      ✓       │
  databricks      │     ✓*     │    ✓     │  ✓   │   ✓     │      ✓       │
  sap_bw          │     ✓*     │    ✓     │  ✓   │   —     │      ✓       │
  sql_server      │     —      │    ✓     │  —   │   ✓     │      ✓       │
                  └────────────┴──────────┴──────┴─────────┴──────────────┘
                  * "pipelines" generalized to "orchestration / jobs / workflows"
```

A **run** consists of a set of **scopes** (selected source instances) crossed with a set of **modules** (selected analyses). The platform dispatches only `(scope, module)` pairs where the module declares `supports={scope.type}`.

### 4.2 Core abstractions

```python
# src/<pkg>/sources/__init__.py

class SourceType(StrEnum):
    SYNAPSE_WORKSPACE = "synapse_workspace"
    ADF              = "adf"
    DATABRICKS       = "databricks"
    SAP_BW           = "sap_bw"
    SQL_SERVER       = "sql_server"          # future
    SNOWFLAKE        = "snowflake"           # future

@dataclass(frozen=True)
class SourceDescriptor:
    type: SourceType
    id: str                          # ARM resource id, or canonical id per source
    display_name: str
    subscription_id: str | None
    resource_group: str | None
    location: str | None
    extras: dict[str, Any]           # source-specific (e.g. databricks_url)

@dataclass(frozen=True)
class Credentials:
    tenant_id: str
    client_id: str
    client_secret: str
    # Optional per-source overrides (e.g. databricks PAT) live in extras
    extras: dict[str, str] = field(default_factory=dict)

class SourceProvider(Protocol):
    type: SourceType
    display_name: str
    required_env: tuple[str, ...]           # for non-ARM creds (e.g. DATABRICKS_PAT)
    def discover(self, creds: Credentials, subscription_id: str) -> list[SourceDescriptor]: ...
    def validate(self, descriptor: SourceDescriptor, creds: Credentials) -> list[ConfigCheck]: ...
    def make_clients(self, descriptor: SourceDescriptor, creds: Credentials) -> Any: ...

SOURCE_REGISTRY: dict[SourceType, SourceProvider] = { ... }
```

### 4.3 Module declares source support

```python
# src/<pkg>/modules/__init__.py

@dataclass(frozen=True)
class ModuleSpec:
    name: str
    factory: ModuleFactory
    supports: frozenset[SourceType]
    description: str

MODULE_REGISTRY: dict[str, ModuleSpec] = {
    "pipelines":         ModuleSpec("pipelines", _pipelines,
                                    supports=frozenset({SYNAPSE_WORKSPACE, ADF}), …),
    "security":          ModuleSpec("security", _security,
                                    supports=frozenset({SYNAPSE_WORKSPACE}), …),
    "fabric_mapping":    ModuleSpec("fabric_mapping", _fabric_mapping,
                                    supports=frozenset({SYNAPSE_WORKSPACE, ADF, DATABRICKS, SAP_BW}), …),
    …
}
```

### 4.4 Run model

```python
@dataclass
class RunRequest:
    scopes: list[SourceDescriptor]     # one or many
    modules: list[str]                 # logical names
    lookback_days: int
    # ...

@dataclass
class RunPlan:
    pairs: list[tuple[SourceDescriptor, ModuleSpec]]   # filtered by supports

def plan_run(req: RunRequest) -> RunPlan:
    pairs = []
    for scope in req.scopes:
        for mod_name in req.modules:
            mod = MODULE_REGISTRY[mod_name]
            if scope.type in mod.supports:
                pairs.append((scope, mod))
    return RunPlan(pairs=pairs)
```

Artifact naming: `<source_type>__<short_scope_id>__<module>.json`. The run manifest tracks the source/scope tuple per artifact.

### 4.5 Frontend changes

| Page | Today | Target |
|---|---|---|
| Configuration | Single Synapse workspace dropdown | Source-type tabs (Synapse / ADF / Databricks / …). Each tab lists discoverable scopes for that source. Selected scopes accumulate into `config.scopes: SourceDescriptor[]`. |
| Run | Module checkboxes + lookback | Same, plus per-scope selector ("run these modules against these scopes"). Module checkboxes greyed out where no selected scope supports them. |
| Dashboard | Per-module sections, single source assumed | Per-module sections aggregated **across all scopes**; each card breaks down by source where relevant. New **Estate** card at top showing scope count + readiness rollup. |
| Recommendations | Per-module recs | Same, with source/scope filter pills. |
| Runs history | Run id + status | Adds scope-count and source-types columns. |
| Diff | Already implemented (`web/src/pages/RunDiff.tsx`) | No change needed — operates on manifest. |

### 4.6 Data flow

```
        ┌──────────────────────────────────────────────────────────────┐
        │                       Configuration                          │
        │  Creds (tenant/client/secret/sub)  +  Scopes[] (source list) │
        └────────────────────────────┬─────────────────────────────────┘
                                     │
                              SOURCE_REGISTRY
                              .discover() per type
                                     │
        ┌────────────────────────────▼─────────────────────────────────┐
        │                          Run page                            │
        │   Modules[] × Scopes[] → RunPlan (filtered by supports)      │
        └────────────────────────────┬─────────────────────────────────┘
                                     │
                              Plan dispatched
                                     │
        ┌────────────────────────────▼─────────────────────────────────┐
        │   For each (scope, module): collect → analyze → write JSON   │
        │   Manifest entry: {source_type, scope_id, module, sha, …}    │
        └────────────────────────────┬─────────────────────────────────┘
                                     │
        ┌────────────────────────────▼─────────────────────────────────┐
        │       Dashboard / Recommendations / Runbook / PDF            │
        │  Aggregate across scopes; filter by source-type or scope id  │
        └──────────────────────────────────────────────────────────────┘
```

---

## 5. Source effort matrix (per source after Phase 1 lands)

Cost estimates assume the abstractions in §4 are in place.

| Source | API / SDK | Discovery | Pipelines | Security | Cost | Storage | Notes | **Total** |
|---|---|---|---|---|---|---|---|---|
| **Synapse workspace** (today) | `azure-mgmt-synapse`, `azure-synapse-artifacts` | done | done | done | done | done | Baseline | (done) |
| **ADF** | `azure-mgmt-datafactory` | S | M (90% reuse of pipelines) | n/a | S (reuse) | n/a | Schema near-identical to Synapse pipelines | **~80–100h** |
| **Databricks** | Databricks REST + `databricks-sdk` | S | M (jobs/workflows ≠ pipelines schema; new collector) | M | M | M (Unity Catalog) | New auth method (PAT or SP via AAD) | **~120–160h** |
| **SAP BW** | OData / RFC via `pyrfc` or 3rd-party connector | M | L (InfoProviders, transformations — alien schema) | M | M (license-based, not consumption) | L | Hardest source — Fabric story is also less mature | **~200–280h** |
| **SQL Server / SSIS** | `pyodbc`, SSIS XML | S | M (SSIS packages → pipeline mapping) | M | n/a (no consumption) | S | DMVs are well-known | **~100–140h** |
| **Snowflake** | `snowflake-connector-python` | S | n/a | M | M (credit usage) | M | Fabric mapping is a hot topic | **~120–160h** |

After Phase 1 the marginal cost is roughly *linear in genuinely new collector/schema work* and *constant for boilerplate* (provider registration, frontend wiring, config plumbing).

---

## 6. Phased delivery plan

Each phase is independently shippable and produces no user-visible regression on completion.

### Phase 0 — Decisions and stubs (this document + skeleton, ~1 week)
- Land this `feasibility_study.md` on `unified_SMA` branch.
- Stub the `sources/` package skeleton: `SourceType`, `SourceDescriptor`, `Credentials`, `SourceProvider` protocol, empty `SOURCE_REGISTRY`. **No wire-up to existing code yet.**
- Stub the `ModuleSpec` dataclass with `supports`. **All existing modules default `supports={SYNAPSE_WORKSPACE}`.**
- Lock the decisions in §9.
- ADR docs under `docs/adr/`.

**Exit criteria:** PR merged with stubs + tests for the stubs (constructors, registry lookup). Behavior unchanged.

### Phase 1 — Foundational refactor (no new source, ~80–120h)
Refactor the existing Synapse code to *use* the new abstractions without changing user-visible behavior.

- Move Synapse-specific discovery from `web/config_io.py` into `sources/synapse_workspace/provider.py`. `config_io.py` becomes a thin shim that calls `SOURCE_REGISTRY[SYNAPSE_WORKSPACE].discover()`.
- Refactor `config.py` and `doctor.py` to read scopes from a `SCOPES` list (with backwards-compat that a single `SYNAPSE_WORKSPACE_NAME` env var auto-populates a single scope).
- Refactor CLI: `analyze-all` accepts `--scope` (repeatable) or reads from config.
- Refactor `web/api/` endpoints to accept scope context per request.
- Frontend: Configuration page gains a (single-tab, hidden tabs) scope picker. Visually identical to today.
- Run manifest: artifact records gain `source_type` + `scope_id` fields. Existing single-scope runs auto-populate.
- Comprehensive regression tests — every existing test should still pass; add ~10 new tests for the scope abstraction.

**Exit criteria:** all current tests green; CLI usage unchanged; SPA behavior unchanged; manifest schema bumped to v2 with auto-upgrade for v1.

### Phase 2 — ADF as second source (~80–100h)
- Add `azure-mgmt-datafactory` dependency.
- Implement `sources/adf/provider.py` (discover, validate, make_clients).
- Implement `modules/pipelines/sources/adf_collector.py` — reuses `fabric_compat`, `expression_compat`, `schedule_mapper`, `run_stats`, `models` unchanged via composition.
- Update `pipelines` ModuleSpec: `supports = {SYNAPSE_WORKSPACE, ADF}`.
- Update `cost`, `fabric_mapping`: `supports += {ADF}`.
- Frontend: Configuration gains a real source-type tab UI; Run page filters checkboxes by scope support.
- Tests: ADF fixtures (can largely recycle Synapse pipeline fixtures since schema matches); new provider tests; multi-scope run integration test.
- Docs: new `docs/user-guide/12-adf.md`; updates to getting-started + modes + Mermaid.

**Exit criteria:** user can select a Synapse workspace and an ADF factory in one run; Dashboard shows merged pipelines view; Diff handles multi-scope correctly.

### Phase 3 — Unified UI polish (~40–60h)
- Estate summary card on Dashboard (scope count, source breakdown, overall readiness).
- Per-source/per-scope filter pills across Dashboard, Recommendations, Runbook.
- "Estate Migration Plan" PDF export — aggregated runbook across all scopes.
- Runs history columns: scope count + source-type chips.
- Re-record the user-guide getting-started Mermaid diagram.

**Exit criteria:** the multi-source story feels first-class, not bolted on.

### Phase 4 — Databricks (~120–160h)
- `sources/databricks/provider.py` — Databricks REST + `databricks-sdk`. AAD SP auth where supported; PAT fallback in `creds.extras`.
- New module `modules/databricks_workflows` (or extend `pipelines` to be `orchestration`) with jobs/workflow collector.
- Cost: DBU-based attribution model; reuse the CU-projection framework.
- Frontend: new source-type tab; Databricks-specific config fields.
- Documentation.

**Exit criteria:** Databricks workspaces appear in the same Run, Dashboard, Recommendations as Synapse + ADF.

### Phase 5 — SAP BW (~200–280h)
- `sources/sap_bw/provider.py` — OData / RFC. May require optional native dependency (`pyrfc`); document install path.
- New schema and rules for InfoProviders, transformations, process chains → Fabric mapping.
- Likely the biggest knowledge-gathering effort (Fabric SAP BW migration is a maturing area).
- Costs and run-history are different shape (license-based, not consumption).

**Exit criteria:** SAP BW estate visible alongside cloud sources; Fabric mapping covers the common BW patterns.

### Phase 6+ — Subsequent sources (~100–160h each)
- SQL Server / SSIS, Snowflake, etc. Each is a `SourceProvider` + (optional) module extension.

---

## 7. Scaffolding plan

This section enumerates the **directory and file scaffolding** to be created in `unified_SMA`. Phases 0 and 1 create the structure; Phase 2+ fill in source-specific code.

### 7.1 Python package skeleton

```
src/usma/
├── sources/                              # NEW (Phase 0)
│   ├── __init__.py                       # SourceType, Credentials, SourceDescriptor, SourceProvider, SOURCE_REGISTRY
│   ├── base.py                           # Protocol + base classes, helper for ARM discovery
│   ├── synapse_workspace/                # NEW (Phase 1) — moved from web/config_io.py
│   │   ├── __init__.py
│   │   ├── provider.py                   # discover/validate/make_clients
│   │   └── tests/test_provider.py
│   ├── adf/                              # NEW (Phase 2)
│   │   ├── __init__.py
│   │   ├── provider.py
│   │   └── tests/test_provider.py
│   ├── databricks/                       # NEW (Phase 4)
│   │   ├── __init__.py
│   │   ├── provider.py
│   │   └── tests/test_provider.py
│   ├── sap_bw/                           # NEW (Phase 5)
│   │   ├── __init__.py
│   │   ├── provider.py
│   │   └── tests/test_provider.py
│   └── README.md                         # contributor doc — "how to add a source"
│
├── modules/
│   ├── __init__.py                       # MODIFIED (Phase 0) — ModuleSpec dataclass with `supports`
│   ├── pipelines/
│   │   ├── analyzer.py                   # MODIFIED (Phase 2) — accepts source_type
│   │   ├── sources/                      # NEW (Phase 2)
│   │   │   ├── __init__.py
│   │   │   ├── synapse_collector.py      # extracted from current artifacts_client.py
│   │   │   └── adf_collector.py          # NEW (Phase 2)
│   │   ├── artifacts_client.py           # KEPT as alias importing synapse_collector for back-compat
│   │   └── …
│   ├── databricks_workflows/             # NEW (Phase 4)
│   │   └── …
│   └── …
│
├── config.py                             # MODIFIED (Phase 1) — adds scopes list; legacy single-workspace path preserved
├── doctor.py                             # MODIFIED (Phase 1) — per-scope checks
├── cli.py                                # MODIFIED (Phase 1) — --scope flag, multi-scope analyze-all
│
├── reporting/
│   └── run_manifest.py                   # MODIFIED (Phase 1) — manifest schema v2 with source_type/scope_id
│
└── web/
    ├── config_io.py                      # REFACTORED (Phase 1) — thin shim over SOURCE_REGISTRY
    └── api/
        ├── config.py                     # MODIFIED (Phase 1+2) — multi-source discover/validate
        └── runs.py                       # MODIFIED (Phase 1) — accepts scopes in run request
```

### 7.2 Frontend skeleton

```
web/src/
├── api/
│   └── loader.ts                         # MODIFIED — apiDiscoverByType(sourceType), scopes in RunRequest
├── pages/
│   ├── Configuration.tsx                 # REFACTORED (Phase 1 stub, Phase 2 real) — source-type tabs + scope list
│   ├── Run.tsx                           # MODIFIED — modules × scopes matrix
│   ├── Dashboard.tsx                     # MODIFIED (Phase 3) — estate card + per-source breakdown
│   ├── Recommendations.tsx               # MODIFIED (Phase 3) — source filter pills
│   ├── RunsHistory.tsx                   # MODIFIED (Phase 3) — scope/source columns
│   └── RunDiff.tsx                       # no change
└── components/
    ├── SourcePicker.tsx                  # NEW (Phase 1) — generic source-type tabbed picker
    ├── ScopeList.tsx                     # NEW (Phase 1) — selected-scopes table
    ├── EstateSummaryCard.tsx             # NEW (Phase 3)
    └── …
```

### 7.3 Docs skeleton

```
docs/
├── adr/                                  # NEW (Phase 0)
│   ├── 0001-multi-source-architecture.md
│   ├── 0002-scope-and-credentials-model.md
│   ├── 0003-manifest-v2-schema.md
│   └── 0004-package-rename.md            # if D2 is "yes"
├── user-guide/
│   ├── 01-getting-started.md             # MODIFIED — multi-source intro
│   ├── 02-modes.md                       # MODIFIED — scope concept
│   ├── 12-adf.md                         # NEW (Phase 2)
│   ├── 13-databricks.md                  # NEW (Phase 4)
│   ├── 14-sap-bw.md                      # NEW (Phase 5)
│   └── 99-adding-a-source.md             # NEW (Phase 1) — contributor guide
└── architecture/                         # NEW (Phase 1)
    ├── overview.md
    ├── sources.md
    └── modules.md
```

### 7.4 Tests skeleton

```
tests/
├── sources/                              # NEW (Phase 0)
│   ├── test_registry.py
│   ├── test_source_descriptor.py
│   ├── synapse_workspace/test_provider.py    # Phase 1
│   ├── adf/test_provider.py              # Phase 2
│   ├── databricks/test_provider.py       # Phase 4
│   └── sap_bw/test_provider.py           # Phase 5
├── test_run_plan.py                      # NEW (Phase 1) — module × scope filtering
├── test_manifest_v2.py                   # NEW (Phase 1)
├── fixtures/
│   ├── sources/synapse/…                 # moved from existing fixtures
│   ├── sources/adf/…                     # Phase 2
│   ├── sources/databricks/…              # Phase 4
│   └── sources/sap_bw/…                  # Phase 5
└── …
```

### 7.5 Config / packaging skeleton

```
pyproject.toml                            # additions per phase:
  Phase 2: azure-mgmt-datafactory
  Phase 4: databricks-sdk
  Phase 5: (optional) pyrfc — gated behind extras

.env.example                              # MODIFIED — show multi-source examples
```

---

## 8. Migration / backwards compatibility plan

| Concern | Strategy |
|---|---|
| Existing single-workspace `.env` files | Phase 1 preserves the `SYNAPSE_WORKSPACE_NAME` + `SYNAPSE_RESOURCE_GROUP` env vars; if present, auto-create a single Synapse scope at startup. |
| Existing runs on disk | Manifest v1 → v2 auto-upgrade on read: missing `source_type` defaults to `synapse_workspace`, `scope_id` defaults to a synthetic id derived from `workspace_name`. |
| CLI scripts in customer pipelines | `analyze-all` with no `--scope` flag still works against legacy env vars. |
| Static report bundles (already published) | Loader detects manifest version; v1 bundles continue to render as today. |
| Renaming the Python package (D2) | If approved, schedule the rename for Phase 3 with a 1-release deprecation shim (`usma` → re-exports from new name with `DeprecationWarning`). |

---

## 9. Open decisions (lock before Phase 1)

| ID | Decision | Default recommendation |
|---|---|---|
| **D1** | Single-scope or multi-scope per run? | **Multi-scope.** Estate-level reporting is the differentiator. |
| **D2** | Rename the Python package / repo / brand? | **Yes — schedule for Phase 3.** Sticking with "usma" is misleading once ADF/Databricks ship. Working name: **EstateMigrationAnalyzer (EMA)** or **FabricEstateAnalyzer (FEA)**. |
| **D3** | Credentials: one set for all sources, or per-scope? | **Both — shared default + per-scope override** stored in `Credentials.extras`. |
| **D4** | UI grouping: one tab per source vs. one big list | **Tabs in Configuration**, flat list with source pills elsewhere. |
| **D5** | "pipelines" module name — generalize to "orchestration"? | **Keep `pipelines` for now**; revisit before Databricks (Phase 4). Aliasing strategy is cheap. |
| **D6** | Optional native deps (e.g. `pyrfc` for SAP BW) | **Extras (`pip install ema[sapbw]`).** Don't force install for all users. |
| **D7** | Manifest v2 location of `source_type`/`scope_id` | **On each `ArtifactRecord`,** plus a top-level `scopes: [SourceDescriptor]` list. |
| **D8** | Run artifact naming convention | `<source_type>__<short_scope_hash>__<module>.json`. Short hash to avoid PII/long names. |
| **D9** | Module support metadata: hardcoded or declarative? | **Hardcoded `supports` on `ModuleSpec`** (Python). Declarative YAML is over-engineering for the current scale. |
| **D10** | Frontend state model — Redux/Zustand vs. current ad-hoc hooks | **Keep current pattern**; introduce a `ScopeContext` only if prop drilling becomes painful in Phase 2. |

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Phase 1 refactor introduces regressions in existing Synapse functionality | M | H | Comprehensive test coverage before refactor; feature-flag the new scope-aware paths; run full e2e against a known Synapse workspace before merging. |
| ADF / Databricks / SAP BW APIs evolve and break collectors | L–M | M | Pin SDK versions; per-source contract tests; nightly smoke against test instances where feasible. |
| SAP BW connectivity (Phase 5) hits org-specific firewalls / native lib issues | H | M | Optional `[sapbw]` extra; document RFC connector setup; allow JSON-import-mode for customers who export their BW metadata offline. |
| "Generalizing pipelines" causes hidden behavioral drift in current Synapse reports | M | M | Keep the Synapse code path untouched in Phase 1; only Phase 2 introduces the multi-source split inside `pipelines`. |
| Frontend complexity balloons with N source types | M | M | Source picker is generic, not per-source-coded; per-source UI lives in small components registered by source type. |
| Package rename (D2) breaks downstream consumers | L | M | Deprecation shim for one minor release; clear `CHANGELOG.md` entry; coordinate with anyone embedding the tool. |
| Manifest v2 migration corrupts existing runs | L | H | Read-only auto-upgrade (never rewrite v1 files on disk); write v2 only for new runs. |

---

## 11. Recommended next steps

1. **Land this document on `unified_SMA`** (this PR).
2. **Lock D1–D10** with stakeholders (the decisions above).
3. **PR #2 — Phase 0 stubs:** create `sources/__init__.py`, `sources/base.py`, `ModuleSpec` dataclass, `tests/sources/test_registry.py`, ADRs 0001–0003. No production code change.
4. **PR #3 — Phase 1 part A:** move Synapse discovery into `sources/synapse_workspace/provider.py`. `web/config_io.py` becomes a shim. All existing tests still pass.
5. **PR #4 — Phase 1 part B:** scope-aware `RunRequest` and manifest v2. Single-scope behavior preserved by default.
6. **PR #5 — Phase 1 part C:** frontend `SourcePicker` / `ScopeList` components landed but visually identical to today (one source, one scope, no tabs visible yet).
7. **PR #6 — Phase 2:** ADF source + multi-source UI revealed.

---

## 12. Appendix — file-level effort estimate (Phases 0–2 only)

| Phase | New files | Modified files | Test files | Est. effort |
|---|---|---|---|---|
| Phase 0 | 6 | 1 (`modules/__init__.py`) | 3 | ~16h |
| Phase 1 | ~12 | ~10 | ~10 | ~80–120h |
| Phase 2 | ~10 | ~8 | ~6 | ~80–100h |
| **Subtotal** | **~28** | **~19** | **~19** | **~180–240h** |

Phases 3–6 effort estimates are in §5 and §6.

---

*End of feasibility study.*
