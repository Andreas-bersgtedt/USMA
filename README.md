# Unified Solution Migration Analyzer

Python tooling that inventories and analyzes **data integration estates** to assess readiness for migration to **Microsoft Fabric**. The original target was Azure Synapse Analytics (dedicated/serverless SQL, Spark, pipelines); the same engine now also analyzes **Azure Data Factory** factories, **Azure Databricks** workspaces, **Google BigQuery** projects, and **Snowflake** accounts side-by-side in the same run, with **SAP BW** on the roadmap.

> **Product name:** **USMA — Unified Solution Migration Analyzer** (formerly *Synapse Migration Analyzer*). The CLI exposes both entry points during the deprecation window:
>
> - `sma …` — original entry point (kept until the package directory rename ships)
> - `usma …` — new entry point, identical behavior
>
> Examples in this README still use `sma` for backward compatibility.

> **Supported sources today**
>
> - 🟢 **Synapse workspace** — full module coverage (dedicated SQL, serverless SQL, Spark, pipelines, monitoring, storage, governance, security, cost, fabric_mapping, fabric_validation).
> - 🟢 **Azure Data Factory** — `pipelines`, `fabric_mapping` and `cost` modules (factory discovery + REPLACE UX, ADF-aware recommendations / runbook hints, ADF run-history client). See [docs/user-guide/20-data-factory.md](docs/user-guide/20-data-factory.md).
> - 🟡 **Databricks** — *alpha.* `databricks_workflows` + `fabric_mapping` + `cost` modules with workspace discovery, Configuration radio, and Run page wiring. Run history, Unity Catalog, and DLT pipelines are deferred. See [docs/user-guide/21-databricks.md](docs/user-guide/21-databricks.md).
> - 🟡 **Google BigQuery** — *alpha.* `bigquery_workloads` + `cost` + `fabric_mapping` modules with ADC-only project discovery, Dashboard surface (headline tiles, daily / last-24h bar charts, GCS storage view, top-tables, sqlglot query features), GCP cost via billing-export tables, and Estate-Overview CU rollup. See [docs/user-guide/22-bigquery.md](docs/user-guide/22-bigquery.md).
> - 🟡 **Snowflake** — *alpha.* `snowflake_workloads` + `cost` + `fabric_mapping` modules with OAuth-refresh-token authentication, account-as-scope discovery, multi-cloud bucketing via `CURRENT_REGION()`, Snowflake cost via `ACCOUNT_USAGE.METERING_HISTORY` + `USAGE_IN_CURRENCY_DAILY`, and Estate-Overview CU rollup. Dashboard surface lands in a follow-up slice. See [docs/user-guide/23-snowflake.md](docs/user-guide/23-snowflake.md).
> - ⚪ **SAP BW**, **SSIS** — optional pip extras registered today; analyzers land in subsequent phases.
>
> A single run can fan out across **multiple scopes** (e.g. one Synapse workspace + one ADF factory). Each scope writes its own per-scope artefacts and the Dashboard / Recommendations / Runbook / Estate Overview UI surfaces source-aware labels (e.g. "Azure Data Factory pipelines" vs "Synapse pipelines", "(factory)" vs "(workspace)") and filter pills.

> Modules:
> - `dedicated_pools` — **Synapse only.** Dedicated SQL pool inventory, schema/table/index/usage/security/workload-management, T-SQL code-object capture, column-level collation audit, materialized-view inventory, statistics-freshness report, column stats, distribution-key advisor (skew + filter-selectivity heuristics), and a per-object "T-SQL surface gaps" rollup with stable code-object ids
> - `serverless_pools` — **Synapse only.** Built-in serverless SQL pool, databases, external data sources & external tables, top queries, daily data-scanned, cost estimate
> - `spark_pools` — **Synapse only.** Apache Spark pool inventory & configuration, plus notebook and Spark-job-definition inventory
> - `pipelines` — **Cross-source (Synapse + ADF).** Pipelines, linked services, datasets, triggers, integration runtimes, with activity-level Fabric-compatibility classification, plus rolling 7/14/28/90-day **run-history statistics** (executions, success rate, avg duration, avg MB moved per Copy/Dataflow run, plus runtime-derived vCore-hours per Mapping-Dataflow run from `compute.coreCount` × `executionDuration`, projected to Fabric CU-hours)
> - `databricks_workflows` — **Databricks only** (alpha). Workflows (jobs), tasks (Notebook / SQL / JAR / Python wheel / dbt / Run Job), schedules, job clusters, and **SQL warehouses** (inventory, query history, REST CPU-seconds, Unity Catalog `system.billing` DBU usage, plus a per-warehouse Fabric F-SKU mapping that rolls up into the Dashboard headline and the Estate Overview), normalized into the same wire format that feeds `fabric_mapping`.
> - `bigquery_workloads` — **BigQuery only** (alpha). Datasets, tables (`TABLE` / `VIEW` / `MATERIALIZED_VIEW` / `EXTERNAL` / `SNAPSHOT`), routines (UDFs / stored procs / table functions), scheduled queries (Data Transfer Service), and completed jobs from Cloud Logging audit entries — with `total_slot_ms` rolled up into 7/14/28/90-day windows and converted to Fabric Spark CU-hours via the documented slot-ratio caveat.
> - `snowflake_workloads` — **Snowflake only** (alpha). Warehouses (with auto-suspend / scaling policy), databases, schemas, tables (`TABLE` / `VIEW` / `MATERIALIZED_VIEW` / `EXTERNAL` / `ICEBERG` / `DYNAMIC`), routines (SQL / Python / JS / Java / Scala), stages, streams, tasks, pipes, and completed jobs from `ACCOUNT_USAGE.QUERY_HISTORY` — with credits rolled up into 7/14/28/90-day windows and converted to Fabric warehouse CU-hours via the documented credit-to-vCore caveat.
> - `monitoring` — **Synapse only.** Historical Azure Monitor metrics for dedicated SQL pools (DWU, queries, connections)
> - `storage` — **Synapse only.** ADLS / Storage account inventory scoped to the workspace (default ADLS Gen2 + linked-service references; set `SMA_STORAGE_INCLUDE_ALL=1` to scan every account in the subscription), Azure Monitor capacity metrics (UsedCapacity, BlobCapacity), and dedicated SQL pool size in MB / GB
> - `fabric_mapping` — **Cross-source.** Aggregates the above and produces Fabric migration recommendations (collation, T-SQL surface, activity gaps, sizing hints). Recommendation text + runbook rollback hints are source-aware ("Azure Data Factory pipeline" vs "Synapse pipeline" vs "Databricks workflow").
>
> Opt-in modules (enable with `analyze-all --include <name>` or the dedicated `sma analyze-<name>` subcommand):
> - `governance` — **Synapse only.** Workspace + resource RBAC, managed private endpoints, customer-managed keys, Microsoft Purview detection, with severity-tagged findings
> - `security` — **Synapse only.** Firewall rules, AAD-only / TLS / public network access, per-pool TDE, AAD admins, linked-service credential inventory with inline-secret detection
> - `cost` — **Cross-source.** Microsoft Cost Management consumption (month-over-month, by resource kind), paired with the `fabric_mapping` CU projection for a side-by-side TCO delta (requires `pip install -e ".[cost]"` and Cost Management Reader)
> - `fabric_validation` — *experimental* post-migration runner that diffs object counts, row counts, collation and T-SQL surface gap resolution against a target Fabric Warehouse

📘 **New here?** See the module-based [QUICKSTART.md](QUICKSTART.md).

💻 **Want a browser UI?** `sma serve --with-api` boots a local FastAPI control plane plus a SPA so analysts can edit `.env`, kick off runs, watch live progress, and compare runs without touching the CLI. See [Web control plane (opt-in)](#web-control-plane-opt-in) below. For an end-to-end UI process overview (Mermaid diagram), see [docs/user-guide/01-getting-started.md → Process overview](docs/user-guide/01-getting-started.md#process-overview).

## Architecture

```
src/usma/
├── cli.py                       # `sma` CLI (Click)
├── config.py                    # .env / env-var loader
├── auth.py                      # azure-identity ClientSecretCredential + SQL access tokens
├── modules/
│   ├── dedicated_pools/         # ARM + DMV-driven analysis
│   ├── serverless_pools/        # ARM + serverless DMVs
│   ├── spark_pools/             # ARM (big_data_pools) + Synapse Artifacts (notebooks, SJDs)
│   ├── pipelines/               # azure-synapse-artifacts SDK + Fabric-compat classifier
│   ├── monitoring/              # azure-mgmt-monitor — dedicated-pool metrics
│   ├── storage/                 # azure-mgmt-storage + Azure Monitor capacity + DMV pool sizing
│   ├── governance/              # RBAC + managed private endpoints + CMK + Purview detection
│   ├── security/                # firewall + AAD + TDE + linked-service credentials
│   ├── cost/                    # Cost Management consumption + Fabric TCO delta (opt-in extra)
│   ├── fabric_validation/       # post-migration runner against a Fabric Warehouse (experimental)
│   └── fabric_mapping/          # aggregator + heuristic rules (incl. T-SQL surface scan)
├── web/                         # FastAPI control plane (`sma serve --with-api`)
└── reporting/                   # shared writers (JSON/CSV/Markdown), shared HTML CSS/JS,
                                  #   and the top-level index.html aggregator
```

Each module exposes:
- `analyzer.py` with a class providing `.run() -> <ModulePydanticModel>`
- `models.py` with Pydantic v2 models
- `reporting.py` writing JSON / CSV / Markdown, plus a per-module `html_report.py`
- `arm_client.py` / `sql_client.py` / `artifacts_client.py` as appropriate
- `collectors[.py|/]` and (for SQL-backed modules) `queries/*.sql`

A new CLI subcommand is registered in [cli.py](src/usma/cli.py).

## Prerequisites

- Python **3.12+**
- **Microsoft ODBC Driver 18 for SQL Server** installed on the host machine (required by `pyodbc`).
  - Windows: <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>
- A **service principal** with:
  - `Reader` on the Synapse workspace (control plane).
  - **Synapse Artifact User** on the workspace for `analyze-pipelines` and `analyze-spark-pools` (notebooks / SJDs).
  - **Monitoring Reader** at the subscription / resource-group scope for `analyze-monitoring` and `analyze-storage` (capacity metrics).
  - For `analyze-storage`: `Reader` on each storage account attached to the workspace (default ADLS Gen2 + storage accounts referenced by linked services). Granting Reader at the subscription / RG scope is the simplest. Set `SMA_STORAGE_INCLUDE_ALL=1` to opt back into the legacy subscription-wide scan.
  - **Synapse SQL access** to each dedicated pool (granted via `CREATE USER [<sp>] FROM EXTERNAL PROVIDER` in the pool, plus role memberships such as `db_datareader`, `VIEW DATABASE STATE` for DMVs, and `VIEW DEFINITION` so the catalog exposes stored procedures and user-defined functions in `sys.objects` / `sys.sql_modules`).

> For a reviewer-ready summary of the analyzer's full access surface
> (per-module RBAC, what ends up in output, what does not leave the host),
> see [**docs/user-guide/17-access-and-security.md**](docs/user-guide/17-access-and-security.md)
> or run `sma access-report --out access-report.md`.
>
> For a breakdown of what running the analyzer *costs* (essentially zero —
> all reads are free-tier Azure APIs and DMV scans on capacity you already
> pay for) and how SMA projects Fabric cost, see
> [**docs/user-guide/18-cost.md**](docs/user-guide/18-cost.md).

## Setup

Quickstart bootstrapper (Windows):

```powershell
# Clones the repo, creates .venv, installs all extras (dev,cost,web),
# builds the React SPA, runs `sma doctor --offline`, and on success
# launches `sma serve --with-api --static-dir web\dist`.
.\quickstart.ps1                       # public fork
.\quickstart.ps1 -Repo Private         # internal fork
.\quickstart.ps1 -SkipWebBuild -NoServe  # CLI-only setup
```

Manual setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

Copy-Item .env.example .env
# edit .env with tenant/client/secret/subscription/workspace
```

## Run

```powershell
sma doctor                                        # consolidated host + auth pre-flight
sma doctor --offline                              # skip live Azure calls

sma analyze-dedicated-pools                       # all formats
sma analyze-serverless-pools
sma analyze-spark-pools
sma analyze-pipelines
sma analyze-monitoring                            # Azure Monitor metrics for dedicated pools
sma analyze-storage                               # ADLS inventory + capacity + dedicated-pool size
sma map-to-fabric                                 # aggregates prior outputs

sma analyze-all                                   # runs everything end-to-end (7 steps)
sma analyze-all --skip monitoring --skip storage  # opt out of selected modules
sma analyze-all --include governance --include security --include cost  # opt in to mid-term modules
sma analyze-all --with-webui                      # also copy the prebuilt React SPA into output/webui/
sma -v analyze-dedicated-pools                    # verbose

# Mid-term modules (opt-in; emit JSON / CSV / Markdown / HTML)
sma analyze-governance                            # RBAC + MPE + CMK + Purview, with findings
sma analyze-security                              # firewall + AAD + TDE + credentials, with findings
sma analyze-cost --months 6                       # consumption + Fabric TCO delta, with findings
sma validate-fabric                               # experimental post-migration diff vs a Fabric Warehouse
sma run-delta                                     # diff the last two runs (manifest + delta report)

# Tooling for the web SPA / FastAPI control plane
sma export-schema                                 # emit per-module Pydantic JSON Schema to ./schemas/

# Web control plane (opt-in; see section below)
pip install -e ".[web]"                           # install FastAPI + uvicorn + sse-starlette + httpx
sma serve --with-api                              # local browser UI at http://127.0.0.1:8000/
```

Outputs land in `./output/` (override via `SMA_OUTPUT_DIR`). Every analyzer emits JSON, CSV,
Markdown and HTML. After `analyze-all` (or via `sma index`) a top-level `index.html` is
(re)generated, linking to whichever module reports exist. See each module section in
[QUICKSTART.md](QUICKSTART.md) for the file list.

## Tests

```powershell
pytest -q          # full suite (incl. tests/web/), no Azure access required
sma doctor         # host + auth pre-flight (uses .env when present)
```

## Web control plane (opt-in)

`sma serve --with-api` starts a local FastAPI backend (mounted at `/api/*`) and
the React SPA on the same origin. Analysts can:

> 📖 **Full UI walkthrough:** see the [User guide](docs/user-guide/README.md)
> for a per-page reference (Dashboard, Code objects, Recommendations,
> Runbook, Delta, Run, Runs, Diff, Configuration), troubleshooting matrix
> and FAQ.


- Edit `.env` from the browser (Configuration page) — secret values are
  write-only; reads return only `set` / `unset`. The page exposes a
  **source-type radio** (Synapse workspace vs Azure Data Factory) and
  auto-discovers the matching workspaces / factories in the chosen
  subscription.
- Kick off analyzer runs (Run page) with a module checklist that filters
  itself by source type (e.g. ADF mode only shows the modules that have
  an ADF collector), a global **Lookback** selector (1 / 3 / 7 / 14 /
  28 / 60 days, default 14 — applied to pipelines, Spark history and
  monitoring), an optional **multi-scope** picker that fans the run out
  across more than one workspace / factory in a single execution, and
  live progress over Server-Sent Events.
- Browse run history (Runs page, with per-row Delete) and diff any two
  runs (Diff page). The history grid shows a **Scopes** column with the
  per-scope source-type chips so multi-source runs are immediately
  recognisable; the Diff page splits per-scope artefacts into their own
  column so two scopes are never collapsed onto a colliding key.
- See workspace-/factory-level vitals on the **Dashboard**: readiness
  score, T-SQL surface findings, recommendation count, SKU advisory, top
  blockers, storage stats (dedicated-pool data / index sizes, ADLS used
  capacity per account, per-pool table breakdown) and pipeline activity
  over the last *n* days (daily run rate, success rate, daily data
  movement, top-10 pipelines by run count). Labels follow the source
  type — an ADF run says "factory" / "Azure Data Factory pipelines"
  where a Synapse run says "workspace" / "Synapse pipelines".
- See an **Estate overview** across every run on disk — scopes grouped
  by tenant / subscription / resource group with a per-row **Source**
  chip, hero totals (scopes, runs, tenants, subscriptions, ready /
  effort / blocked split, avg T-SQL %, estimated F-SKU needed, actual
  vs projected monthly spend), an estate readiness trend chart, per-row
  sparkline of the last 50 runs, and an estate-deduped *top blockers*
  table. CSV export includes a `source_type` column.
- **Export a PDF report** from the Estate overview that bundles the
  estate totals + each workspace's latest Dashboard (top blockers,
  full recommendations, cost summary, runbook). Uses the browser's
  Save-as-PDF print dialog so no extra dependencies are needed.

```powershell
pip install -e ".[web]"                          # FastAPI + uvicorn + sse-starlette + httpx
cd web; npm install; npm run build; cd ..       # build the SPA bundle

sma serve --with-api                             # http://127.0.0.1:8000/
sma serve --with-api --port 8001                 # custom port
sma serve --with-api --runs-dir D:\runs          # custom run repo
```

**Security:** the API has **no authentication**. It is bound to loopback
(`127.0.0.1`) by default and refuses non-loopback hosts unless you pass
`--i-know-this-is-not-auth`. State-changing requests (`POST` / `PUT` /
`DELETE`) require the `X-SMA-API: 1` header (the SPA sets it; CSRF mitigation).
Run directories live under `--runs-dir` (default `./runs`) and run / module
identifiers are validated against strict regexes to prevent path traversal.
Do not expose the control plane on a shared host.

## What the dedicated_pools module captures

| Area | Source | File |
|---|---|---|
| Pool inventory, SKU, DWU, status, collation, max size | ARM (`azure-mgmt-synapse`) | `arm_client.py` |
| Schemas + object counts | `sys.schemas` / `sys.objects` | `queries/schemas.sql` |
| Tables: distribution, partitioning, row counts, storage MB, index type | `sys.pdw_*`, `sys.dm_pdw_nodes_db_partition_stats` | `queries/tables.sql` |
| Indexes (incl. CCI/heap) | `sys.indexes` | `queries/indexes.sql` |
| Usage snapshot (active/completed/failed requests, durations, sessions) | `sys.dm_pdw_exec_*` | `queries/usage.sql` |
| Database principals & role memberships | `sys.database_principals`, `sys.database_role_members` | `queries/security.sql` |
| Workload groups & classifier counts | `sys.workload_management_*` | `queries/workload.sql` |
| Code objects (procs / views / functions) with stable id | `sys.sql_modules` | `queries/code_objects.sql` |
| Top consumed tables / views (sqlglot AST + 30-day workload cache) | `sys.dm_pdw_exec_requests` + `INFORMATION_SCHEMA.TABLES` | `queries/workload_commands.sql` + `workload_parser.py` |
| **v2** Column collation audit (per-column vs DB default) | `sys.columns` + `sys.databases` | `queries/column_collation.sql` |
| **v2** Materialized-view inventory | `sys.views` + `sys.indexes` | `queries/materialized_views.sql` |
| **v2** Statistics freshness (last_updated, modification_counter) | `sys.stats` + `sys.dm_db_stats_properties` | `queries/statistics_freshness.sql` |
| **v2** Column-level row/distinct/null/skew stats | `sys.dm_pdw_nodes_db_partition_stats` (+ derived) | `queries/column_stats.sql` |
| **v2** Distribution-key candidates (skew + filter-selectivity scoring) | pure Python over the above + code objects | `distribution_advisor.py` + `query_pattern_extractor.py` |
| **v2** T-SQL surface gap rollup linked to stable `code_object_id` | pure Python over `code_objects` | `tsql_surface_gap.py` |

Paused pools are detected via control-plane `status` and DMV collection is skipped (recorded in `errors`).

## Roadmap

The seven core modules (`dedicated_pools`, `serverless_pools`, `spark_pools`, `pipelines`,
`monitoring`, `storage`, `fabric_mapping`) are implemented end-to-end with reporting, a Fabric-mapping
aggregator, a Click-based CLI, and a `sma doctor` self-test. The roadmap below is grouped
by horizon. Items are ordered roughly by user value within each band; nothing here is
committed work.

### Near-term (next minor releases)

Polish and depth on what already exists.

> **Status (v2 scaffolding shipped):** all bullets in this section now have a working
> Python skeleton in the codebase. SDK-bound bits (live DMVs / Log Analytics / Livy job
> history) are wired with try/except so older Synapse versions don't fail the run; pure-
> Python heuristics (distribution advisor, notebook lint, expression compat, schedule
> mapper, DWU-hours derivation, readiness score, runbook builder, capacity projection,
> storage-account cost attribution) are fully implemented and unit-tested.

- **`dedicated_pools` v2** — column-level collation audit, distribution-key candidate
  suggestions (skew + filter selectivity heuristics), materialized-view inventory,
  statistics freshness report, and a "T-SQL surface gaps" rollup that links each finding
  back to the offending object via a stable code-object id.
- **`serverless_pools` v2** — capture `OPENROWSET` / external-table column projections,
  per-database query history with **partition-pruning** analysis, and per-storage-account
  cost attribution for the 30-day data-scanned figure.
- **`spark_pools` v2** — library / package inventory (custom + workspace-level), recent
  Livy job-run history, runtime-version compatibility check against Fabric Spark, and a
  notebook lint pass (Synapse-only magics, `spark.synapse.*` configs).
- **`pipelines` v2** — per-activity parameter-binding analysis, expression-language
  compatibility checks (`@pipeline()`, `@activity()`, system variables), and trigger-time
  schedule mapping to Fabric Data Factory pipeline schedules.
- **`monitoring` v2** — Log Analytics / KQL queries (long-running queries, top users,
  failed logins, blocked sessions) over 30/60/90-day windows, plus a derived "active DWU
  hours per day" series for sizing.
- **`fabric_mapping` v2** — auto-generated **migration runbook** (sequenced, dependency-aware
  steps with rollback notes), Fabric **capacity (CU) cost projection** sourced from
  monitoring metrics + workload mix, and a per-pool readiness score (0-100) with the
  five biggest blockers called out.
- **CLI / DX** — `sma analyze-all --skip <module>`, structured JSON-lines logs behind
  `--log-format json`, deterministic exit codes per failure class, and a `--since` flag
  on monitoring to override the default window.

### Mid-term

New modules and integrations once the core depth is in place.

> **Status (v1.2.0 — production):** the four bullets below shipped as v0
> scaffolding in 1.1.0 and were promoted to v1 in 1.2.0. They are now
> first-class production capabilities alongside the seven core modules,
> still opt-in via `analyze-all --include …` (or the dedicated
> `sma analyze-<module>` subcommand). Each one ships full rules engines,
> severity-tagged findings, per-module HTML reports, and documented
> data-source / RBAC requirements (see [QUICKSTART.md](QUICKSTART.md) and
> [CHANGELOG.md](CHANGELOG.md)).

- **`governance` module** — workspace- and resource-level RBAC export (control plane +
  data plane) with role-name resolution, managed-private-endpoint inventory,
  customer-managed-key configuration, and **Microsoft Purview** account
  configuration detection (presence / absence of a linked Purview account on
  the workspace — full lineage extraction is on the long-term roadmap).
  **(v1: rules + HTML report)**
- **`security` module** — firewall rules, AAD-only enforcement, TLS minimum version,
  encryption-at-rest configuration, AAD admin inventory, per-pool TDE state,
  and a linked-service credential inventory with **inline-secret detection**
  (literal `password` / `accountKey` / `sasToken` / `SecureString` vs. Key Vault
  references — types and locations only, never values). **(v1: rules + HTML report)**
- **`cost` module** — month-over-month consumption pull from
  Microsoft.Consumption / Cost Management for the workspace's resource group, broken down
  by resource kind / pool / storage account, paired with the `fabric_mapping` CU
  projection for a side-by-side TCO delta. **(v1: rules + HTML report)**
  Requires the optional `azure-mgmt-costmanagement` extra (`pip install -e ".[cost]"`)
  and **Cost Management Reader** on the subscription or workspace resource group.
  Without the SDK or RBAC, the analyzer still runs but emits `cost.sdk_missing` /
  `cost.collection_error` findings instead of live data.
- **Incremental / delta runs** — persist a run manifest (hash + timestamp + record-count
  peek per collector) so subsequent `analyze-all` invocations produce a diff report
  against the prior run in markdown, HTML, and JSON forms.
  **(v1: HTML + JSON delta + record-count peeks)**

### Long-term / exploratory

Ideas that need design work or external dependencies.

> **Fabric-side validation** shipped in v1.2 as the `fabric_validation` module
> (`sma validate-fabric`, opt-in / experimental). The bullet was removed from
> this section once it had a working implementation.

- **Code conversion assist** — automated rewrite hints for the T-SQL surface gaps
  detected today (surrogate keys via `IDENTITY` → Fabric pattern, `MERGE` simplifications,
  unsupported hints, etc.). Suggestions only — never silent rewrites of customer code.
- **Notebook conversion assist** — rewrite hints for the most common Synapse-Spark-only
  patterns (`mssparkutils` → `notebookutils`, linked-service mounts, runtime-specific APIs).
- **GitHub Action / Azure DevOps task** — packaged invocation of `analyze-all` with
  artifact upload and a PR-comment summary of the readiness score delta.
- **Workspace-of-workspaces** — point at a subscription and fan out across every Synapse
  workspace, producing a portfolio-level rollup.
- **Public extension points** — documented hooks so customers / partners can register
  their own collectors and Fabric-mapping rules without forking.

### Non-goals

- **Performing the migration itself.** This tool inventories and assesses; data movement
  and DDL/DML translation remain out of scope.
- **Modifying the source workspace.** All Azure access is read-only by design (Reader,
  Monitoring Reader, Synapse Artifact User, plus `db_datareader` + `VIEW DATABASE STATE` + `VIEW DEFINITION`
  on dedicated pools).
- **A multi-tenant hosted GUI.** A local browser UI ships as the opt-in
  [web control plane](#web-control-plane-opt-in) (`sma serve --with-api`,
  loopback-only, no authentication), and the static deliverable mode
  (`sma serve`) renders the same SPA against an `output/` directory. A
  multi-tenant hosted service with authentication / authorization remains
  out of scope.

Have a request or want to contribute? Open an issue on the
[GitHub repository](https://github.com/anbergst_microsoft/USMA/issues)
with the module name and a concrete scenario. See [CONTRIBUTING.md](CONTRIBUTING.md) for
the contribution workflow, [DEPENDENCIES.md](DEPENDENCIES.md) for the third-party
dependency inventory, and [SECURITY.md](SECURITY.md) for vulnerability reports.

## License

This project is licensed under the [MIT License](LICENSE).

## Trademarks

"Azure", "Azure Synapse Analytics", "Microsoft Fabric" and related product names are
trademarks of Microsoft Corporation. This project is an independent, community-driven
tool and is **not affiliated with, endorsed by, or sponsored by Microsoft**. The MIT
license covers only the source code in this repository; it does not grant any rights
to use Microsoft trademarks.

