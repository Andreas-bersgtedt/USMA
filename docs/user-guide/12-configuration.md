# 12. Configuration

## Purpose

Edit the analyzer's connection settings — Azure tenant / subscription /
workspace, dedicated pool, and the ODBC driver — without touching
`.env` files in the terminal. Validate the configuration with a single
click before kicking off a run.

## How to open

- URL: `/configuration`.
- Modes: control plane only.

## Inputs / outputs

Reads from and writes to `./.env` in the working directory of the
`sma serve` process. The page calls:

- `GET /api/config` — current effective values (with secrets redacted).
- `PUT /api/config` — persist edits.
- `POST /api/config/validate` — field-only checks (env vars present,
  GUIDs well-formed, output dir writable). Instant.
- `POST /api/config/validate?live=true` — field checks **plus** live
  Azure + Synapse connectivity tests using the saved service
  principal. Takes a few seconds.

## Layout

```
Configuration  (reads / writes ./.env)

Azure
  Tenant ID            [ 00000000-0000-0000-0000-000000000000 ]
  Client ID            [ 00000000-0000-0000-0000-000000000000 ]
  Client secret        [ ••••••••  (leave empty to keep) ]
  Subscription ID      [ 00000000-0000-0000-0000-000000000000 ]
  Dedicated pool       [ dwpool01                              ]   (optional)

SQL
  ODBC driver          [ ODBC Driver 18 for SQL Server         ]

Synapse workspace                                          [ Discover workspaces ] [ Refresh list ]
  Workspace            [ syn-prod-eu (rg-synapse-prod, westeurope) — current  v ]
                       └─ "Enter manually…" reverts to plain text fields

[ Save configuration ]    [ Validate fields ]    [ Validate live access ]

Validation
  Configuration
    ● OK    .env file present
    ● OK    AZURE_TENANT_ID
    ● OK    AZURE_CLIENT_ID
    ● OK    AZURE_SUBSCRIPTION_ID
    ● OK    AZURE_CLIENT_SECRET — set
    ● OK    output_dir writable
  Control plane
    ● OK    AAD token (ARM)
    ● OK    Synapse workspace (ARM Reader) — syn-prod-eu in westeurope
  Data plane
    ● OK    Synapse Artifacts (Synapse Artifact User)
    ● OK    AAD token (SQL)
    ● OK    Serverless SQL SELECT 1 (syn-prod-eu-ondemand.sql.azuresynapse.net)
    ● FAIL  Dedicated SQL SELECT 1 (syn-prod-eu.sql.azuresynapse.net / dwpool01)
            — Login failed for user '<token-identified principal>'.
```

## Field reference

### Azure section

| Field             | `.env` key                  | Notes |
| ----------------- | --------------------------- | ----- |
| **Tenant ID**     | `AZURE_TENANT_ID`           | The directory the service principal lives in. |
| **Client ID**     | `AZURE_CLIENT_ID`           | The service principal app id. |
| **Client secret** | `AZURE_CLIENT_SECRET`       | Password input. **Leave empty to keep the existing value** — the secret is never sent back to the browser. |
| **Subscription ID** | `AZURE_SUBSCRIPTION_ID`   | The subscription that owns the Synapse workspace. |
| **Dedicated pool** | `SYNAPSE_DEDICATED_POOL`   | Optional. When set, the analyzer focuses on a single pool; otherwise it enumerates all pools in the workspace. |

The **Synapse workspace** card (resource group + workspace name) is
populated separately — see [Synapse workspace selector](#synapse-workspace-selector)
below.

### SQL section

| Field            | `.env` key       | Notes |
| ---------------- | ---------------- | ----- |
| **ODBC driver**  | `SQL_ODBC_DRIVER` | E.g. `ODBC Driver 18 for SQL Server`. Must match a driver actually installed on the host (run `sma doctor` to list installed drivers). |

### Buttons

| Button                       | Effect |
| ---------------------------- | ------ |
| **Save configuration**       | Posts the form to `/api/config`. The server updates `./.env` atomically and reloads the in-process settings. |
| **Validate fields**          | Local checks only: env vars present, GUIDs well-formed, output directory writable. Instant. |
| **Validate live access**     | Field checks **plus** live connectivity using the saved service principal. Tests Azure ARM (token + `workspaces.get`) and the Synapse data plane (Artifacts REST + `SELECT 1` against the serverless and — when `SYNAPSE_DEDICATED_POOL` is set — the dedicated SQL endpoint). Takes a few seconds. Use this to confirm both **Azure RBAC** and **Synapse data-plane RBAC** are in place before kicking off a run. |

### Validation results

Each check is a row with a status pill, grouped by **category**:

- **Configuration** — local checks against `./.env`.
- **Control plane** — Azure ARM + Synapse management API.
- **Data plane** — Synapse Artifacts REST + SQL endpoints.

Result states:

- ● **OK** — green.
- ● **FAIL** — red. The sub-text contains the underlying error
  message verbatim (e.g. `Login failed for user '<token-identified
  principal>'`, `(403) AuthorizationFailed`, ODBC `IM002` driver
  missing, etc.). Use this to pinpoint the missing role assignment or
  driver.

Live checks performed (when **Validate live access** is clicked):

| Category       | Check                                | Confirms |
| -------------- | ------------------------------------ | -------- |
| Control plane  | `AAD token (ARM)`                    | Service-principal credentials are valid for `management.azure.com`. |
| Control plane  | `Synapse workspace (ARM Reader)`     | The SP has at least Reader on the Synapse workspace resource. |
| Data plane     | `Synapse Artifacts (Synapse Artifact User)` | The SP can list pipelines via the workspace dev endpoint. |
| Data plane     | `Spark Livy (Synapse Compute Operator)` | The SP can call Livy on a Spark pool — required for the spark_pools module's job-history collection. The check probes the first available Spark pool with `get_spark_batch_jobs(size=1)`; a 403 here means the SP is missing the action `Microsoft.Synapse/workspaces/bigDataPools/useCompute/action`, granted by the **Synapse Compute Operator** role (or higher, e.g. Synapse Administrator) on the pool. If the workspace has no Spark pools the check passes with `no Spark pools — skipped`. |
| Data plane     | `AAD token (SQL)`                    | A token can be issued for `database.windows.net`. |
| Data plane     | `Serverless SQL SELECT 1 (...)`      | TCP + login + query on the built-in serverless endpoint. |
| Data plane     | `Dedicated SQL SELECT 1 (...)`       | Same against the dedicated pool — only when `SYNAPSE_DEDICATED_POOL` is set. |

### Accessible workspaces table

When the live validation succeeds, the page caches the list of
Synapse workspaces the SP can read. The selector above the form
uses the same list — pick a workspace and click **Save** to switch.
The validation card no longer renders a "Use this" column; it just
shows the count and points back to the selector.

### Synapse workspace selector

The **Synapse workspace** card replaces the static **Resource group**
+ **Workspace name** text fields. It writes the same two `.env`
keys (`SYNAPSE_RESOURCE_GROUP`, `SYNAPSE_WORKSPACE_NAME`) but lets you
pick from a discovered list instead of typing.

| Control                | Effect |
| ---------------------- | ------ |
| **Discover workspaces**| Calls `POST /api/config/validate?live=true` and caches the result. The dropdown appears once the call completes. Same call as **Validate live access** — you'll see the live validation results below the form. |
| **Refresh list**       | Re-runs the discovery call. |
| **Workspace** dropdown | Lists every workspace the SP can read in the configured subscription, formatted as `name (resource group, location)`. The currently saved workspace is marked `— current`. Picking a different one updates the form fields; click **Save** to persist. |
| **Enter manually…**    | Last entry in the dropdown. Drops back to plain text fields when you need to type a workspace that the SP can't list (e.g. cross-tenant or first-time onboarding before the SP has Reader). The link **Pick from list** above the inputs jumps back to the dropdown. |

**.env edits take effect on the next run.** The server reloads `.env`
with `override=True` for every analyzer invocation, so a freshly
saved workspace is honoured without restarting `sma serve`.

## Common tasks

### "Onboard a new workspace"

1. Fill in **Tenant**, **Client**, **Client secret**, **Subscription**.
2. Click **Discover workspaces** in the Synapse workspace card and
   pick the target workspace from the dropdown. (If the SP can't yet
   list workspaces, choose **Enter manually…** and type the resource
   group and workspace name.)
3. Click **Save configuration**, then **Validate live access**.
4. When every row under **Control plane** and **Data plane** is green,
   go to [09. Run page](09-run-page.md) and start a run.
5. If a Data-plane check fails with `Login failed for user '<token-identified
   principal>'`, the SP needs to be added as a Synapse SQL login. For
   the serverless endpoint a single `CREATE LOGIN [<sp-name>] FROM
   EXTERNAL PROVIDER; CREATE USER [<sp-name>] FOR LOGIN [<sp-name>];`
   in `master` is usually enough; for the dedicated pool repeat the
   `CREATE USER` in the pool database and grant the appropriate role
   (e.g. `db_datareader`).

### "Rotate the client secret"

1. Generate the new secret in Azure AD.
2. Paste it into **Client secret**.
3. **Save configuration** → **Validate live access**.

The page never displays the existing secret — leave the field empty
on a Save if you only changed other fields.

### "Switch to a different ODBC driver"

`sma doctor --json` lists the installed drivers. Copy the exact name
into the **ODBC driver** field; click **Validate fields** to confirm.

## Advanced environment variables

Not exposed in the UI \u2014 set these in `.env` directly when needed.

| Variable | Default | Effect |
| -------- | ------- | ------ |
| `SMA_STORAGE_INCLUDE_ALL` | unset | When set to `1` / `true` / `yes`, the **storage** module inventories every storage account in the configured subscription (legacy behaviour). When unset, only accounts attached to the workspace are inventoried \u2014 the workspace's default ADLS Gen2 plus any storage account referenced by a linked service. |
| `SMA_OUTPUT_DIR` | `./output` | Where collector CSV / JSON / HTML artefacts are written. The Run page `Output dir` override takes precedence per-run. |

## Empty / error states

- *API not reachable* — the FastAPI server isn't running.
- *Save failed: permission denied* — the `sma serve` process cannot
  write to `./.env`. Check file permissions.
- One or more red checks — see [13. Troubleshooting](13-troubleshooting.md).

## Related

- [01. Getting started](01-getting-started.md) — initial `.env` setup
- [09. Run page](09-run-page.md) — use the saved configuration
- [14. Security & deployment](14-security.md) — how secrets are handled

## Backup & restore

The Configuration page exposes a collapsed **Backup & restore** panel
that lets you export every run under `runs/` as a single zip file and
import that zip back on another machine.

### Export

Click **Download all run data (.zip)** to fetch a zip from
`GET /api/runs-archive/export`. The browser will offer a save dialog
for a file named `sma-runs-<timestamp>.zip`. The archive contains a
`manifest.json` at the root plus `runs/<id>/` trees for every run.

`.env` lives **outside** `runs_dir` and is therefore never included.
As defence-in-depth the archiver also drops dotfiles and refuses to
follow symlinks.

### Import

Select an export zip via **Import zip file**, choose how to handle
duplicate run ids (**Skip existing** is the default), then click
**Import**. The archive is validated before any files are written —
path-traversal, symlink entries, oversized files, and zip-bomb
compression ratios are all rejected. Valid runs are extracted to a
tempdir and atomically moved into `runs_dir`.

Conflict modes:

- **Skip existing** — leave a run already present on disk alone.
- **Overwrite** — replace the existing run directory.
- **Rename incoming run** — keep both by assigning the incoming run a
  fresh id (the new id is written into its `run.json`).

A run that is currently `queued` or `running` cannot be overwritten —
cancel it first via the runs history.
