# 23. Snowflake sources

> **Phase 7 preview.** Snowflake support is **alpha** in v0.9: the
> analyzer crawls a Snowflake account via the `snowflake_workloads`
> module and now also runs `cost` against
> `SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY` +
> `USAGE_IN_CURRENCY_DAILY` (Slice 7-H). The SPA Dashboard section
> lands in Slice 7-I; multi-cloud parity smoke-testing in Slice 7-J.

USMA (the Unified Solution Migration Analyzer — see ADR 0004) treats
"the thing you're migrating" as a generic **source scope**. For
Snowflake the scope unit is the **account** — the same locator that
the OAuth integration is bound to. One USMA scope ↔ one Snowflake
account. The scope is described by a `SourceDescriptor`:

```python
SourceDescriptor(
    type=SourceType.SNOWFLAKE,
    id="acme-prod",                   # Snowflake account locator
    display_name="acme-prod",
    subscription_id=None,             # n/a — account id is the scope key
    resource_group=None,
    extras={"platform": "azure",      # aws | azure | gcp, from CURRENT_REGION()
            "region": "AZURE_WESTEUROPE"},
)
```

The Configuration page in the SPA exposes one tab per source type; CLI
users target a scope via the repeatable `--scope` flag on `analyze-all`
(see below).

## Quick start (CLI)

```powershell
# Single-scope run against one Snowflake account.
sma analyze-all --scope snowflake:acme-prod

# Pin the cloud explicitly (overrides CURRENT_REGION() sniffing):
sma analyze-all --scope snowflake:acme-prod@azure

# Mixed run — one Synapse workspace + one Snowflake account.
sma analyze-all `
  --scope synapse_workspace:ws-eu-prod `
  --scope snowflake:acme-prod
```

The `--scope` flag accepts `<source_type>:<display_name>[@<platform>]`.
For Snowflake the `<display_name>` is the **account locator** and the
optional `@<platform>` suffix is one of `aws` / `azure` / `gcp` — used
to pin the Estate Overview hyperscaler bucket when you want to
override the auto-detection from `CURRENT_REGION()`.

> Today the **wave execution still pivots on the legacy single scope**
> from `.env`. The `--scope` flag persists the user's intent into the
> manifest so the SPA can render scope counts + source-type chips —
> true multi-scope dispatch lands in Phase 4.5. For a Snowflake-only
> run today, set `SMA_SOURCE_TYPE=snowflake` and `SNOWFLAKE_ACCOUNT`
> (plus the OAuth env var family — see below) in your `.env`.

## Authentication

Snowflake does **not** use the Azure service principal. The analyzer
authenticates via Snowflake's built-in **OAuth security integration**
(refresh-token grant) — see [ADR-0007](../adr/0007-snowflake-auth.md)
for why we chose this over key-pair JWT. The four AAD fields
(`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`,
`AZURE_SUBSCRIPTION_ID`) are ignored when `SMA_SOURCE_TYPE=snowflake`.

### One-time Snowflake setup (ACCOUNTADMIN)

```sql
-- Create the built-in OAuth security integration. USMA uses the
-- refresh-token grant against a fixed localhost redirect URI.
CREATE OR REPLACE SECURITY INTEGRATION usma_oauth
  TYPE = OAUTH
  ENABLED = TRUE
  OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  OAUTH_REDIRECT_URI = 'http://localhost:53682/'
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  OAUTH_REFRESH_TOKEN_VALIDITY = 7776000  -- 90 days, the Snowflake max
  BLOCKED_ROLES_LIST = ()                 -- so SYSADMIN/ACCOUNTADMIN can be requested
;

-- Capture the client id + secret (paste these into the SPA later).
SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('USMA_OAUTH');
```

> **Redirect-URI exact match.** Snowflake's `OAUTH_REDIRECT_URI` is
> matched byte-exactly (host + port + path; no wildcards). The
> default USMA listener is `http://localhost:53682/` — change either
> end to match the other. Override via the `SNOWFLAKE_OAUTH_REDIRECT_URI`
> env var if your dev box has port 53682 in use.

### Browser sign-in from the SPA — preferred for dev boxes (Slice 7-E.2)

The Configuration page's Snowflake card has a **Sign in to Snowflake**
button. Clicking it opens your default browser at Snowflake's consent
screen, runs the refresh-token grant against a one-shot localhost
listener, and writes the refresh token into the same `.env` that
backs the rest of the SPA config. Once the SPA reports
*"Signed in"*, click **Discover databases** and pick the account —
no Snowflake CLI required.

### Service-account / CI flow

For non-interactive hosts, set the OAuth env var family directly:

```powershell
$env:SMA_SOURCE_TYPE                     = "snowflake"
$env:SNOWFLAKE_ACCOUNT                   = "acme-prod"
$env:SNOWFLAKE_USER                      = "USMA_SVC"
$env:SNOWFLAKE_ROLE                      = "USMA_RO"      # optional, defaults to PUBLIC
$env:SNOWFLAKE_WAREHOUSE                 = "USMA_WH"
$env:SNOWFLAKE_OAUTH_CLIENT_ID           = "<from SYSTEM$SHOW_OAUTH_CLIENT_SECRETS>"
$env:SNOWFLAKE_OAUTH_CLIENT_SECRET       = "<from SYSTEM$SHOW_OAUTH_CLIENT_SECRETS>"
$env:SNOWFLAKE_OAUTH_REFRESH_TOKEN       = "<minted out-of-band>"
$env:SMA_SNOWFLAKE_PLATFORM              = "azure"         # optional cloud-bucket hint
```

A small refresh-token minting helper can be lifted from
`SnowflakeProvider._resolve_connect_kwargs` if you need to script
this; in practice most CI shops simply replay the SPA sign-in once
per environment and persist the token to a vault.

### Required privileges

| Role | Why USMA needs it |
|---|---|
| `ACCOUNTADMIN` (one-time, for setup) | Create the OAuth security integration; grant `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` to the service role. |
| `USAGE` on the warehouse picked for the run | Execute the discovery `SHOW` commands and the `ACCOUNT_USAGE` reads. |
| `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` | Read `SNOWFLAKE.ACCOUNT_USAGE.*` views (warehouses, query history, metering, storage, usage-in-currency). |
| `MONITOR USAGE` on the account (production) | Surface `WAREHOUSE_METERING_HISTORY` credits without ACCOUNTADMIN. |

No personal access tokens or password+MFA flows are used. Snowflake
2024-Q4 deprecated password-only authentication for service
identities; USMA's OAuth-refresh-token path is the documented
replacement.

### Copy-pasteable role bootstrap

> ⚠ The default `PUBLIC` role **does not** have the privileges below.
> Pointing USMA at it produces a run with `database_count > 0` but
> `table_count = 0`, plus a `cost.collection_error` finding citing
> *"Schema 'SNOWFLAKE.ACCOUNT_USAGE' does not exist or not
> authorized."* The workloads module now emits a privilege caveat
> when it detects this pattern.

Run this once as `ACCOUNTADMIN` to create a read-only role wired for
USMA, then set `SNOWFLAKE_ROLE=USMA_RO` on the Configuration page:

```sql
USE ROLE ACCOUNTADMIN;

-- 1. Dedicated read-only role + warehouse.
CREATE ROLE IF NOT EXISTS USMA_RO;
CREATE WAREHOUSE IF NOT EXISTS USMA_RO_WH
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE;
GRANT USAGE ON WAREHOUSE USMA_RO_WH TO ROLE USMA_RO;

-- 2. ACCOUNT_USAGE (cost client + query history + metering).
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE USMA_RO;
GRANT MONITOR USAGE ON ACCOUNT TO ROLE USMA_RO;

-- 3. Inventory across each user database to be analyzed.
--    Repeat per database (or wrap in a stored proc / Terraform).
GRANT USAGE ON DATABASE  <db>                         TO ROLE USMA_RO;
GRANT USAGE ON ALL SCHEMAS IN DATABASE <db>           TO ROLE USMA_RO;
GRANT USAGE ON FUTURE SCHEMAS IN DATABASE <db>        TO ROLE USMA_RO;
GRANT REFERENCES ON ALL TABLES IN DATABASE <db>       TO ROLE USMA_RO;
GRANT REFERENCES ON FUTURE TABLES IN DATABASE <db>    TO ROLE USMA_RO;
GRANT REFERENCES ON ALL VIEWS IN DATABASE <db>        TO ROLE USMA_RO;
GRANT REFERENCES ON FUTURE VIEWS IN DATABASE <db>     TO ROLE USMA_RO;
GRANT USAGE ON ALL FUNCTIONS IN DATABASE <db>         TO ROLE USMA_RO;
GRANT USAGE ON ALL PROCEDURES IN DATABASE <db>        TO ROLE USMA_RO;

-- 4. Bind to the OAuth integration user.
GRANT ROLE USMA_RO TO USER <oauth_user>;
```

`REFERENCES` is the minimum privilege for `SHOW TABLES` /
`SHOW VIEWS` to return rows — USMA never reads data, only metadata.

## Support matrix

| Module | Synapse | ADF | Databricks | BigQuery | Snowflake | Notes |
|---|---|---|---|---|---|---|
| `snowflake_workloads` | n/a | n/a | n/a | n/a | ✅ | warehouses, databases, schemas, tables, routines, stages, streams, tasks, pipes, jobs — Phase 7-C |
| `fabric_mapping` | ✅ | ✅ | ✅ | ✅ | ✅ | per-object compatibility + warehouse F-SKU rec from the 7-day window — Phase 7-D |
| `cost` | ✅ | ✅ | ✅ | ✅ | ✅ | Snowflake scopes use `snowflake_cost_client` reading `ACCOUNT_USAGE.METERING_HISTORY` joined to `USAGE_IN_CURRENCY_DAILY` — Phase 7-H |
| `pipelines` (static / run history) | ✅ / ✅ | ✅ / ⛔ | ⛔ | ⛔ | ⛔ | Snowflake tasks + streams surface via `snowflake_workloads` instead |
| `databricks_workflows` | n/a | n/a | ✅ | n/a | n/a | Databricks-only |
| `bigquery_workloads` | n/a | n/a | n/a | ✅ | n/a | BigQuery-only |
| `monitoring`, `code_objects`, `serverless_pools`, `dedicated_pools`, `spark_pools` | ✅ | n/a | n/a | n/a | n/a | Synapse-only concepts |
| `storage`, `security`, `governance` | ✅ | ⛔ | ⛔ | ⛔ | ⛔ | per-source equivalents land alongside their workloads modules |

The active support map is exposed by `GET /api/modules` and by
`MODULE_SPECS[<module>].supports_source(SourceType.SNOWFLAKE)` in code.

## What's collected from a Snowflake account

The `SnowflakeWorkloadsCollector` (see
`usma.modules.snowflake_workloads.collector`) returns workload-shaped
data normalised to the analyzer's wire format. Inventory comes from
`SHOW` commands (cheaper than `INFORMATION_SCHEMA` and works against
all editions); job history comes from
`SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`:

* `iter_warehouses()` — every warehouse with size, type
  (`STANDARD` / `SNOWPARK-OPTIMIZED`), auto-suspend, auto-resume,
  scaling policy, min/max cluster counts.
* `iter_databases()` / `iter_schemas()` — every database / schema in
  the account, system databases filtered by default (override via
  `SMA_SNOWFLAKE_INCLUDE_SYSTEM_DBS=1`).
* `iter_tables(schema)` — `TABLE` / `VIEW` / `MATERIALIZED_VIEW` /
  `EXTERNAL` / `ICEBERG` / `DYNAMIC` with clustering keys and
  row / byte counts.
* `iter_routines(schema)` — UDFs and stored procedures with kind
  (`SQL`, `PYTHON`, `JAVASCRIPT`, `JAVA`, `SCALA`). Language matters
  for the Fabric compatibility verdict (JS / Java → unsupported).
* `iter_stages(schema)`, `iter_streams(schema)`, `iter_tasks(schema)`,
  `iter_pipes(schema)` — Snowflake-specific replatform candidates.
* `iter_jobs(lookback_days)` — completed queries from
  `ACCOUNT_USAGE.QUERY_HISTORY`, bucketed by `query_type`
  (`SELECT` / `INSERT` / `MERGE` / `COPY` / `CREATE_TASK` / …) with
  `execution_time_ms` carried through as the basis for the
  vCore-hour → Fabric CU-hour rollup. Defaults: 28-day lookback,
  10 000 row cap (override via `SMA_SNOWFLAKE_JOB_LOOKBACK_DAYS` /
  `SMA_SNOWFLAKE_JOB_LIMIT`).

### Credit → vCore-hour → Fabric CU conversion

`modules/snowflake_workloads/run_stats.py` ships two module-level
constants and a documented caveat that the analyzer surfaces in
`SnowflakeWorkloadsAnalysis.caveats` when any credit math actually
ran:

* `CREDIT_TO_VCORE_HOURS` — derived per warehouse size from a doubling
  progression (X-Small = 1 credit/hour ≈ 8 vCore-hours,
  Small = 16, Medium = 32, …, 6X-Large = 4096). Calibrate via
  `SMA_SNOWFLAKE_CREDIT_TO_VCORE_RATIO`.
* `CREDIT_TO_CU_HOURS = 0.5` — the conservative proxy used by the
  Estate Overview's `_snowflake_daily_cu` helper. Reused from the
  shared `VCORE_HOURS_TO_CU_HOURS` constant so one edit changes
  Databricks / BigQuery / Snowflake conversions together.

Rolling windows of 7 / 14 / 28 / 90 days are aggregated into
`SnowflakeWarehouseWindowStats` rows (one per window × warehouse) with
`est_credits`, `est_vcore_hours`, `est_cu_hours_fabric_warehouse`.

## Estate Overview CU rollup (Slice 7-F)

`_snowflake_daily_cu(snowflake)` in `web/estate.py` reads
`warehouse_window_stats`, prefers the 7-day window (matches the
Dashboard tile selection), falls back to the largest available window
when 7-day is absent, sums `est_cu_hours_fabric_warehouse` across all
warehouses, and converts to sustained CU/day. The result folds into
`projected_fabric_cu` exactly the same way the Databricks and
BigQuery contributions do, so a Snowflake-only run lights up both
**Projected Fabric CU** and **Recommended SKU** columns in the
Estate Overview.

The Estate Overview's hyperscaler grouping consumes
`extras["platform"]` — set automatically by `SnowflakeProvider` from
`CURRENT_REGION()` prefix sniffing (`AWS_*` → `aws`, `AZURE_*` →
`azure`, `GCP_*` → `gcp`) — so Snowflake-on-Azure accounts bucket
under Azure, Snowflake-on-AWS under AWS, etc.

## Cost (Slice 7-H)

`modules/cost/snowflake_cost_client.py` runs three queries on every
analyzer cycle (honours `SMA_COST_DISABLE_LIVE=1` for offline test
runs):

1. **Rate query** — `SUM(usage_in_currency) / SUM(usage)` from
   `SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY` filtered to
   `usage_type IN ('compute', 'cloud services')`, giving the effective
   **$/credit** rate for the contract.
2. **Compute query** — `SUM(credits_used)` per warehouse per month
   from `SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY`. Credits are
   multiplied by the rate-query result and emitted as
   `MonthlyCostRow(resource_kind="snowflake_warehouse", sku="credits")`
   rows. The synthetic `CLOUD_SERVICES` rollup row gets its own
   `resource_kind="cloud_services"` so it's visible in the breakdown.
3. **Storage query** — `SUM(usage_in_currency)` per month from
   `USAGE_IN_CURRENCY_DAILY` filtered to `usage_type='storage'`,
   emitted as `MonthlyCostRow(resource_kind="storage", sku="storage_tb_month")`
   rows.

Status vocabulary matches the Azure / GCP cost clients:
`ok | sdk_missing | live_disabled | missing_config | empty_window | error`.
`missing_config` fires when `SNOWFLAKE_ACCOUNT` is unset — the
analyzer finishes with that status (no error) so the surrounding
`fabric_compare` / `rules` steps still run.

If the rate query returns no rows, the compute rows still ship with
`cost=0.0` and the credit volume preserved in `usage_quantity`, so
the operator sees usage even when the contract rate hasn't been
ingested yet (typical for trial accounts).

## Configuration page (SPA)

Once `sma serve --with-api` is running, open the **Configuration** tab
and pick the **Snowflake** radio. The page exposes:

* `SNOWFLAKE_ACCOUNT` / `SNOWFLAKE_USER` / `SNOWFLAKE_ROLE` /
  `SNOWFLAKE_WAREHOUSE` plain text fields.
* Cloud-bucket hint (`SMA_SNOWFLAKE_PLATFORM`) — `aws` / `azure` /
  `gcp`.
* **Sign in to Snowflake** — runs the refresh-token grant against
  `http://localhost:53682/` and persists the refresh token.
* **Discover databases** — once signed in, lists the databases visible
  to the configured role (one-account ⇒ one descriptor returned).
* **Validate** — runs three live probes (OAuth resolution surfacing
  account + user, `CURRENT_VERSION()` to confirm data-plane
  reachability, `SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY LIMIT 1`
  to confirm `ACCOUNT_USAGE` access).

The Configuration page **skips** the Synapse SQL / Spark Livy probes
when `SMA_SOURCE_TYPE=snowflake`.

## Run page

When `source_type=snowflake`, the Run page's module checklist limits
the selectable modules to:

* `snowflake_workloads` — default on
* `cost` — default on (reads `ACCOUNT_USAGE`; set
  `SMA_COST_DISABLE_LIVE=1` to skip for offline test runs)
* `fabric_mapping` — default on (consumes the workloads artefact)

Synapse-only, Databricks-only, and BigQuery-only modules are hidden
so you can't accidentally schedule a wave that can never finish.

## Limits & known gaps

* **Account ⇒ scope.** USMA pegs one scope to one Snowflake account
  (matching the OAuth integration's auth contract). Org-level
  multi-account rollups are not on the roadmap; run the analyzer
  per account and aggregate in the Estate Overview.
* **`ACCOUNT_USAGE` latency.** Snowflake refreshes
  `WAREHOUSE_METERING_HISTORY` / `USAGE_IN_CURRENCY_DAILY` on a
  ~45-minute / ~3-hour lag respectively. Cost figures for the
  current day are partial; expect them to settle a couple of days
  out. The analyzer captures `generated_at` in the JSON so
  downstream consumers can apply their own freshness gate.
* **Snowpark Container Services.** Compute outside the warehouse
  meter (SPCS, hybrid tables, dynamic-table refreshes) lands in
  `METERING_HISTORY` as its own service rows. The cost client picks
  these up; the workloads compatibility verdict treats them as
  partial (no Fabric equivalent yet).
* **Cross-region replication** is reported as raw storage cost but
  the workloads matrix does not yet model the cross-region pipeline
  shape on the Fabric side.
* **Legacy run attribution (Snowflake-on-AWS).** Runs created before
  the cloud-aware identity fix inherited `AZURE_TENANT_ID` /
  `AZURE_SUBSCRIPTION_ID` from `.env`. Snowflake-on-Azure accounts
  are unaffected (they really do live in an Azure tenant). For
  Snowflake-on-AWS, run `sma migrate-run-attribution` (or use the
  **Fix non-Azure run attribution** panel on the
  [Configuration](12-configuration.md#fix-non-azure-run-attribution)
  page). New runs are attributed correctly automatically.

## Roadmap

* **Slice 7-I — Dashboard section.** A Snowflake card row mirroring
  the Databricks / BigQuery layout (headline tiles, daily outcome
  stack, hourly per-statement-type bars, storage rollup, top users by
  credits, statement-type breakdown, most-used tables, query
  features via sqlglot's Snowflake dialect).
* **Slice 7-J — Multi-cloud parity smoke.** Confirm discovery +
  validation hold on Snowflake-on-AWS, Snowflake-on-Azure, and
  Snowflake-on-GCP with `CURRENT_REGION()` populating
  `extras["platform"]` correctly through to the Estate Overview
  hyperscaler bucket.

