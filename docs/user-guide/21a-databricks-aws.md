# 21a. Databricks on AWS *(alpha)*

> **Phase 4.7 preview.** AWS Databricks support is **alpha**: the
> existing `databricks_workflows` collector / analyzer is reused
> unchanged (it talks to the cloud-agnostic `databricks-sdk`
> `WorkspaceClient`), but the cost module is not yet wired to the
> Databricks Usage API — AWS scopes therefore emit a `caveat` instead
> of cost rows. See [ADR-0005](../adr/0005-multi-cloud-databricks.md)
> for the design rationale.

USMA's source-type stays `SourceType.DATABRICKS` regardless of cloud;
the cloud is carried as `extras["platform"]` on `SourceDescriptor` and
as `SMA_DATABRICKS_PLATFORM` in `.env`. Setting `aws` swaps the
provider (`DatabricksAwsProvider`) but leaves the analyzer, reporting
and SPA modules untouched. See
[`docs/user-guide/21-databricks.md`](21-databricks.md) for the
Azure-specific path.

## Choosing AWS in the SPA

Open **Configuration → Source type → Databricks**. A second pill row
appears below the source-type radio with **Azure** (default) and
**AWS** (`alpha` badge). Picking AWS hides the workspace auto-discover
panel and renders an inline hint pointing at the `.env` keys below.

## Authentication

The AWS provider supports three auth flavours, tried in this order:

1. **Personal Access Token (PAT)** — set `DATABRICKS_TOKEN`. Simplest;
   recommended for one-off assessments.
2. **OAuth machine-to-machine (M2M)** — set `DATABRICKS_CLIENT_ID` +
   `DATABRICKS_CLIENT_SECRET` on a Databricks service principal. The
   provider exchanges them for a workspace token on every call. Use
   this for unattended / CI runs.
3. **Instance profile** — when running on an EC2 host with an attached
   IAM role configured for Databricks; the SDK picks it up
   automatically. No env vars required.

Account-API discovery additionally requires:

* `DATABRICKS_ACCOUNT_ID` — the [Databricks account ID](https://docs.databricks.com/aws/admin/account-settings/#account-id)
* `DATABRICKS_ACCOUNT_CLIENT_ID` + `DATABRICKS_ACCOUNT_CLIENT_SECRET` —
  account-level OAuth SP creds. See
  [Authenticate to the Databricks account](https://docs.databricks.com/aws/dev-tools/auth/oauth-m2m).

Without those, set `DATABRICKS_HOST` and the provider falls back to
**single-workspace mode** (no enumeration; the provided host is the
only scope).

## `.env` keys reference

| Key | Required? | Purpose |
|---|---|---|
| `SMA_DATABRICKS_PLATFORM` | yes (`aws`) | Cloud discriminator. Absent / `azure` → existing Azure provider |
| `DATABRICKS_HOST` | yes¹ | `https://dbc-xxxx.cloud.databricks.com` |
| `DATABRICKS_TOKEN` | one-of | Personal access token (data-plane) |
| `DATABRICKS_CLIENT_ID` | one-of | Workspace SP OAuth M2M client id |
| `DATABRICKS_CLIENT_SECRET` | one-of | Workspace SP OAuth M2M client secret |
| `DATABRICKS_ACCOUNT_ID` | for discovery | Enables Account-API workspace enumeration |
| `DATABRICKS_ACCOUNT_CLIENT_ID` | for discovery | Account-level SP OAuth M2M client id |
| `DATABRICKS_ACCOUNT_CLIENT_SECRET` | for discovery | Account-level SP OAuth M2M client secret |

¹ Required unless `DATABRICKS_ACCOUNT_ID` is set (Account-API mode
discovers hosts automatically).

The four `AZURE_*` SP variables are **ignored** in AWS mode; the
`doctor` command's AAD checks are skipped automatically.

## CLI quick start

```powershell
# Single AWS workspace via explicit host
$env:SMA_DATABRICKS_PLATFORM = "aws"
$env:DATABRICKS_HOST          = "https://dbc-abc12345-6789.cloud.databricks.com"
$env:DATABRICKS_TOKEN         = "<PAT>"

sma analyze-all --scope databricks-aws:dbc-abc12345-6789.cloud.databricks.com
```

The `databricks-aws:` scope shortcut accepts either the bare host
(`dbc-…cloud.databricks.com`) or the full URL form (with or without
`https://`). Any trailing `@<sub>/<rg>` segment is silently dropped —
AWS workspaces have no Azure coordinates.

Multiple workspaces under the same account:

```powershell
$env:DATABRICKS_ACCOUNT_ID            = "1234abcd-…"
$env:DATABRICKS_ACCOUNT_CLIENT_ID     = "…"
$env:DATABRICKS_ACCOUNT_CLIENT_SECRET = "…"

sma analyze-all `
  --scope databricks-aws:dbc-aaaa.cloud.databricks.com `
  --scope databricks-aws:dbc-bbbb.cloud.databricks.com
```

`sma doctor` validates each scope via `current_user.me()` on the
data plane (no ARM probe) and surfaces a single PASS/FAIL per
workspace.

## Support matrix (AWS-specific)

| Module | AWS Databricks | Notes |
|---|---|---|
| `databricks_workflows` | ✅ | Identical collector — Phase 4-B logic reused as-is |
| `fabric_mapping` | ✅ | Task-level rules are cloud-agnostic |
| `cost` | ⛔ | Azure Cost Management is unavailable; AWS Usage API ingestion is **Phase 4.7.5** |
| `monitoring` | ⛔ | Synapse-only |

A `caveat` is emitted on every AWS Databricks workflow noting that
cost figures are unavailable so the assessment report stays honest.

## Limitations (alpha)

* No account-level browser OAuth — only client-credentials flow. UI
  prompts for interactive consent are not supported.
* PrivateLink-only workspaces require the runner to be inside the VPC
  / VPN. The validator does not currently probe network reachability
  before failing — expect a generic `requests.ConnectionError`.
* No AWS-billed cost yet (see Phase 4.7.5 in
  [`USMA_planning_Manifest.md`](../../USMA_planning_Manifest.md)).
* GCP Databricks reuses the same code path (`platform="gcp"` is
  reserved) but has no provider implementation in 0.7.
* **Legacy run attribution.** Runs created before the cloud-aware
  identity fix inherited `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`
  from `.env`. If your Estate overview shows an AWS Databricks
  workspace grouped under an Azure tenant, run
  `sma migrate-run-attribution` (or use the **Fix non-Azure run
  attribution** panel on the [Configuration](12-configuration.md#fix-non-azure-run-attribution)
  page). New runs are attributed correctly automatically.
