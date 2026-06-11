# Quick Start

A **module-based** walkthrough of the Unified Solution Migration Analyzer (USMA). Each module is a self-contained analyzer that reads from data integration services (Synapse Analytics, Azure Data Factory, Azure Databricks *(alpha)*, Google BigQuery *(alpha)*, and Snowflake *(alpha)*) and produces typed reports for assessing **Microsoft Fabric** migration readiness.

> Both the `usma` and the legacy `sma` CLI aliases are registered during the
> deprecation window — every command in this guide accepts either prefix.

> **Sources in this guide.** §0 (host setup, service-principal, `.env`) is
> Synapse-centric for historical reasons. For analyzing an Azure Data
> Factory factory instead of (or alongside) a Synapse workspace, also
> read [docs/user-guide/20-data-factory.md](docs/user-guide/20-data-factory.md).
> For analyzing an Azure Databricks workspace, also read
> [docs/user-guide/21-databricks.md](docs/user-guide/21-databricks.md)
> — the SP setup is similar, but the Configuration page exposes a
> four-way Synapse / ADF / Databricks / BigQuery radio that
> auto-discovers the right resource type and a multi-scope picker
> that fans a single run across all of them. For analyzing a Google
> BigQuery project, also read
> [docs/user-guide/22-bigquery.md](docs/user-guide/22-bigquery.md)
> — BigQuery uses **Application Default Credentials** instead of the
> Azure service principal, so the AAD fields are skipped entirely.
> For analyzing a Snowflake account, also read
> [docs/user-guide/23-snowflake.md](docs/user-guide/23-snowflake.md)
> — Snowflake uses its built-in **OAuth refresh-token** integration
> (see [ADR-0007](docs/adr/0007-snowflake-auth.md)), so the AAD
> fields are likewise skipped.
> For analyzing a **standalone Dedicated SQL pool (formerly SQL DW)**
> — a `Microsoft.Sql/servers/<server>/databases/<db>` with
> `edition='DataWarehouse'` and **no** parent Synapse workspace — also
> read [docs/user-guide/24-standalone-dedicated-sql.md](docs/user-guide/24-standalone-dedicated-sql.md)
> and [ADR-0009](docs/adr/0009-standalone-dedicated-sql.md). It uses
> the same Azure SP as Synapse but discovers via
> `Microsoft.Sql/servers` and connects to `<server>.database.windows.net`.

| # | Module | Source(s) | CLI command | Source |
|---|---|---|---|---|
| 1 | `dedicated_pools`  | Synapse + standalone DWU | `sma analyze-dedicated-pools`  | [src/.../modules/dedicated_pools/](src/usma/modules/dedicated_pools) |
| 2 | `serverless_pools` | Synapse | `sma analyze-serverless-pools` | [src/.../modules/serverless_pools/](src/usma/modules/serverless_pools) |
| 3 | `spark_pools`      | Synapse | `sma analyze-spark-pools`      | [src/.../modules/spark_pools/](src/usma/modules/spark_pools) |
| 4 | `pipelines`        | Synapse + ADF | `sma analyze-pipelines`        | [src/.../modules/pipelines/](src/usma/modules/pipelines) |
| 4b | `databricks_workflows` | Databricks | _via_ `sma analyze-all` | [src/.../modules/databricks_workflows/](src/usma/modules/databricks_workflows) |
| 4c | `bigquery_workloads` | BigQuery | _via_ `sma analyze-all` | [src/.../modules/bigquery_workloads/](src/usma/modules/bigquery_workloads) |
| 4d | `snowflake_workloads` | Snowflake | _via_ `sma analyze-all` | [src/.../modules/snowflake_workloads/](src/usma/modules/snowflake_workloads) |
| 5 | `monitoring`       | Synapse + standalone DWU | `sma analyze-monitoring`       | [src/.../modules/monitoring/](src/usma/modules/monitoring) |
| 6 | `storage`          | Synapse + standalone DWU | `sma analyze-storage`          | [src/.../modules/storage/](src/usma/modules/storage) |
| 7 | `fabric_mapping`   | Synapse + ADF | `sma map-to-fabric`            | [src/.../modules/fabric_mapping/](src/usma/modules/fabric_mapping) |
| 8 | `governance` (v1.2)        | Synapse | `sma analyze-governance`       | [src/.../modules/governance/](src/usma/modules/governance) |
| 9 | `security`   (v1.2)        | Synapse | `sma analyze-security`         | [src/.../modules/security/](src/usma/modules/security) |
| 10 | `cost`       (v1.2)       | Synapse + ADF | `sma analyze-cost`             | [src/.../modules/cost/](src/usma/modules/cost) |
| 11 | `fabric_validation` (v0)  | Fabric target | `sma validate-fabric`         | [src/.../modules/fabric_validation/](src/usma/modules/fabric_validation) |
| ★ | _all_              | —            | `sma analyze-all`              | runs 1→7 + opt-in mid-term modules; fans out per scope when multiple are configured; refreshes `index.html`, emits run manifest + delta |
| 🔄 | _delta_            | —            | `sma run-delta`                | re-emits `run_manifest.json` + `run_delta.{md,html,json}` against the previous run |
| 🔗 | _index_            | —            | `sma index`                    | (re)builds the `index.html` landing page in `SMA_OUTPUT_DIR` |
| ⚙ | _self-check_       | —            | `sma doctor`                   | host + auth pre-flight ([doctor.py](src/usma/doctor.py)) |
| 🛠 | _migration_        | —            | `sma migrate-run-attribution`  | rewrites legacy `run.json` files so BigQuery / Snowflake-on-AWS / Databricks-on-AWS·GCP runs no longer inherit Azure tenant / subscription IDs (`--dry-run` previews). See [docs/user-guide/12-configuration.md](docs/user-guide/12-configuration.md#fix-non-azure-run-attribution) |

---

## 0. One-time setup (applies to all modules)

### 0.0 Security prerequisites at a glance

USMA is a **read-only assessment tool**. It never writes to Azure, never
exfiltrates data off the host, and has no telemetry. Before you run it
against a production workspace, make sure each of the items below is in
place — every link points to **official Microsoft / Google documentation**
so your security / compliance team can verify the controls independently.

For the exhaustive surface-by-surface breakdown, see
[docs/user-guide/17-access-and-security.md](docs/user-guide/17-access-and-security.md)
or generate a reviewer-ready report with `sma access-report --out access-report.md`.

#### 0.0.1 Identity & credentials

- **Microsoft Entra service principal** is the default identity. Create one
  per environment; do not share an SP across tenants.
  [Create a service principal (portal)](https://learn.microsoft.com/entra/identity-platform/howto-create-service-principal-portal)
  · [`az ad sp create-for-rbac`](https://learn.microsoft.com/cli/azure/ad/sp#az-ad-sp-create-for-rbac)
- **Rotate the client secret** on a schedule and store the active value in a
  secret manager (e.g. Azure Key Vault). USMA loads it from `.env` at runtime
  only; the secret is never written to logs, outputs, or run manifests.
  [Key Vault secrets](https://learn.microsoft.com/azure/key-vault/secrets/about-secrets)
  · [App registration credentials lifecycle](https://learn.microsoft.com/entra/identity-platform/howto-create-service-principal-portal#option-3-create-a-new-application-secret)
- **Prefer managed identities or workload identity federation** where the
  host supports them (Azure VM, Container App, GitHub Actions). USMA picks
  these up automatically via `DefaultAzureCredential` when the `AZURE_CLIENT_SECRET`
  is left unset.
  [DefaultAzureCredential](https://learn.microsoft.com/azure/developer/python/sdk/authentication-azure-hosted-apps)
  · [Workload identity federation](https://learn.microsoft.com/entra/workload-id/workload-identity-federation)
- **Conditional Access policies** that require MFA on service principals are
  honoured — the analyzer will fail fast with the standard AAD error.
  [Conditional Access for workload identities](https://learn.microsoft.com/entra/identity/conditional-access/workload-identity)

#### 0.0.2 Least-privilege RBAC (Azure)

Assign every role at the **narrowest scope** that still covers the resource
you are analyzing (workspace > resource group > subscription).

| Role | Scope | Required for | Reference |
|---|---|---|---|
| [Reader](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#reader) | Workspace / RG / Subscription | All modules (ARM control plane) | [Azure built-in roles](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles) |
| [Monitoring Reader](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#monitoring-reader) | RG or Subscription | `analyze-monitoring`, `analyze-storage` (capacity metrics) | [Azure Monitor roles](https://learn.microsoft.com/azure/azure-monitor/roles-permissions-security) |
| [Cost Management Reader](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#cost-management-reader) | RG or Subscription | `analyze-cost` | [Assign Cost Management access](https://learn.microsoft.com/azure/cost-management-billing/costs/assign-access-acm-data) |
| Reader on each linked storage account | Storage account / RG | `analyze-storage` for non-default accounts | [Storage RBAC](https://learn.microsoft.com/azure/storage/blobs/assign-azure-role-data-access) |

> The `Reader` role grants list/get only. USMA never requests write actions
> against Azure resources — the [Azure activity log](https://learn.microsoft.com/azure/azure-monitor/essentials/activity-log)
> will show `read` operations only.

#### 0.0.3 Synapse RBAC (data plane)

**Azure RBAC and [Synapse RBAC](https://learn.microsoft.com/azure/synapse-analytics/security/synapse-workspace-understand-what-role-you-need)
are separate.** Being subscription Owner does **not** grant artifact access.

| Synapse role | Required for | Reference |
|---|---|---|
| [Synapse Artifact User](https://learn.microsoft.com/azure/synapse-analytics/security/synapse-workspace-synapse-rbac-roles#synapse-artifact-user) | `analyze-pipelines`, `analyze-spark-pools` (notebooks / SJDs), `analyze-security` (linked services) | [Synapse RBAC roles](https://learn.microsoft.com/azure/synapse-analytics/security/synapse-workspace-synapse-rbac-roles) |
| [Synapse Compute Operator](https://learn.microsoft.com/azure/synapse-analytics/security/synapse-workspace-synapse-rbac-roles#synapse-compute-operator) | `analyze-spark-pools` Livy job-history (vCore-h & Fabric CU-h projection) | same |

Grant via Synapse Studio → **Manage** → [Access control](https://learn.microsoft.com/azure/synapse-analytics/security/how-to-manage-synapse-rbac-role-assignments).

#### 0.0.4 SQL access (Synapse dedicated + serverless pools)

The service principal must be added to **each pool / database** with the
minimum grants below. Use [Microsoft Entra authentication](https://learn.microsoft.com/azure/azure-sql/database/authentication-aad-overview)
exclusively — USMA never uses SQL logins / passwords.

```sql
-- Dedicated SQL pool (run as a Synapse AAD admin in EACH pool)
CREATE USER [sma-analyzer] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [sma-analyzer];
GRANT VIEW DATABASE STATE TO [sma-analyzer];
GRANT VIEW DEFINITION  TO [sma-analyzer];

-- Serverless SQL (run as a Synapse AAD admin in master)
CREATE LOGIN [sma-analyzer] FROM EXTERNAL PROVIDER;
GRANT VIEW SERVER STATE     TO [sma-analyzer];
GRANT VIEW ANY DEFINITION   TO [sma-analyzer];
```

References: [CREATE USER FROM EXTERNAL PROVIDER](https://learn.microsoft.com/sql/t-sql/statements/create-user-transact-sql)
· [Configure Synapse AAD admin](https://learn.microsoft.com/azure/synapse-analytics/sql/active-directory-authentication)
· [Database-level roles](https://learn.microsoft.com/sql/relational-databases/security/authentication-access/database-level-roles).

#### 0.0.5 Host security

- **OS encryption.** Run on an encrypted disk (BitLocker / FileVault / LUKS).
  Outputs and `.env` carry the same sensitivity tier as a SQL schema export.
- **ODBC driver.** Install [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server);
  TLS 1.2+ is enforced by default (`Encrypt=yes;TrustServerCertificate=no`).
- **`.env` placement.** Keep `.env` outside any synced folder (OneDrive,
  iCloud, Dropbox, source-controlled directories). The control plane
  tightens its file permissions to `0600` on POSIX hosts when it writes.
- **Outputs directory.** Treat `./output/` and `./runs/` like a schema
  export. They contain workspace metadata, schema/object names and SQL
  text — never row data, never plaintext secrets. Exclude from backups
  that cross a trust boundary.
- **`.gitignore`.** The repo's [`.gitignore`](.gitignore) already excludes
  `.env`, `.venv/`, `output/`, `runs/`. Do not override these.

#### 0.0.6 Network egress

USMA's outbound traffic is restricted to documented Azure / Google endpoints.
Allow-list these per workspace / region for tight-egress hosts:

- `https://login.microsoftonline.com` — [Microsoft Entra ID token endpoint](https://learn.microsoft.com/entra/identity-platform/v2-protocols)
- `https://management.azure.com` — [Azure Resource Manager](https://learn.microsoft.com/azure/azure-resource-manager/management/overview)
- `https://<workspace>.dev.azuresynapse.net` — [Synapse Artifacts REST](https://learn.microsoft.com/rest/api/synapse/data-plane/operation-groups)
- `https://<workspace>.sql.azuresynapse.net` and `https://<workspace>-ondemand.sql.azuresynapse.net` — [Synapse SQL endpoints](https://learn.microsoft.com/azure/synapse-analytics/sql/connect-overview)
- `https://<storage>.dfs.core.windows.net`, `https://<storage>.blob.core.windows.net` — [ADLS Gen2 endpoints](https://learn.microsoft.com/azure/storage/blobs/data-lake-storage-introduction)
- `https://bigquery.googleapis.com`, `https://logging.googleapis.com` — when analyzing BigQuery ([Google Cloud endpoints](https://cloud.google.com/bigquery/docs/reference/rest))

#### 0.0.7 Web control plane (`sma serve --with-api`)

The optional browser UI is **loopback-only, single-user, and has no
authentication**. See the per-control plane threat model in
[docs/user-guide/14-security.md](docs/user-guide/14-security.md) and
[SECURITY.md](SECURITY.md). Key constraints:

- Binds `127.0.0.1` only. Non-loopback binds require an explicit
  `--i-know-this-is-not-auth` flag.
- All write endpoints require the `X-SMA-API: 1` header (CSRF defence in
  depth). CORS is disabled.
- `GET /api/config` never returns the client secret — only `set` / `unset`.
- If you need remote access, terminate at an authenticating reverse proxy.
  [Azure Front Door + Microsoft Entra auth](https://learn.microsoft.com/azure/frontdoor/standard-premium/how-to-configure-aad-auth)
  or [App Service Easy Auth](https://learn.microsoft.com/azure/app-service/overview-authentication-authorization)
  are common patterns.
- **Do not expose the control plane on shared / multi-user hosts.**

#### 0.0.8 Source-specific prerequisites

USMA can analyze four source types in a single run. Each one has its own
identity model and least-privilege role set, summarized below. The
per-source user-guide chapters carry the full collector / module matrix.

##### Azure Data Factory (ADF)

Reuses the **same Microsoft Entra service principal** as Synapse — no
extra credentials needed. All ADF reads go through ARM; the data plane
is untouched.

| Plane | Required role | Scope | Reference |
|---|---|---|---|
| ARM control plane | [Reader](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#reader) | Factory / RG / Subscription | [ADF roles & requirements](https://learn.microsoft.com/azure/data-factory/concepts-roles-permissions) |
| Cost Management | [Cost Management Reader](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#cost-management-reader) | RG / Subscription | [Assign Cost Management access](https://learn.microsoft.com/azure/cost-management-billing/costs/assign-access-acm-data) |

> ADF Contributor / Data Factory Contributor is **not** required. The
> analyzer never invokes `createRun`, `cancelRun`, or any write action.
> See [ADF built-in roles](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#data-factory-contributor)
> and [docs/user-guide/20-data-factory.md](docs/user-guide/20-data-factory.md).

##### Azure Databricks *(alpha)*

Reuses the same Microsoft Entra SP, but the SP must **also be a workspace
user** in Databricks itself. The analyzer authenticates via the standard
[Microsoft Entra OAuth flow for Databricks](https://learn.microsoft.com/azure/databricks/dev-tools/auth/aad/service-prin-aad-token)
(audience `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default`) — **no personal
access tokens (PATs)** are issued or stored.

| Plane | Required role | Scope | Reference |
|---|---|---|---|
| ARM control plane | [Reader](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#reader) | Databricks workspace / RG | [Azure Databricks RBAC](https://learn.microsoft.com/azure/databricks/admin/users-groups/service-principals#--add-a-microsoft-entra-id-service-principal-to-azure-databricks-using-the-azure-portal) |
| Workspace data plane (`/api/2.1/jobs/list`) | **User** entitlement (or **Workflows access** under SCIM) | Databricks workspace | [Service principals in workspaces](https://learn.microsoft.com/azure/databricks/admin/users-groups/service-principals) · [Workspace entitlements](https://learn.microsoft.com/azure/databricks/admin/users-groups/groups#entitlements) |
| Cost Management | [Cost Management Reader](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles#cost-management-reader) | RG / Subscription | [Cost Management RBAC](https://learn.microsoft.com/azure/cost-management-billing/costs/assign-access-acm-data) |

Add the SP to the workspace via **Workspace settings → Identity and
access → Service principals** ([guide](https://learn.microsoft.com/azure/databricks/admin/users-groups/service-principals#--add-a-microsoft-entra-id-service-principal-to-azure-databricks-using-the-azure-portal))
and assign at least the **User** entitlement. If your workspace uses
[Unity Catalog](https://learn.microsoft.com/azure/databricks/data-governance/unity-catalog/),
no extra catalog-level grants are required for the alpha collector. See
[docs/user-guide/21-databricks.md](docs/user-guide/21-databricks.md).

##### Databricks on AWS *(alpha — Phase 4.7)*

When `SMA_DATABRICKS_PLATFORM=aws`, the analyzer swaps the Azure
ARM-backed provider for `DatabricksAwsProvider` and the four
`AZURE_*` SP variables are ignored. Auth is one of: **PAT**
(`DATABRICKS_TOKEN`), **OAuth M2M** (`DATABRICKS_CLIENT_ID` +
`DATABRICKS_CLIENT_SECRET`), or an attached EC2 **instance profile**.
For multi-workspace discovery, also set `DATABRICKS_ACCOUNT_ID` plus
the account-level `DATABRICKS_ACCOUNT_CLIENT_ID` /
`DATABRICKS_ACCOUNT_CLIENT_SECRET`. CLI:
`sma analyze-all --scope databricks-aws:<workspace-host>`. Cost
attribution via the Databricks Usage API is **Phase 4.7.5** — AWS
scopes emit a `caveat` instead of cost rows. See
[docs/user-guide/21a-databricks-aws.md](docs/user-guide/21a-databricks-aws.md)
and [ADR-0005](docs/adr/0005-multi-cloud-databricks.md).

##### Google BigQuery *(alpha)*

BigQuery does **not** use Azure credentials. The analyzer authenticates via
[Application Default Credentials (ADC)](https://cloud.google.com/docs/authentication/application-default-credentials)
— the four `AZURE_*` env vars are ignored when `SMA_SOURCE_TYPE=bigquery`.

| Plane | Required IAM role | Scope | Reference |
|---|---|---|---|
| BigQuery metadata | [`roles/bigquery.metadataViewer`](https://cloud.google.com/bigquery/docs/access-control#bigquery.metadataViewer) | Project | [BigQuery IAM roles](https://cloud.google.com/bigquery/docs/access-control) |
| BigQuery data (for `INFORMATION_SCHEMA` fallbacks) | [`roles/bigquery.dataViewer`](https://cloud.google.com/bigquery/docs/access-control#bigquery.dataViewer) | Project | same |
| Cloud Logging (job-completion entries) | [`roles/logging.viewer`](https://cloud.google.com/logging/docs/access-control#logging.viewer) | Project | [Cloud Logging IAM](https://cloud.google.com/logging/docs/access-control) |
| Data Transfer Service (scheduled queries) | [`roles/bigquerydatatransfer.user`](https://cloud.google.com/bigquery/docs/use-service-accounts) | Project | optional — analyzer emits a `caveat` if missing |
| Project auto-discovery (SPA only) | [`roles/browser`](https://cloud.google.com/iam/docs/understanding-roles#browser-role) **or** [`roles/resourcemanager.projectViewer`](https://cloud.google.com/iam/docs/understanding-roles#resourcemanager.projectViewer) | Organization / project | [Cloud Resource Manager](https://cloud.google.com/resource-manager/docs/access-control-proj) |

**Authentication options** (pick one; all flow through
[`google.auth.default()`](https://cloud.google.com/docs/authentication/provide-credentials-adc)):

1. **Service-account JSON key** (CI / non-interactive). Generate via
   [creating service-account keys](https://cloud.google.com/iam/docs/keys-create-delete);
   point `GOOGLE_APPLICATION_CREDENTIALS` at the absolute path. Treat the
   key file the same as `.env` — keep it off synced folders and rotate
   regularly. Better: prefer
   [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation)
   so no static key ever touches the host.
2. **User credentials** (`gcloud auth application-default login`) for
   dev boxes — see [gcloud ADC docs](https://cloud.google.com/sdk/gcloud/reference/auth/application-default/login).
3. **Browser sign-in from the SPA** — the Configuration page's BigQuery
   card runs an installed-app OAuth flow against `localhost`; only works
   when the SMA backend and browser are on the same machine.

See [docs/user-guide/22-bigquery.md](docs/user-guide/22-bigquery.md) for
the complete collector matrix and BigQuery-specific gotchas (slot-hour
projection, regional endpoint pinning, billing-export dataset).

#### 0.0.9 Pre-run checklist

- [ ] Service principal created at the narrowest tenant scope possible.
- [ ] Reader assigned at workspace / RG (not subscription) when feasible.
- [ ] Monitoring Reader / Cost Management Reader assigned only if running
      those modules.
- [ ] Synapse Artifact User (and Compute Operator for Spark Livy) granted
      via Synapse Studio.
- [ ] SQL grants applied per §0.0.4 in every pool.
- [ ] `.env` written outside any synced / version-controlled folder.
- [ ] Host disk is encrypted; `runs/` and `output/` are excluded from
      cross-boundary backups.
- [ ] Control plane (`--with-api`) not exposed beyond loopback.
- [ ] `sma access-report --out access-report.md` captured for the change
      ticket.

---

### 0.1 Install host prerequisites

- **Python 3.12+** — `python --version` (Linux: `python3.12 --version`)
- **Microsoft ODBC Driver 18 for SQL Server** (required by `pyodbc`)
  - Windows: <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>
  - Ubuntu / Debian (apt):
    ```bash
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | sudo gpg --dearmor -o /usr/share/keyrings/microsoft.gpg
    echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft.gpg] \
      https://packages.microsoft.com/ubuntu/$(lsb_release -rs)/prod $(lsb_release -cs) main" \
      | sudo tee /etc/apt/sources.list.d/mssql-release.list
    sudo apt-get update
    sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
    ```
  - macOS (Homebrew):
    ```bash
    brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
    brew update && HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18 mssql-tools18
    ```
  - Older Ubuntu LTS releases that only ship driver 17 are auto-detected — `sma`
    will fall back to `ODBC Driver 17 for SQL Server` and `sma doctor` prints
    a warn-only line so you know what was picked.
- **Git** (to clone the repo)
- **Node.js 18+** + **npm** (only if you want the browser UI bundled by `quickstart.sh` / `quickstart.ps1`; pass `--skip-web-build` / `-SkipWebBuild` otherwise)
- **Azure CLI 2.50+** (`az`) — used in [§0.3](#03-create-a-service-principal--grant-access) to create the service principal
  - Windows: <https://learn.microsoft.com/cli/azure/install-azure-cli-windows>
  - macOS / Linux: <https://learn.microsoft.com/cli/azure/install-azure-cli>
  - Verify: `az --version`
  - If you cannot install `az` on this host, see the **Portal alternative** in §0.3 below.

### 0.2 Clone & install

#### Fast path — `quickstart.ps1` (Windows)

The repo ships a [PowerShell bootstrapper](quickstart.ps1) that performs the
entire §0.2 sequence in one command. It clones the repo (skip with
`-SkipClone` if already cloned), creates `.venv`, installs **all** optional
extras (`dev,cost,web`) so every analyzer module and the control plane are
ready out of the box, builds the React SPA bundle in `web/dist`, runs
`sma doctor --offline`, and — when the doctor passes — auto-launches
`sma serve --with-api --static-dir web\dist` so the browser UI is live at
`http://127.0.0.1:8000/` at the end of bootstrap.

If you don't have the repo cloned yet, you can grab just the bootstrapper
straight from GitHub and run it from any working directory — it will clone
the repo for you (unless you pass `-SkipClone`):

```powershell
$url = 'https://raw.githubusercontent.com/Andreas-bersgtedt/USMA/main/quickstart.ps1'
Invoke-WebRequest -Uri $url -OutFile 'quickstart.ps1'
```

```powershell
# Public fork (default)
.\quickstart.ps1

# Private fork
.\quickstart.ps1 -Repo Private

# CLI-only host (no Node, no browser UI)
.\quickstart.ps1 -SkipWebBuild -NoServe
```

Key switches: `-SkipClone`, `-Branch <name>`, `-PythonExe py`,
`-SkipDoctor`, `-SkipWebBuild`, `-NoServe`. See the script header for the
full parameter list.

#### Fast path — `quickstart.sh` (Linux / macOS)

The repo also ships a [Bash bootstrapper](quickstart.sh) that performs the
same end-to-end sequence on Ubuntu, other Linux distros, and macOS. It
installs **all** optional extras (`dev,cost,web,databricks,bigquery,snowflake`),
builds the SPA bundle, runs `sma doctor --offline`, and launches
`sma serve --with-api --static-dir web/dist`.

```bash
# Run directly from the cloned repo
chmod +x quickstart.sh
./quickstart.sh                           # public fork, main branch, full install + serve
./quickstart.sh --repo private            # private fork
./quickstart.sh --skip-web-build --no-serve  # CLI-only host
./quickstart.sh --branch feature/foo --skip-clone  # already cloned, just install

# Or download the bootstrapper and let it clone for you:
curl -fsSL https://raw.githubusercontent.com/Andreas-bersgtedt/USMA/main/quickstart.sh -o quickstart.sh
chmod +x quickstart.sh
./quickstart.sh
```

Key flags: `--skip-clone`, `--branch NAME`, `--python EXE`, `--skip-doctor`,
`--skip-web-build`, `--no-serve`. Run `./quickstart.sh --help` for the
full list.

The Linux/macOS paths honour the **XDG Base Directory spec**:
`$XDG_CACHE_HOME/usma` (default `~/.cache/usma`) for cached pricing data,
`$XDG_DATA_HOME/usma` (default `~/.local/share/usma`) for the per-user run
repository when `./runs` is absent. Set `SMA_RUNS_DIR` to override.

After the bootstrapper finishes, jump to [§0.3](#03-create-a-service-principal--grant-access)
for service-principal setup, then [§0.4](#04-configure-env) to fill in `.env`.

#### Manual path

```powershell
git clone https://github.com/anbergst_microsoft/USMA.git
cd USMA

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Optional: enable live Cost Management queries for the `cost` module
pip install -e ".[cost]"

# Optional: enable the browser-driven control plane (`sma serve --with-api`)
pip install -e ".[web]"
```

> The `cost` extra installs `azure-mgmt-costmanagement`. Without it, `sma analyze-cost`
> will run but emit a `cost.sdk_missing` finding instead of live data.
>
> The `web` extra installs FastAPI + uvicorn + sse-starlette + httpx. Without it,
> `sma serve` still works as a static file server, but `sma serve --with-api`
> exits with an install error. See [Optional — browser-driven control plane](#optional--browser-driven-control-plane).

Verify the CLI is on the path:

```powershell
sma --version
sma --help
```

### 0.3 Create a service principal & grant access

> **Dependency:** this step uses the **Azure CLI** (`az`). Install it via the link in [§0.1](#01-install-host-prerequisites) before running the commands below. Verify with `az --version` first. If you cannot install `az`, jump to the **Portal alternative** at the end of this section.

#### 0.3.1 Sign in and pick the right subscription

```powershell
az login                                # opens a browser; sign in as a user with rights to create app registrations
az account show --query "{tenantId:tenantId, subscriptionId:id, name:name}" -o table
az account set --subscription "<SUB_ID_OR_NAME>"   # only if you have multiple subscriptions
```

Keep `tenantId` and `subscriptionId` from the output — they go straight into [§0.4](#04-configure-env) as `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`.

#### 0.3.2 Find the workspace resource ID

```powershell
az synapse workspace show `
  --name <WS> --resource-group <RG> `
  --query id -o tsv
```

> Don't have the `synapse` extension? Run `az extension add --name synapse` once. (`az resource show --resource-type Microsoft.Synapse/workspaces ...` works without the extension.)

#### 0.3.3 Create the service principal

```powershell
# Create the SP and capture the appId/password (clientId/clientSecret).
# Replace <SUB_ID>, <RG>, <WS> with the values from §0.3.1 / §0.3.2.
az ad sp create-for-rbac --name "sma-analyzer" --role Reader `
  --scopes /subscriptions/<SUB_ID>/resourceGroups/<RG>/providers/Microsoft.Synapse/workspaces/<WS>
```

The command prints something like:

```json
{
  "appId":       "00000000-0000-0000-0000-000000000000",   // → AZURE_CLIENT_ID
  "displayName": "sma-analyzer",
  "password":    "<one-time-secret>",                       // → AZURE_CLIENT_SECRET (shown ONCE)
  "tenant":      "00000000-0000-0000-0000-000000000000"     // → AZURE_TENANT_ID
}
```

**Copy the `password` immediately** — it is shown only once. If you lose it, recreate the credential with `az ad sp credential reset --id <appId>`.

The `Reader` role above covers **control-plane** (ARM) calls. Each module that hits a **data-plane** endpoint (SQL pools, Synapse Artifacts, Azure Monitor) requires additional grants documented in that module's section below — typically a SQL `CREATE USER ... FROM EXTERNAL PROVIDER`, **Synapse Artifact User**, and **Monitoring Reader**.

#### 0.3.4 Common `az` errors

| Symptom | Cause | Fix |
|---|---|---|
| `'az' is not recognized as the name of a cmdlet` | Azure CLI not installed or not on `PATH` | Install per §0.1 and reopen PowerShell |
| `Insufficient privileges to complete the operation` | Your signed-in user can't create app registrations in the tenant | Ask a Microsoft Entra ID admin to run §0.3.3, or use the **Portal alternative** below |
| `The role assignment already exists` | SP already has Reader at that scope | Safe to ignore |
| `(AuthorizationFailed) ... does not have authorization to perform action 'Microsoft.Authorization/roleAssignments/write'` | You can create the SP but not assign roles | Run `az ad sp create-for-rbac` **without** `--role`/`--scopes`, then have an Owner of the workspace assign **Reader** to the new SP via the portal (IAM blade) |

#### Portal alternative (no `az` required)

1. **Microsoft Entra ID → App registrations → + New registration** — name it `sma-analyzer`, accept defaults. After creation, copy **Application (client) ID** → `AZURE_CLIENT_ID` and **Directory (tenant) ID** → `AZURE_TENANT_ID`.
2. **Certificates & secrets → + New client secret** — set an expiry, copy the **Value** column immediately → `AZURE_CLIENT_SECRET`.
3. **Synapse workspace → Access control (IAM) → + Add → Add role assignment** — role **Reader**, assign access to **User, group, or service principal**, search for `sma-analyzer`, save. Repeat for **Monitoring Reader** (subscription / resource-group scope) if you plan to run `sma analyze-monitoring`, and for **Synapse Artifact User** (Synapse Studio → Manage → Access control) for `sma analyze-pipelines` / `sma analyze-spark-pools`. Add **Synapse Compute Operator** (same Synapse RBAC blade) if you want `analyze-spark-pools` to also collect Livy job-history (interactive sessions + scheduled batches with Fabric CU-h projection) — Synapse Artifact User alone is not sufficient.
4. Continue with [§0.4](#04-configure-env).

### 0.3.5 Permissions cheat-sheet (read this if you hit `Unauthorized`)

Azure RBAC and **Synapse RBAC** are *separate*. Being Owner of the subscription does **not** grant access to artifacts inside a Synapse workspace — Synapse RBAC is managed inside the workspace itself.

| Plane | Used by | Role to assign | Where to assign it |
|---|---|---|---|
| **Azure ARM (control plane)** | Pool definitions, Spark pool ARM details, dedicated/serverless SQL pool listing | **Reader** | Subscription, resource group, **or** workspace resource (any of these) |
| **Synapse artifacts (data plane)** — `*.dev.azuresynapse.net` | `analyze-pipelines`, `analyze-spark-pools` (notebooks, SJDs, linked services, datasets, triggers) | **Synapse Artifact User** (read-only) or **Synapse User** | Synapse Studio → **Manage → Access control** at workspace scope |
| **Synapse Spark Livy** — `*.dev.azuresynapse.net/livyApi` | `analyze-spark-pools` Livy job-history (interactive sessions + scheduled batches, vCore-h, Fabric CU-h projection) | **Synapse Compute Operator** (or higher, e.g. **Synapse Administrator**) — grants the action `Microsoft.Synapse/workspaces/bigDataPools/useCompute/action`. Synapse Artifact User is **not** sufficient for Livy. | Synapse Studio → **Manage → Access control**, scope `Workspace` (or per Spark pool) |
| **Azure Monitor metrics** | `analyze-monitoring` | **Monitoring Reader** | Subscription or resource group containing the workspace |
| **Azure Cost Management** | `analyze-cost` | **Cost Management Reader** | Subscription or the workspace's resource group |
| **SQL endpoints** (dedicated + serverless) | `analyze-dedicated-pools`, `analyze-serverless-pools` | SQL `CREATE USER [<sp-name>] FROM EXTERNAL PROVIDER;` + `db_datareader` + `GRANT VIEW DATABASE STATE` + `GRANT VIEW DEFINITION` (so procedures / functions are visible in `sys.objects` / `sys.sql_modules`) | Inside each database (run as a SQL admin) |

Grant Synapse RBAC via **Synapse Studio**:
`https://web.azuresynapse.net` → pick workspace → **Manage** → **Access control** → **+ Add** → scope `Workspace`, role **Synapse Artifact User**, paste the principal's **object ID** or app name → **Apply**. Allow ~1 minute for propagation.

Or via Azure CLI (run as a workspace admin):

```powershell
az synapse role assignment create `
  --workspace-name <WS> `
  --role "Synapse Artifact User" `
  --assignee <APP_ID_OR_OBJECT_ID>
```

**Symptom → fix:**

| Error fragment in `errors[]` | Cause | Fix |
|---|---|---|
| `notebooks: (Unauthorized) ... Microsoft.Synapse/workspaces/artifacts/read` | Missing Synapse RBAC | Assign **Synapse Artifact User** at workspace scope |
| `spark_job_definitions: (Unauthorized) ... artifacts/read` | Missing Synapse RBAC | Assign **Synapse Artifact User** at workspace scope |
| `pipelines: (Unauthorized) ... artifacts/read` | Missing Synapse RBAC | Assign **Synapse Artifact User** at workspace scope |
| `spark_history_batches[<pool>]: (Unauthorized) ... Microsoft.Synapse/workspaces/bigDataPools/useCompute/action` | Missing Synapse RBAC for Spark Livy. Synapse Artifact User does **not** include `useCompute`. | Assign **Synapse Compute Operator** (or higher) at workspace scope or per Spark pool |
| `spark_history_sessions[<pool>]: (Unauthorized) ... bigDataPools/useCompute/action` | Same as above (Livy session listing requires the same action) | Assign **Synapse Compute Operator** |
| `metrics[...]: (Forbidden)` from Azure Monitor | Missing **Monitoring Reader** | Assign **Monitoring Reader** at the resource-group / subscription scope |
| `Login failed for user '<token-identified principal>'` (SQL) | SP not added to the database | Run `CREATE USER [<sp-name>] FROM EXTERNAL PROVIDER;` then `ALTER ROLE db_datareader ADD MEMBER [<sp-name>];` |

The analyzer surfaces unauthorized artifact errors with an inline hint, e.g.:

```
notebooks: (Unauthorized) ... [hint: missing Synapse RBAC role on the workspace.
Grant 'Synapse Artifact User' (or higher) to this principal at workspace scope.
See QUICKSTART.md section 'Permissions' for details.]
```

### 0.4 Configure `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

Required for every module:

```ini
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_SUBSCRIPTION_ID=...
SYNAPSE_RESOURCE_GROUP=...
SYNAPSE_WORKSPACE_NAME=...
SMA_OUTPUT_DIR=./output
```

See [.env.example](.env.example) for the full list and per-module overrides.

### 0.5 Smoke-test the install

The CLI ships with a consolidated self-check. It verifies the host (Python version, ODBC driver), every required Python package, the `.env` file and required variables, output-directory permissions, and (when credentials are present) live Azure access:

```powershell
sma doctor             # full check: includes live AAD + Synapse workspace probe
sma doctor --offline   # skip live Azure calls — useful before populating .env
```

Sample output (offline mode, fresh install):

```text
                              sma doctor
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳────────────────────────────────────┓
┃ Check                          ┃ Status ┃ Detail                             ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇────────────────────────────────────┩
│ Python >= 3.12                 │  PASS  │ 3.12.10 on Windows-11               │
│ import azure.identity          │  PASS  │                                    │
│ import azure.mgmt.synapse      │  PASS  │                                    │
│ import azure.mgmt.resource     │  PASS  │                                    │
│ import azure.mgmt.monitor      │  PASS  │                                    │
│ import azure.mgmt.storage      │  PASS  │                                    │
│ import azure.synapse.artifacts │  PASS  │                                    │
│ import pyodbc                  │  PASS  │                                    │
│ ODBC driver                    │  PASS  │ ODBC Driver 18 for SQL Server      │
│ .env present                   │  PASS  │ ./.env                              │
│ Required env vars              │  PASS  │ 6 variables set                    │
│ Output dir writable            │  PASS  │ ./output                            │
│ AAD token (ARM)                │  SKIP  │ live checks disabled (--offline)   │
│ Synapse workspace reachable    │  SKIP  │ live checks disabled (--offline)   │
│ SQL access token               │  SKIP  │ live checks disabled (--offline)   │
└────────────────────────────────┴────────┴────────────────────────────────────┘
All required checks passed.
```

`sma doctor` exits **0** on success and **1** when any required check fails — safe to use in CI.

You can also run the full pytest suite:

```powershell
pytest -q
```

All suites pass without any Azure credentials configured: config loading, reporting writers, fabric_mapping rules, the T-SQL surface scanner, the pipelines Fabric-compat classifier, the monitoring reporter, and the doctor self-checks.

---

## Module 1 — `dedicated_pools`

Inventories every dedicated SQL pool in the configured Synapse workspace and collects schemas, tables (with distribution/partitioning/storage), indexes, a usage snapshot, security principals, workload-management groups, and T-SQL code objects. The **v2** layer adds a column-level collation audit, materialized-view inventory, statistics-freshness report, column-level stats, a distribution-key advisor (skew + filter-selectivity heuristics), and a per-object T-SQL surface gap rollup linked back to each finding via a stable `code_object_id`.

> **Both topologies supported (v5.4).** Set `SMA_SOURCE_TYPE=synapse_dedicated_sql`
> to point this module at a **standalone Dedicated SQL pool (formerly SQL DW)**
> — a `Microsoft.Sql/servers/<server>/databases/<db>` with `edition='DataWarehouse'`
> and no parent Synapse workspace. The DMV surface, collectors, and analyzer
> rules are identical to the workspace-pool path; only ARM discovery and the
> endpoint FQDN (`<server>.database.windows.net`) differ. See
> [docs/user-guide/24-standalone-dedicated-sql.md](docs/user-guide/24-standalone-dedicated-sql.md)
> and [ADR-0009](docs/adr/0009-standalone-dedicated-sql.md).

### 1.1 What it captures

| Area | Source | File |
|---|---|---|
| Pool inventory, SKU, DWU, status, collation, max size | ARM (`azure-mgmt-synapse`) | [arm_client.py](src/usma/modules/dedicated_pools/arm_client.py) |
| Schemas + object counts | `sys.schemas` / `sys.objects` | [queries/schemas.sql](src/usma/modules/dedicated_pools/queries/schemas.sql) |
| Tables: distribution, partitioning, rows, MB, index type | `sys.pdw_*`, `sys.dm_pdw_nodes_db_partition_stats` | [queries/tables.sql](src/usma/modules/dedicated_pools/queries/tables.sql) |
| Indexes (CCI / heap / clustered / NCI) | `sys.indexes` | [queries/indexes.sql](src/usma/modules/dedicated_pools/queries/indexes.sql) |
| Usage snapshot (active/completed/failed requests, durations, sessions) | `sys.dm_pdw_exec_*` | [queries/usage.sql](src/usma/modules/dedicated_pools/queries/usage.sql) |
| Database principals & role memberships | `sys.database_principals`, `sys.database_role_members` | [queries/security.sql](src/usma/modules/dedicated_pools/queries/security.sql) |
| Workload groups & classifier counts | `sys.workload_management_*` | [queries/workload.sql](src/usma/modules/dedicated_pools/queries/workload.sql) |
| Code objects (procs / views / functions) with stable `code_object_id` | `sys.sql_modules` | [queries/code_objects.sql](src/usma/modules/dedicated_pools/queries/code_objects.sql) |
| Top consumed tables / views (sqlglot AST + 30-day workload cache) | `sys.dm_pdw_exec_requests` + `INFORMATION_SCHEMA.TABLES` | [queries/workload_commands.sql](src/usma/modules/dedicated_pools/queries/workload_commands.sql) + [workload_parser.py](src/usma/modules/dedicated_pools/workload_parser.py) |
| **v2** Column collation audit (per-column vs DB default) | `sys.columns` + `sys.databases` | [queries/column_collation.sql](src/usma/modules/dedicated_pools/queries/column_collation.sql) |
| **v2** Materialized-view inventory | `sys.views` + `sys.indexes` | [queries/materialized_views.sql](src/usma/modules/dedicated_pools/queries/materialized_views.sql) |
| **v2** Statistics freshness (last_updated, modification_counter) | `sys.stats` + `sys.dm_db_stats_properties` | [queries/statistics_freshness.sql](src/usma/modules/dedicated_pools/queries/statistics_freshness.sql) |
| **v2** Column-level row/distinct/null/skew stats | derived | [queries/column_stats.sql](src/usma/modules/dedicated_pools/queries/column_stats.sql) |
| **v2** Distribution-key candidates (skew + filter-selectivity scoring) | pure-Python advisor | [distribution_advisor.py](src/usma/modules/dedicated_pools/distribution_advisor.py) + [query_pattern_extractor.py](src/usma/modules/dedicated_pools/query_pattern_extractor.py) |
| **v2** T-SQL surface gap rollup linked to `code_object_id` | pure-Python over code objects | [tsql_surface_gap.py](src/usma/modules/dedicated_pools/tsql_surface_gap.py) |

> Paused pools are detected via control-plane `status`; DMV collection is skipped and noted in the result's `errors` list.

### 1.2 Module-specific prerequisites

For **each** dedicated SQL pool you want to analyze, run this **once** (signed in as a Synapse-AAD admin):

```sql
-- Connect to the dedicated pool (e.g. <ws>.sql.azuresynapse.net, database=<pool>)
CREATE USER [sma-analyzer] FROM EXTERNAL PROVIDER;
EXEC sp_addrolemember 'db_datareader', 'sma-analyzer';
GRANT VIEW DATABASE STATE TO [sma-analyzer];
-- Required so sys.sql_modules / sys.objects expose stored procedures and
-- user-defined functions to the analyzer. Without this grant the catalog
-- silently hides P / FN / IF / TF rows and the report shows views only.
GRANT VIEW DEFINITION TO [sma-analyzer];
```

Optional `.env` knobs:

```ini
SYNAPSE_DEDICATED_POOL=         # leave blank to scan all pools; set to a name to limit
SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server
SQL_LOGIN_TIMEOUT=30
SQL_QUERY_TIMEOUT=120
```

### 1.3 Run

```powershell
sma analyze-dedicated-pools                          # all formats (json, csv, markdown, html)
sma analyze-dedicated-pools -f json -f markdown      # selected formats
sma -v analyze-dedicated-pools                       # verbose / debug logging
```

### 1.4 Outputs (under `./output/` by default)

- `dedicated_pools.json` — full structured result (machine-readable)
- `dedicated_pools.md` — human-readable summary report
- `dedicated_pools.html` — styled report (sharable; linked from `index.html`)
- Per-entity CSVs for spreadsheet workflows:
  - `pools_inventory.csv`
  - `schemas.csv`
  - `tables.csv`
  - `indexes.csv`
  - `usage.csv`
  - `security.csv`
  - `workload_groups.csv`
  - **v2** `column_collations.csv`, `materialized_views.csv`, `statistics.csv`, `column_stats.csv`, `distribution_candidates.csv`, `tsql_surface_gaps.csv` (each emitted only when its collector returned rows)

### 1.5 Programmatic use

```python
from usma.config import load_config
from usma.modules.dedicated_pools.analyzer import DedicatedPoolsAnalyzer
from usma.reporting import write_reports

cfg = load_config()
result = DedicatedPoolsAnalyzer(cfg).run()      # WorkspaceAnalysis Pydantic model

for pool in result.pools:
    print(pool.inventory.name, pool.inventory.sku_capacity, len(pool.tables), "tables")

write_reports(result, cfg.output_dir, formats=["json", "markdown"])
```

### 1.6 Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `Missing required environment variables` | `.env` not populated | Copy `.env.example` → `.env`, fill values |
| `Login failed for user '<token-identified principal>'` | SP not added to the pool | Run the `CREATE USER … FROM EXTERNAL PROVIDER` snippet in §1.2 |
| `The user does not have permission to perform this action` on DMVs | Missing `VIEW DATABASE STATE` | Run `GRANT VIEW DATABASE STATE TO [sma-analyzer]` |
| `code_objects` contains only views (no procedures or functions) | Missing `VIEW DEFINITION` — `sys.objects` / `sys.sql_modules` filter out P/FN/IF/TF rows for principals without it | Run `GRANT VIEW DEFINITION TO [sma-analyzer]` (per §1.2) and re-run the analyzer |
| `IM002 / Data source name not found` | ODBC Driver 18 not installed | Install the driver from the link in §0.1 |
| Pool reported but no tables/usage | Pool is **paused** | Resume the pool or accept the `errors` entry |
| `(pyodbc) … TLS/SSL` errors | TLS settings on host | Ensure `Encrypt=yes;TrustServerCertificate=no` (default) and the host trusts the cert chain |

---

## Module 2 — `serverless_pools`

Inventories the workspace's **built-in serverless SQL endpoint** (`<workspace>-ondemand.sql.azuresynapse.net`).

### 2.1 What it captures

| Area | Source | File |
|---|---|---|
| Logical databases on the serverless endpoint | `sys.databases` | [queries/databases.sql](src/usma/modules/serverless_pools/queries/databases.sql) |
| External data sources (per database) | `sys.external_data_sources` | [queries/external_data_sources.sql](src/usma/modules/serverless_pools/queries/external_data_sources.sql) |
| External tables (per database) | `sys.external_tables` + joins | [queries/external_tables.sql](src/usma/modules/serverless_pools/queries/external_tables.sql) |
| Top 100 expensive queries (last 14 d) | `sys.dm_exec_requests_history` | [queries/top_queries.sql](src/usma/modules/serverless_pools/queries/top_queries.sql) |
| Daily data-scanned aggregation (last 30 d) | `sys.dm_exec_requests_history` | [queries/data_processed.sql](src/usma/modules/serverless_pools/queries/data_processed.sql) |
| Cost estimate (USD / TB scanned) | derived | override list price via `SMA_SERVERLESS_PRICE_PER_TB` (default `5.0`) |
| Usage snapshot | scaffold (extend as needed) | [queries/usage.sql](src/usma/modules/serverless_pools/queries/usage.sql) |

### 2.2 Module-specific prerequisites

The same service principal needs SQL access on the **serverless** endpoint. Connect to `master` on `<ws>-ondemand.sql.azuresynapse.net` as a Synapse-AAD admin and run:

```sql
CREATE LOGIN [sma-analyzer] FROM EXTERNAL PROVIDER;
GRANT VIEW SERVER STATE TO [sma-analyzer];
GRANT VIEW ANY DEFINITION TO [sma-analyzer];
```

For each user database on serverless that you want full external-table inventory for, also run:

```sql
USE [<your-serverless-db>];
CREATE USER [sma-analyzer] FROM EXTERNAL PROVIDER;
GRANT VIEW DEFINITION TO [sma-analyzer];
```

### 2.3 Run & outputs

```powershell
sma analyze-serverless-pools
```

Produces (under `./output/`):
- `serverless_pools.json`, `serverless_pools.md`, `serverless_pools.html`
- `serverless_databases.csv`, `serverless_external_data_sources.csv`, `serverless_external_tables.csv`, `serverless_usage.csv`
- `serverless_top_queries.csv` (TOP 100 with `data_processed_mb`, `duration_seconds`, command text)
- `serverless_daily_usage.csv` (one row per day with request count + MB scanned)

---

## Module 3 — `spark_pools`

Inventories Apache Spark pools (a.k.a. *big data pools*) attached to the workspace. Control-plane only — no Spark cluster start required.

### 3.1 What it captures

| Area | Source |
|---|---|
| Pool name, location, Spark version, node size/family/count | `azure-mgmt-synapse` `big_data_pools` |
| Autoscale config (min/max nodes) | same |
| Auto-pause delay | same |
| Isolated compute, session-level packages, dynamic executor allocation | same |
| Provisioning state, creation date, tags | same |
| Notebook inventory (language, kernel, attached pool, cell count, size, imports) | Synapse Artifacts `notebook.get_notebooks_by_workspace` |
| Spark job definitions (target pool, main file, class, conf, args) | Synapse Artifacts `spark_job_definition.get_spark_job_definitions_by_workspace` |
| **Spark Livy job history** (interactive sessions + scheduled batch jobs, per pool) with vCore-second and **Fabric CU-hour** projection | `azure-synapse-spark` (`SparkClient` per pool, Livy `2019-11-01-preview`) |

### 3.2 Module-specific prerequisites

Notebooks and Spark job definitions live in the **Synapse Artifacts** plane, so the SP needs **Synapse Artifact User** (or higher) on the workspace — same role as `analyze-pipelines`. Without it, those calls are skipped (errors are logged into the result's `errors` list) and only the control-plane pool inventory is captured.

Livy job history (sessions + batches) requires **Synapse Compute Operator** (or higher) on each Spark pool — Artifact User alone is **not** sufficient. Failures are logged per pool but never abort the rest of the module.

### 3.3 Run & outputs

```powershell
sma analyze-spark-pools
```

Produces:
- `spark_pools.json`, `spark_pools.md`, `spark_pools.html`, `spark_pools.csv`
- `spark_notebooks.csv` (one row per notebook with extracted `imports` column)
- `spark_job_definitions.csv` (target pool, main file, class, args)

### 3.4 Spark execution (Livy history)

For each pool, the analyzer pulls Livy **batches** (`spark_batch.get_spark_batch_jobs`) and **sessions** (`spark_session.get_spark_sessions`), classifies them as `scheduled` (batches — Spark Job Definitions and pipeline-triggered SparkJob activities) or `interactive` (sessions — notebook REPL), and per run extracts the cluster shape from `app_info`: `driver_cores + executor_cores × num_executors = total_vcores`. Wall-clock seconds × total_vcores → **vCore-seconds**, then `/3600` → vCore-hours, then `× 0.5` → **Fabric CU-hours** (1 CU = 2 Spark vCores per [Fabric Spark billing docs](https://learn.microsoft.com/fabric/data-engineering/billing-spark)).

Per pool × kind × window the report surfaces: run count, succeeded / failed counts, total duration-hours, total vCore-hours, projected CU-hours, and avg vCore-hours per run.

Tunable env vars:

| Env var | Default | Notes |
|---|---|---|
| `SMA_SPARK_RUN_HISTORY` | `1` | Set to `0` to skip Livy history entirely |
| `SMA_SPARK_RUN_DAYS` | `90` | Trailing-days lookback window (overridden by the Run page **Lookback** selector) |
| `SMA_SPARK_RUN_LIMIT` | `50000` | Per-pool, per-kind run cap. Livy has no server-side date filter, so the client pages newest-first and stops as soon as a full page is older than the window; this limit is a safety ceiling, raised in 3.2.0 from `5000` to prevent premature truncation on busy workspaces. Hard ceiling: `100000`. |
| `SMA_SPARK_RUN_PAGE_SIZE` | `100` | Livy pagination page size (max 200) |
| `SMA_SPARK_RUN_CONCURRENCY` | `4` | Parallel per-pool fetches via `ThreadPoolExecutor` |

---

## Module 4 — `pipelines`

Inventories the **Synapse Artifacts** plane (`https://<ws>.dev.azuresynapse.net`): pipelines, linked services, datasets, triggers — plus integration runtimes from ARM.

### 4.1 What it captures

| Entity | API | Notes |
|---|---|---|
| Pipelines | `ArtifactsClient.pipeline.get_pipelines_by_workspace` | activity count, distinct activity types, folder, annotations, **Fabric-unsupported / partial activity counts** |
| Activities (flattened) | recursive walk of `activities`, `if_true_activities`, `if_false_activities`, `default_activities`, `cases[].activities` | type, support tier (`supported` / `partial` / `unsupported` / `unknown`), **per-activity reasons + instance caveats**, suggested Fabric equivalent, migration action, doc URL, references |
| Linked services | `linked_service.get_linked_services_by_workspace` | type, integration runtime reference, **`fabric_supported` boolean** |
| Datasets | `dataset.get_datasets_by_workspace` | type, linked service reference, folder |
| Triggers | `trigger.get_triggers_by_workspace` | type, runtime state, attached pipelines |
| Integration runtimes | `azure-mgmt-synapse.integration_runtimes.list_by_workspace` | Managed vs SelfHosted |

### 4.2 Module-specific prerequisites

The service principal needs **Synapse Artifact User** (or higher) on the workspace, in addition to control-plane Reader. Grant via Synapse Studio → **Manage** → **Access control**.

### 4.3 Run & outputs

```powershell
sma analyze-pipelines
sma analyze-pipelines --since 28d            # narrower run-history window for this run
sma analyze-pipelines --no-run-history       # skip the run-history fetch entirely
```

Produces:
- `pipelines.json`, `pipelines.md`, `pipelines.html`
- `pipelines.csv`, `linked_services.csv`, `datasets.csv`, `triggers.csv`, `integration_runtimes.csv`
- `pipeline_activities.csv` (one row per activity with support tier, generic reasons, instance-specific caveats, Fabric equivalent, migration action, doc URL)
- `pipeline_run_stats.csv` — one row per `(pipeline, window)` for 7/14/28/90-day buckets
- `pipeline_run_summary.csv` — one row per pipeline using the 28-day window as the headline

### 4.4 Run-history statistics

The analyzer pulls pipeline run history via `ArtifactsClient.pipeline_run.query_pipeline_runs_by_workspace` (ordered `RunStart DESC` server-side, so when the run cap is hit the oldest runs are dropped — not arbitrary ones) and aggregates it into rolling windows. For pipelines that statically contain a `Copy`, `ExecuteDataFlow` or `Lookup` activity, it also fetches activity-run outputs to compute average data-movement (MB/run from `dataRead` / `dataWritten` on Copy, `runStatus.metrics[*].bytes` on Dataflow) and — for `ExecuteDataFlow` — true **vCore-hours per run** = `compute.coreCount` (defaults to 8 when unset) × `output.executionDuration` (s), which the report then projects to Fabric CU-hours at `0.5 CU-h / vCore-h`. Any pipeline that ends up with zero runs after the global pull is back-filled via a per-pipeline `PipelineName In (...)` query so low-frequency pipelines still appear in the dashboard.

Tunable via env vars (and/or `--since` / `--no-run-history`):

| Env var | Default | Notes |
|---|---|---|
| `SMA_PIPELINES_RUN_HISTORY` | `1` | Set to `0` to skip the fetch entirely |
| `SMA_PIPELINES_RUN_DAYS` | `90` | Widest window (also caps the API range; 7/14/28 are clamped to it). Overridden by the Run page **Lookback** selector. |
| `SMA_PIPELINES_RUN_LIMIT` | `5000` | Safety cap on total runs / activity rows fetched per call; sets `truncated=true` when reached |
| `SMA_PIPELINES_RUN_BACKFILL_PER_PIPELINE` | `10` | When the global cap is hit, fetch up to N most-recent runs per pipeline that ended up with zero global runs |
| `SMA_PIPELINES_ACTIVITY_RUNS` | `1` | Set to `0` to skip activity-run fetch (data-movement metrics will be `null`) |

> **Compatibility detail.** When an activity is marked `partial`, the analyzer
> does **not** stop at the tier label. For each known activity type it ships a
> small knowledge base of *why* the activity is partial in Fabric (auth options,
> connector catalog gaps, parameter-passing differences, batch-count ceilings
> etc.), plus a property-level walker that surfaces *instance-specific* caveats
> from the activity's `type_properties` — for example a `Copy` with
> `enableStaging=true`, a `WebActivity` using `ClientCertificate` auth, a
> `Lookup` with `firstRowOnly=false`, a `ForEach` with `batchCount` above
> Fabric's max, or a `SynapseNotebook` bound to a specific Spark pool. See
> [fabric_compat.py](src/usma/modules/pipelines/fabric_compat.py)
> for the full catalog. The HTML report renders these as expandable cards under
> *Partially-compatible activities*; the markdown report renders them as nested
> bullet lists.

---

## Module 5 — `monitoring`

Pulls historical Azure Monitor metrics for each dedicated SQL pool in the workspace. Produces a window-aggregated summary (min / avg / p95 / max) used by `fabric_mapping` to recommend Fabric capacity sizing.

### 5.1 What it captures

| Metric | Source |
|---|---|
| `DWULimit`, `DWUUsed`, `DWUUsedPercent` | `Microsoft.Synapse/workspaces/sqlPools` metrics |
| `ActiveQueries`, `QueuedQueries` | same |
| `Connections`, `ConnectionsBlockedByFirewall` | same |
| `MemoryUsedPercent`, `CPUPercent` | same |

> The provider does **not** expose `FailedConnections` on `workspaces/sqlPools`; the analyzer requests `ConnectionsBlockedByFirewall` instead. If the provider's catalogue ever drops a metric, `fetch_metrics()` parses the rejected name out of the `BadRequest` response, drops it, and retries with the rest — see [monitor_client.py](src/usma/modules/monitoring/monitor_client.py).

Default window is 7 days at 1-hour interval. Tunable via env vars:

| Env var | Default | Notes |
|---|---|---|
| `SMA_MONITORING_DAYS` | `7` | Window size in days. Overridden by the Run page **Lookback** selector. |
| `SMA_MONITORING_INTERVAL` | `PT1H` | ISO-8601 duration |
| `SMA_MONITORING_AGG` | `Average` | One of `Average` / `Total` / `Maximum` / `Minimum` |

### 5.2 Module-specific prerequisites

The service principal needs `Monitoring Reader` (or any role that grants `Microsoft.Insights/metrics/read`) at the subscription or resource-group scope.

### 5.3 Run & outputs

```powershell
sma analyze-monitoring
sma analyze-monitoring --since 14d        # override SMA_MONITORING_DAYS for one run
```

Produces:
- `monitoring.json` (full series with timestamped points)
- `monitoring_summary.csv` (one row per metric/pool with min/avg/p95/max)
- `monitoring.md`, `monitoring.html`

---

## Module 6 — `storage`

Captures the **storage footprint** that underlies the workspace: every Storage account in the subscription (flagging the workspace-default ADLS Gen2), Azure Monitor capacity metrics (`UsedCapacity`, `BlobCapacity`, `BlobCount`, `ContainerCount`, plus best-effort File / Table / Queue), and the actual on-disk size of every dedicated SQL pool — reserved / data / index space in **MB and GB** plus % of the pool's `MaxSizeBytes`.

### 6.1 What it captures

| Area | Source | File |
|---|---|---|
| Storage account inventory (SKU, kind, access tier, HNS / ADLS Gen2 flag, endpoints) | `azure-mgmt-storage` `storage_accounts.list` (with RG-scope and `get_properties` fallbacks) | [arm_client.py](src/usma/modules/storage/arm_client.py) |
| Workspace-default ADLS Gen2 detection | `azure-mgmt-synapse` `workspaces.get` + parsing of `default_data_lake_storage.account_url` | same |
| Capacity metrics (account scope: `UsedCapacity`) | `azure-mgmt-monitor` `metrics.list` | [monitor_client.py](src/usma/modules/storage/monitor_client.py) |
| Capacity metrics (service scope: `BlobCapacity`, `BlobCount`, `ContainerCount`, best-effort `FileCapacity`/`FileCount`/`TableCapacity`/`QueueCapacity`) | same, walking `/blobServices/default`, `/fileServices/default`, `/tableServices/default`, `/queueServices/default` | same |
| Dedicated SQL pool size (table count, row count, reserved / data / index space in MB + GB, % of MaxSize) | `sys.dm_pdw_nodes_db_partition_stats` JOIN `sys.tables` | [queries/pool_size.sql](src/usma/modules/storage/queries/pool_size.sql) |

> Premium / non-Gen2 accounts that don't expose blob/file/table/queue services are silently skipped — the inventory row is still emitted but no capacity row is added.
>
> Honours `SMA_DEDICATED_POOL` to scope the dedicated-pool size query (same env var used by Module 1).

### 6.2 Required permissions

- `Reader` on the subscription **or** on each individual resource group containing storage accounts. The analyzer tries subscription-wide listing first, then falls back to listing the workspace's own RG, and finally to a direct fetch of the workspace's default ADLS Gen2 — so RG-scoped Reader is enough as long as it covers the RGs you care about.
- `Monitoring Reader` on each storage account (or its RG) for the capacity metrics.
- The same SQL login used by Module 1 (the DMV query runs against `sys.dm_pdw_nodes_db_partition_stats` + `sys.tables`).

### 6.3 Run

```powershell
sma analyze-storage
```

### 6.4 Outputs (under `./output/` by default)

- `storage.json`, `storage.md`, `storage.html`
- `storage_accounts.csv`, `storage_capacity.csv`, `dedicated_pool_storage.csv`

---

## Module 7 — `fabric_mapping`

Aggregates the JSON outputs of modules 1–6 (dedicated, serverless, spark, pipelines, monitoring, storage) from `SMA_OUTPUT_DIR` and applies heuristic rules to produce a **Fabric Warehouse migration recommendation report**. No Azure access required at this stage — it only reads files.

### 7.1 What it does

For each module output it finds in `./output/`, it loads the JSON, summarizes counts, and runs rule sets defined in [rules.py](src/usma/modules/fabric_mapping/rules.py). Examples included today:

| Rule | Severity | Trigger |
|---|---|---|
| Paused dedicated pool | `warning` | `inventory.status == "Paused"` |
| Non-Fabric collation | `warning` | dedicated pool collation ≠ `Latin1_General_100_BIN2_UTF8` |
| Large REPLICATE table | `warning` | distribution = REPLICATE & rows > 50M |
| Large ROUND_ROBIN table | `info` | rows > 100M |
| Large heap table | `info` | index_type = HEAP & rows > 1M |
| Workload groups defined | `info` | any present |
| T-SQL surface gap | `info` / `warning` / `blocker` | code object matches a [tsql_surface.py](src/usma/modules/fabric_mapping/tsql_surface.py) rule (MERGE, cursors, CLR, triggers, XML methods, three-part names…) |
| External tables inventory | `info` | from serverless |
| Serverless cost baseline | `info` | from serverless cost estimate |
| Top serverless query | `info` | per top-query entry |
| Spark pools / notebooks / SJDs | `info` | any present |
| Pipelines inventory | `info` | any pipeline detected |
| Fabric-unsupported activity | `warning` / `blocker` | per [fabric_compat.py](src/usma/modules/pipelines/fabric_compat.py) catalog (ExecuteDataFlow, HDInsight*, AzureML*, SSIS, Custom…) |
| Fabric-unsupported linked service | `warning` | linked service type not in Fabric catalog |
| Self-hosted IR detected | `warning` | IR type starts with "Self" |
| DWU sizing hint | `info` / `warning` | monitoring `DWUUsedPercent` p95 < 30 % (downsize) or > 85 % (upsize) |
| Connections blocked by firewall | `warning` | monitoring `ConnectionsBlockedByFirewall` max > 0 |

Each recommendation has: `id`, `area`, `title`, `severity` (`info`/`warning`/`blocker`), `effort` (`low`/`medium`/`high`), `target`, `detail`, and a `fabric_action`.

### 7.2 Run & outputs

```powershell
# After running modules 1–6 (or `sma analyze-all`):
sma map-to-fabric
```

Produces:
- `fabric_mapping.json`
- `fabric_recommendations.csv`
- `fabric_mapping.md` (sortable summary table + detailed sections)
- `fabric_mapping.html` (readiness card, capacity projection, recommendations grouped by area, runbook by phase)

### 7.3 Programmatic use

```python
from usma.config import load_config
from usma.modules.fabric_mapping.analyzer import FabricMappingAnalyzer

cfg = load_config()
report = FabricMappingAnalyzer(cfg).run()
for rec in report.recommendations:
    print(rec.severity.upper(), rec.title, "→", rec.fabric_action)
```

To add a new rule, append a function to [rules.py](src/usma/modules/fabric_mapping/rules.py) and wire it into `_RULES` in [analyzer.py](src/usma/modules/fabric_mapping/analyzer.py). Side-effect-free pure functions are easy to unit-test.

---

## Module 8 — `governance` (v1.2, opt-in)

Captures workspace- and resource-level RBAC (control plane + data plane) with role-name
resolution, managed-private-endpoint inventory, customer-managed-key configuration, and
Microsoft Purview account detection. Emits severity-tagged findings.

### 8.1 Module-specific prerequisites

- `Reader` on the subscription (or RG) so the analyzer can enumerate role assignments
  against the workspace + its data-plane resources.
- `Microsoft.Authorization/roleAssignments/read` (covered by Reader on most scopes).

### 8.2 Run & outputs

```powershell
sma analyze-governance
# Or as part of the full sweep:
sma analyze-all --include governance
```

Produces (under `./output/`):
- `governance.json`, `governance.md`, `governance.html`
- `governance_role_assignments.csv`, `governance_findings.csv`

---

## Module 9 — `security` (v1.2, opt-in)

Captures firewall rules, AAD-only enforcement, TLS minimum version, encryption-at-rest
configuration, AAD admins, per-pool TDE state, and a linked-service credential inventory
with **inline-secret detection** (literal `password` / `accountKey` / `sasToken` /
`SecureString` vs. Key Vault references — types and locations only, never values).

### 9.1 Module-specific prerequisites

Same Reader + Synapse Artifact User roles already required by Modules 1 / 4. No
additional grants needed for the firewall / TDE / AAD-admin probes.

### 9.2 Run & outputs

```powershell
sma analyze-security
# Or as part of the full sweep:
sma analyze-all --include security
```

Produces (under `./output/`):
- `security.json`, `security.md`, `security.html`
- `security_firewall_rules.csv`, `security_linked_service_credentials.csv`,
  `security_findings.csv`

---

## Module 10 — `cost` (v1.2)

Aggregates Azure Cost Management consumption for the workspace's resource group, attributes
spend by resource kind / pool / storage account, compares the Synapse run-rate against the
Fabric capacity projection produced by Module 7, and emits severity-tagged findings.

### 10.1 Where the data comes from

| Area | Source | File |
|---|---|---|
| Monthly consumption rows (cost + usage by `ResourceId` / `MeterCategory` / `ServiceName`) | **Azure Cost Management** REST API via `azure-mgmt-costmanagement` (`CostManagementClient.query.usage`) at scope `/subscriptions/<sub>/resourceGroups/<rg>` | [cost_client.py](src/usma/modules/cost/cost_client.py) |
| Resource-kind classification (`dedicated_pool` / `spark_pool` / `synapse_workspace` / `storage` / `other`) | derived from each row's `ResourceId` | [cost_client.py](src/usma/modules/cost/cost_client.py) |
| Fabric SKU TCO comparison | reads `output/fabric_mapping.json` (`cu_projection`) — no live calls | [fabric_compare.py](src/usma/modules/cost/fabric_compare.py) |
| Findings (savings / increase / month-over-month spikes / kind concentration / no-data branches) | pure-Python rules engine | [rules.py](src/usma/modules/cost/rules.py) |

### 10.2 Module-specific prerequisites

1. **Optional Python extra** — install `azure-mgmt-costmanagement`:

    ```powershell
    pip install -e ".[cost]"
    ```

   Without it, `sma analyze-cost` runs but emits a `cost.sdk_missing` finding (medium) and
   no live data.

2. **RBAC** — the service principal needs **Cost Management Reader** on the subscription
   or the workspace's resource group. `Reader` alone is not enough; Cost Management is a
   separate provider:

    ```powershell
    az role assignment create `
      --assignee <CLIENT_ID> `
      --role "Cost Management Reader" `
      --scope /subscriptions/<SUB_ID>/resourceGroups/<RG>
    ```

3. **Tunable env vars:**

   | Env var | Default | Effect |
   |---|---|---|
   | `SMA_COST_MONTHS` | `3` | Window: last *N* full months + month-to-date |
   | `SMA_COST_DISABLE_LIVE` | unset | When `1`/`true`/`yes`, skip the SDK call entirely (emits `cost.live_disabled` info finding) |

### 10.3 Run & outputs

```powershell
sma analyze-cost
sma analyze-cost --months 6        # widen the window for one run
```

Produces (under `./output/`):
- `cost.json`, `cost.md`, `cost.html`
- `cost_by_resource_kind.csv`, `cost_findings.csv`

### 10.4 Diagnosing "No cost rows captured"

The rules engine surfaces the underlying reason in the finding `rule_id`:

| `rule_id` | Severity | Meaning |
|---|---|---|
| `cost.sdk_missing` | medium | The optional `azure-mgmt-costmanagement` SDK is not installed |
| `cost.live_disabled` | info | `SMA_COST_DISABLE_LIVE` is set |
| `cost.collection_error` | medium | The Cost Management API call raised — see `errors[]` in `cost.json` (most often `AuthorizationFailed` → missing **Cost Management Reader**) |
| `cost.no_data` | info | The SDK call succeeded but the window genuinely had no rows. Try `SMA_COST_MONTHS=6` |

---

## End-to-end

```powershell
# Run modules 1→7 in execution order (dedicated, serverless, spark, pipelines,
# monitoring, storage, fabric_mapping) and refresh the top-level index.html navigation:
sma analyze-all

# Or rebuild only the index.html landing page after editing/regenerating reports:
sma index
```

Open `output/index.html` in a browser — it links to whichever module HTML reports exist
(and shows a *missing* card with the exact `sma analyze-...` command for the rest).

---

## Optional — browser-driven control plane

For analysts who would rather drive the analyzer from a browser than the CLI,
`sma serve --with-api` boots a local FastAPI backend plus the SPA on the same
origin. It is **opt-in** (extra install) and **loopback-only by default**.

> 📖 **Full UI walkthrough:** see the [User guide](docs/user-guide/README.md)
> for a per-page reference, troubleshooting matrix and FAQ.

```powershell
# Install the [web] extras (one-off)
pip install -e ".[web]"

# Build the SPA bundle once (re-run only when web/ source changes)
cd web
npm install
npm run build
cd ..

# Boot the control plane
sma serve --with-api                       # http://127.0.0.1:8000/
sma serve --with-api --port 8001           # custom port
sma serve --with-api --runs-dir D:\runs    # custom run repo (default ./runs)
```

The SPA gets four extra pages when the API is detected (`GET /api/healthz`):

| Page | What it does |
| --- | --- |
| **Configuration** | Read / write `.env` from the browser. Secrets are write-only; reads return only `set` / `unset`. Includes a **Validate** button and a **Backup & restore** panel that exports every run under `runs/` as a single zip (`GET /api/runs-archive/export`) and re-imports it on another machine with skip / overwrite / rename conflict modes. |
| **Run** | Pick which modules to run (same module set as `analyze-all`), give the run an optional label, watch live progress over Server-Sent Events. |
| **Runs** | History of all completed runs with status, duration, error count, readiness score. |
| **Diff** | Compare any two runs using the same `run_manifest` engine that powers `sma run-delta`. |

The **Dashboard** page gathers workspace-level vitals: readiness score,
T-SQL surface findings, recommendation count and SKU advisory at the top;
top blockers and inputs analyzed; a *Storage* section with stat cards for
dedicated-pool data / index space, ADLS used capacity, storage-account
count and a per-pool table; and a *Pipeline activity (last 7 days)*
section showing daily run rate, success rate, daily data movement, the
sampling window and the top 10 pipelines by run count. Storage and
pipeline sections render only when the corresponding `storage.json` /
`pipelines.json` exist for the selected run.

Results are persisted under `--runs-dir/<id>/` (default `./runs/<id>/`) so the
rest of the SPA — Dashboard, Code objects, Recommendations, Runbook, Delta —
transparently reads from the currently-selected run. The selected run id
lives in the URL hash (`#run=<id>`), so deep-links and refreshes are stable.

### Security posture

The `--with-api` server has **no authentication**:

- Bound to `127.0.0.1` by default.
- Refuses non-loopback hosts unless you pass `--i-know-this-is-not-auth`.
- All state-changing requests (`POST` / `PUT` / `DELETE`) require an
  `X-SMA-API: 1` header (defence in depth against drive-by CSRF; the SPA
  sets it).
- Run id and module name are validated against strict regexes; the run
  repository resolves all paths under `--runs-dir` to block traversal.
- The client secret is never returned over the wire — only its presence.

Do not expose the control plane on a shared or multi-user host. If you need
remote access, put it behind your own authenticating reverse proxy or VPN.

---

## Cross-module rule index — what `fabric_mapping` synthesizes

The fabric_mapping rules engine in [rules.py](src/usma/modules/fabric_mapping/rules.py) walks every other module's JSON output and synthesizes Fabric-Warehouse migration recommendations. Headline detections:

| Rule family | Source | Examples |
|---|---|---|
| Collation mismatch  | dedicated_pools `inventory.collation` | Anything other than Fabric default `Latin1_General_100_BIN2_UTF8` |
| Column-level collation audit | dedicated_pools `column_collations[]` | Per-column collations differing from DB default |
| Materialized-view inventory | dedicated_pools `materialized_views[]` | Indexed views (Fabric Warehouse has no MV) |
| Stale statistics | dedicated_pools `statistics[]` | `days_since_update > 14` |
| Distribution-key advisor | dedicated_pools `distribution_candidates[]` | Top-N candidates per ROUND_ROBIN / large REPLICATE table (skew + filter-selectivity) |
| T-SQL surface scan with stable code-object ids | dedicated_pools `code_objects[].definition` | MERGE, CURSOR, global temp (`##`), CLR / external procs, DML+DDL triggers, XML data-type methods, ROWVERSION, sequences, three-part names — each finding cites `code_object_id`s |
| Activity-level Fabric gaps | pipelines `activities[].support` | `ExecuteDataFlow`, `ExecuteSSISPackage`, `Custom`, HDInsight*, AzureML* |
| Linked-service compatibility | pipelines `linked_services[].fabric_supported` | `HDInsight`, `Cassandra`, `Greenplum`, `Vertica`, … |
| DWU sizing hints | monitoring `DWUUsedPercent` p95 | <30 % → downsize, 30–85 % → match, >85 % → upsize |
| Connections-blocked signal | monitoring `ConnectionsBlockedByFirewall` | Surfaces firewall/auth issues to fix before migrating |

The full keyword list lives in [tsql_surface.py](src/usma/modules/fabric_mapping/tsql_surface.py) and the activity catalog in [pipelines/fabric_compat.py](src/usma/modules/pipelines/fabric_compat.py) — both are designed to be extended.

---

## Modules — Future roadmap

Each future module follows the same conventions as `dedicated_pools`:

```
modules/<name>/
├── analyzer.py        # exposes a class with .run() -> Pydantic result
├── <ctrl>_client.py   # control-plane (ARM / REST) wrapper
├── <data>_client.py   # data-plane (SQL / Livy / Pipelines API) wrapper
├── models.py          # Pydantic v2 models
├── collectors/        # one collector per concern
└── queries/           # SQL / KQL / REST payloads
```

A new CLI subcommand is registered in [cli.py](src/usma/cli.py), and writers in [reporting/](src/usma/reporting) are extended (or reused) to render the new models.

| Module | Planned scope |
|---|---|
| `serverless_pools` v3 | Per-database query history, partition-pruning analysis, OPENROWSET pattern detection |
| `spark_pools` v3 | Library/package inventory, Spark job run history (Livy), notebook API surface scan |
| `pipelines` v3 | Per-activity parameter binding analysis, expression-language compatibility checks |
| `fabric_mapping` v3 | Auto-generated migration runbook with sequenced steps + cost projection |
| `monitoring` v2 | Log Analytics / KQL queries (long-running queries, top users), 30–90 day windows |

> **Mid-term modules already shipped (v1):** `governance` (RBAC + MPE + CMK + Purview), `security`
> (firewall + AAD admins + per-pool TDE + linked-service inline-secret detection), `cost`
> (Cost Management consumption + Fabric SKU TCO delta + month-over-month spike detection),
> `fabric_validation` (post-migration object/row/collation/T-SQL surface checks), and
> **Incremental / delta runs** (run manifest + markdown/HTML/JSON delta with record-count peeks)
> — see the per-module CLI commands in the table at the top.

> Note: `dedicated_pools` v2 (column collation audit, distribution-key advisor with skew + filter
> selectivity, materialized-view inventory, statistics freshness, T-SQL surface gap rollup with
> stable code-object ids) is **shipped** — see [§1.1](#11-what-it-captures) above.

---

## Cheat sheet

```powershell
# Activate venv
.\.venv\Scripts\Activate.ps1

# Per-module
sma analyze-dedicated-pools
sma analyze-serverless-pools
sma analyze-spark-pools
sma analyze-pipelines
sma analyze-monitoring
sma analyze-storage
sma map-to-fabric

# Mid-term modules (v1)
sma analyze-governance
sma analyze-security
sma analyze-cost              # requires .[cost] extra for live data
sma validate-fabric           # post-migration validation

# Everything end-to-end (also refreshes index.html and emits run manifest + delta)
sma analyze-all

# Just compare the current artifacts vs the previous run
sma run-delta

# Just rebuild the top-level landing page
sma index

# Generate a portable Access & Security markdown report from the latest run
sma access-report

# Write the shipped effort rate-card to a hand-editable JSON file
sma effort-card

# Dump the JSON Schema for every module's result model
sma export-schema

# Boot the browser-driven control plane (requires the [web] extra)
sma serve --with-api

# Self-test (host + Azure auth pre-flight)
sma doctor
sma doctor --offline

# Tests
pytest

# Help
sma --help
sma analyze-dedicated-pools --help
```
