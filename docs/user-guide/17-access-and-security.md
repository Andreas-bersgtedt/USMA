# 17. Tool access & security implications

This chapter is for **InfoSec, change-advisory boards, and migration leads**
who need to sign off on running the Unified Solution Migration Analyzer (USMA) against a
production workspace. It documents every Azure / Synapse / SQL surface the
tool touches, what permissions it needs, what ends up on disk afterwards,
and what does **not** leave the host.

> The same information is available as a machine-generated, version-stamped
> Markdown report by running:
> ```
> sma access-report --out access-report.md
> ```
> The CLI command and this chapter are both sourced from the in-code
> manifest at `src/usma/access_manifest.py`, so they
> stay in sync as the analyzer evolves.

## TL;DR

- **All access is read-only.** No analyzer ever writes to Synapse, ADLS,
  Monitor, or Cost Management. The only writes are to the local
  filesystem under `--output-dir` (default `./output/`) and
  `--runs-dir` (default `./runs/`, control-plane only).
- **No telemetry.** No outbound traffic except to documented Azure
  endpoints in the workspace's region. No phone-home, no analytics,
  no third-party APIs.
- **No secret material is captured.** Linked-service credentials are
  classified by shape (Key Vault reference vs. inline SecureString)
  but the literal secret value is never read or persisted.
- **Default identity** is a service principal authenticated via
  `ClientSecretCredential` (`AZURE_TENANT_ID` / `AZURE_CLIENT_ID` /
  `AZURE_CLIENT_SECRET`). The secret lives only in `.env` on the host
  running the CLI (or behind `PUT /api/config` in control-plane mode).

## Identity model

| Surface | Auth | Credential location |
|---|---|---|
| Azure control plane (ARM) | `ClientSecretCredential` -> AAD token for `https://management.azure.com/.default` | `.env` |
| Synapse Artifacts (data plane) | Same SP via `https://dev.azuresynapse.net/.default` | `.env` |
| Synapse SQL (dedicated + serverless) | Same SP via `https://database.windows.net/.default`, used as a `Microsoft Entra access token` for `pyodbc` | `.env` |
| Azure Monitor metrics | Same SP via ARM token | `.env` |
| Azure Cost Management | Same SP via ARM token | `.env` |

There is **one** principal across the whole tool. The same SP must hold
every role listed in the next section.

## Per-module access matrix

The tool is modular; running a subset of modules (`sma analyze-all
--include …` or individual `sma analyze-<x>` commands) reduces the
permission surface. Modules not selected need no grants.

| Module | Azure RBAC | Synapse RBAC | SQL grants |
|---|---|---|---|
| `dedicated_pools` | Reader (workspace) | — | `CREATE USER FROM EXTERNAL PROVIDER`, `db_datareader`, `VIEW DATABASE STATE`, `VIEW DEFINITION` |
| `serverless_pools` | Reader (workspace) | — | `CREATE LOGIN/USER FROM EXTERNAL PROVIDER`, `VIEW SERVER STATE` (for non-caller usage history) |
| `spark_pools` | Reader (workspace) | Synapse Artifact User, **Synapse Compute Operator** (for Livy history) | — |
| `pipelines` | Reader (workspace) | Synapse Artifact User | — |
| `monitoring` | Reader + **Monitoring Reader** (sub/RG) | — | — |
| `storage` | Reader + Monitoring Reader + Reader on each linked storage account | — | Same as `dedicated_pools` |
| `fabric_mapping` | — (post-processing only) | — | — |
| `governance` | Reader (workspace), plus `Microsoft.Authorization/roleAssignments/read` at the scopes being audited | — | — |
| `security` | Reader (workspace) | Synapse Artifact User (linked services) | — |
| `cost` | **Cost Management Reader** (sub or RG) | — | — |
| `fabric_validation` | — (target Fabric workspace only) | Fabric `Viewer` + `db_datareader` on target warehouse | — |

> See [QUICKSTART §0.3.5](../../QUICKSTART.md#035-permissions-cheat-sheet-read-this-if-you-hit-unauthorized)
> for the exact `az` commands to grant each role.

## What the analyzer reads

For each module, the exact surfaces. Every read is a list / get /
`SELECT` against management-plane SDKs, data-plane SDKs, or read-only
DMVs.

- **`dedicated_pools`** — `SynapseManagementClient.workspaces.get`,
  `sql_pools.list_by_workspace`; T-SQL against `sys.objects`,
  `sys.sql_modules`, `sys.parameters`, `sys.tables`, `sys.indexes`,
  `sys.dm_pdw_nodes_db_partition_stats`, `sys.dm_pdw_exec_requests`,
  `sys.dm_pdw_exec_sessions`, `INFORMATION_SCHEMA.TABLES`. All DMV
  scans carry `OPTION (LABEL = 'sma:<query>')` so the analyzer's
  own activity is auditable in `dm_pdw_exec_requests`. The
  top-consumed-tables collector additionally persists parsed-request
  metadata to a local cache under
  `output/.cache/dedicated_pools_workload/` (no PII beyond what was
  already in the submitted SQL — see [05. Code objects](05-code-objects.md)).
- **`serverless_pools`** — `sys.databases`, `sys.external_tables`,
  `sys.external_data_sources`, `sys.dm_exec_requests_history`.
- **`spark_pools`** — `big_data_pools.list_by_workspace`;
  `SparkClient.spark_batch.list` / `spark_session.list` (paged Livy
  job history, configurable window).
- **`pipelines`** — `ArtifactsClient` pipeline / dataset /
  linkedService / notebook / SJD / trigger lists;
  `MonitorManagementClient` pipeline & activity-run history (windowed
  via `SMA_PIPELINES_RUN_DAYS`).
- **`monitoring`** — `MonitorManagementClient.metrics.list` on the
  workspace and pool resource IDs.
- **`storage`** — DMV scan (same as `dedicated_pools`) plus
  `StorageManagementClient.storage_accounts.list / get_properties`
  and `MonitorManagementClient.metrics` (UsedCapacity only — never
  enumerates file contents). Scoped to accounts referenced by the
  workspace's linked services unless `SMA_STORAGE_INCLUDE_ALL=1`.
- **`governance`** — `AuthorizationManagementClient.role_assignments
  .list_for_scope` / `role_definitions.get`;
  `SynapseManagementClient` managed-private-endpoints / keys.
- **`security`** — `SynapseManagementClient` firewall rules / AAD
  admins / TDE per pool; `ArtifactsClient.linked_service.get_…`.
- **`cost`** — `CostManagementClient.query.usage` on the
  subscription or RG scope.
- **`fabric_validation`** — read-only T-SQL against a *target* Fabric
  warehouse (`INFORMATION_SCHEMA`, `sys.tables`, `sys.sql_modules`).
- **`fabric_mapping`** — no Azure calls. Pure post-processing of the
  other modules' JSON outputs.

## What the analyzer writes

Outputs land on the host filesystem only. Nothing is written back to
Azure.

- `./output/` (or `--output-dir`) — JSON, CSV, Markdown, HTML reports
  per module.
- `./runs/` (or `--runs-dir`, control-plane only) — per-run folders
  with the same module outputs plus a `progress.jsonl` event log.
- `./.env` — written **only** by the control plane's
  `PUT /api/config` endpoint (POSIX permissions tightened to `0600`).
  The CLI itself never writes to `.env`.
- `stderr` / `stdout` — Rich-formatted or JSONL logs.

## What ends up in output (treat as sensitive)

The reviewer's mental model should be: *the output directory carries
roughly the same sensitivity tier as a SQL Server schema export.* It
contains:

- Workspace metadata: tenant id, subscription id, resource group,
  workspace name, pool names, region.
- Schema metadata: database / schema / table / view / procedure /
  function names, column names and data types, index definitions.
- **Top-query previews:** up to ~4 KB of SQL text per top query
  (`dedicated_pools.top_queries`, `serverless.top_queries`). This is
  query text only — the analyzer never persists query *results*.
- Login / principal names from `sys.dm_pdw_exec_sessions` (no
  passwords).
- Linked-service definitions with credential **shape** classification
  (Key Vault reference vs. inline `SecureString`). Literal secret
  values are never persisted; see `format_error` for the redaction
  helper used on exception strings.
- Cost Management actuals per resource (in the tenant's currency).

Output does **not** contain:

- Row-level data from any user table or view.
- Plaintext credentials, connection-string secrets, or SAS tokens.
- Encryption keys or key-vault contents.

## What does NOT leave the host

- **No telemetry.** No analytics, no crash reporting, no usage pings.
- **Network egress is restricted to documented Azure endpoints:**
  - `https://management.azure.com` (ARM, Cost Management)
  - `https://login.microsoftonline.com` (AAD token)
  - `https://<workspace>.dev.azuresynapse.net` (Artifacts, Livy)
  - `https://<workspace>.sql.azuresynapse.net` (dedicated SQL pools)
  - `https://<workspace>-ondemand.sql.azuresynapse.net` (serverless)
  - `https://<storage>.dfs.core.windows.net` /
    `https://<storage>.blob.core.windows.net` (storage)
  Allow-listing these per-workspace FQDNs is sufficient.
- **The web control plane is loopback-only by default.** Binding a
  non-loopback host requires the explicit
  `--i-know-this-is-not-auth` flag. See chapter
  [14. Security posture](14-security.md) for the control plane's own
  threat model (CSRF header, CORS-disabled, path-traversal allow-lists).

## Secret handling

- The SP client secret is read from `./.env` via `load_dotenv(...,
  override=True)` and held in process memory for the duration of a
  run. It is never written to logs, output files, or run manifests.
- The `format_error` helper redacts secrets that may appear in
  exception strings (e.g. connection-string `Password=…`) before any
  exception text is logged or persisted to a finding.
- The control plane's `GET /api/config` returns only `client_secret:
  "set"` / `"unset"` — the actual value is never returned over HTTP.
  `PUT /api/config` writes back to `.env` and tightens permissions to
  `0600` on POSIX hosts.

## Threat model

- **Insider with workspace `Reader`.** No additional reach. The
  analyzer's failure modes when run by a less-privileged principal
  are documented per finding (e.g. `sec.collection_error`,
  `cost.live_disabled`).
- **Host compromise.** The runs directory and `.env` together fully
  describe both the workspace inventory and the SP credentials. Treat
  the host (and its backups) accordingly.
- **Drive-by attack on the control plane.** Loopback bind +
  `X-SMA-API: 1` header requirement + disabled CORS means a malicious
  page in another browser tab cannot mutate state. There is still no
  authentication — running `--with-api` on a shared host is
  documented as unsafe.
- **Untrusted run artefact.** Run folders contain only deterministic
  JSON / Markdown / HTML. The HTML reports do not embed arbitrary
  third-party content; they are generated from the JSON via Jinja
  with autoescape. Opening a run folder from a peer is safe.

## Recommended pre-run checklist for reviewers

- [ ] The SP holds **Reader** on the workspace and nothing higher.
- [ ] If `analyze-cost` is run: **Cost Management Reader** at the
      narrowest scope (workspace RG, not subscription, when possible).
- [ ] If `analyze-monitoring` / `analyze-storage` is run:
      **Monitoring Reader** at the workspace RG.
- [ ] SQL grants on each pool are exactly the four listed for
      `dedicated_pools` — no `CONTROL`, no schema-owner roles.
- [ ] The `.env` lives outside any shared / synced filesystem
      (no OneDrive / iCloud / repo-synced location).
- [ ] The `runs/` directory is on an encrypted volume and is
      excluded from any automated backup that crosses a trust boundary.
- [ ] `sma access-report --out access-report.md` is captured and
      attached to the change ticket.

## Related

- [14. Security posture](14-security.md) — control-plane threat model
  (loopback bind, CSRF header, path-traversal allow-lists).
- [SECURITY.md](../../SECURITY.md) — vulnerability reporting process.
- [QUICKSTART §0.3.5](../../QUICKSTART.md#035-permissions-cheat-sheet-read-this-if-you-hit-unauthorized)
  — exact `az` commands to grant each role.
