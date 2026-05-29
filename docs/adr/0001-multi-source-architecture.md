# 0001 — Multi-source plugin architecture

**Status:** Accepted (2026-05-19)
**Date:** 2026-05-19
**Decision IDs (feasibility study §9):** D1, D9

## Context

The tool currently analyzes a single Azure Synapse Analytics workspace.
Customers increasingly have additional sources (ADF, Databricks, SAP BW,
SQL Server / SSIS, Snowflake) and want a unified estate-wide readiness
view rather than separate tools.

Today, "Synapse" assumptions are baked into config, discovery, the
pipelines collector, the Configuration page, and several other modules.
Adding ADF as a parallel, copy-pasted module would compound the problem
for every subsequent source.

## Decision

Introduce two orthogonal axes:

1. **Source** — a kind of data-estate system. Modeled as a
   `SourceProvider` plugin in `src/synapse_migration_analyzer/sources/`.
   The provider is responsible for discovery, validation, and producing
   data-plane clients for that source.

2. **Module** — a unit of analysis (`pipelines`, `cost`, `security`,
   `fabric_mapping`, ...). Each module is described by a `ModuleSpec`
   that declares a `supports: frozenset[SourceType]` set.

A **run** is a cartesian product of selected scopes (concrete
`SourceDescriptor` instances) and selected modules, filtered by
`module.supports & {scope.type}`.

## Consequences

**Positive**
- Adding a new source becomes a localized addition: one `provider.py`,
  one entry in `SourceType`, and a `supports` update on relevant modules.
- The existing Synapse code path can be preserved verbatim during the
  Phase-1 refactor (the migration is "move, don't rewrite").
- Estate-wide aggregation (Dashboard, Recommendations, PDF) becomes a
  natural extension rather than a retrofit.

**Negative**
- Phase 1 is pure refactor with zero user-visible benefit; carries the
  usual regression risk of any large internal restructure.
- Slight runtime overhead from registry dispatch (negligible).

**Neutral**
- Module `supports` is hardcoded in Python (`ModuleSpec`) rather than
  declared in YAML / a database. See ADR-0002 for credentials.

## Alternatives considered

- **Parallel `modules/adf/` package** mirroring `modules/pipelines/`:
  rejected because it scales linearly in maintenance cost per added source
  and produces forked copies of `fabric_compat`, `expression_compat`, etc.
- **One mega-collector that knows about all sources internally**:
  rejected because it concentrates change risk and violates the
  open-closed principle for new sources.
- **External plugin entry points (e.g. `entry_points = ...` in `pyproject.toml`)**:
  rejected for now (over-engineered for the current scale; we don't ship
  third-party plugins). May revisit when external authors want to add
  sources.
