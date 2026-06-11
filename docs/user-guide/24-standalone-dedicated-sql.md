# 24. Standalone Dedicated SQL pool (formerly SQL DW)

> **Phase 6 (alpha).** Standalone *Dedicated SQL pool (formerly SQL DW)*
> support landed alongside Synapse-workspace pools in v5.4 — see
> [ADR-0009](../adr/0009-standalone-dedicated-sql.md) and
> [docs/architecture/standalone-dedicated-sql.md](../architecture/standalone-dedicated-sql.md).
> The data-plane code (DMVs, collectors, distribution advisor, T-SQL
> gap rollup) is identical to the workspace-pool path — only ARM
> discovery and the endpoint URL differ, so all `dedicated_pools`
> outputs and analyzer rules apply unchanged.

USMA treats the standalone topology as a **sibling source type**
(`synapse_dedicated_sql`) so the Configuration page, CLI, and run
manifest can keep workspace-attached pools and standalone DWU servers
clearly distinguished.

| Aspect | Synapse workspace pool | Standalone (formerly SQL DW) |
|---|---|---|
| ARM provider | `Microsoft.Synapse/workspaces/<ws>/sqlPools/<pool>` | `Microsoft.Sql/servers/<server>/databases/<db>` |
| SDK list call | `sql_pools.list_by_workspace(rg, ws)` | `databases.list_by_server(rg, server)` filtered to `sku.tier == "DataWarehouse"` |
| Endpoint FQDN | `<ws>.sql.azuresynapse.net` | `<server>.database.windows.net` |
| AAD token audience | `https://database.windows.net/.default` | `https://database.windows.net/.default` (same) |
| MPP engine / DMVs | SQL DW | SQL DW (identical) |
| Source type | `synapse_workspace` | `synapse_dedicated_sql` |

## When to pick this source type

Choose **Dedicated SQL pool** rather than **Synapse workspace** when
the pool you want to inventory is provisioned directly under
`Microsoft.Sql/servers/<server>` and has **no parent Synapse
workspace**. The symptom is that `sma analyze-dedicated-pools` against
a Synapse-workspace scope fails with:

```text
(ParentResourceNotFound) Failed to perform 'read' on resource(s) of type
'workspaces/sqlPools', because the parent resource
'/.../Microsoft.Synapse/workspaces/<ws>' could not be found.
```

If you have *both* topologies in the same estate, configure each as its
own scope (one source type per scope) — they will run independently and
each contribute a row to the Estate Overview.

## Quick start (CLI)

```powershell
# Single-scope run against one standalone SQL server (uses .env)
$env:SMA_SOURCE_TYPE = "synapse_dedicated_sql"
sma analyze-dedicated-pools

# Or via --scope (overrides .env), filtered to one database
sma analyze-dedicated-pools `
  --scope synapse_dedicated_sql:devlebdatalakesql@$env:AZ_SUB/dev_datalake_rg_sql
```

The `--scope` flag accepts `<source_type>:<display_name>[@<sub>/<rg>]`.
The display name is the **SQL server name**.

## Quick start (web SPA)

1. Open the Configuration page.
2. Under **Source type**, pick **Dedicated SQL pool**.
3. Fill in Tenant / Client ID / Client Secret / Subscription as for any
   other Azure source.
4. Click **Discover SQL servers** — the dropdown lists every
   `Microsoft.Sql/servers` resource the SP can read in the subscription.
5. Pick the server. The optional **Dedicated SQL pool / database**
   field narrows the scan to a single database; leave it blank to scan
   every DWU database on the server.
6. Click **Validate (live)** to confirm both planes work, then start a
   run from the Run page.

## Authentication

Same service principal as Synapse: tenant ID, client ID, client secret
in `.env`. The SP needs:

- **Reader** on the SQL server resource (control plane) — for
  `servers.get` and `databases.list_by_server`.
- **Subscription Reader** — only if you want auto-discovery to surface
  every SQL server in the subscription. Not required when you already
  know the resource group + server name.
- A **`CREATE USER … FROM EXTERNAL PROVIDER`** + `db_datareader` +
  `VIEW DATABASE STATE` + `VIEW DEFINITION` grant on each DWU database
  (data plane) — identical to the Synapse-workspace setup.

The AAD token audience for the SQL data plane is
`https://database.windows.net/.default`, so a single token mints fine
for both topologies; no extra app-registration permissions are
required.

## Configuration mapping

The standalone topology reuses the same `.env` keys as Synapse — the
SPA just reinterprets them based on `SMA_SOURCE_TYPE`:

```ini
SMA_SOURCE_TYPE=synapse_dedicated_sql
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_SUBSCRIPTION_ID=...
SYNAPSE_RESOURCE_GROUP=dev_datalake_rg_sql      # the SQL server's RG
SYNAPSE_WORKSPACE_NAME=devlebdatalakesql        # the SQL server name
SYNAPSE_DEDICATED_POOL=                          # leave blank for all DWU dbs; set to one db to filter
SMA_OUTPUT_DIR=./output
```

## Support matrix

| Module | Synapse workspace | Standalone DWU | Notes |
|---|---|---|---|
| `dedicated_pools` | ✅ | ✅ | identical engine — every collector and analyzer rule applies unchanged |
| `serverless_pools` | ✅ | n/a | the standalone topology has no built-in serverless endpoint |
| `spark_pools` | ✅ | n/a | Spark lives under the Synapse workspace |
| `pipelines` | ✅ | n/a | pipelines live under the Synapse workspace or ADF |
| `monitoring` | ✅ | ⛔ | DWU capacity metrics for the standalone topology land in a follow-up slice |
| `storage`, `security`, `governance` | ✅ | ⛔ | Synapse-workspace concepts; the standalone topology has no equivalent |
| `cost` | ✅ | ⛔ | cost resource-id classifier still gated to the Synapse namespace; tracked separately |
| `fabric_mapping` | ✅ | ✅ | consumes `dedicated_pools.json` unchanged |

The active support map is exposed by `MODULE_SPECS[<module>].supports`
in code; `MODULE_SPECS["dedicated_pools"].supports` covers both
`SourceType.SYNAPSE_WORKSPACE` and `SourceType.SYNAPSE_DEDICATED_SQL`
out of the box.

## What's collected from a standalone DWU server

The `SqlServerArmClient` (see
[src/usma/modules/dedicated_pools/sql_server_arm_client.py](../../src/usma/modules/dedicated_pools/sql_server_arm_client.py))
returns the same `PoolInventory` shape as the Synapse-workspace
client:

- One row per database with `edition == "DataWarehouse"` (master and
  non-DWU databases are filtered out).
- SKU name + capacity (e.g. `DW1000c`, `DW500c`) drive the DWU column.
- `status` (`Online`, `Paused`, `Scaling`, …) gates whether the DMV
  pass runs; paused databases are surfaced with a caveat instead of an
  empty SQL section.
- All downstream DMV collectors run against
  `<server>.database.windows.net` as if it were any other DWU pool —
  no analyzer code branches on the source type.

## Limits & known gaps (alpha)

- **Cost attribution is gated.** The cost module's resource-id
  classifier only recognises `Microsoft.Synapse/workspaces/.../sqlPools`
  today; standalone DWU rows show up as `other`. A widening to
  `Microsoft.Sql/servers/.../databases` (filtered by DWU edition) is
  the obvious follow-up.
- **No DWU capacity metric collection.** `monitoring` queries metrics
  on the Synapse-workspace resource id; the standalone equivalent
  (`Microsoft.Sql/servers/.../databases`) needs a separate metric
  client wiring.
- **Auto-pause / scale-action history.** Not currently collected for
  workspace pools either; tracked as a generic dedicated-pools gap.
- **Estate Overview cloud chip.** Standalone DWU shows the generic
  "Dedicated SQL" label in the type column; the per-cloud Azure chip
  works unchanged.
