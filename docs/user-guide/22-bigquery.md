# 22. Google BigQuery sources

> **Phase 5 preview.** BigQuery support is **alpha** in v0.8: the
> analyzer can crawl a Google Cloud project via the
> `bigquery_workloads` module and surface workload-level Fabric
> compatibility, but only a subset of analyzers is wired up. See the
> support matrix below.

USMA (the Unified Solution Migration Analyzer — see ADR 0004) treats
"the thing you're migrating" as a generic **source scope**. A scope
can be a Synapse workspace, an ADF factory, a Databricks workspace, a
Google BigQuery project, or (eventually) an SAP BW system. Each scope
is described by a `SourceDescriptor`:

```python
SourceDescriptor(
    type=SourceType.BIGQUERY,
    id="my-gcp-project-123",          # GCP project id (no ARM URL)
    display_name="my-gcp-project-123",
    subscription_id=None,             # n/a — GCP project id is the scope key
    resource_group=None,
    extras={"project_number": "987654321000"},
)
```

The Configuration page in the SPA exposes one tab per source type; CLI
users target a scope via the repeatable `--scope` flag on `analyze-all`
(see below).

## Quick start (CLI)

```powershell
# Single-scope run against one BigQuery project.
sma analyze-all --scope bigquery:my-gcp-project-123

# Mixed run — one Synapse workspace + one ADF factory + one Databricks
# workspace + one BigQuery project.
sma analyze-all `
  --scope synapse_workspace:ws-eu-prod `
  --scope adf:adf-prod@$env:AZ_SUB/rg-data `
  --scope databricks:dbx-prod@$env:AZ_SUB/rg-data `
  --scope bigquery:my-gcp-project-123
```

The `--scope` flag accepts `<source_type>:<display_name>[@<sub>/<rg>]`.
For BigQuery the `<display_name>` is the **GCP project id** and the
`@<sub>/<rg>` suffix is omitted entirely — GCP has no Azure
subscription / resource-group concept.

> Today the **wave execution still pivots on the legacy single scope**
> from `.env`. The `--scope` flag persists the user's intent into the
> manifest so the SPA can render scope counts + source-type chips —
> true multi-scope dispatch lands in Phase 4.5. For a BigQuery-only
> run today, set `SMA_SOURCE_TYPE=bigquery` and
> `SMA_GCP_PROJECT_ID=<project-id>` in your `.env` (so the SPA's
> Configuration page picks the right radio + remembers the project),
> then add `--scope` so the manifest records the source type.

## Authentication

BigQuery does **not** use the Azure service principal. The analyzer
authenticates via **Application Default Credentials (ADC)** — the
standard Google Cloud auth mechanism — so the four AAD fields
(`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`,
`AZURE_SUBSCRIPTION_ID`) are ignored when `SMA_SOURCE_TYPE=bigquery`.

Two equivalent options:

1. **Service-account JSON key (CI / non-interactive hosts).**
   Generate a JSON key for a service account in the target project,
   save it locally, and point `GOOGLE_APPLICATION_CREDENTIALS` at the
   absolute path. The analyzer never reads the key file directly — it
   defers to `google.auth.default()`, which respects the env var.

   ```powershell
   $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\secrets\sma-bq-reader.json"
   ```

2. **User credentials (interactive / dev box).** Run
   `gcloud auth application-default login` once; the resulting token is
   picked up by `google.auth.default()` without any env var.

   ```powershell
   gcloud auth application-default login
   ```

3. **Browser sign-in from the SPA — no gcloud CLI required
   (Slice 5-I, preferred for dev boxes).** The Configuration page's
   BigQuery card has a **Sign in to Google** button. Clicking it
   opens your default browser at Google's consent screen, runs the
   installed-app OAuth flow against a temporary `localhost` listener,
   and writes the resulting refresh token to the canonical ADC path
   (`%APPDATA%\gcloud\application_default_credentials.json` on
   Windows). Once the SPA reports *"Signed in as `<you>`"*, the
   project dropdown auto-populates — no shell commands, no gcloud
   install. **Caveat:** the OAuth callback redirects to `localhost`,
   so this only works when the SMA backend (`sma serve`) and your
   browser run on the same machine. For remote / shared deployments,
   use option 1 (SA key) or Workload Identity Federation.

The principal (service account or user) must have the following IAM
roles, scoped to the target project:

* **BigQuery Metadata Viewer** (`roles/bigquery.metadataViewer`) — read
  dataset / table / routine metadata.
* **BigQuery Data Viewer** (`roles/bigquery.dataViewer`) — required by
  some `INFORMATION_SCHEMA` views the collector falls back to.
* **Logs Viewer** (`roles/logging.viewer`) — pull
  `jobservice.jobcompleted` / `jobChange` entries from Cloud Logging
  for slot-hour rollups.
* **BigQuery Data Transfer Service User**
  (`roles/bigquerydatatransfer.user`) — enumerate scheduled queries
  (`data_source_id="scheduled_query"`). Optional; the analyzer
  surfaces a `caveat` and continues if this role is missing.
* **Browser** (`roles/browser`) on the **organisation** (or
  `roles/resourcemanager.projectViewer` on each candidate project) —
  required only for the Configuration page's auto-discover button,
  which calls Cloud Resource Manager `search_projects` to enumerate
  every project visible to the principal.

No personal access tokens or AAD credentials are required.

## Support matrix

| Module | Synapse | ADF | Databricks | BigQuery | Notes |
|---|---|---|---|---|---|
| `bigquery_workloads` | n/a | n/a | n/a | ✅ | datasets, tables, routines, scheduled queries, jobs — Phase 5-B |
| `pipelines` (static) | ✅ | ✅ | ⛔ | ⛔ | BigQuery scheduled queries surface via `bigquery_workloads` instead |
| `pipelines` (run history) | ✅ | ⛔ | ⛔ | ⛔ | BigQuery job run history is part of `bigquery_workloads` itself |
| `fabric_mapping` | ✅ | ✅ | ✅ | ✅ | per-job-type compatibility rules (SELECT ✅, LOAD / EXTRACT / COPY ⚠, ML_* ⛔) — Phase 5-C |
| `cost` | ✅ | ✅ | ✅ | ✅ | BigQuery scopes use `gcp_cost_client` reading the BigQuery billing-export tables (Phase 5-H) |
| `databricks_workflows` | n/a | n/a | ✅ | n/a | Databricks-only |
| `monitoring`, `code_objects`, `serverless_pools`, `dedicated_pools`, `spark_pools` | ✅ | n/a | n/a | n/a | Synapse-only concepts |
| `storage`, `security`, `governance` | ✅ | ⛔ | ⛔ | ⛔ | per-source equivalents land alongside the cost client |

The active support map is exposed by `GET /api/modules` and by
`MODULE_SPECS[<module>].supports_source(SourceType.BIGQUERY)` in code,
so the Run-page checkbox matrix can grey out incompatible cells
automatically.

## What's collected from a BigQuery project

The `BigQueryWorkloadsCollector` (see
`usma.modules.bigquery_workloads.collector`)
returns workload-shaped data normalized to the analyzer's wire format:

* `iter_datasets()` — every dataset in the project (location, default
  table expiration, labels).
* `iter_tables(dataset)` — `TABLE` / `VIEW` / `MATERIALIZED_VIEW` /
  `EXTERNAL` / `SNAPSHOT` with partitioning + clustering keys and
  row / byte counts.
* `iter_routines(dataset)` — UDFs, stored procedures and table
  functions (language, return type, signature).
* `iter_scheduled_queries()` — Data Transfer Service jobs filtered to
  `data_source_id="scheduled_query"`, mapped to Fabric pipeline /
  Data Factory candidates by the `fabric_mapping` rule.
* `iter_jobs(lookback_days)` — completed jobs pulled from Cloud
  Logging (`jobservice.jobcompleted` and `jobChange` audit entries are
  both handled), bucketed by job type
  (`QUERY` / `LOAD` / `EXTRACT` / `COPY` / `QUERY_SCRIPT` / `ML_*`)
  with `total_slot_ms` carried through as the basis for the
  slot-hour → vCore-hour → Fabric Spark CU-hour rollup.

### Slot-hour → Fabric Spark CU conversion

`modules/bigquery_workloads/run_stats.py` ships two module-level
constants (and a documented caveat that the analyzer surfaces in
`BigQueryWorkloadsAnalysis.caveats` when any slot-hour math actually
ran):

* `SLOT_HOURS_TO_VCORE_HOURS = 0.5` — BigQuery slot ≈ 0.5 vCore (the
  conservative on-demand-pricing default; flat-rate edition customers
  can override).
* `SLOT_HOURS_TO_CU_HOURS = 0.25` — equals
  `0.5 × VCORE_HOURS_TO_CU_HOURS` (the latter is reused from the
  Spark-pools client, so one edit changes Databricks / BigQuery /
  Spark conversions together).

Rolling windows of 7 / 14 / 28 / 90 days are aggregated into
`BigQueryJobWindowStats` rows with `total_slot_hours`,
`est_cu_hours_fabric_spark`, `success_rate`, `avg_duration_seconds`
and `total_billed_bytes`.

## Configuration page (SPA)

Once `sma serve --with-api` is running, open the **Configuration** tab
and pick the **BigQuery** radio. Unlike the other three source types,
**no Azure credentials are required** — the page skips straight to a
dedicated GCP-project selector. Click **Discover GCP projects** to
populate the dropdown from
`google.cloud.resourcemanager_v3.ProjectsClient.search_projects(query="state:ACTIVE")`.
The dropdown shows the project id alongside its numeric project
number, with the project currently pinned by `SMA_GCP_PROJECT_ID`
marked **— current**. Pick one and click **Save**; the analyzer
persists both `SMA_GCP_PROJECT_ID` and (for SPA-internal
display-name continuity) `SYNAPSE_WORKSPACE_NAME` to the same value.
**Validate** then runs the BigQuery-specific probes (`ADC resolution`,
`bigquery.Client.get_service_account_email`, `logging_v2.Client.list_entries`
against the data-access audit log) and **skips** the Synapse SQL /
Spark Livy gauntlet that doesn't apply to GCP.

## Run page

When `source_type=bigquery`, the Run page's module checklist limits
the selectable modules to:

* `bigquery_workloads` — default on
* `cost` — default on (reads the BigQuery billing-export tables; set
  `SMA_GCP_BILLING_DATASET` + `SMA_GCP_BILLING_TABLE` or
  `SMA_GCP_BILLING_ACCOUNT` to enable — otherwise the collector skips
  itself with a `missing_config` status)
* `fabric_mapping` — default on (consumes the workloads artefacts)

Synapse-only and Databricks-only modules are hidden so you can't
accidentally schedule a wave that can never finish.

## Dashboard

After a run completes, the Dashboard renders a **BigQuery workload**
section between Databricks workflows and Serverless. Layout is
intentionally byte-faithful to the other source sections so the eye
moves between Synapse / Databricks / BigQuery without re-learning the
chrome:

* **Headline tiles** (3 cards) — total jobs in the window with
  jobs/day + success-rate sub, total slot-hours with billed bytes
  sub, and estimated Fabric capacity in CU-hr with sustained CU/day
  sub.
* **Charts row** — daily job-outcome stacks (28 trailing days,
  succeeded / failed / cancelled+other in `var(--ok)` / `var(--err)` /
  `var(--muted)`) and last-24h hourly bars stacked per
  `statement_type` (SELECT / DML / MERGE / …) with a dashed average
  line. The hourly chart prefers `total_slot_hours` and falls back to
  a job-count view when slot accounting is missing — same idiom as
  `DatabricksHourlyBars`.
* **Storage view** — per-dataset rollup of `num_bytes` (BigQuery's
  active logical-storage metric, billed under standard logical-byte
  pricing). 3-card headline (total TiB / dataset count / top dataset
  with % share) plus a table listing every dataset with table count,
  row count, logical size, % of estate, and the largest table by
  size. Physical bytes / long-term vs active split would require
  `INFORMATION_SCHEMA.TABLE_STORAGE` and are **not** collected today
  — the section makes the metric explicit so estate planners are not
  misled.
* **Top users by slot-hours** — top 10 user-emails by aggregate
  slot-hours, with OK / failed counts, billed GB, and average
  duration.
* **Statement-type breakdown** — top 10 statement types by slot-hour
  share.
* **Most-used tables** — top 20 tables ranked by distinct jobs that
  referenced them. Totals are **not** apportioned across joined
  tables (BigQuery does not expose per-table cost split), so the
  ranking is "which tables are in hot paths" rather than per-table
  spend.
* **Query features (sqlglot)** — per-feature counts (window
  functions, MERGE, ARRAY_AGG, JSON_EXTRACT, scripting, …) parsed
  from captured `query_text` using sqlglot's BigQuery dialect.
  Empty by default — set `SMA_BQ_CAPTURE_QUERY_TEXT=1` on the
  analyzer host to populate `query_text` (off by default because SQL
  frequently carries embedded literals / PII).
* **Caveats + errors** footers surface the slot-ratio caveat and any
  collector errors (e.g. audit-log API quota hit) inline.

The section is gated on the BigQuery report carrying any jobs / daily
stats / tables; runs without a BigQuery scope render nothing here and
do not affect other sections.

## Limits & known gaps

* **GCP cost attribution requires a billing export to BigQuery.**
  Slice 5-H ships `modules/cost/gcp_cost_client.py` which queries the
  project's `gcp_billing_export_v1_*` table directly. Configure via
  `SMA_GCP_BILLING_DATASET` (e.g. `billing-host.exports`),
  `SMA_GCP_BILLING_TABLE` (e.g.
  `gcp_billing_export_v1_ABCDEF_012345_678901`), or set
  `SMA_GCP_BILLING_ACCOUNT` and let the analyzer derive the table name.
  If none of those are set the cost run is skipped with a
  `missing_config` status and the Estate Overview / TCO delta stay
  blank for the BigQuery scope.
* **Slot ≈ 0.5 vCore is an assumption.** Real ratios depend on
  on-demand vs flat-rate edition, autoscaler reservations and slot
  allocation policy. The analyzer surfaces this as a caveat on every
  run that produced slot-hour rollups.
* **No `INFORMATION_SCHEMA.JOBS_BY_*` fallback.** Job stats come
  exclusively from Cloud Logging audit entries. Projects with audit
  logging disabled will see empty `bigquery_jobs` arrays and a
  `caveat` on the analysis.
* **No estate-overview rollup yet.** `web/estate.py` does not yet have
  a `_bigquery_daily_cu` helper, so the Estate Overview leaves
  `projected_fabric_cu` blank for BigQuery-only scopes. Tracked as
  Phase 5-F (shipped — see CHANGELOG).
* **No BigLake / BigQuery Omni / BigQuery ML model deep-dive.**
  `ML_*` job types are surfaced in the job-bucket recs but not
  classified for Fabric compatibility yet.
* **Legacy run attribution.** Runs created before the cloud-aware
  identity fix inherited `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`
  from `.env`, even for BigQuery scopes. If your Estate overview
  shows a BigQuery project under an Azure tenant, run
  `sma migrate-run-attribution` (or use the **Fix non-Azure run
  attribution** panel on the [Configuration](12-configuration.md#fix-non-azure-run-attribution)
  page). New runs are attributed correctly automatically.

## Roadmap

1. **Phase 5-E** — documentation: this chapter,
   `01-getting-started.md` BigQuery row, README + QUICKSTART rows.
   (shipped)
2. **Phase 5-F** — `_bigquery_daily_cu` helper in `web/estate.py` so
   the Estate Overview's `projected_fabric_cu` + recommended Fabric
   SKU light up for BigQuery scopes. (shipped)
3. **Phase 5-H** *(this slice)* — `modules/cost/gcp_cost_client.py`
   reading the project's billing-export tables,
   `MODULE_SPECS["cost"].supports` widened to include `BIGQUERY`, and
   `MODULES_BY_SOURCE.bigquery` updated to include `cost`. (shipped)
4. **Phase 5-J** — Dashboard BigQuery section: headline tiles, daily
   + last-24h hourly bar charts side-by-side, GCS storage view,
   top-users / statement-type / top-tables breakdowns, sqlglot
   query-features table. Removes the standalone `/bigquery` route in
   favour of a single Dashboard surface matching the other source
   sections. (shipped)
5. **Phase 6+** — SAP BW (revived from Phase-0 stubs), SQL Server /
   SSIS, Snowflake.

See also: [99-adding-a-source.md](./99-adding-a-source.md) for the
developer guide to adding a new source type, and ADR 0001-0004 for the
architectural decisions behind the unified source model.
