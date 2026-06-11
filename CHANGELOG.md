# Changelog

All notable changes to **Unified Solution Migration Analyzer** (formerly Unified Synapse Migration Analyzer, originally Synapse Migration Analyzer) are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Standalone Dedicated SQL pool (formerly SQL DW) support.** New
  `SourceType.SYNAPSE_DEDICATED_SQL` source type points the
  `dedicated_pools` module at a `Microsoft.Sql/servers/<server>/databases/<db>`
  with `edition='DataWarehouse'` and **no** parent Synapse workspace,
  fixing the `(ParentResourceNotFound) Failed to perform 'read' on
  resource(s) of type 'workspaces/sqlPools'` error reported when a
  client's pool is provisioned directly under
  `Microsoft.Sql/servers`. The DMV surface, collectors, distribution
  advisor, T-SQL gap rollup, and Fabric mapping rules are reused
  unchanged — only ARM discovery (`SqlManagementClient.databases.list_by_server`
  filtered to `sku.tier == 'DataWarehouse'`) and the endpoint FQDN
  (`<server>.database.windows.net`) differ. See
  [ADR-0009](docs/adr/0009-standalone-dedicated-sql.md) and the
  architecture write-up at
  [docs/architecture/standalone-dedicated-sql.md](docs/architecture/standalone-dedicated-sql.md).
  - **Backend:** `SynapseDedicatedSqlProvider` (discover / validate /
    `make_clients`); `SqlServerArmClient` with DWU-tier filtering and
    a `sql_endpoint` returning `<server>.database.windows.net`;
    `DedicatedPoolsAnalyzer` refactored around a
    `DedicatedPoolArmClient` Protocol with scope-based selection; new
    `azure-mgmt-sql>=3.0` dependency; doctor + `MODULE_SPECS`
    predicates widened.
  - **Web SPA + API:** `POST /api/config/discover-sql-servers`
    endpoint; Configuration page **Dedicated SQL pool** radio with
    server dropdown, optional per-database filter, and live
    validation; `synapse_dedicated_sql` added to the `SourceTypeId`
    union, `apiDiscoverSqlServers` loader, and Estate Overview label
    map.
  - **CLI:** `SMA_SOURCE_TYPE=synapse_dedicated_sql` reuses
    `SYNAPSE_RESOURCE_GROUP` (the SQL server's RG) and
    `SYNAPSE_WORKSPACE_NAME` (the SQL server's name);
    `SYNAPSE_DEDICATED_POOL` optionally narrows the scan to a single
    database. `--scope synapse_dedicated_sql:<server>` works on
    `analyze-dedicated-pools` and `analyze-all`.
  - **Docs:** new
    [docs/user-guide/24-standalone-dedicated-sql.md](docs/user-guide/24-standalone-dedicated-sql.md);
    QUICKSTART preamble, §1 dedicated_pools callout, and module
    table updated; `sma access-report` now lists the standalone
    topology under the `dedicated_pools` entry and adds
    `<server>.database.windows.net` to the documented egress
    allow-list.
  - **Tests:** 28 new unit + integration tests
    ([tests/sources/synapse_dedicated_sql/](tests/sources/synapse_dedicated_sql),
    [tests/test_dedicated_pools_sql_server_arm.py](tests/test_dedicated_pools_sql_server_arm.py),
    [tests/test_dedicated_pools_analyzer_dispatch.py](tests/test_dedicated_pools_analyzer_dispatch.py),
    [tests/web/test_source_type_dedicated_sql.py](tests/web/test_source_type_dedicated_sql.py)).

### Known limits (alpha)

- Cost attribution still gates on the `Microsoft.Synapse/workspaces/.../sqlPools`
  resource-id prefix; standalone DWU rows land in the `other` bucket
  for now.
- `monitoring` does not yet pull DWU capacity metrics for the
  standalone topology; the equivalent
  `Microsoft.Sql/servers/.../databases` metric wiring is a follow-up.

### Slice D — Estate overview + Fabric mapping (this release)

- `MODULE_SPECS["fabric_mapping"].supports` now includes
  `SYNAPSE_DEDICATED_SQL`, so a standalone DWU scope automatically
  gets the same `dedicated_pools.json` → `fabric_mapping.json`
  pipeline as a workspace-attached pool. No rule changes needed —
  the analyzer reads the artefact, not the source type.
- `web/storage.py` `_SCOPE_DIR_RE` widened to accept
  `synapse_dedicated_sql__<slug>` so multi-scope runs enumerate
  per-scope artefacts correctly.
- SPA: `ScopeRef.source_type` literal widened in
  `web/src/api/loader.ts`; `SOURCE_TYPE_LABELS` /
  `SOURCE_NOUN_LABELS` in `web/src/lib/labels.ts` gain
  "Dedicated SQL pool (formerly SQL DW)" / "server";
  `ScopeFilter` source-label map gains a "Dedicated SQL" chip.
- 4 new guardrail tests in
  [tests/test_dedicated_pools_slice_d.py](tests/test_dedicated_pools_slice_d.py):
  fabric_mapping predicate, scope-dir regex (incl. traversal
  rejection), Estate Overview cloud bucket = `azure`, and an
  end-to-end run of `FabricMappingAnalyzer` over a hand-built
  standalone `dedicated_pools.json` to prove the rules fire unchanged.

### Slice E — End-to-end smoke runbook (this release)

- New "End-to-end smoke runbook" section in
  [docs/user-guide/24-standalone-dedicated-sql.md](docs/user-guide/24-standalone-dedicated-sql.md):
  pre-flight RBAC + db-level grants + AAD-admin checklist, CLI
  smoke commands, SPA walk-through, an 8-row verification table,
  and a "Gotchas learned during development" section covering the
  `master` database, SKU tier capitalisation, the
  `SYNAPSE_DEDICATED_POOL` reuse, AAD audience sharing, firewall
  surprises, and the AAD-admin gap as the #1 setup failure mode.

### Fixed

- **Run page** now hides the eleven module checkboxes that do not
  apply to a standalone Dedicated SQL pool scope
  (`synapse_dedicated_sql`). Only `dedicated_pools` is selectable;
  `fabric_mapping` runs implicitly as before. Previously the Run
  page fell through to a default that showed every module, letting
  users tick analyzers (`serverless_pools`, `spark_pools`,
  `pipelines`, `storage`, `monitoring`, `governance`, `security`,
  `cost`, `fabric_validation`, plus the cross-platform Databricks /
  BigQuery / Snowflake collectors) that have no Synapse workspace
  to read from.
- **Dedicated pool DMV connection** falls back to
  `Authentication=ActiveDirectoryServicePrincipal` with `UID` /
  `PWD` from the configured service principal when the AAD
  `SQL_COPT_SS_ACCESS_TOKEN` handshake is rejected with the
  canonical `[28000] Login failed for user ''. (18456)` +
  `Invalid connection string attribute` pair. This pattern is
  observed on standalone Dedicated SQL pools reached via
  `*.database.windows.net` where the gateway refuses the token
  struct that the same code path accepts on
  `*.sql.azuresynapse.net`. The fallback is the documented
  Microsoft path for ODBC Driver 17.4+/18.x service-principal auth
  against Azure SQL.
- **Dedicated pool DMV connection — ODBC value quoting + aggregated
  error.** The SP-direct fallback above now properly escapes any
  `}` inside the service-principal secret (doubling it to `}}` per
  the ODBC connection-string grammar), so secrets that happen to
  contain `}` no longer corrupt the connection string into the
  same `Invalid connection string attribute` rejection we were
  trying to recover from. When both AAD paths fail, the analyzer
  now raises an aggregated `_DedicatedPoolAuthError` listing
  every attempt with its labelled error text — and when both
  attempts report `Login failed for user ''`, the message appends
  a pointer to "user-guide §24 'AAD admin on the SQL server'"
  because the missing server-level Azure AD admin is the
  #1 setup failure mode for standalone DWU.

## [5.3.3] - 2026-06-01

### Fixed
- **Run attribution: non-Azure scopes no longer inherit Azure tenant /
  subscription.** `JobRunner.start()` unconditionally stamped
  `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` / `resource_group` /
  `workspace_name` onto every `run.json`, so BigQuery,
  Snowflake-on-AWS, and Databricks-on-AWS/GCP runs were grouped under
  the unrelated Azure tenant on the Estate Overview. New runs now
  leave those fields `null` for non-Azure scopes and use the scope's
  own `display_name` as the workspace name.

### Added
- **`sma migrate-run-attribution` CLI** (`--dry-run`, `--runs-dir`) and
  matching `GET` / `POST /api/migrations/run-attribution` endpoints
  that rewrite existing `run.json` files in place.
- **Configuration tab: "Fix non-Azure run attribution" panel** that
  self-detects pending migrations and exposes a single-click migrate
  button (only renders when something needs fixing).
- **Docs:** new section in [docs/user-guide/12-configuration.md](docs/user-guide/12-configuration.md);
  troubleshooting entry in [docs/user-guide/13-troubleshooting.md](docs/user-guide/13-troubleshooting.md);
  legacy-runs callout in [docs/user-guide/16-overview.md](docs/user-guide/16-overview.md);
  glossary entries for *Run attribution* and *Non-Azure scope*; source
  cross-references in the BigQuery / Snowflake / Databricks-on-AWS
  chapters; CLI row in [QUICKSTART.md](QUICKSTART.md).

## [5.3.2] - 2026-05-31

### Fixed
- **SPA: enable the Delete button for stalled `running` runs.** The
  RunsHistory row Delete button was disabled for any run with status
  `queued`/`running`, which made the 5.3.1 backend fix unreachable from
  the UI — crashed runs stayed un-deletable. The button is now always
  active and the backend distinguishes genuinely in-flight runs (409)
  from stale ones (deleted in place). Tooltip updated to explain the
  new behaviour. Requires rebuilding `web/dist` (already done in this
  release).

## [5.3.1] - 2026-05-31

### Fixed
- **Crashed runs are now deletable from RunsHistory.** When a run was
  killed before its status could be written (server restart, OOM, worker
  process crash), its `run.json` stayed at `status: "running"` forever
  and `DELETE /api/runs/{id}/data` refused with 409 — leaving an
  un-removable ghost row in the UI. The endpoint now detects stale
  runs (status `running`/`queued` with no live worker registered in the
  in-process `JobRunner`), stamps them as `failed`, and proceeds with
  the delete. Genuinely in-flight runs still 409 the same way.
  Regression test in [tests/web/test_delete_stale_run.py](tests/web/test_delete_stale_run.py).

## [5.3.0] - 2026-05-31

### Added
- **Cross-platform (Windows / Linux / macOS) support.** New `usma.platform`
  helper module centralises OS-aware decisions so the codebase runs natively
  on Ubuntu Desktop, other Linux distros, and macOS in addition to Windows.
  - `cache_dir()`, `config_dir()`, `data_dir()` follow the **XDG Base
    Directory spec** on Linux/macOS (honouring `$XDG_CACHE_HOME`,
    `$XDG_CONFIG_HOME`, `$XDG_DATA_HOME`) and the Known Folders convention
    (`%LOCALAPPDATA%`, `%APPDATA%`) on Windows.
  - `default_odbc_driver()` prefers `ODBC Driver 18 for SQL Server` but
    auto-falls back to driver 17 when 18 isn’t installed (covers older
    Ubuntu LTS releases).
  - `odbc_install_hint()` returns OS-appropriate install commands
    (apt-get on Linux, brew on macOS, MSI link on Windows) and is surfaced
    by `sma doctor` when no driver is detected.
  - `default_runs_dir()` resolves `$SMA_RUNS_DIR` → `./runs` (if present)
    → OS per-user data dir, used by `sma serve --with-api`.
- **`quickstart.sh`** — Bash bootstrapper mirroring `quickstart.ps1` for
  Linux and macOS. Clones the repo, creates `.venv`, installs every extra
  (`dev,cost,web,databricks,bigquery,snowflake`), builds the SPA, runs
  `sma doctor --offline`, and launches `sma serve --with-api`. Flags:
  `--skip-clone`, `--branch`, `--python`, `--skip-doctor`, `--skip-web-build`,
  `--no-serve`.
- **`QUICKSTART.md`** §0.1 now ships copy-paste install commands for the
  Microsoft ODBC Driver 18 on Ubuntu/Debian and macOS, and §0.2 has a new
  **Fast path — `quickstart.sh` (Linux / macOS)** subsection.
- **`tests/test_platform.py`** (14 new tests) covers per-OS cache / config /
  data dir resolution, XDG overrides, runs-dir fallback chain, ODBC driver
  18→17 fallback, and per-OS install-hint phrasing.

### Changed
- `usma.config.load_config` now defaults `SQL_ODBC_DRIVER` via
  `platform.default_odbc_driver()` so hosts with only driver 17 work out
  of the box.
- `usma.modules.cost.fabric_pricing` cache path now resolves via
  `platform.cache_dir()` instead of the hardcoded `~/.cache/usma/` POSIX
  layout, so Windows hosts cache pricing under `%LOCALAPPDATA%\USMA\Cache\`
  and Linux hosts honour `$XDG_CACHE_HOME`.
- `sma serve --with-api --runs-dir` default is now lazy: empty string →
  `$SMA_RUNS_DIR` → `./runs` (if present) → OS per-user data dir. Existing
  invocations that pass `--runs-dir` explicitly are unaffected.
- `sma doctor` ODBC-driver-missing failure now prints OS-specific install
  instructions instead of the Windows-flavoured learn.microsoft.com URL.

## [5.2.1] - 2026-05-29

### Fixed
- **Ruff lint clean-up.** Removed unused imports across the `bigquery_workloads`,
  `snowflake_workloads`, `databricks` AWS provider, web `config_io`, and test
  modules; dropped stray `f`-prefixes on placeholder-free strings; removed an
  unused local in the Snowflake analyzer and a couple of tests; corrected an
  invalid `# noqa` directive in `web/config_io.py`. No behaviour change.

## [5.2.0] - 2026-05-29

### Changed
- **Brand rename: "Unified Synapse Migration Analyzer" → "Unified Solution
  Migration Analyzer".** The product now covers Synapse + Data Factory +
  Databricks + BigQuery + Snowflake, so the "Synapse" in the human-readable
  name no longer scopes the analyzer correctly. Acronym `USMA` is unchanged
  (U**nified** **S**olution **M**igration **A**nalyzer), so package
  imports (`from usma...`), CLI entry points (`usma`,
  `synapse-migration-analyzer`), env-var prefixes (`SMA_*`), config
  schema field names, ADR filenames, and repo / runtime paths are
  untouched. Pure rebrand of titles, banners, docs, and HTML/Markdown
  report headers across 55 files. See ADR-0004 for the rename rationale
  and lineage.
- **Snowflake dashboard parity with BigQuery.** Daily query-volume chart
  trimmed to the last 7 days and a 24-hour hourly companion chart
  (stacked by `query_type` with a dashed average line) added side by
  side, matching the BigQuery dashboard layout.
- **Estate Overview: removed the "readiness over time" card.** The card
  duplicated information already visible in the per-source readiness
  rollup and added noise to the landing page.

### Added
- **Help tab: completed the user-guide manifest.** Registered the seven
  chapters that existed on disk but were not yet wired into the in-app
  Help tab: `16. Estate overview`, `20. Data Factory (ADF) sources`,
  `21. Databricks sources`, `21a. Databricks on AWS (alpha)`,
  `22. Google BigQuery sources`, `23. Snowflake sources`, and
  `99. Adding a new source`. Source chapters now live under a new
  **Sources** sidebar section. Also fixed a broken inline help link on
  the Estate Overview page (`slug="overview"` → `slug="16-overview"`)
  so the question-mark icon resolves to a real chapter instead of a
  404 panel.

## [5.1.3] - 2026-05-28

### Fixed
- **Estate Overview: Databricks-on-AWS scopes were mis-grouped under
  Azure.** Two gaps caused 5.1.2 to bucket all Databricks scopes as
  Azure: (1) the SPA-launched run path didn't forward the
  descriptor's `extras` (so `extras["platform"]="aws"` never reached
  `run.json`), and (2) the estate cloud resolver only consulted the
  top-level `platform` field. Fixed by forwarding `extras` from
  `SourceDescriptor` to `ScopeRef` in the run starter, by also
  reading `scopes[0].extras["platform"]`, and by adding a
  host-suffix fallback (`*.cloud.databricks.com` → AWS,
  `*.gcp.databricks.com` → GCP, `*.azuredatabricks.net` → Azure) so
  legacy runs that pre-date the platform discriminator still bucket
  correctly without re-running.

## [5.1.2] - 2026-05-28

### Added
- **Estate Overview: hyperscaler grouping.** Scopes are now bucketed
  by cloud (Azure / AWS / Google Cloud / On-premises) before the
  existing Tenant · Subscription sub-grouping. The grouping is
  driven by a new `cloud` field on `EstateWorkspace`, derived from
  the latest run's first scope `source_type` plus, for Databricks,
  the scope's `extras["platform"]` discriminator (so Azure
  Databricks, Databricks on AWS, and BigQuery on GCP each land in
  the right bucket).

## [5.1.1] - 2026-05-28

### Added
- **Databricks SQL warehouse CPU-seconds + Unity Catalog usage
  capture.** The Databricks module now queries warehouse-level
  CPU-seconds via the REST API and Unity Catalog `system.billing`
  usage tables, enriching `sql_warehouses[]` with
  `total_cpu_seconds` and DBU evidence so the Fabric mapping has a
  real workload signal rather than configuration alone.
- **Per-warehouse Fabric F-SKU mapping.** New
  `SqlWarehouseFabricMapping` model plus `classify_warehouse` /
  `recommend_fabric_sku` helpers in
  `databricks_workflows/fabric_compat.py` translate each SQL
  warehouse's average concurrent CUs (with a 4× peak-to-avg
  headroom default) into a Fabric Warehouse F-SKU. Mappings flow
  through the analyzer, `fabric_mapping/rules.py` recommendations,
  CSV / Markdown reports, and the SPA via
  `databricks_workflows.sql_warehouse_fabric_mappings`.
- **Dashboard: SQL warehouse table + rolled-up F-SKU banner.**
  The Databricks section renders the per-warehouse mapping with
  CPU-seconds and DBU-hours as supporting evidence, plus a
  one-line "Rolled-up Fabric SKU: F<n>" banner above the table.

### Changed
- **Dashboard "Recommended SKU" stat card now falls back to the
  Databricks SQL rollup** when no
  `fabric_mapping.capacity_projection` is present, so
  Databricks-only scopes get a real headline instead of "—".
- **Estate Overview rollup includes Databricks SQL warehouses.**
  A new `_databricks_sql_daily_cu` helper sums
  `avg_concurrent_cus × peak_to_avg_headroom` across all
  warehouses and adds it to `projected_fabric_cu`, so
  `recommended_fabric_sku` is populated for Databricks-only
  workspaces in the estate index.

## [5.1.0] - 2026-05-27

### Added
- **Databricks on AWS as a first-class source (Phase 4.7).** The
  Databricks scope gained a multi-cloud platform discriminator
  (`azure` / `aws`, with `gcp` reserved). New
  `DatabricksAwsProvider` builds a workspace client from a single
  Databricks service principal via OAuth (account-level
  client-id / secret + workspace URL); per-platform dispatch keeps
  legacy Azure-Databricks descriptors on the existing Entra path.
  Configuration / CLI / `doctor` learned the new env vars
  (`SMA_DATABRICKS_PLATFORM`, `DATABRICKS_HOST`,
  `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`,
  `DATABRICKS_ACCOUNT_ID`) and forward them through to the
  analyzer at run time. See
  [ADR-0005](docs/adr/0005-multi-cloud-databricks.md) and the
  [AWS Databricks user guide](docs/user-guide/21a-databricks-aws.md).
- **SPA: per-cloud Configuration form for Databricks** with an
  Azure / AWS sub-radio, OAuth-driven workspace discovery for AWS,
  and an alpha badge on the platform selector.
- **Databricks SQL Warehouse inventory + query history (Phase
  4.7.8).** The Databricks module now collects SQL warehouses and
  recent query history (with pagination), feeding the workflows
  rollup and Fabric mapping.

### Changed
- **Tightened the `[databricks]` extra** and added an AWS smoke
  test so multi-cloud regressions are caught in CI.
- **QUICKSTART: new §0.0 consolidated security prerequisites**
  with official Microsoft / Databricks doc links.

### Fixed
- **Cost: skip Azure Cost Management for Databricks-on-AWS/GCP.**
  The analyzer no longer tries to authenticate Databricks SP creds
  against Azure ARM; non-Azure Databricks scopes get
  `collection_status = "skipped"` with an explanatory
  `cost.skipped_non_azure` info finding, and cost.json / .md /
  .html still render so downstream readers don't 404.
- **Cost: filter rows to the scope's ARM id** so ADF factories
  hosted in a shared resource group no longer pull in sibling
  workloads' spend.
- **Databricks: ListQueriesResponse pagination** now traverses all
  pages, and the analyzer distinguishes an empty workspace from
  missing SP entitlements with a clear caveat.
- **SPA: AWS Databricks workspace dropdown** persists the selected
  workspace across re-renders.

## [5.0.0] - 2026-05-22

### Added
- **Google BigQuery as a first-class source (alpha).** New
  `bigquery_workloads` module collects datasets, tables
  (`TABLE` / `VIEW` / `MATERIALIZED_VIEW` / `EXTERNAL` / `SNAPSHOT`),
  routines (UDFs / stored procs / table functions), scheduled queries
  (Data Transfer Service), and completed jobs from
  `INFORMATION_SCHEMA.JOBS_BY_PROJECT` (with Cloud Logging fallback).
  `total_slot_ms` is rolled up into 7 / 14 / 28 / 90-day windows and
  converted to Fabric Spark CU-hours via the documented slot-ratio
  caveat. ADC-only auth (no AAD), with an in-browser OAuth sign-in
  for the web control plane. GCP cost via billing-export tables; CU
  totals land in the Estate Overview rollup. SPA: BigQuery section
  inside the Dashboard with headline tiles, daily + last-24h bar
  charts, GCS storage view, most-used tables, dimensional
  breakdowns, and sqlglot-driven query feature detection
  (MERGE / scripting / UDF / ML / geo / ...).
- **Live Fabric pricing via the Azure Retail Prices API.** New
  `fabric_pricing.py` resolves PAYG, 1Y RI, and 3Y RI rates per
  F-SKU on demand (region from `USMA_FABRIC_REGION`, currency from
  `USMA_FABRIC_CURRENCY`, 24 h disk cache under
  `~/.cache/usma/fabric_prices.json`, `USMA_OFFLINE=1` to opt out).
  `FabricCostComparison` gained `fabric_estimated_monthly_cost_1y_ri`,
  `_3y_ri`, `pricing_source`, `pricing_region`, and
  `pricing_currency`. The static fallback table is aligned to MS
  public pricing for F2–F64; F128+ returns `None` when the API is
  unavailable rather than fabricating a guess.
- **Estate render-time price re-resolution.** When a run's
  `cost.json` is missing the Fabric monthly figure but does carry a
  recommended F-SKU, the Estate Overview now calls
  `estimate_monthly_costs(sku)` at render time so stale artefacts no
  longer surface as "—" in the rollup.
- **EstateOverview headlines 1Y RI.** The *Projected monthly spend
  (Fabric)* tile now leads with the 1Y RI total (when available) and
  carries PAYG list price (and 3Y RI when present) in the subtitle.
- **Estate Overview — Source mix pie chart.** New visual breaks
  down workspaces by source (Synapse / ADF / Databricks /
  BigQuery), complementing the existing readiness chart.

### Changed
- **Rebrand: Unified Solution Migration Analyzer (USMA).** The
  Python package was renamed from `synapse_migration_analyzer` to
  `usma` (see [ADR-0004](docs/adr/0004-package-rename.md)). Reports,
  the SPA shell, docs, and the GitHub repo all moved to the new
  name. Both `sma` and `usma` CLI entry points are registered during
  the deprecation window — every command in this guide accepts
  either prefix. Existing clones of `SynapseMigrationAnalyzer.git`
  continue to work via GitHub's permanent redirect.
- **Cost rollup: latest month, not multi-month average.** The
  Estate Overview's *Actual monthly spend* card and the
  Synapse-vs-Fabric delta now use the most recent
  `monthly_totals` key (max `YYYY-MM`) instead of the mean across
  the window. When the cost window includes the current month
  the value is month-to-date and partial — configure
  `SMA_COST_MONTHS` so the window ends on a completed month if you
  want a full prior-month figure. The model field
  `synapse_avg_monthly_cost` keeps its name for back-compat; only
  the computed value and the human-facing labels changed.
- **SPA defaults: all modules selected by default,** with alpha
  badges next to Databricks and BigQuery so the experimental scope
  types are obvious in the Configuration page.

### Fixed
- BigQuery scope identity no longer inherits stale Synapse env vars
  between runs; the analyzer warns explicitly when an audit-log
  query returns zero jobs.
- BigQuery now uses the GCP project *id* (not display name) and
  binds the quota project to the ADC.

## [4.0.0] - 2026-05-18

### Added
- **Business-impact axis on every recommendation.** `Recommendation`
  now carries `impact` (`high` / `medium` / `low` / `unknown`) and an
  optional `impact_detail` string. Severity says *what kind of
  problem*, impact says *how much it matters for the workload*. The
  Recommendations page renders an Impact pill (red / amber / green /
  muted), an Impact filter dropdown, six rollup tiles
  (blockers / warnings / info / high / medium / low+unknown) that
  recompute against the active filter, and a *Group by* selector
  (None / Area / Target) that collapses rows under headers.
- **Workload-aware rule severities.** When `top_consumed_objects` is
  available, the dedicated-pool rules build a workload share lookup
  keyed by both qualified `schema.name` and bare object name. Heap
  size, stale statistics, MERGE / per-rule T-SQL findings, and
  distribution-advisor candidates now flex severity (and impact)
  based on whether the object is a hot driver of CPU elapsed-ms or
  is cold and safe to defer. `_tsql_recommendations` additionally
  emits per-object hot recs (`dp.tsql.<rule_id>.<pool>.hot.<cid>`)
  so a Workload Group rule warning on a 12 %-elapsed table is
  surfaced as a blocker without changing the aggregate roll-up.
- **Storage rules module.** New `rules_for_storage` flags
  dedicated-pool storage at ≥ 95 % used (blocker), ≥ 80 % used
  (warning), default-workspace storage account, and large
  workspace-attached accounts. Wired into `analyzer.py` via
  `storage.json`, with phase mapping
  (`storage.dedicated_pool → foundation`,
  `storage.accounts → ingest_shortcuts`) and a rollback hint
  ("keep source storage online read-only until OneLake shortcuts
  verified").
- **Heuristic refinements.** Replicated-table candidate now triggers
  on `rows > 50M OR data_space_mb > 2048` (not rows alone). Stats
  rule detects high-churn (`modification_counter / row_count > 0.10`)
  in addition to age > 14 d. DWU monitoring gains a *bursty* tier
  (`p95 > 85 ∧ avg < 40` → autoscale hint). Pipeline run-history
  split into `idle_30d`, `idle_7d`, `chronic_failure` (sr < 0.50),
  and `low_success` (0.50 ≤ sr < 0.95) — each with its own severity
  and impact.
- **Critical-path / parallel effort projection.** `EffortRollup`
  gains `parallel_p50_hours` / `parallel_p90_hours` (and per-phase
  `max_step_*`). With N workers the phase finishes in
  `max(longest_step, phase_total / N)`; phases stay sequential
  between each other. The Runbook page renders the parallel hours
  alongside the sequential hours, including calendar-days converted
  with the same `ceil((h / 8) × 1.15)` formula.
- **Runbook step state.** Each step gets a Status dropdown
  (Not started / In progress / Done / Skipped). Skipping requires a
  free-text reason — captured inline and persisted in
  `localStorage` keyed on `source_recommendation_id` (or
  `phase + order` as fallback). Phase headers show live
  `X/Y done · Z skipped · est. P50 h remaining`. Done rows dim;
  skipped rows strike through.
- **Copy as Markdown.** New buttons on the Runbook page emit a
  GitHub-flavoured Markdown table — *Copy phase as Markdown*
  per-phase, *Copy whole runbook as Markdown* at the top. Skip
  reasons travel with the export so reviewers see why steps were
  dropped.
- **Docs alignment.** `docs/user-guide/06-recommendations.md` and
  `07-runbook.md` rewritten to reflect the impact axis, the
  six-phase model
  (foundation / data_plane_prep / ingest_shortcuts / compute_migration /
  orchestration_migration / verification), the new storage and
  pipelines.runs.* areas, step-state semantics, copy-as-markdown,
  and the critical-path projection.

### Added (previous Unreleased entries)
- **Top consumed tables / views rewritten end-to-end with sqlglot +
  workload cache.** The "hit and miss" behaviour of the old SQL-side
  LIKE join is gone, replaced with a parser-driven pipeline:
  - New `workload_commands.sql` pulls submitted commands from
    `sys.dm_pdw_exec_requests` (the user-statement DMV) instead of the
    distributed step-text DMV.
  - New `workload_parser.py` walks the sqlglot AST and resolves each
    `Table` node against the pool's `INFORMATION_SCHEMA` catalog,
    tagging it `qualified`, `unqualified-resolved` or `ambiguous`.
    CTE names, temp tables and references outside the catalog are
    dropped automatically.
  - New `workload_cache.py` persists parsed requests under
    `output/.cache/dedicated_pools_workload/<workspace>__<pool>.json`
    with a 30-day TTL, so the DMV's rolling-buffer roll-off stops
    gutting the ranking between runs.
  - `TopConsumedObject` gains `elapsed_time_ms` and `match_kind` and
    `PoolAnalysis` carries a new `WorkloadCaptureStats` health signal
    (DMV row count, parse success/fail, cache window, oldest/newest).
  - SQL Surface page adds a **Rank by** toggle (Elapsed time *(default)*
    / Usage count), an Elapsed column, a match-kind badge that dims
    heuristic rows, and a muted capture-stats footnote so readers can
    judge how trustworthy the ranking is.
  - Adds `sqlglot>=23.0` to the runtime dependencies.
- **Dashboard 28-day / 24-hour split charts for every time-series
  workload.** The DWU utilization, Pipeline activity, Spark pools and
  Serverless SQL sections each now render the existing 28-day chart on
  the left and a new 24-hour companion chart on the right, so today's
  activity is visible at a glance without losing the trending view.
  - Pipelines: new `hourly_status` field on `PipelineRunHistory`
    (ISO-hour-keyed succeeded/failed/other counts), populated by the
    pipelines analyzer from the same in-memory run list as
    `daily_status` &mdash; no extra Azure calls required.
  - Spark: new `SparkHourlyBars` component bins per-pool vCore-hours
    into 24 hourly slots anchored to the top of the current local hour.
  - Serverless SQL: new `data_processed_hourly.sql` query bucketing
    `sys.dm_exec_requests_history` per UTC hour over the trailing 24h,
    new `ServerlessHourlyUsage` model + `hourly_usage` field on
    `ServerlessAnalysis`, surfaced as a clustered queries/MB bar chart
    matching the 28-day view. Daily chart now shows up to 28 days
    (was 7).
  - Older runs without hourly data render an empty-state hint inviting
    a Re-analyze.
- **Per-section Re-analyze buttons on the dashboard (control plane).**
  The DWU, Pipelines, Spark and Serverless section headers each now
  expose a Re-analyze button that triggers a targeted analyzer run for
  just that workload module (plus `fabric_mapping` so the top-line
  readiness/cost stays fresh) using the section's current window
  length. Progress streams via SSE; the page auto-reloads onto the new
  run when done. Other modules carry forward from the prior run so
  partial re-runs are safe.

## [3.4.0] - 2026-05-17

### Added
- **"Delete all runs" button on the Runs page.** New
  `DELETE /api/runs` endpoint best-effort cancels every run still
  flagged `queued`/`running` (this picks up stalled runs left behind
  by a server restart whose meta never transitioned out of `running`)
  and force-deletes every run directory on disk regardless of status.
  The UI surfaces the action above the runs table behind a typed
  `DELETE ALL` confirmation, reports how many runs were deleted and
  how many stalled entries were cancelled, and clears the active-run
  hash so other pages stop loading a deleted run.
  - `web/src/api/loader.ts`: new `apiDeleteAllRuns()` helper plus
    `DeleteAllRunsResult` type.
  - `web/src/pages/RunsHistory.tsx`: top-of-page action row with
    busy state and result message.
  - `src/synapse_migration_analyzer/web/api/runs.py`: `delete_all_runs`
    handler ordered before `/{run_id}` to avoid path-param collision.

### Fixed
- **Dedicated SQL pools analyzer no longer aborts when a pool is not
  online.** Previously only `status == "paused"` was skipped; pools
  in `Pausing`, `Resuming`, `Scaling`, `Creating`, `Deleting`,
  `Recovering`, `Restoring`, `Disabled`, or `Inaccessible` states
  raised a `28000 / 18456 Login failed for user '<token-identified
  principal>'` ODBC error that killed the whole module. The analyzer
  now skips any non-`Online` pool with a per-pool warning recorded on
  `analysis.errors`, still consumes the budgeted progress steps, and
  continues with the rest of the run. A try/except around
  `sql.session()` also catches unexpected login failures (e.g. the
  pool transitions out of `Online` between the ARM listing and the
  SQL connect) and turns them into a skip rather than a hard failure.

## [3.3.0] - 2026-05-15

### Added
- **DWU utilization section on the run Dashboard.** A new section
  between *Top blockers* and *Storage* plots historical
  `DWUUsedPercent` per dedicated SQL pool over the monitoring window,
  driven entirely by the existing `monitoring.json` artefact (no
  backend changes). Headline stat cards show estate-wide peak DWU %,
  p95 DWU %, total active hours and pools observed; an inline-SVG
  multi-line chart (one polyline per pool, dashed 100 % reference,
  auto Y-scale for >100 % bursts, gap handling for null samples,
  colour-coded legend) renders without a chart library; a per-pool
  table breaks out DWU limit, peak DWU, peak %, p95 %, avg % and
  active hours. Severity colours are inverted vs. `PctPill` (90 % =
  red, 70 % = amber) because high DWU % means *saturated*, not
  *healthy*. The section auto-hides when the monitoring module didn't
  run or returned no DWU series (paused pools, missing
  `Monitoring Reader` RBAC). Also wired into the SPA's static-mode
  module probe so the *Run* page state pill reflects monitoring
  availability.

### Frontend
- `web/src/types.ts`: new `MonitoringMetricSeries`,
  `MonitoringDwuDayStat`, `MonitoringReport` types mirroring the
  pydantic `MonitoringAnalysis` model.
- `web/src/api/loader.ts`: new `loadMonitoring()` plus probe entry.
- `web/src/pages/Dashboard.tsx`: `DwuUtilizationSection`,
  `DwuLineChart`, `DwuPctTag` components.

## [3.2.0] - 2026-05-14

### Added
- **Global *Lookback* selector on the Run page.** A new dropdown
  exposes 1 / 3 / 7 / 14 / 28 / 60 day windows (default **14**) and
  is applied uniformly to every analyzer that fetches run history:
  `pipelines`, `spark_pools` (Livy) and `monitoring` (Azure Monitor
  metrics). The frontend posts `days` on `POST /api/runs` and the
  job runner overrides `SMA_PIPELINES_RUN_DAYS`,
  `SMA_SPARK_RUN_DAYS` and `SMA_MONITORING_DAYS` for the duration
  of the run, restoring the previous values in `finally`. Analyzers
  that don't fetch run history ignore the field. `StartRunRequest`
  validates the value (`1 ≤ days ≤ 365`).
- **Per-pool progress for Spark Livy history fetches.** The
  `spark_pools` analyzer now reports two progress steps per pool
  (`spark batch history [pool]`, `spark session history [pool]`) so
  the Run page bar reflects Livy work as it pages, instead of
  freezing at a single `spark_history` step on busy workspaces.

### Changed
- **`SMA_SPARK_RUN_LIMIT` default raised from `5000` → `50000`.**
  The Synapse Livy job/session list APIs have no server-side time
  filter, so the client already pages offset-based newest-first and
  terminates as soon as a full page is older than the lookback
  window (`stop_old`). The previous 5 000 limit was therefore the
  *only* cause of truncation on production workspaces. The hard
  safety ceiling stays at `_HARD_LIMIT = 100000`. Explicit
  `SMA_SPARK_RUN_LIMIT` overrides still win.

### Fixed
- **Pipeline run-history daily chunking is on by default.** The
  `_collect_run_history` path now requests one day at a time and
  back-fills pipelines with zero global runs via a per-status
  `Status Equals` server-side query, eliminating the residual
  `truncated=true` cases on workspaces with high-frequency
  pipelines. (Shipped in `6a75b0f`, called out here for visibility.)

## [3.1.0] - 2026-05-13

### Fixed
- Ruff release-check failures (F401/F541/F811/F821/F841/E402) across
  `access_manifest`, `serverless_pools/collectors`, `spark_pools/spark_history_client`,
  `fabric_mapping/analyzer`, `web/estate`, `web/schemas`, and the test suite.

## [3.0.0] - 2026-05-13

### Added
- **Backup & restore for run data.** New `/api/runs-archive/export` and
  `/api/runs-archive/import` endpoints and a matching **Backup & restore**
  panel on the Configuration page let users download every run under
  `runs/` as a single zip and reimport it on another machine. The
  archive contains a `manifest.json` plus `runs/<id>/` trees only;
  `.env` lives outside `runs_dir` and is therefore never included, and
  the archiver additionally drops dotfiles and refuses to follow
  symlinks. Imports validate the zip against path-traversal, symlink
  entries, per-file and total size caps, and zip-bomb compression
  ratios before extracting each run atomically (write to tempdir →
  `os.replace`). Three conflict modes are supported: `skip_existing`
  (default), `overwrite`, and `rename` (assign the incoming run a
  fresh id and rewrite `run.json`). In-flight runs cannot be
  overwritten — cancel first.
- **Resource-days alongside hours in every effort summary.** The
  Runbook header, per-phase mini-table, the Markdown and HTML reports,
  the Estate Overview workspace table + totals card, and the CSV
  export now show `ceil((hours / 8) × 1.15)` (8 working hours per day
  plus 15 % spillage, rounded up) so plans can be quoted directly in
  resource-days. New helper `synapse_migration_analyzer.effort.days_from_hours`.

### Changed
- **Configuration page label polish.** Button labels are now plain
  sentence case for consistency: **Save** → **Save configuration**,
  **Validate (fields)** → **Validate fields**, **Validate access (live)**
  → **Validate live access**. The validation pill reads **All OK** /
  **Failed** (and per-row **OK** / **Fail**) instead of all-caps. The
  intro paragraph explains the `.env` path and the client-secret
  status (*set* / *unset*) instead of dumping a raw status token.
  The effort-card **Reset to default** button is now **Reset to
  shipped defaults**.

### Versioning
- **Major version bump to 3.0.0** to mark the new control-plane
  capabilities (run-data backup/restore, resource-days estimation,
  effort rate card webform) that ship together in this release.

## [2.10.0] - 2026-05-14

### Added
- **Configurable effort estimates / rate card.** Every runbook step now
  carries a P50 and P90 hour figure derived from a small, fully
  editable JSON rate card (`effort-card.json`). The estimator groups
  recommendations by area, sums *coefficient × count* using the same
  artefacts the other modules already produced (tables, indexes,
  notebooks, pipelines, linked services, triggers, runtimes, external
  tables, serverless queries), applies the area cap, splits the area
  total across the steps in that area, adds the phase's `base_hours`
  share, divides by `team_velocity`, and derives P90 = P50 ×
  `confidence_p50_to_p90_multiplier` (default 1.8). Steps that match
  no rule fall back to the qualitative `low / medium / high` map.
- **Runbook page in the SPA** — a header summary card with total
  P50 / P90 plus a per-phase mini-table, two new columns (`P50 (h)` /
  `P90 (h)`) on every step row with a tooltip showing the component
  breakdown, and the same breakdown surfaced inside each step's
  expanded panel (annotated *(capped)* where applicable).
- **Configuration page in the SPA** — a new default-collapsed
  **Effort card (advanced)** panel that lazy-loads on toggle, edits
  the JSON in-place, and exposes Save / Reset to default. The panel
  stays out of the way for users who don't tune.
- **CLI surface.** New `sma effort-card` subcommand writes the
  shipped defaults to a hand-editable file; `sma map-to-fabric` and
  `sma analyze-all` accept `--effort-card path/to/card.json`; the
  `SMA_EFFORT_CARD` environment variable is honoured everywhere.
- **Control-plane API.** `GET / PUT / DELETE /api/effort-card`
  (CSRF-guarded via `X-SMA-API: 1`) reads and writes
  `effort-card.json` next to the `.env`, with path-traversal
  protection.
- **Reports.** `fabric_mapping.md` and `.html` gain an *Estimated
  effort* section; `fabric_mapping.json` `runbook[*]` gain
  `effort_hours_p50`, `effort_hours_p90`, `effort_breakdown`, and a
  new top-level `effort_summary` object.
- **User-guide chapter 19 — Effort estimates & rate card**
  ([`docs/user-guide/19-effort.md`](docs/user-guide/19-effort.md))
  documents the model, the full schema, the calibration loop, and the
  three ways to override the defaults. Cross-linked from the SPA Help.

## [2.9.0] - 2026-05-13

### Added
- **User guide chapter 18 — Cost of running the analyzer**
  ([`docs/user-guide/18-cost.md`](docs/user-guide/18-cost.md)). Two-part
  reviewer chapter: (A) Azure consumption SMA itself adds to the bill
  — essentially zero, because every API SMA calls is in a free tier and
  the DMV scans run on capacity that is already paid for, with a worked
  example for a 5-pool weekly cadence (<$3/year); (B) the Fabric cost
  projection methodology, including the 1 CU = 2 Spark vCores ratio,
  the DIU/MDF vCore-hour → CU-hour mapping, the DWU → CU lookup, the
  peak-day-not-weekly-average rule introduced in 2.6.3, F-SKU sizing
  with configurable headroom (`SMA_FABRIC_HEADROOM`), and an explicit
  "verify on the pricing page before quoting procurement" caveat.
  Cross-linked from the SPA Help, the user-guide index, and `README.md`.
- **`OPTION (LABEL = 'sma:<query>')` on the dedicated-pool DMV scans**
  (`tables.sql`, `column_stats.sql`, `top_queries.sql`, `usage.sql`)
  so the analyzer's own activity is auditable in
  `sys.dm_pdw_exec_requests`. Pool DBAs can now run
  `SELECT label, COUNT(*) FROM sys.dm_pdw_exec_requests WHERE label
  LIKE 'sma:%' GROUP BY label;` to see exactly what SMA executed and
  how much pool time it consumed. `top_consumed_objects.sql` already
  carried this label (shipped in 2.7.0); the rest now match.

## [2.8.0] - 2026-05-13

### Added
- **`sma access-report` CLI subcommand.** Emits a Markdown report of
  every Azure / Synapse / SQL surface the analyzer touches, the RBAC
  required to make those calls succeed, what ends up in the on-disk
  output, and what does not leave the host. Needs no Azure access and
  reads no workspace state — the manifest is a static, version-stamped
  description of the analyzer's own footprint, suitable for attaching
  to InfoSec / change-advisory tickets. Output can be redirected with
  `--out access-report.md`.
- **User guide chapter 17 — Tool access & security implications**
  ([`docs/user-guide/17-access-and-security.md`](docs/user-guide/17-access-and-security.md)).
  Reviewer-oriented walkthrough of the per-module access matrix, the
  identity model (single SP via `ClientSecretCredential`), what gets
  written to disk, what counts as sensitive in output (workspace /
  schema metadata, ~4 KB SQL previews, login names — explicitly no
  row data and no plaintext secrets), the egress allow-list (per-
  workspace Azure FQDNs only, no telemetry), secret handling, threat
  model, and a pre-run reviewer checklist. Bundled into the SPA Help
  as a hidden-from-pager but routable chapter, and cross-linked from
  `README.md`, `SECURITY.md`, and `docs/user-guide/README.md`.
- New `src/synapse_migration_analyzer/access_manifest.py` is the single
  source of truth shared between the CLI command and the user-guide
  chapter so the two cannot drift out of sync.

## [2.7.0] - 2026-05-13

### Added
- **SQL Surface page** consolidates dedicated SQL pool code objects,
  top dedicated SQL pool queries by elapsed time, and top serverless
  SQL queries into a single page with collapsible `<details>` sections
  so users can jump straight to what they need without scrolling.
  Nav entry renamed from "Code objects" to "SQL Surface" and made
  visible whenever either `dedicated_pools` or `serverless_pools` has
  data (via new `NavEntry.requiresAny`).
- **Top dedicated SQL pool queries** collector
  (`sys.dm_pdw_exec_requests` + `sys.dm_pdw_exec_sessions`, last 14
  days, top 100 by elapsed time) on `PoolAnalysis.top_queries`, with a
  filterable / sortable drill-down on the SQL Surface page.
- **Top consumed tables/views** collector for dedicated pools using a
  deduped, time-bounded LIKE join over `sys.dm_pdw_sql_requests` with
  qualified `schema.name` boundary matching to avoid substring false
  positives. Surfaced as a new collapsible section on SQL Surface with
  top-10 default, show-more, filter, and a relative-usage bar.
- **Top serverless SQL queries** moved off the Dashboard onto SQL
  Surface; Dashboard's serverless section now shows KPIs and the 7-day
  clustered-bar chart only and points users at SQL Surface for the
  drill-down.

### Changed
- Per-pool analyzer step budget bumped from 14 to 15 to account for
  the new top-consumed-objects collector.

## [2.6.5] - 2026-05-12

### Changed
- **Removed success / failure dimensions from the Spark execution
  panel** on the Dashboard. Most Synapse Spark runs are interactive
  notebook sessions where "failed" includes user-cancelled cells and
  SIGTERM-on-idle, so the success rate was a noisy signal that didn't
  help capacity planning. The panel now shows Spark runs, compute, and
  estimated Fabric CU only — with the per-pool table showing runs,
  duration, vCore-hr, CU-hr, and avg vCore-hr/run.

## [2.6.4] - 2026-05-12

### Fixed
- **Spark daily bar chart on the Dashboard dropped today's runs and any
  interactive-session pool that only had non-zero CU-hours**. Two bugs:
  1. The day bins were pre-seeded for ``[now - 28d, now - 1d]`` because
     the loop used ``start + i * day`` for ``i in [0, 28)``. UTC date of
     ``now`` (today) had no entry so today's runs silently dropped.
     The window now bins exactly ``windowDays`` UTC days **ending today
     inclusive**.
  2. Interactive Livy sessions report ``est_cu_hours_fabric_spark``
     directly but often have ``vcore_hours = null``; those runs
     rendered as zero-height segments and looked like the pool had no
     activity. The chart now falls back to
     ``est_cu_hours_fabric_spark / 0.5`` when ``vcore_hours`` is
     missing so interactive pools surface alongside scheduled pools.

## [2.6.3] - 2026-05-12

### Changed
- **Recommended Fabric SKU now sizes for the busiest day, not the
  weekly average.** Fabric capacity smooths CU-second consumption over
  a rolling 24-hour burndown window, so a one-day burst that exceeds
  `F-SKU × 24` CU-hours will throttle even when the weekly average is
  comfortable. The Spark + Pipelines contribution to the SKU
  recommendation is now derived from the worst single UTC day inside
  the observation window divided by 24h, not the window total divided
  by `window_days × 24`. Previously a workspace that ran 1680 Spark
  CU-hours all on Tuesday looked like ~10 CU sustained (avg over a
  week); it now correctly sizes for the actual 70 CU peak day.
- DW DWU contribution is unchanged — monitoring metrics already give
  per-timestamp peaks.

### Added
- New per-window field `peak_day_cu_hours` on
  `SparkRunWindowStats` and `PipelineRunWindowStats`. Computed at
  collection time from the actual run timestamps (UTC date of
  submission for Spark, UTC date of `run_end` for pipelines). Older
  artefacts without this field fall back to a conservative
  "avg×2" peak-day estimate so historical runs still produce a
  sensible projection.
- 1 new test (`test_legacy_payload_without_peak_day_falls_back`).

## [2.6.2] - 2026-05-12

### Changed
- **Recommended Fabric SKU now factors in Spark and Pipelines CU-hours**
  in addition to peak DWU. The capacity projection adds a sustained-CU
  contribution for Spark Livy (`est_cu_hours_fabric_spark`) and for
  Pipelines (DIU + Mapping-Data-Flow vCore + orchestration), normalises
  each over its observation window (default 7 days), applies the same
  headroom multiplier to the combined total, and sizes the smallest
  covering F-SKU. Previously DW DWU was the only signal and the
  recommended SKU could be undersized for Spark-heavy workspaces.
- A capacity projection is now produced even when no DW monitoring
  data is available, as long as Spark and/or Pipelines have history.

### Added
- `CapacityProjection` exposes per-component breakdown:
  `dwu_cu_contribution`, `spark_cu_contribution`,
  `pipelines_cu_contribution` (post-headroom CU). The Dashboard and
  printable report render these as `… CU (h%) · DW X + Spark Y + Pipelines Z CU`.
- 3 new tests in `tests/test_cu_projection.py` covering Spark-only,
  combined DW+Spark+Pipelines, and the all-zero short-circuit.

## [2.6.1] - 2026-05-12

### Added
- **Carry-forward provenance in the SPA.** The Dashboard's Storage,
  Pipeline, Spark, and Serverless section headers now show a small
  `↺ carried · <relative-age>` pill whenever the underlying module
  artefact was inherited from a prior run (rather than produced by the
  currently-selected run). Hovering the pill reveals the source run id
  and the original generation timestamp.
- New top-of-Dashboard banner summarising which modules were
  refreshed in the current run vs. carried forward. Silent on a
  clean full run so it never adds noise.
- The `Runs` page now flags incremental runs with an `↺ incremental`
  pill next to the run id, with a tooltip listing the carried modules.
- New reusable `Provenance` component (`useCurrentRunMeta`,
  `moduleProvenance`, `ProvenanceBadge`) so future module pages can
  surface the same provenance signal with one line of JSX.

### Tests
- `web/src/__tests__/provenance.test.ts` covers the helper for all
  four cases (no meta, executed-in-run, unknown module, carried).

## [2.6.0] - 2026-05-12

### Added
- **Incremental runs (control-plane).** When a run only selects a
  subset of modules, the runner now copies every other module's
  artefacts forward from the most recent completed run with the same
  workspace identity (`tenant_id` / `subscription_id` /
  `resource_group` / `workspace_name`). This lets users refresh one
  module at a time without losing the rest of the workspace view.
  Carried artefacts retain their original mtime via `shutil.copy2`,
  and the carry chain collapses to the original producer (so run C
  inheriting from run B that inherited from run A correctly records
  A as the source).
- New `ModuleState` value `"carried"` and new `RunMeta.carried_from`
  mapping (`module -> source_run_id`) plus per-`ModuleStatus`
  `carried_from_run_id` / `carried_from_started_at` fields capture
  provenance for downstream UI. Backwards-compatible: existing run
  records deserialise unchanged (new fields default to empty).
- `FilesystemRunRepo.latest_for_workspace(...)` to look up the most
  recent terminal run for a workspace identity, with `exclude_id`
  so a run never carries from itself.
- New `modules_carried` SSE event so the live UI can react when
  carry-forward happened.

### Tests
- `tests/web/test_carry_forward.py` covers: cross-run carry-forward,
  workspace isolation, no-carry when the module is being refreshed,
  ignoring non-terminal (running/cancelled) prior runs, and the
  carry-chain collapse-to-source rule.

## [2.5.12] - 2026-05-12

### Changed
- **Removed the interactive vs scheduled dimension from the Spark
  execution UI.** Livy telemetry does not expose a 100%-reliable
  discriminator across all Synapse run types, and showing a split
  that doesn't match Synapse Studio is worse than showing no split.
  The Spark execution StatCards, per-pool table, and daily stacked
  bar chart now display per-pool totals only. The underlying
  classification is still computed and persisted in
  `spark_pools.json` (so downstream tooling that can verify the
  signal still has access), but the Dashboard no longer surfaces it.

## [2.5.11] - 2026-05-12

### Changed
- **Spark execution section is now per-pool.** The table previously
  produced one row per (pool, kind) which made it hard to read at a
  glance (you saw `sparkpool001 / scheduled` and `sparkpool001 /
  interactive` as two separate rows). It now collapses to one row per
  pool with an `Interactive` column showing the count *and* share of
  all runs on that pool (e.g. `2 (3.8%)`), plus a per-pool success-rate
  pill. Per-pool totals also include runs across both trigger kinds.
- **Spark daily bar chart now shows per-pool vCore-hr only.** The
  chart was previously a clustered bar chart with both vCore-hr and
  the derived Fabric CU-hr (= vCore-hr × 0.5) side-by-side, which was
  redundant. It is now a stacked bar chart with one segment per Spark
  pool per day, deterministic per-pool colors, and a per-pool legend
  + window-total footer. Hovering a segment shows `<day> — <pool>:
  <vCore-hr>`.

## [2.5.10] - 2026-05-12

### Fixed
- **Pipeline vs. interactive notebook classification now uses the
  authoritative Synapse signal.** v2.5.7 introduced a name-pattern
  regex (`<name>_<pool>_<unix-ts>`) to detect pipeline-triggered
  sessions, but Synapse Studio uses the *same* auto-name pattern for
  user-attached interactive notebooks, producing false positives
  (e.g. an ad-hoc session on the `InteractiveLab` pool was bucketed
  as scheduled).

  The classifier now keys off the Spark conf instead. Synapse's
  pipeline framework and Spark Job Definition runtime inject these
  keys into `livyInfo.jobCreationRequest.conf`:
  - `spark.synapse.context.pipelinejobid`
  - `spark.synapse.context.activityrunid`
  - `spark.synapse.context.activityname`
  - `spark.synapse.nbs.runid`

  None of these are present on user-attached Studio notebook sessions,
  so a non-empty value for any of them is treated as the definitive
  scheduled marker. Tags remain as a defensive secondary signal.

### Added
- `SparkRunRecord.pipeline_job_id`, `activity_run_id`, `activity_name`,
  `notebook_name`, `notebook_run_id` — the Synapse-injected
  correlation IDs are now surfaced on every record (when present) so
  downstream tooling can join Spark Livy runs to the parent pipeline /
  activity run without re-querying the Synapse REST API.

## [2.5.9] - 2026-05-12

### Fixed
- **Dashboard now renders available sections for partial runs.** Running a
  single module (e.g. only `analyze-spark-pools`) no longer produces a hard
  error — the Dashboard used to bail out with *"No fabric_mapping.json for
  run …"* whenever the fabric_mapping module hadn't been selected. The page
  now checks for output from any module (fabric_mapping, storage, pipelines,
  spark_pools, serverless) and renders the corresponding sections; only the
  global header/readiness/StatCards/Inputs panel — which all depend on
  fabric_mapping — are hidden in partial-run mode, with a one-line banner
  explaining the partial state. The empty-state message is reserved for the
  case where *no* module produced any output.

## [2.5.8] - 2026-05-12

### Fixed
- **Pipeline-triggered Spark notebook runs no longer report as failed.**
  v2.5.6–2.5.7 used divergent failure semantics per Livy endpoint and
  treated `result="Cancelled"` as a failure unconditionally. Synapse
  pipelines and Spark Job Definitions routinely reap the underlying
  Livy session/batch as `state=dead`/`killed` + `result=Cancelled`
  **after** the notebook code returned successfully — Synapse Studio
  shows these as Succeeded, but the analyzer was reporting them as
  failed (e.g. 51 scheduled runs → 0 ok / 51 failed despite the parent
  pipelines all succeeding).

  Outcome classification is now unified and aligned with the Studio
  Monitor view for both batches and sessions:
  - `result=Succeeded` or `state=success` → succeeded.
  - `result=Failed` / `result=FailedTimeout` or `state=error` → failed.
  - `result=Cancelled` while still in a non-terminal state → failed
    (genuine user/admin cancel mid-run).
  - Any other terminal cleanup state (`dead` / `killed` /
    `shutting_down` / `stopped`) — including `result=Cancelled`
    paired with one of those, which is the platform's post-success
    teardown signature — → succeeded.

### Added
- Diagnostic INFO log per (pool, Livy endpoint) emits a top-10
  histogram of observed `state`/`result`/`trigger`→`outcome` tuples
  immediately after a collection pass, making it easy to compare the
  analyzer's classification against Synapse Studio when investigating
  edge cases.

## [2.5.7] - 2026-05-12

### Fixed
- **Pipeline-triggered notebook executions now correctly bucket under
  *scheduled* instead of *interactive*.** Synapse pipelines and Spark
  Job Definitions launch notebook runs through the Livy *session*
  endpoint (Synapse Studio shows Type = "Spark session") rather than the
  Livy *batch* endpoint, so v2.5.0–2.5.6 was tagging them as
  interactive ad-hoc runs. The classifier is now decoupled from the
  Livy endpoint:
  - Livy *batch* records remain *scheduled*.
  - Livy *session* records are classified per-record: *scheduled* if
    the session has an auto-generated `<source>_<pool>_<unix-ts>` name
    or carries pipeline / trigger / scheduler tags (e.g.
    `JobType=SparkNotebook`); *interactive* otherwise (user-attached
    notebook in Studio).
  Failure semantics still follow the Livy endpoint (batch → strict;
  session → explicit failure result only), so stopped pipeline
  notebooks continue to count as successful runs.

## [2.5.6] - 2026-05-12

### Fixed
- **Stopped notebook sessions no longer counted as Spark failures.** The
  Synapse Studio UI shows interactive notebook sessions that shut down
  cleanly as *Stopped*. Livy reports those with `state=dead` /
  `shutting_down` / `stopped` / `killed` and `result=Uncertain`, which
  v2.5.x was classifying as failed. The classifier now distinguishes
  session kind: for interactive sessions, only an explicit failure
  `result` (`Failed`, `Cancelled`, `FailedTimeout`) counts as a failure;
  any terminal state without an explicit failure is a successful run.
  Batch jobs keep the previous strict semantics.
- **Stopped sessions are no longer dropped from the time-window rollup.**
  When a session has shut down, the Livy SDK sometimes omits the
  `scheduler` block, leaving `submitted_at = None`. The aggregator
  excludes such records from every window, so the Dashboard showed fewer
  runs than Synapse Studio. `_normalize` now falls back to
  `plugin.preparation_started_at` / `plugin.submission_started_at` /
  `livy_info.created_at` / top-level `created_at` for the submission
  timestamp, and to `plugin.cleanup_started_at` /
  `plugin.monitoring_started_at` / `livy_info.deleted_at` for the end
  timestamp.

### Added
- Spark Livy pagination now logs `scanned`, `yielded`, and the server's
  reported `total` per (pool, kind), and emits a warning when fewer runs
  were scanned than the server reports — a quick way to spot a stale
  `SMA_SPARK_RUN_LIMIT` or pagination regression.

## [2.5.5] - 2026-05-12

### Added
- **Spark vCore-hr / CU-hr daily bar chart on the Dashboard.** The
  *Spark execution* section now renders a clustered bar chart binning
  `spark_runs[].submitted_at` by UTC day across the last 28 days,
  plotting blue *vCore-hours per day* alongside orange *Fabric CU-hours*
  (=vCore × 0.5). Days with no activity are pre-seeded so the x-axis is
  continuous; bars carry SVG `<title>` tooltips with the exact daily
  totals, and a footer shows the window total in both units.

## [2.5.4] - 2026-05-11

### Fixed
- **Spark Livy history calls now respect the server-side page-size cap.**
  The Synapse Livy endpoint hard-caps `size` at 20 per page and rejects
  larger requests with HTTP 400 *"Size cannot be more than 20"*. v2.5.0
  shipped with `_MAX_PAGE_SIZE = 200` and an `SMA_SPARK_RUN_PAGE_SIZE`
  default of 100, which caused every workspace to hit the 400 and yield
  zero runs (the `errors=2` symptom on the Dashboard). Both the constant
  and the env-var default are now `20`; the analyzer's existing
  `min(page_size, _MAX_PAGE_SIZE)` clamp prevents user overrides from
  re-introducing the issue.

## [2.5.3] - 2026-05-11

### Fixed
- **Spark execution section now renders a diagnostic panel when Livy
  history collection fails.** Previously, if the analyzer could list
  Spark pools but the per-pool batch / session calls returned 403
  (`bigDataPools/useCompute/action` not granted) or otherwise produced
  zero runs, the Dashboard silently dropped the entire Spark section,
  making it look as if v2.5.2's wiring was broken. The Dashboard now
  shows pools discovered, the Livy collection status (*blocked* vs.
  *empty*), and the verbatim error strings from `spark_pools.json`
  with a direct pointer to the *Synapse Compute Operator* RBAC
  requirement.

## [2.5.2] - 2026-05-11

### Fixed
- **Spark Livy run statistics now appear in the SPA Dashboard.** v2.5.0
  added the analyzer module (`spark_pools.json` with `spark_runs` and per
  pool / per kind `run_stats`) but the React SPA never loaded the file or
  rendered the data, so users saw their Synapse Spark numbers nowhere in
  the UI. The Dashboard now surfaces a *Spark execution (last 7 days)*
  section with totals for runs, success rate, vCore-hours, and estimated
  Fabric CU-hours (using the documented 1 CU = 2 Spark vCores rate, i.e.
  `est_cu_hours_fabric_spark = vcore_hours * 0.5`), plus a per-pool /
  per-kind breakdown table.

### Added
- New SPA types: `SparkPoolsReport`, `SparkPoolRunStats`,
  `SparkRunWindowStats`, `SparkRunRecord` (mirroring the pydantic models
  emitted by `SparkAnalysis.to_dict()`), and a `loadSparkPools()` JSON
  loader.
- The Dashboard's *Recommended SKU* stat-card sub-text now also includes
  Spark's contribution to the daily-CU rollup (e.g. *"+ 0.42 CU/day from
  Spark"*), alongside the existing pipeline integration contribution.

## [2.5.1] - 2026-05-11

### Added
- **Doctor + live-validation now probe Spark Livy permissions.** Both
  `sma doctor` and the SPA's *Validate access (live)* card now issue a
  tiny `get_spark_batch_jobs(size=1)` against the first available Spark
  pool to confirm the SP has the action
  `Microsoft.Synapse/workspaces/bigDataPools/useCompute/action`. A 403
  here surfaces as `Spark Livy (Synapse Compute Operator)` FAIL with the
  verbatim error — telling the operator exactly which Synapse RBAC role
  to grant. Skipped (with a `warn`) when the workspace has no Spark pools.
  `azure.synapse.spark` is also added to the doctor package-import
  manifest.
- **Permissions docs + error-hint coverage for Spark Livy.** QUICKSTART
  and the Configuration user-guide page now call out the **Synapse
  Compute Operator** role explicitly (with the exact RBAC action) as a
  requirement for the spark_pools module's Livy job-history collection,
  and clarify that Synapse Artifact User is NOT sufficient. The
  `format_error` helper now emits a Spark-specific hint when it sees
  `bigDataPools/useCompute` in an `Unauthorized` exception.

### Fixed
- `format_error` no longer over-attaches the Artifact-User hint to Spark
  Livy 403s. The artifacts hint now requires the specific
  `artifacts/read` action substring rather than the generic phrase
  "Synapse RBAC" (which appears in both error families).

## [2.5.0] - 2026-05-11

### Added
- **Spark execution module — Livy job history with Fabric CU projection.**
  The `spark_pools` analyzer now pulls Synapse Spark Livy history per pool
  (interactive notebook **sessions** and scheduled / pipeline-triggered
  **batch jobs**) and rolls it up per pool × kind × window
  (7 / 14 / 28 / 90 days). For each run the analyzer extracts the cluster
  shape from `app_info` (driver vCores + executor vCores × #executors),
  multiplies by wall-clock seconds to get **vCore-seconds**, divides by
  3600 for vCore-hours, and converts to Fabric CU-hours using the
  documented rate **1 CU = 2 Spark vCores ⇒ 1 vCore-second = 0.5
  CU-second**. New env vars: `SMA_SPARK_RUN_HISTORY` (default `1`),
  `SMA_SPARK_RUN_DAYS` (default `90`), `SMA_SPARK_RUN_LIMIT` (`5000`),
  `SMA_SPARK_RUN_PAGE_SIZE` (`100`), `SMA_SPARK_RUN_CONCURRENCY` (`4` —
  parallel per-pool fetches). New dep: `azure-synapse-spark>=0.7`. Stored
  on `SparkAnalysis.spark_runs` (raw per-job records) and
  `SparkAnalysis.run_stats` (per-pool aggregates); surfaced as a "Spark
  execution" section in the Spark pools HTML report.

## [2.4.2] - 2026-05-11

### Added
- **Mapping-dataflow vCore-hours derived from Spark runtime, not billing.**
  The pipelines module now reads each `ExecuteDataFlow` activity's
  `compute.coreCount` / `compute.computeType` from the pipeline
  definition (defaults to **8 cores** when unset) and multiplies it by
  the activity's wall-clock runtime (`output.executionDuration` in
  seconds, falling back to `duration_in_ms / 1000`) to produce true
  vCore-hours per run. Those are then projected to Fabric CU-hours
  using the documented `0.5 CU-h / vCore-h` ratio. Example: 1 driver ×
  4 vCores × 36 s = 144 vCore-seconds = 0.04 vCore-hours = **0.02 CU-h**
  for that single dataflow execution, matching what the
  user-facing dashboard now reports. New `Activity.dataflow_cores` /
  `Activity.dataflow_compute_type` fields surface the captured cluster
  shape per activity.

### Fixed
- **Low-frequency pipelines no longer disappear from the dashboard
  when the global run-history cap is hit.** Previously a single very
  busy pipeline could consume the entire `SMA_PIPELINES_RUN_LIMIT`
  budget (default 5,000 runs / 90 days) and starve every other
  pipeline out of the result set — the dashboard then showed
  `0 runs` and `last_run_at = —` for pipelines that had actually
  executed in the window. The Synapse run-history query is now
  ordered `RunStart DESC` server-side (so truncation drops the
  *oldest* runs, not arbitrary ones), and any pipeline that ends up
  with zero runs after the global pull is back-filled with a
  per-pipeline `PipelineName In (...)` query batched 25 pipelines /
  request, up to 10 runs / pipeline. New env var
  `SMA_PIPELINES_RUN_BACKFILL_PER_PIPELINE` (default `10`) tunes the
  per-pipeline cap.

## [2.4.1] - 2026-05-09

### Fixed
- **Estate overview: total projected Fabric CU now includes pipeline
  integration activity.** The hero "Estimated SKU needed" card and the
  per-workspace `projected_fabric_cu` column previously only reflected
  the dedicated-pool capacity projection (`fabric_mapping.capacity_projection.estimated_cu`),
  so workspaces whose Fabric footprint is dominated by Data Factory
  pipelines were sized too small and disagreed with the per-workspace
  Dashboard which already added the pipeline contribution as
  `+ X CU/day from pipelines`. The estate aggregator now reads
  `pipelines.json` for each workspace's latest run, computes steady-state
  CU/day from `est_cu_hours_from_diu + est_cu_hours_from_orchestration`
  using the same 7-day-window math as the Dashboard, and folds it into
  the per-workspace and estate totals. The Export PDF report picks up
  the corrected value automatically.

## [2.4.0] - 2026-05-08

### Added
- **Export PDF report (control plane).** New **Export PDF report**
  button on the Estate overview toolbar opens a print-friendly
  `/report/print` route in a new tab and auto-triggers the browser's
  print dialog. The report consolidates the estate overview totals,
  workspaces table, and estate-wide top blockers, then emits one
  page per workspace (page-break before each) with readiness /
  T-SQL / recommendations / SKU stat cards, top blockers, the full
  recommendations list, the cost summary (Synapse vs modeled Fabric,
  Δ abs / Δ %), and the runbook. Backed by a new
  `apiGetRunModule<T>(runId, name)` loader helper that hits the
  existing `/api/runs/{id}/modules/{slug}` endpoint, plus a
  print-only stylesheet (A4, hidden topbar/footer/nav, visible pill
  borders for B/W output). Browser print is used so no headless
  Chromium / WeasyPrint dependency is added — pick **Save as PDF**
  in the print dialog.
- **Estate overview tab (control plane).** New default landing page in
  control-plane mode (`sma serve --with-api`) that consolidates *every
  run on disk* into a single estate-wide view. Multi-tenant aware:
  workspaces are grouped by **tenant · subscription · resource
  group**, and both **actual Synapse spend** and **projected Fabric
  spend / CU** are surfaced side-by-side as clearly-labelled,
  comparable columns. Includes hero totals (workspaces, runs,
  tenants, subscriptions, ready/effort/blocked split, avg T-SQL %,
  total CU and both monthly-cost dimensions), an estate readiness
  trend chart, per-workspace sparkline of the last 50 runs (cap is
  configurable via `SMA_ESTATE_MAX_HISTORY`), and an
  estate-deduped *top blockers* table sorted by how many workspaces
  each blocker hits. Backed by a new `EstateIndex` aggregator with
  mtime-based caching and three new endpoints:
  `GET /api/estate`, `GET /api/estate/workspaces/{key}`, and
  `GET /api/estate/export.csv`. `RunMeta` now persists `tenant_id`,
  `subscription_id`, `resource_group` and `workspace_name` so the
  rollup stays accurate across re-runs and multi-tenant scans.
  Dashboard moves from `/` to `/dashboard`; static-mode SPAs are
  unchanged.
- **Run page: smarter defaults.** The **Label** field now pre-fills with
  the configured workspace name (`SYNAPSE_WORKSPACE_NAME`) on first load
  so multi-workspace run histories stay readable; manual edits are
  preserved (`labelEdited` guard). Every module \u2014 including
  `fabric_validation` \u2014 is selected by default; uncheck the modules
  you don't need rather than hunting for the missing ones.
- **Configuration page: Synapse workspace selector.** The static
  *Resource group* + *Workspace name* text fields are replaced with a
  **Discover workspaces** button that hits
  `POST /api/config/validate?live=true` and renders the result as a
  dropdown (name + resource group + location, with the active workspace
  marked `\u2014 current`). Picking a workspace updates the form fields
  in-place; **Save** persists. An *Enter manually\u2026* fallback drops
  back to plain text inputs when the SP can't list workspaces (e.g.
  cross-tenant onboarding).
- **Runs history: per-row Delete button.** Each row now offers a
  **Delete** action with a `window.confirm` prompt. Backed by a new
  `DELETE /api/runs/{id}/data` endpoint that removes the run folder via
  `shutil.rmtree` and clears it from the in-memory event-counter map.
  The endpoint refuses (`409 Conflict`) while the run is `queued` /
  `running`. Deleting the active run clears `#run=<id>` so the next
  load picks a different run.
- **`SMA_STORAGE_INCLUDE_ALL` escape hatch.** The new workspace-scoped
  storage scan (see *Changed*) can be reverted to the legacy
  subscription-wide behaviour by setting `SMA_STORAGE_INCLUDE_ALL=1` in
  `.env`. Useful when a linked-service URL the parser doesn't recognise
  hides a storage account from the inventory.

### Changed
- **Storage inventory is now scoped to the workspace.** The `storage`
  module no longer enumerates every account in the subscription. It
  collects the workspace's default ADLS Gen2 plus any account whose
  host appears in a Synapse linked-service payload (`*.dfs.core.windows.net`,
  `*.blob.core.windows.net`, etc.) and inventories only those accounts.
  This dramatically reduces noise on subscriptions shared with
  non-Synapse workloads.
- **Configuration page: Accessible workspaces table simplified.** The
  redundant **Use this** column has been removed \u2014 the new dropdown
  above the form is the canonical way to switch. The validation card
  now shows only the count.

### Fixed
- **`load_dotenv()` now uses `override=True`.** Long-lived `sma serve`
  processes correctly pick up Configuration edits to `.env` on the next
  run without a manual restart. Previously the in-process environment
  shadowed `.env` updates and the analyzer kept hitting the old
  workspace.

## [2.3.0] - 2026-05-07

### Added
- **Configuration page: "Validate access (live)" button.** In addition to
  the existing field/format checks, the SPA can now exercise real Azure
  control-plane and Synapse data-plane connectivity using the saved
  service principal:
  - **Control plane** — AAD token for `https://management.azure.com/.default`
    and `SynapseManagementClient.workspaces.get` (proves Reader on the
    workspace).
  - **Data plane** — `ArtifactsClient.pipeline.get_pipelines_by_workspace`
    (proves Synapse Artifact User), AAD token for
    `https://database.windows.net/.default`, and `SELECT 1` against the
    serverless SQL endpoint (and the dedicated pool when
    `SYNAPSE_DEDICATED_POOL` is set).
  - Backed by `POST /api/config/validate?live=true`. Each check is
    tagged with a `category` (`Configuration` / `Control plane` /
    `Data plane`) and rendered as grouped sections with the underlying
    error message in the FAIL detail. Connection failures never
    propagate as 5xx; they show up as a red pill with the actual
    exception text so missing role assignments are obvious.
- **Dashboard: Serverless SQL section.** New tiles for
  *Avg daily queries*, *Total data scanned*, *Avg query data size* and
  *Estimated cost*, plus an inline-SVG dual-axis clustered bar chart
  over the last 7 days (queries vs MB scanned). Reads the existing
  `serverless_pools.json`. When `daily_usage` is empty (typical when
  the SP lacks `VIEW SERVER STATE` and `sys.dm_exec_requests_history`
  only returns the caller's own history) the section still renders the
  database / external-table counts and a permissions hint.

### Fixed
- **Serverless `data_processed.sql` filter.** Aggregation now filters
  `status = 'Completed'` instead of `'Succeeded'` — the latter is not
  a valid value of `sys.dm_exec_requests_history.status` and silently
  yielded zero rows, so daily-usage and cost-estimate were always 0
  TB / $0 even when queries had run.

## [2.2.2] - 2026-05-06

### Removed
- **Reverted the in-app Fabric validation page** added in 2.2.1. The
  `fabric_validation` module is still considered **experimental** —
  schema, CLI flags and check coverage may change without notice — so
  it should not have been promoted to a first-class SPA page yet. The
  module continues to run when explicitly included; consume the
  `fabric_validation.json` / Markdown / HTML output directly.

### Documentation
- [`docs/user-guide/09-run-page.md`](docs/user-guide/09-run-page.md):
  the `fabric_validation` row in the modules table is now flagged
  **Experimental**, with a one-line summary of what the checks cover
  and a note that the SPA does not surface this module.

## [2.2.1] - 2026-05-06

### Added
- **In-app Fabric validation page** (`/fabric-validation`). Renders the
  existing `fabric_validation.json` output (post-migration validation
  vs. a target Fabric warehouse): KPI tiles (pass rate, mismatches,
  missing, errors), shared status + text filter, collection-error
  list, and four tables for object-count, row-count, collation, and
  T-SQL surface checks. Each row is colour-coded via a `StatusPill`
  (`match`/`resolved` green; `mismatch`/`still_present`/`missing`/
  `object_missing`/`error` red; `extra` amber). Visible in both static
  and control-plane modes; no backend changes.

## [2.2.0] - 2026-05-06

### Added
- **In-app Cost / Governance / Security pages.** The SPA now renders
  the existing `cost.json`, `governance.json` and `security.json`
  outputs that previously were only viewable as standalone per-module
  HTML reports.
  - **Cost** (`/cost`): collection-status banner (covers `sdk_missing`,
    `live_disabled`, `empty_window`, `error`), header KPIs (months
    observed, window total, average monthly, finding count), Fabric
    capacity comparison card (Synapse avg vs estimated Fabric SKU,
    `delta_pct`), severity-sorted findings table with text + severity
    filters, monthly totals with month-over-month deltas, breakdown by
    resource kind (cost + share of total) and top 25 resources by
    spend.
  - **Governance** (`/governance`): KPI strip (role assignments,
    privileged role count, managed private endpoints with pending /
    not-approved count, customer-managed keys enabled / configured,
    findings), severity-sorted findings table, role assignments table
    with text filter and a "privileged only" toggle (`Owner`,
    `Contributor`, `User Access Administrator`, `Role Based Access
    Control Administrator`), managed private endpoints table with
    approval-state pills, customer-managed key cards, Purview block.
  - **Security** (`/security`): KPI strip (findings, firewall rules
    incl. allow-all count, pool TDE coverage, inline-secret count),
    workspace settings card (AAD-only, public network access, minimum
    TLS, encryption at rest, managed VNet), severity-sorted findings
    table, firewall rules table with red `allow-all` / amber
    `allow-azure` pills, dedicated pool TDE table, credentials
    inventory with red `inline` / green `vaulted` pills, AAD admins
    list.
  - All three pages are visible in both static and control-plane
    modes; no backend or schema changes were needed (the
    `/api/runs/<id>/modules/<module>` endpoint already serves these
    JSON files).

### Fixed
- **Cost module: 429 throttles from Cost Management no longer abort the
  module.** `CostClient.fetch_monthly_breakdown_with_status` now retries
  `query.usage` with bounded exponential backoff + full jitter on 429
  and 5xx responses, honouring a `Retry-After` header when the service
  sends one. Defaults: 5 attempts, 2 s base, 60 s cap. Tunable via
  `SMA_COST_RETRY_MAX`, `SMA_COST_RETRY_BASE_MS`, `SMA_COST_RETRY_CAP_MS`.
  After the final failed attempt the original exception is still
  surfaced to the analyzer (which already converts it into a
  `cost.collection_error` finding), so behaviour for genuinely broken
  scopes / credentials is unchanged.

### Changed
- **Dashboard / Pipeline activity: "Data moved" tile now shows total
  with daily-average as subtext.** The previous "Daily data movement"
  headline divided the rolling-window total by the window length,
  which read as continuous flow even when the only activity in the
  window was a single burst (e.g. one 53 MB run \u2192 "8 MB / day").
  The headline is now `total_moved_mb` (e.g. "53 MB"), and the
  subtext shows `~ X MB / day avg \u00b7 N pipelines with data activity`
  (or `no data-movement activity observed` when no pipeline reported
  bytes). Also tightened MB rounding so values under 10 MB show one
  decimal (7.57 \u2192 "7.6 MB" instead of "8 MB").

### Fixed
- **In-app Help: links to repo-root docs (`README.md`, `QUICKSTART.md`,
  `CHANGELOG.md`, `SECURITY.md`) no longer 404.** Those four files are
  now bundled into the SPA at build time as hidden chapters
  (`/help/repo-readme`, `/help/repo-quickstart`,
  `/help/repo-changelog`, `/help/repo-security`) and the markdown link
  rewriter maps `../../FILE.md` (and any number of `../`) to the
  matching in-app route. The files stay where they are at the repo
  root; nothing was moved. Hidden chapters are reachable by direct
  link but do not appear in the Help sidebar or the prev/next pager.

## [2.1.0] - 2026-05-06

**Minor release** — the user guide is now reachable from inside the SPA.
No breaking changes. Existing CLI / JSON / CSV / Markdown / HTML
deliverables are unchanged.

### Added
- **In-app user guide.** New `Help` page at `/help` (and `/help/<slug>`)
  renders all 16 chapters of [`docs/user-guide/`](docs/user-guide/README.md)
  inside the SPA. The chapters are bundled at build time via Vite's
  `?raw` import — no API surface added, works identically in static
  and control-plane mode, and a single source of truth (the same
  Markdown files GitHub renders in the repo). Sidebar groups chapters
  by section (Orientation / Read-only pages / Control-plane pages /
  Reference) with prev / next pager links at the bottom of each chapter.
- **"Help on this page" link** on every page (Dashboard, Code objects,
  Recommendations, Runbook, Delta, Run, Runs history, Diff,
  Configuration). A small `?` icon next to the page title links to the
  matching user-guide chapter; deep-linking to `#anchors` inside a
  chapter is supported.
- Bundle adds `react-markdown`, `remark-gfm` and `rehype-slug`. Pulled
  out into a separate `markdown-*.js` chunk so the rest of the SPA is
  not penalised when the user never opens Help.
- New SPA test (`web/src/__tests__/help.smoke.test.ts`) asserts every
  chapter loads with a non-empty body and starts with a level-1
  heading.

## [2.0.1] - 2026-05-05

**Documentation patch.** No code changes.

- New in-repo **User guide** at [`docs/user-guide/`](docs/user-guide/README.md):
  15 chapters covering orientation (getting started, static vs control-plane
  modes, the run picker), every SPA page (Dashboard, Code objects,
  Recommendations, Runbook, Delta, Run, Runs history, Diff, Configuration),
  and reference material (troubleshooting matrix, security & deployment,
  FAQ).
- README and QUICKSTART now link the user guide from their control-plane
  sections.

## [2.0.0] - 2026-05-05

**Major release** — promotes the browser-driven control plane from an
opt-in preview to a first-class surface alongside the CLI. The `sma serve
--with-api` server, the React SPA, the run repository, the live progress
stream and the workspace-vitals Dashboard are all now part of the
supported product. The CLI, JSON / CSV / Markdown / HTML deliverables and
module contracts are unchanged from 1.2.x — every report file in
`output/` keeps the same shape — so existing automation continues to work
without modification.

**Highlights**

- One-shot Windows bootstrapper (`quickstart.ps1`) that installs every
  optional extra, builds the React SPA and launches the control plane.
- Browser control plane: Configuration / Run / Runs / Diff pages plus a
  workspace-level Dashboard with readiness, T-SQL surface, storage and
  pipeline-activity panels.
- Hardened SPA: deep-link / refresh-safe routing, run-id persistence
  across tabs, fixed Diff endpoint, faster and partial-failure-tolerant
  pipelines run-history collection.

**Upgrade notes**

- `pip install -e ".[web]"` (or rerun `quickstart.ps1`) is required to
  pick up the FastAPI / SSE dependencies for `sma serve --with-api`.
- Rebuild the SPA bundle (`cd web; npm install; npm run build`) so
  `web/dist/` matches the new types.
- Run files written by 1.2.x remain readable; no migration required.

### Added
- **Dashboard: storage statistics and pipeline daily activity.** The web
  SPA's main Dashboard now renders two extra sections sourced from
  `storage.json` and `pipelines.json`:
  - *Storage* — stat cards for dedicated-pool data / index space, ADLS
    used capacity, storage-account count, plus a per-pool table with row
    counts, data / index / reserved space and `% of max`.
  - *Pipeline activity (last 7 days)* — stat cards for daily run rate,
    success rate, daily data movement and the sampling window, plus a
    top-10 table with per-pipeline runs / day, success / failure breakdown,
    average data moved per run, total data moved and last run
    timestamp / status. Daily rates are derived from the 7-day rolling
    window (`run_count / 7`). Both sections render only when the
    underlying JSON is present, so deployments without those modules see
    the original Dashboard unchanged.
- **`quickstart.ps1` now bootstraps a fully working environment.** All
  optional pip extras (`dev`, `cost`, `web`) are installed unconditionally,
  the React SPA in `web/` is built by default (`npm install` + `npm run
  build`), and on a successful `sma doctor --offline` the script
  auto-launches `sma serve --with-api --static-dir web\dist` so the
  control plane is reachable in the browser at the end of bootstrap.
  Three new switches opt out: `-SkipDoctor`, `-SkipWebBuild`, `-NoServe`.
- **Web control plane (opt-in).** New `sma serve --with-api` boots a local
  FastAPI backend mounted at `/api/*` plus a SPA with four new pages:
  Configuration (read/write `.env`), Run (start an analyzer run with a
  module checklist), Runs (history), Diff (compare any two runs). The
  loopback-only HTTP server has no authentication; pass
  `--i-know-this-is-not-auth` to bind a non-loopback host. Live progress
  uses Server-Sent Events at `GET /api/runs/<id>/events`. Install via
  `pip install -e '.[web]'`. Static "deliverable" mode (the existing
  `sma serve` without `--with-api`) is unchanged.

### Fixed
- **SPA routing: deep links and tab navigation no longer 404 / blank.**
  `_SafeStaticFiles` now serves `index.html` for unknown non-asset paths
  so React Router's client-side routes survive a hard reload, and the
  selected run id is mirrored into `sessionStorage` (in addition to the
  URL hash) so navigating between tabs after picking a run keeps the
  module pages populated.
- **`GET /api/runs/<a>/diff/<b>` returned HTTP 500.** The endpoint
  attempted to JSON-encode `RunManifestEntry` dataclasses directly. The
  response now goes through `dataclasses.asdict` with ISO datetime
  serialization so the Diff page renders as expected.
- **Pipelines run-history collection: latency and partial-failure
  resilience.** Per-pipeline run / activity-run queries are batched and
  short-circuited when the workspace returns empty windows, and a single
  pipeline that errors no longer aborts the entire history pull —
  the failure is captured in the report's `errors[]` and other
  pipelines continue to be sampled.

### Fixed
- **Dedicated SQL pool storage reported `0 tables / 0 rows` and table
  sizes / row counts were missing from `tables.csv`.** Both the
  `storage` module's `pool_size.sql` and the `dedicated_pools`
  module's `tables.sql` joined `sys.dm_pdw_nodes_db_partition_stats`
  to `sys.tables` directly on `object_id`. In Synapse Dedicated SQL
  Pool the DMV's `object_id` is the *node-local* physical table id,
  not the user-database `sys.tables.object_id`, so the join collapsed
  to zero matches and the analyzer reported empty storage / row
  counts even on full pools. Both queries now route through
  `sys.pdw_nodes_tables` -> `sys.pdw_table_mappings` -> `sys.tables`,
  the documented mapping path. Verified end-to-end against
  `testpool001` (6 user tables, 9.4M rows, 6.28 GB reserved).

### Documentation
- **`VIEW DEFINITION` is now called out as a required SQL grant** on
  dedicated pools (and serverless) in [README.md](README.md) and
  [QUICKSTART.md](QUICKSTART.md). Without it, Synapse silently filters
  procedures and user-defined functions out of `sys.objects` /
  `sys.sql_modules` for the analyzer's principal, producing
  views-only `code_objects` reports. Added a troubleshooting row
  ("`code_objects` contains only views") that points to the missing
  grant.

### Fixed
- **`code_objects` only contained views, not procedures or functions.**
  The dedicated_pools `code_objects.sql` query drove from
  `sys.sql_modules` with an INNER JOIN, which silently dropped any
  procedure or function whose module body was unavailable (encrypted
  definition, restricted permission, or Synapse catalog edge cases),
  collapsing the inventory to "views only" in some tenants. The query
  now drives FROM `sys.objects` and LEFT JOINs `sys.sql_modules`, so
  procedures and functions are surfaced even when their body is hidden.
  `definition_length` becomes `NULL` instead of erroring when the body
  is missing. The `o.type` filter is RTRIM-ed for safety. Added a
  per-type INFO log line in the collector so a future regression is
  visible at runtime, plus a regression test pinning the join shape and
  verifying mixed P / V / FN / IF object types flow through.

### Added
- **`sma serve` CLI command.** Thin wrapper around `http.server` so
  analysts can browse `output_dir` (HTML reports + the optional `webui/`
  SPA) without running `python -m http.server` manually. Loopback-only
  by default (`--host 0.0.0.0` to expose), opens the browser at
  `webui/index.html` when present and falls back to the analyzer's
  `index.html` otherwise. Read-only.
- **Index banner for the web SPA.** When `webui/index.html` is present
  in the output directory, the analyzer-emitted `index.html` now
  renders an "Open web UI" banner above the per-module cards, so the
  static deliverable points analysts at the interactive view without
  hiding the per-module HTML reports they may still need.
- **Vitest smoke test for the SPA.** New `web/src/__tests__/fixtures.smoke.test.ts`
  loads committed fixtures under `tests/fixtures/web/` and asserts the
  documented top-level fields exist with the right primitive types.
  Closes the Phase 2 DoD item in `web/PLAN.md`.
- **GitHub Actions CI workflow.** New `.github/workflows/ci.yml` runs
  ruff + pytest for Python and `npm install && npm run typecheck && npm test && npm run build`
  for the web SPA, uploading `web/dist/` as a workflow artefact.
- **`sma export-schema` CLI command.** Emits the Pydantic JSON Schema
  for each module's top-level model (one `<module>.schema.json` per
  module, plus `x-sma-version` / `x-sma-module` stamps) into
  `./schemas/` (or `--out-dir <path>`). Lets the React SPA in `web/`
  regenerate `web/src/types.ts` via `json-schema-to-typescript` /
  `quicktype` instead of hand-maintaining the type contract. The
  command needs no Azure credentials.
- **`sma analyze-all --with-webui` flag.** After a successful run,
  copies the prebuilt static SPA from `web/dist/` (override with
  `--webui-dist <path>`) into `<output_dir>/webui/`, alongside copies
  of the analyzer's JSON outputs so the SPA can `fetch("./<module>.json")`
  out of the box. Missing `web/dist/` is a non-fatal warning. Closes
  the Phase 2 item tracked in `web/PLAN.md`.

### Changed
- **`analyze-all --with-webui` runs the SPA copy before rebuilding the
  index.** This ensures the analyzer's `index.html` picks up the SPA
  banner on the same run that installs the SPA.

### Changed
- **Web SPA dev mode simplified.** Vite now serves `$SMA_DATA_DIR`
  (default `../output`) as its `publicDir` during `npm run dev`, so the
  same `fetch("./fabric_mapping.json")` works in dev and prod. The
  unused `/data/*` proxy was removed. The loader now warns on missing
  modules instead of swallowing errors silently and caches each
  module's promise in memory so navigation between pages doesn't
  re-fetch JSON.

## [1.2.1] - 2026-05-01

### Added
- **SQL-plane analysis for stored procedures and functions.** The
  `dedicated_pools` collector now captures per-object metadata (create/modify
  date, line count, definition length, ANSI-NULLS / quoted-identifier flags)
  plus a parameter signature list (`sys.parameters`) for every procedure and
  function. Each `CodeObject` is classified as **`compatible`** /
  **`needs_review`** / **`incompatible`** based on its T-SQL surface gaps
  (blocker -> incompatible, warning -> needs_review, otherwise compatible),
  and a per-pool `code_object_summary` rolls counts up by object type and
  compatibility. The dedicated-pool markdown + HTML reports gain a
  "Code objects (SQL plane)" section with object-type breakdown,
  compatibility pills, parameter drill-downs, and a rule-rollup table.
  `fabric_mapping`'s `ReadinessSummary` adds `tsql_compatibility_pct`,
  `tsql_objects_total`, `tsql_objects_incompatible`, and
  `tsql_objects_needs_review` so the executive summary now shows
  "% T-SQL compatible" alongside the readiness score. Test suite grows to
  **195 tests** (+11 covering the classifier, summary rollup, and
  fabric_mapping integration).

### Changed
- **Lint baseline cleaned.** Removed unused imports, collapsed
  semicolon-separated statements in
  `dedicated_pools/distribution_advisor.py`, and hoisted the late
  `pydantic` import in `monitoring/reporting.py` to the top of the file.
  `ruff check src tests` is now clean.
- **Docs alignment.** Clarified that the `governance` module performs
  Purview **detection only** (not lineage extraction), and that
  `fabric_validation` remains a v0 preview opt-in via
  `--include fabric_validation`.

## [1.2.0] - 2026-04-29

### Summary
- **Mid-term modules promoted to production.** `governance`, `security`, `cost`,
  and **incremental / delta runs** all graduate from v0 scaffolding to v1 with
  full rules engines, severity-tagged findings, per-module HTML reports, and
  documented data-source / RBAC requirements. They remain opt-in via
  `analyze-all --include <module>` (or the dedicated `sma analyze-<module>`
  subcommand) but are now considered first-class capabilities alongside the
  seven core modules.
- **Cost module data-source dependency documented.** Required role:
  **Cost Management Reader** at subscription or workspace-RG scope. Required
  optional Python extra: `pip install -e ".[cost]"` (pulls in
  `azure-mgmt-costmanagement>=4.0`). When either is missing, the analyzer
  still runs and surfaces specific status findings
  (`cost.sdk_missing` / `cost.collection_error` / `cost.live_disabled`)
  instead of silently emitting empty data. New env vars: `SMA_COST_MONTHS`,
  `SMA_COST_DISABLE_LIVE`.
- **Fabric-side validation moved to long-term roadmap.** The post-migration
  Fabric-warehouse runner is no longer tracked under mid-term — it requires
  Fabric-side connectivity / permissions design that's outside the scope of
  this release.

### Added
- **Incremental / delta runs v1.** Promoted from v0 scaffolding. `analyze-all`
  now auto-emits `run_manifest.json`, `run_delta.md`, **`run_delta.html`**, and
  **`run_delta.json`** (machine-readable). Each artifact entry now includes a
  top-level `record_count` peek (findings / rows / pools / role_assignments /
  pipelines / notebooks / spark_jobs / external_tables / firewall_rules /
  credentials) so the delta surfaces +N findings or -N rows alongside the
  SHA / size changes. New `peek_record_count` helper, new `diff_summary`
  returning `{added, removed, changed, unchanged, total}`, sorted HTML
  artifacts table (changed → added → removed → unchanged → name) with a
  5-stat grid, and an `index.html` linkout for the new `run_delta` card.
  `sma run-delta` emits all four files; failures during manifest / delta
  emission remain non-fatal (warning + `partial_failure`).
- **`cost` module v1.** Promoted from v0 scaffolding with a pure-Python rules
  engine, per-resource aggregation, and a per-module HTML report. New finding
  ids: `cost.no_data` (info, when Cost Management returned no rows),
  `cost.fabric.savings` (info, when the Fabric SKU estimate is ≥10% cheaper
  than the Synapse average), `cost.fabric.increase` (medium, when Fabric is
  ≥10% more expensive — points at CU sizing / reservation pricing),
  `cost.concentration.dedicated_pool` and `cost.concentration.spark_pool`
  (info, when one resource kind exceeds 70% of total spend), and
  `cost.month_over_month.spike` (medium, when a month rises ≥25% over the
  prior). New `by_resource_name` aggregation and CSV (`cost_by_resource_kind.csv`,
  `cost_findings.csv`). Markdown gains a Findings section; HTML report
  contains a 6-stat grid (rows, months, kinds, findings, Synapse avg/mo,
  Fabric SKU), severity-sorted findings table, monthly-totals table with
  ±25% MoM pills, by-kind / by-resource breakdowns, and a Fabric comparison
  card. `sma analyze-cost` and `analyze-all --include cost` emit HTML.
- **`governance` module v1.** Promoted from v0 scaffolding to a fully-built
  module with a pure-Python rules engine, severity-tagged findings, role-name
  resolution against `Microsoft.Authorization/roleDefinitions`, and a per-module
  HTML report. New finding ids: `gov.rbac.subscription_privileged` (high),
  `gov.rbac.workspace_privileged` (medium), `gov.mpe.not_approved` and
  `gov.mpe.provisioning_failed` (medium), `gov.cmk.not_configured` (info),
  `gov.cmk.disabled` (medium), `gov.purview.not_configured` (info). Privileged
  built-in roles are detected by name (Owner, Contributor, User Access
  Administrator, Role Based Access Control Administrator). RBAC assignments
  are deduplicated across scopes by assignment id. `sma analyze-governance`
  now emits HTML alongside JSON / CSV / Markdown, and `analyze-all --include
  governance` picks up the HTML automatically.
- **`security` module v1.** Promoted from v0 scaffolding with three new live
  collectors: per-pool **Transparent Data Encryption** state
  (`sql_pool_transparent_data_encryptions.get`), workspace **AAD/SQL
  administrators**, and **linked-service payloads** pulled directly from the
  Synapse Artifacts plane (so credential classification no longer piggy-backs
  on `pipelines.json`). New finding ids: `sec.workspace.no_aad_admin` (high),
  `sec.workspace.encryption_platform_managed` (info),
  `sec.workspace.no_managed_vnet` (medium, only when public network access is
  enabled), `sec.pool.tde_disabled` (high, per dedicated pool), and
  `sec.credentials.inline_secret` (high) when `password` / `accountKey` /
  `secret` / `sasToken` literals or nested `{"type": "SecureString"}` payloads
  appear in a linked-service definition (Key Vault references are still
  treated as safe). New per-module HTML report (severity-sorted findings,
  workspace-settings card, firewall table, pool-TDE table, credential
  rollup with inline-secret column). Markdown gains AAD-admin and pool-TDE
  sections; CSV gains `security_pool_tde.csv`. `sma analyze-security` and
  `analyze-all --include security` emit HTML.
- 32 new unit tests (173 total, up from 141) covering the governance rules
  engine + role-name caching, the security TDE / AAD / managed-VNet /
  encryption / inline-secret rules, inline-secret detection (literal vs.
  `SecureString` vs. `AzureKeyVaultSecret` reference), the cost rules
  (Fabric savings / increase / no-data / concentration / month-over-month
  spike) + by-resource aggregation, and HTML round-trips for all three
  modules.

## [1.1.0] - 2026-04-28

### Added
- **Mid-term roadmap scaffolding (v0).** Five new opt-in capabilities, all
  fail-soft and gated behind explicit CLI flags / `analyze-all --include`:
  - `governance` module — workspace + resource-level RBAC, managed private
    endpoints, customer-managed keys, and Microsoft Purview lineage capture.
  - `security` module — firewall rules, AAD-only / TLS / public-network
    settings, and a linked-service credential inventory (types only, never
    secret values), plus a pure-Python rules engine producing severity-tagged
    findings (`sec.firewall.allow_all`, `sec.workspace.tls_below_12`, …).
  - `cost` module — month-over-month consumption pull from Microsoft Cost
    Management aggregated by resource kind, paired with the `fabric_mapping`
    capacity projection for a side-by-side TCO delta vs. Fabric SKU pricing.
  - `fabric_validation` module — optional post-migration runner that connects
    to a target Fabric Warehouse and diffs object counts, row counts,
    collation, and T-SQL surface-gap resolution against the prior
    `dedicated_pools.json`.
  - **Incremental / delta runs** — `analyze-all` now writes a
    `run_manifest.json` (SHA-256 per artifact + version + workspace metadata)
    and a `run_delta.md` comparing against the previous manifest. New
    `sma run-delta` subcommand can be invoked standalone.
- New CLI subcommands: `sma analyze-governance`, `sma analyze-security`,
  `sma analyze-cost` (with `--months N`), `sma validate-fabric`,
  `sma run-delta`. Mid-term modules are opt-in via
  `sma analyze-all --include governance --include security …`.
- 32 new unit tests (141 total) covering rules, scope/resource classification,
  diff helpers, manifest hashing, and Fabric TCO comparison math.

## [1.0.1] - 2026-04-28

### Added
- **Pipeline run-history statistics** in the `pipelines` module. Each pipeline
  now reports rolling 7 / 14 / 28 / 90-day windows with execution counts,
  success / failure counts, success rate, average duration, and p95 duration.
- **Per-run data-movement metrics**. For pipelines that statically contain a
  `Copy`, `ExecuteDataFlow`, or `Lookup` activity, the analyzer fetches activity
  runs and surfaces `avg_data_moved_mb_per_run` and `total_data_moved_mb` per
  window — derived from `dataRead` / `dataWritten` (Copy) and
  `runStatus.metrics[*].bytes` (Dataflow).
- New CSV outputs:
  - `pipeline_run_stats.csv` — one row per `(pipeline, window)`
  - `pipeline_run_summary.csv` — one row per pipeline using the 28-day window as
    the headline
- New section in the pipelines HTML report (TOC entry **Runtime statistics**,
  headline tiles, success-rate pills color-coded ≥99% / 95–99% / <95%) and an
  equivalent table in the markdown report.
- New CLI flags on `sma analyze-pipelines`:
  - `--since <N>d` — narrows the run-history window for the current run
  - `--no-run-history` — skips the run-history fetch entirely
- New environment variables to tune run-history collection:
  - `SMA_PIPELINES_RUN_HISTORY` (default `1`)
  - `SMA_PIPELINES_RUN_DAYS` (default `90`)
  - `SMA_PIPELINES_RUN_LIMIT` (default `5000`; sets `truncated=true` when hit)
  - `SMA_PIPELINES_ACTIVITY_RUNS` (default `1`)
- New `fabric_mapping` recommendations driven by run history:
  - `pl.runs.idle` — pipelines with no runs in the observed window
  - `pl.runs.low_success.<pipeline>` — pipelines below 95% success in the 28-day
    window (with at least 5 terminal runs)
  - `pl.runs.heavy_data_movement` — pipelines averaging ≥ 1 GB / run
- `DEPENDENCIES.md` — third-party dependency inventory, linked from `README.md`.
- 17 new unit tests across `tests/test_pipelines_run_stats.py` and
  `tests/test_pipelines_run_history_rules.py` (109 tests total, up from 92).

### Changed
- `pipelines.json` schema now includes a `run_history` object (nullable when
  collection is skipped or fails). The `fabric_mapping` aggregator consumes it
  automatically.
- `README.md` and `QUICKSTART.md` updated to document the new statistics, CLI
  flags, env vars, and dependency inventory link.

### Fixed
- `SECURITY.md`: replaced an invalid GitHub Security Advisories URL.

### Required permissions
- The new run-history collection uses the existing **Synapse Artifact User**
  workspace role already required by `analyze-pipelines`. No additional
  permissions are needed.

## [1.0.0] - Initial release

- Initial public release: dedicated pools, serverless pools, spark pools,
  pipelines, monitoring, storage, and fabric_mapping modules with JSON / CSV /
  Markdown / HTML reporting.
