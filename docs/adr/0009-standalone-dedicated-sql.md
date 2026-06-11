# ADR-0009: Standalone Dedicated SQL pool (formerly SQL DW) as a sibling source type

* **Status:** Accepted (Phase 6.A — branch `feature/standalone-dedicated-sql-dw`)
* **Date:** 2026-06
* **Supersedes:** —
* **Superseded-by:** —

## Context

`SourceType.SYNAPSE_WORKSPACE` and its provider hard-code the
"Synapse workspace owns its dedicated SQL pools" topology, i.e.
`Microsoft.Synapse/workspaces/<ws>/sqlPools/<pool>`. Customers running
the classic standalone **Dedicated SQL pool (formerly SQL DW)**
provision a `Microsoft.Sql/servers/<server>/databases/<db>` resource
instead — there is no parent workspace. Today's analyzer fails with
`ParentResourceNotFound` against any such estate.

The data-plane (DMVs, T-SQL surface, distribution semantics) is
identical between the two — both run the MPP "SQL DW" engine. Only
ARM discovery and the endpoint URL differ.

## Decision

Introduce **`SourceType.SYNAPSE_DEDICATED_SQL`** as a sibling of
`SYNAPSE_WORKSPACE`. A new provider (`SynapseDedicatedSqlProvider`)
enumerates `Microsoft.Sql/servers/...` and filters databases by
`sku.tier == "DataWarehouse"`. A new ARM client
(`SqlServerArmClient`) replaces the workspace-bound
`SynapseArmClient` inside `DedicatedPoolsAnalyzer` whenever the
configured scope is the new type.

The analyzer module (`modules/dedicated_pools/`) keeps its
collectors, distribution advisor, T-SQL surface-gap rollup and
reporting code **unchanged** — they all talk to the SQL endpoint, not
to ARM. The `DedicatedPoolsAnalyzer.__init__` signature gains an
optional `arm_client` parameter so the existing constructor calls
(`DedicatedPoolsAnalyzer(cfg)`) keep working under the legacy
workspace topology.

`MODULE_SPECS["dedicated_pools"].supports` is widened to include both
source types. Other modules (`pipelines`, `monitoring`, `storage`,
`security`, `governance`, `fabric_validation`, `serverless_pools`,
`spark_pools`) stay scoped to `SYNAPSE_WORKSPACE` — they have no
analogue under a standalone SQL server. `cost` and `fabric_mapping`
are deferred to Slice D and tracked in the planning doc.

## Why not extend `SYNAPSE_WORKSPACE` with a discriminator?

Considered, rejected:

* The ARM provider namespace is fundamentally different
  (`Microsoft.Synapse` vs `Microsoft.Sql`), so the SDK clients,
  resource-ids and validation calls all branch on the discriminator.
  That defeats the simplicity argument of Option B.
* Every consumer that resolves a descriptor today calls
  `get_provider(SourceType.SYNAPSE_WORKSPACE)` and expects a
  `SynapseManagementClient`. Smuggling a non-workspace descriptor
  through that path would surface as confusing `AttributeError`s
  downstream.
* Following the established pattern set by ADR-0001 (one source type
  per ARM provider namespace), a sibling is the natural fit.

## Why not the existing `SourceType.SQL_SERVER` placeholder?

`SQL_SERVER` was reserved for on-prem SQL Server / SSIS (Phase 6+)
which has its own discovery story (no ARM, `pyodbc` against a
self-managed instance, SSIS XML packages). The standalone SQL DW is
fundamentally an Azure-hosted MPP product; sharing the name would
mislead users and force the future on-prem provider to fight for the
same enum slot.

## Consequences

### Positive

* Zero changes to the data-plane analyzer code path — 13 collectors,
  the distribution advisor, the T-SQL gap rollup and the SQL client
  are reused 1:1.
* Existing manifests and artifacts are untouched (the new source type
  produces new per-scope subdirectories under the standard
  `<source_type>__<short_scope_id>__<module>.json` scheme).
* The fix is a clean addition: no behaviour change for current
  `SYNAPSE_WORKSPACE` customers.

### Negative

* A second ARM-list path means a second `validate()` matrix entry for
  the doctor + SPA Configuration page. The web tab is deferred to
  Slice B but tracked.
* The `dedicated_pool` env-var name now serves a double duty (filter
  by SQL-pool name vs filter by SQL database name). The behaviour is
  identical from the user's perspective; only the underlying call
  path differs.

## References

* Planning doc — [`docs/architecture/standalone-dedicated-sql.md`](../architecture/standalone-dedicated-sql.md)
* Provider — `src/usma/sources/synapse_dedicated_sql/provider.py`
* ARM client — `src/usma/modules/dedicated_pools/sql_server_arm_client.py`
