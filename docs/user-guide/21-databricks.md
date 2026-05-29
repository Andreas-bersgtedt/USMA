# 21. Databricks sources

> **Phase 4 preview.** Databricks support is **alpha** in v0.7: the
> analyzer can crawl an Azure Databricks workspace via the
> `databricks_workflows` module and surface workflow-level Fabric
> compatibility + workspace-scoped consumption cost, but only a subset
> of analyzers is wired up. See the support matrix below.

> **Phase 4.7 (alpha): AWS Databricks** is supported via
> `SMA_DATABRICKS_PLATFORM=aws` — see
> [`21a-databricks-aws.md`](21a-databricks-aws.md) and
> [ADR-0005](../adr/0005-multi-cloud-databricks.md). The rest of this
> page describes the **Azure** path (the default when the
> discriminator is unset).

USMA (the Unified Solution Migration Analyzer — see ADR 0004) treats
"the thing you're migrating" as a generic **source scope**. A scope can
be a Synapse workspace, an ADF factory, a Databricks workspace, or
(eventually) an SAP BW system. Each scope is described by a
`SourceDescriptor`:

```python
SourceDescriptor(
    type=SourceType.DATABRICKS,
    id="/subscriptions/.../providers/Microsoft.Databricks/workspaces/dbx-prod",
    display_name="dbx-prod",
    subscription_id="...",
    resource_group="rg-data",
    extras={"workspace_url": "adb-1234567890.5.azuredatabricks.net"},
)
```

The Configuration page in the SPA exposes one tab per source type; CLI
users target a scope via the repeatable `--scope` flag on `analyze-all`
(see below).

## Quick start (CLI)

```powershell
# Single-scope run against one Databricks workspace.
sma analyze-all --scope databricks:dbx-prod@$env:AZ_SUB/rg-data

# Mixed run — one Synapse workspace + one ADF factory + one Databricks workspace.
sma analyze-all `
  --scope synapse_workspace:ws-eu-prod `
  --scope adf:adf-prod@$env:AZ_SUB/rg-data `
  --scope databricks:dbx-prod@$env:AZ_SUB/rg-data
```

The `--scope` flag accepts `<source_type>:<display_name>[@<sub>/<rg>]`.
When the `@<sub>/<rg>` suffix is omitted, the analyzer reuses the
subscription ID and resource group already in your `.env`.

> Today the **wave execution still pivots on the legacy single scope**
> from `.env` (`SYNAPSE_WORKSPACE_NAME` / `SYNAPSE_RESOURCE_GROUP`). The
> `--scope` flag persists the user's intent into the manifest so the
> SPA can render scope counts + source-type chips — true multi-scope
> dispatch lands in Phase 4.5. For a Databricks-only run today, point
> your `.env` at the workspace's subscription + RG (and set
> `SMA_SOURCE_TYPE=databricks` so the SPA's Configuration page picks
> the right radio), then add `--scope` so the manifest records the
> source type.

## Authentication

The analyzer uses the **same service principal as Synapse/ADF**: tenant
ID, client ID, and client secret in `.env`. The SP must have:

* **Reader** on the Databricks workspace (control plane, ARM) — required
  for `DatabricksProvider.validate()` / `.discover()` and for the
  Configuration page's auto-discover button to surface the workspace.
* **Reader** on the subscription if you want auto-discovery to list
  every workspace under the subscription.
* **Workspace-user** access in Databricks itself — needed so the
  `databricks_workflows` module can call the `/api/2.1/jobs/list` data
  plane to inventory jobs. Add the SP to the workspace via
  `Settings → Identity and access → Service principals`, then assign at
  least the `User` entitlement (or `Workflows access` if your workspace
  has SCIM-driven role separation).

No personal access tokens are required — the analyzer exchanges the
service principal credential for an AAD bearer token scoped to
`2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default` (the Databricks login
audience) using the existing `azure-identity` flow.

## Support matrix

| Module | Synapse | ADF | Databricks | Notes |
|---|---|---|---|---|
| `databricks_workflows` | n/a | n/a | ✅ | jobs, tasks, schedules, clusters, **SQL warehouses (inventory, query history, REST CPU-seconds, Unity Catalog `system.billing` DBU usage, per-warehouse Fabric F-SKU mapping)** — Phase 4-B / 4.7.8 / 5.1.1 |
| `pipelines` (static) | ✅ | ✅ | ⛔ | Databricks Workflows are surfaced by `databricks_workflows` instead |
| `pipelines` (run history) | ✅ | ⛔ | ⛔ | Databricks job run history is Phase 4.5 |
| `fabric_mapping` | ✅ | ✅ | ✅ | task-level compatibility rules (Notebook ✅, JAR ⚠, dbt ⚠, SQL ✅) — Phase 4-C |
| `cost` | ✅ | ✅ | ✅ | workspace-scoped Cost Management slice (resource kind = `Microsoft.Databricks/workspaces`) — Phase 4-C |
| `monitoring` | ✅ | ⛔ | ⛔ | requires Synapse data plane |
| `code_objects`, `serverless_pools`, `dedicated_pools`, `spark_pools` | ✅ | n/a | n/a | Synapse-only concepts |
| `storage`, `security`, `governance` | ✅ | ⛔ | ⛔ | per-source equivalents land alongside the run-history collector |

The active support map is exposed by `GET /api/modules` and by
`MODULE_SPECS[<module>].supports_source(SourceType.DATABRICKS)` in
code, so the Run-page checkbox matrix can grey out incompatible cells
automatically.

## What's collected from a Databricks workspace

The `DatabricksWorkflowsCollector` (see
`usma.modules.databricks_workflows.sources.databricks_collector`)
returns workflow-shaped data normalized to the analyzer's wire format:

* `iter_jobs_with_tasks()` — workflows and their task tree (Notebook /
  SQL / JAR / Python wheel / dbt / Run Job tasks), each with the cluster
  spec (`new_cluster` vs `existing_cluster_id`) and dependencies.
* `list_job_clusters()` — distinct cluster shapes referenced by jobs,
  for sizing + node-type Fabric mapping.
* `list_schedules()` — cron / continuous schedule per job.
* `list_sql_warehouses()` + `iter_query_history()` — SQL warehouse
  inventory and recent query history (paginated). Warehouses are
  enriched with REST-derived `total_cpu_seconds` and, when available,
  Unity Catalog `system.billing` DBU-hours; both feed the
  per-warehouse Fabric F-SKU mapping in
  `databricks_workflows.sql_warehouse_fabric_mappings` and roll up
  into the Dashboard's Recommended SKU stat card and the Estate
  Overview's `projected_fabric_cu` / `recommended_fabric_sku`.

Job **run history** (failure rate, average duration, DBU-hours per run)
is intentionally omitted for the alpha; the static analyzer emits a
`caveat` per Databricks workflow rather than silently producing
zero-cost rows.

## Configuration page (SPA)

Once `sma serve --with-api` is running, open the **Configuration** tab
and pick the **Databricks** radio. After saving tenant / client /
secret / subscription, click **Discover workspaces**. The dropdown
populates from `DatabricksProvider.discover()`; the `workspace_url`
("adb-XXXX.N.azuredatabricks.net") is shown alongside the workspace
name so you can confirm you're pointing at the right region. Pick one
and click **Save** again to lock it in. **Validate** runs the
control-plane probe (ARM GET on the workspace) and short-circuits the
Synapse SQL / Spark Livy gauntlet that doesn't apply to Databricks.

## Run page

When `source_type=databricks`, the Run page's module checklist limits
the selectable modules to:

* `databricks_workflows` — default on
* `fabric_mapping` — default on (consumes the workflows artefacts)
* `cost` — default on (requires the `[cost]` pip extra)

Synapse-only modules (`dedicated_pools`, `serverless_pools`,
`spark_pools`, `monitoring`, `storage`, `governance`, `security`) are
hidden so you can't accidentally schedule a wave that can never finish.

## Limits & known gaps

* **No Databricks run history yet.** Run-count / failure-rate /
  DBU-hours columns will be empty for Databricks workflows until the
  Phase 4.5 collector lands. The static analyzer surfaces a one-line
  caveat per workflow.
* **Unity Catalog inventory is not crawled.** Databricks tables /
  views / catalog ACLs are out of scope for v0.7; the equivalent of
  the Synapse `code_objects` rollup lands in Phase 4.6.
* **No DLT pipelines yet.** Delta Live Tables pipelines are listed
  via a separate REST surface and are deferred to Phase 4.5.
* **dbt / JAR tasks classified at the task level only.** Per-model
  (dbt) and per-class (JAR) analysis is on the Phase 5 roadmap.

## Roadmap

1. **Phase 4.5** — Databricks run-history + DLT pipelines + monitoring slot.
2. **Phase 4.6** — Unity Catalog inventory (catalogs, schemas, tables,
   external locations) → code-object surface for Databricks.
3. **Phase 5** — SAP BW scope; same `SourceDescriptor` shape.

See also: [99-adding-a-source.md](./99-adding-a-source.md) for the
developer guide to adding a new source type, and ADR 0001-0004 for the
architectural decisions behind the unified source model.
