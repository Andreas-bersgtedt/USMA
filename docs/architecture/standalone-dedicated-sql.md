# Standalone Dedicated SQL pool (formerly SQL DW) — design + plan

> **Branch:** `feature/standalone-dedicated-sql-dw`
> **Status:** Slices A–G shipped. Live customer execution confirmed against `Microsoft.Sql/servers/demodata001/databases/testdedicatedpool` (DW900, North Europe) — dedicated_pools, monitoring (DWU utilization) and storage all populate end-to-end; Fabric mapping consumes the artefacts unchanged.
> **Related:** [ADR-0009](../adr/0009-standalone-dedicated-sql.md)

## Problem

`sma analyze-dedicated-pools` only knows how to enumerate dedicated SQL
pools that live **inside a Synapse workspace**:

```python
SynapseManagementClient.sql_pools.list_by_workspace(rg, ws_name)
```

A real-world subset of customers still runs the **classic standalone
Azure SQL Data Warehouse** — now branded *Dedicated SQL pool (formerly
SQL DW)* — which is provisioned as a `Microsoft.Sql/servers/<server>/
databases/<db>` resource with `edition = "DataWarehouse"`. There is
**no parent Synapse workspace**, so the existing ARM list call returns:

```
(ParentResourceNotFound) Failed to perform 'read' on resource(s) of type
'workspaces/sqlPools', because the parent resource
'/.../Microsoft.Synapse/workspaces/<ws>' could not be found.
```

(see the original report on this branch).

## Resource-shape comparison

| Aspect | Synapse workspace pool | Standalone (formerly SQL DW) |
|---|---|---|
| ARM provider | `Microsoft.Synapse/workspaces/<ws>/sqlPools/<pool>` | `Microsoft.Sql/servers/<server>/databases/<db>` |
| SDK list call | `sql_pools.list_by_workspace(rg, ws)` | `databases.list_by_server(rg, server)` + filter `sku.tier == "DataWarehouse"` |
| Endpoint FQDN | `<ws>.sql.azuresynapse.net` | `<server>.database.windows.net` |
| AAD token audience | `https://database.windows.net/.default` | `https://database.windows.net/.default` (same) |
| Engine / DMVs | MPP "SQL DW" code | MPP "SQL DW" code (identical) |
| DWU SKU values | `DW100c`, `DW200c`, … `DW30000c` | Same family |
| Status field | `Online`, `Paused`, `Scaling`, … | `Online`, `Paused`, `Scaling`, … |

> **Implication:** the entire data-plane analyzer
> (`DedicatedPoolSqlClient`, all 13 collectors, the distribution
> advisor, the T-SQL surface-gap rollup) works **unchanged** because
> the underlying engine and DMVs are identical. Only ARM discovery
> and the endpoint URL differ.

## Design

A **new sibling source type** `SYNAPSE_DEDICATED_SQL` with its own
provider and ARM client. The `dedicated_pools` analyzer becomes
ARM-client-agnostic so it can serve both source types without
duplicating any data-plane code.

```
SourceType.SYNAPSE_DEDICATED_SQL = "synapse_dedicated_sql"

sources/synapse_dedicated_sql/
    __init__.py            # register_provider
    provider.py            # SynapseDedicatedSqlProvider

modules/dedicated_pools/
    arm_client.py          # SynapseArmClient (existing — workspace pools)
    sql_server_arm_client.py   # NEW — SqlServerArmClient (standalone DWU dbs)
    analyzer.py            # DedicatedPoolsAnalyzer now takes an injected ARM client
```

### Scope identity

Each `SourceDescriptor` represents **one SQL server**. A server may
host zero, one or many DWU databases (mixed with regular Azure SQL
databases that the ARM client skips). The descriptor:

```python
SourceDescriptor(
    type=SourceType.SYNAPSE_DEDICATED_SQL,
    id="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Sql/servers/<server>",
    display_name="<server>",
    subscription_id="<sub>",
    resource_group="<rg>",
    extras={"sql_server_fqdn": "<server>.database.windows.net"},
)
```

### Env-var wiring

Following the ADF / Databricks-Azure precedent of reusing the legacy
field names (so users only flip `SMA_SOURCE_TYPE`):

| Env var | Meaning |
|---|---|
| `SMA_SOURCE_TYPE=synapse_dedicated_sql` | Selects the new source |
| `SYNAPSE_RESOURCE_GROUP` | Resource group hosting the SQL server |
| `SYNAPSE_WORKSPACE_NAME` | **SQL server name** (e.g. `devlebdatalakesql`) |
| `SYNAPSE_DEDICATED_POOL` (optional) | Filter to a single database name |

### Provider responsibilities

| Method | Behaviour |
|---|---|
| `discover()` | `SqlManagementClient.servers.list()` → one descriptor per server. (DWU filtering happens at analyzer time.) |
| `validate()` | `servers.get()` for ARM Reader; `databases.list_by_server()` to confirm at least one `edition='DataWarehouse'` exists; AAD SQL-token probe. |
| `make_clients()` | Returns a small bundle with the credential + server FQDN. |

### Analyzer refactor (non-breaking)

```python
class DedicatedPoolsAnalyzer:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        progress: ProgressReporter | None = None,
        arm_client: DedicatedPoolArmClient | None = None,   # NEW
    ) -> None:
        self._cfg = cfg
        self._arm = arm_client or SynapseArmClient(cfg.azure)
        ...
```

A small `DedicatedPoolArmClient` protocol with two methods
(`list_dedicated_pools()` and `endpoint(pool_name)`) lets the CLI pick
the right concrete implementation based on the configured scope type.
Existing callers that don't pass `arm_client` keep working exactly as
before.

### CLI

```bash
sma --env-file .env analyze-dedicated-pools
sma --scope synapse_dedicated_sql:devlebdatalakesql@<sub>/dev_datalake_rg_sql analyze-dedicated-pools
```

The CLI command inspects `cfg.primary_scope().type` and instantiates
the matching ARM client. No changes are needed at the run-plan layer
because `MODULE_SPECS["dedicated_pools"].supports` is widened to
include both source types.

### Doctor

`_is_synapse_or_adf` (used to gate the SQL-token check) is widened to
include `SYNAPSE_DEDICATED_SQL` so `sma doctor` still validates the
SQL access token for standalone scopes.

## Slice plan

| Slice | Scope | Status |
|---|---|---|
| **A — Backend foundations** | New `SourceType`, provider, ARM client, analyzer refactor, config + CLI wiring, doctor predicate, unit tests. **No SPA, no docs beyond this file + ADR.** | ✅ done |
| B — Web SPA Configuration | Surface the new source type in the Configuration page (radio entry + server-name field), live validation via the new provider, `web/config_io.py` read/write/validate, `/api/config/discover-sql-servers` endpoint, estate-card label. | ✅ done |
| C — Documentation & telemetry | New `docs/user-guide/24-standalone-dedicated-sql.md`, update `access_manifest.py`, `QUICKSTART.md`, `CHANGELOG.md`. | ✅ done |
| D — Estate overview + Fabric mapping | Verify the standalone descriptor surfaces correctly in the Estate Overview tile and that the existing `fabric_mapping` rules consume the dedicated-pool artifact unchanged. | ✅ done |
| E — End-to-end smoke against a real customer scope | Validate against the dev tenant that triggered the report; document runbook gotchas. | ✅ done |
| **F — Monitoring (DWU utilization)** | `MonitorClient.list_standalone_dwu_resource_ids()` enumerates DWU-tier databases under `Microsoft.Sql/servers`. `STANDALONE_DWU_POOL_METRICS` requests the snake_case names exposed by `Microsoft.Sql/servers/databases` (`dwu_consumption_percent`, `dwu_limit`, `cpu_percent`, …) and `STANDALONE_TO_WORKSPACE_METRIC` rewrites them to the workspace PascalCase set so `fabric_mapping.cu_projection` derives the Fabric SKU recommendation from real utilization. `MonitoringAnalyzer.run()` dispatches by `cfg.primary_scope().type`. SPA Run page exposes the **Monitoring** checkbox for standalone scopes. | ✅ done |
| **G — Storage (Dashboard Storage card)** | `StorageAnalyzer` is scope-aware: workspace ADLS / blob inventory is skipped entirely for standalone scopes (no parent workspace) and `run()` short-circuits to the per-pool DMV path. Pool discovery flows through the shared `DedicatedPoolArmClient` Protocol via `_select_arm_client(cfg)` so `SqlServerArmClient.list_dedicated_pools()` + `sql_endpoint()` are picked automatically. SPA Run page exposes the **Storage** checkbox for standalone scopes. | ✅ done |
| **H — SQL Surface empty-state fix** | SPA `<CodeObjects>` page no longer short-circuits to the empty state when `code_objects` is empty but `top_queries` / `top_consumed_objects` / `workload_capture_stats` carry data (typical raw-staging DWU shape). | ✅ done |

## Out of scope

* **On-prem SQL Server / SSIS** — that is still the planned Phase 6+
  `SourceType.SQL_SERVER` work. Standalone DWU is an Azure-hosted MPP
  engine; SQL Server / SSIS is SMP + a completely different SDK
  (`pyodbc` to a self-managed instance + SSIS XML parsing).
* **Cost attribution for standalone DWU** — the cost module's resource-id
  classifier today expects either `Microsoft.Synapse/workspaces/.../
  sqlPools` or `Microsoft.DataFactory/factories/...`. Adding the
  `Microsoft.Sql/servers/.../databases` namespace is straightforward
  and is still tracked as a follow-up; standalone DWU usage lands in
  the `other` bucket until then.
* **Auto-pause / scale-action history** — not currently collected for
  workspace pools either; tracked separately.
