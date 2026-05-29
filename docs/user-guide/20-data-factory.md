# 20. Data Factory (ADF) sources

> **Phase 2 preview.** ADF support is **alpha** in v0.6: the analyzer
> can crawl an Azure Data Factory (ADF) factory through the same
> pipelines module that today handles Synapse workspaces, but only a
> subset of analyzers is wired up. See the support matrix below.

USMA (the Unified Solution Migration Analyzer — see ADR 0004) treats
"the thing you're migrating" as a generic **source scope**. A scope can
be a Synapse workspace, an ADF factory, a Databricks workspace, or
(eventually) an SAP BW system. Each scope is described by a
`SourceDescriptor`:

```python
SourceDescriptor(
    type=SourceType.ADF,
    id="/subscriptions/.../providers/Microsoft.DataFactory/factories/adf-prod",
    display_name="adf-prod",
    subscription_id="...",
    resource_group="rg-data",
)
```

The Configuration page in the SPA exposes one tab per source type; CLI
users target a scope via the repeatable `--scope` flag on `analyze-all`
(see below).

## Quick start (CLI)

```powershell
# Single-scope run that points at one ADF factory.
sma analyze-all --scope adf:adf-prod@$env:AZ_SUB/rg-data

# Mixed run — one Synapse workspace + one ADF factory.
sma analyze-all `
  --scope synapse_workspace:ws-eu-prod `
  --scope adf:adf-prod@$env:AZ_SUB/rg-data
```

The `--scope` flag accepts `<source_type>:<display_name>[@<sub>/<rg>]`.
When the `@<sub>/<rg>` suffix is omitted, the analyzer reuses the
subscription ID and resource group already in your `.env`.

> Today the **wave execution still pivots on the legacy single scope**
> from `.env` (`SYNAPSE_WORKSPACE_NAME` / `SYNAPSE_RESOURCE_GROUP`). The
> `--scope` flag persists the user's intent into the manifest so the
> SPA can render scope counts + source-type chips — true multi-scope
> dispatch lands in Phase 2.5. For an ADF-only run today, point your
> `.env` at the factory's subscription + RG, then add `--scope` so the
> manifest records the source type.

## Authentication

The analyzer uses the same service principal as Synapse: tenant ID,
client ID, and client secret in `.env`. The SP must have **Reader** on
the ADF factory (control plane) and on the subscription if you want
auto-discovery to surface the factory. No data-plane roles are needed
for the alpha analyzer set — ADF activities are read through the ARM
management API, not the run-history data plane.

## Support matrix

| Module | Synapse workspace | ADF factory | Notes |
|---|---|---|---|
| `pipelines` (static analysis) | ✅ | ✅ | activities, linked services, datasets, triggers, IRs |
| `pipelines` (run history) | ✅ | ⛔ | ADF run history collector is Phase 2.5 |
| `fabric_mapping` | ✅ | ✅ | activity-level compatibility rules apply unchanged |
| `cost` | ✅ | ⛔ | ADF cost ingestion (per-activity DIU + run minutes) is Phase 2.5 |
| `monitoring` | ✅ | ⛔ | requires Synapse data plane |
| `code_objects`, `serverless_pools`, `dedicated_pools`, `spark_pools` | ✅ | n/a | Synapse-only concepts |
| `storage`, `security`, `governance` | ✅ | ⛔ | ADF equivalents land alongside the run-history collector |

The active support map is exposed by `GET /api/modules` and by
`MODULE_SPECS[<module>].supports_source(SourceType.ADF)` in code, so
the Run-page checkbox matrix can grey out incompatible cells
automatically.

## What's collected from an ADF factory

The `AdfCollector` (see `usma.modules.pipelines.sources.adf_collector`)
returns the same shapes as the Synapse collector:

* `iter_pipelines_with_activities()` — pipelines and their activity
  tree, normalized to the analyzer's wire format.
* `list_linked_services()`, `list_datasets()`, `list_triggers()`,
  `list_integration_runtimes()` — the rest of the factory inventory.

Run history (`iter_pipeline_run_summaries`,
`iter_activity_run_metrics`) is intentionally omitted for the alpha;
the static analyzer emits a `caveat` per ADF pipeline rather than
silently producing zero-cost rows.

## Limits & known gaps

* **No ADF run history yet.** Run-count / failure-rate / DIU-hours
  columns will be empty for ADF pipelines until the Phase 2.5 collector
  lands.
* **Self-hosted IRs are surfaced read-only.** The fabric mapping module
  flags self-hosted IRs as `partial` (Fabric uses Data Gateways
  instead), but does not yet emit a runbook step for the migration.
* **Mapping data flows count as one activity.** Activity-level rules
  apply; per-transformation analysis is on the Phase 3 roadmap.

## Roadmap

1. **Phase 2.5** — ADF run-history collector + cost ingestion + monitoring slot.
2. **Phase 3** — Databricks scope, then SAP BW scope; same `SourceDescriptor` shape.
3. **Phase 3** — per-scope checkbox matrix on the Run page, fully driven by `MODULE_SPECS.supports_source(...)`.

See also: [99-adding-a-source.md](./99-adding-a-source.md) for the
developer guide to adding a new source type, and ADR 0001-0004 for the
architectural decisions behind the unified source model.
