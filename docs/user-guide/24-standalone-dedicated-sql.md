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
  --scope synapse_dedicated_sql:examplesqlserver@$env:AZ_SUB/rg-example-sql
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
SYNAPSE_RESOURCE_GROUP=rg-example-sql      # the SQL server's RG
SYNAPSE_WORKSPACE_NAME=examplesqlserver        # the SQL server name
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
| `monitoring` | ✅ | ✅ | DWU capacity metrics pulled from `Microsoft.Sql/servers/databases` (snake_case names normalised to the workspace PascalCase set) so `fabric_mapping.cu_projection` derives a Fabric SKU recommendation from real DWU utilization |
| `storage` | ✅ | ✅ | per-pool reserved / data / index / unused space via DMVs — exactly what the SPA Dashboard Storage card needs; workspace ADLS / blob inventory is skipped (no parent workspace) |
| `security`, `governance` | ✅ | ⛔ | Synapse-workspace concepts; the standalone topology has no equivalent |
| `cost` | ✅ | ⛔ | cost resource-id classifier still gated to the Synapse namespace; tracked separately |
| `fabric_mapping` | ✅ | ✅ | consumes `dedicated_pools.json` + `monitoring.json` unchanged |

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
- **Auto-pause / scale-action history.** Not currently collected for
  workspace pools either; tracked as a generic dedicated-pools gap.
- **Estate Overview cloud chip.** Standalone DWU shows the generic
  "Dedicated SQL" label in the type column; the per-cloud Azure chip
  works unchanged.
- **SQL Surface page when the DB has no code objects.** If the pool
  contains zero stored procedures / views / functions but plenty of
  query history (typical of raw-staging DWUs), the SPA SQL Surface
  page still renders the **Top Queries**, **Top Consumed Objects**
  and **Workload Capture Stats** sections from `top_queries` /
  `top_consumed_objects` / `workload_capture_stats` in
  `dedicated_pools.json` — the page no longer short-circuits to the
  empty state just because `code_objects` is empty.

## End-to-end smoke runbook

Use this checklist the first time you point USMA at a customer's
standalone Dedicated SQL pool server. It is the same flow Slice E was
designed to validate; treat it as the production-readiness gate for
that scope.

### Pre-flight (one-time per server)

1. **Service-principal RBAC.** On the
   `Microsoft.Sql/servers/<server>` resource (NOT the resource group
   alone), grant the SP **Reader** so
   `SqlManagementClient.servers.get` and `databases.list_by_server`
   succeed. Subscription Reader is only needed if you want
   auto-discovery to surface the server in the dropdown.
2. **AAD admin on the SQL server.** The SP can only run
   `CREATE USER … FROM EXTERNAL PROVIDER` against a database when a
   server-level AAD admin already exists. If the customer's server
   has none, set one (e.g. their DBA group) — this is a one-time
   server-property change in the portal / az CLI.
3. **Per-database grants.** Sign in to **each DWU database** (master
   does not need grants) as the AAD admin and run:
   ```sql
   CREATE USER [<sp-display-name>] FROM EXTERNAL PROVIDER;
   EXEC sp_addrolemember 'db_datareader', '<sp-display-name>';
   GRANT VIEW DATABASE STATE TO [<sp-display-name>];
   GRANT VIEW DEFINITION TO [<sp-display-name>];
   ```
   Skipping `VIEW DEFINITION` will silently drop stored procedures /
   functions from the `code_objects` collector (you will see views
   only).
4. **Networking.** The SP must be able to reach
   `<server>.database.windows.net` on TCP/1433. If the server has a
   firewall, add the analyzer host's outbound IP, or enable "Allow
   Azure services and resources to access this server" for a
   service-principal-only run.
5. **ODBC.** Run `sma doctor --offline` first to verify ODBC Driver
   18 is present; the standalone topology uses the same pyodbc path
   as workspace pools.

### Live smoke (CLI, single scope)

```powershell
# .env — minimum keys for a standalone DWU scope
$env:SMA_SOURCE_TYPE = "synapse_dedicated_sql"
$env:AZURE_TENANT_ID = "..."
$env:AZURE_CLIENT_ID = "..."
$env:AZURE_CLIENT_SECRET = "..."
$env:AZURE_SUBSCRIPTION_ID = "..."
$env:SYNAPSE_RESOURCE_GROUP = "<sql-server-rg>"
$env:SYNAPSE_WORKSPACE_NAME = "<sql-server-name>"
# Optional: narrow to one DWU database (omit to scan all)
# $env:SYNAPSE_DEDICATED_POOL = "<database-name>"

# 1. Doctor — confirms ARM token + SQL token both mint cleanly.
sma doctor

# 2. Dry inventory — fast ARM-only pass to prove discovery works.
sma analyze-dedicated-pools -f json

# 3. Full run — DMVs + analyzer rules + Fabric mapping.
sma analyze-all
```

Expected artefacts under `./output/`:

- `dedicated_pools.json` — one entry per DWU database; paused
  databases listed with `status: "Paused"` and an `errors` row
  explaining DMV collection was skipped.
- `dedicated_pools.md` / `.html` — same content rendered.
- `fabric_mapping.json` — recommendations consume
  `dedicated_pools.json` unchanged (Slice D test pins this).
- `run_manifest.json` — the scope row should show
  `source_type: "synapse_dedicated_sql"` and an `id` of the form
  `/subscriptions/.../providers/Microsoft.Sql/servers/<server>`.

### Live smoke (SPA, single scope)

1. Configuration → pick **Dedicated SQL pool** → fill SP fields.
2. **Discover SQL servers** — dropdown should populate with every
   `Microsoft.Sql/servers` the SP can read.
3. **Validate (live)** — three green ConfigCheck rows expected
   (`arm.servers.get`, `sql.token.mint`,
   `arm.databases.list_by_server` filtered to DWU).
4. Run page → pick the `dedicated_pools` + `fabric_mapping` modules
   → **Start run**.
5. Once finished, **Estate Overview** should show a new tile with
   the cloud chip `azure` and the type label `Dedicated SQL pool
   (formerly SQL DW)`.

### Verification checklist

| # | Check | How to verify |
|---|---|---|
| 1 | Discovery returned at least one server | SPA dropdown is non-empty; CLI run prints `Found N SQL server(s)` |
| 2 | At least one DWU database was found | `dedicated_pools.json` has ≥ 1 pool entry; non-DWU databases are filtered out by `sku.tier == "DataWarehouse"` |
| 3 | DMV collection ran | `pools[i].tables` / `code_objects` non-empty for any `Online` database |
| 4 | Paused databases handled gracefully | `pools[i].status == "Paused"` carries a non-fatal caveat in `errors`, not a top-level run failure |
| 5 | Fabric mapping consumed the artefact | `fabric_mapping.json` exists; `recommendations` non-empty for any non-trivial pool |
| 6 | Estate Overview cloud bucket is `azure` | `/api/estate` JSON includes `cloud: "azure"` for the new workspace key |
| 7 | Run manifest stamps the new source type | `run.json` → `scopes[0].source_type == "synapse_dedicated_sql"` |
| 8 | Cost module behaves predictably | `cost.json` lands in `by_resource_kind.other` (known limit; not a failure) |

### Gotchas learned during development

- **`master` database leaks in if you bypass the filter.** The ARM
  client must keep its `sku.tier == "DataWarehouse"` guard — `master`
  is reported with `sku.tier == "System"` and a `null` capacity,
  which would crash the DWU column. Don't widen the filter.
- **SKU tier capitalisation.** The Azure REST API returns
  `"DataWarehouse"` (PascalCase) for DWU databases. Don't lowercase
  before comparing.
- **`SYNAPSE_DEDICATED_POOL` is a database-name filter, not a pool
  name.** For the standalone topology it scopes the DMV pass to a
  single database; for workspace pools it scopes to a single pool
  name. Same key, two slightly different meanings — documented in
  the env-vars sample above to avoid surprise.
- **Custom AAD audience is shared with Synapse.** The SQL token
  uses `https://database.windows.net/.default` for both topologies.
  No new app-registration permission is needed; the existing
  Synapse SP credential works as-is.
- **Standalone DWU servers don't expose a `dev.azuresynapse.net`
  data plane.** Anything that lives behind Synapse Studio (Spark
  pools, pipelines, serverless SQL, lake databases) simply does not
  exist on a standalone DWU server. The Configuration page hides
  those modules behind the source-type predicate; the CLI's
  `analyze-all` skips them via `MODULE_SPECS[*].supports_source`.
- **Firewall / private-endpoint surprises.** Standalone DWU servers
  more commonly have customer-managed firewalls than Synapse
  workspaces. The DMV pass fails fast with a clear
  `pyodbc.OperationalError` if the analyzer host's egress IP is not
  whitelisted — surface that gotcha early in the runbook.
- **AAD admin gap is the #1 setup failure mode.** Without a
  server-level AAD admin, the `CREATE USER … FROM EXTERNAL
  PROVIDER` step in pre-flight #3 fails with a misleading
  permissions error; verify in the portal before reaching for
  RBAC.
