# 13. Troubleshooting

A symptom → cause → fix matrix for the most common problems.

## Quick index

- [Page-level issues](#page-level-issues)
- [Data quality issues](#data-quality-issues)
- [Run / API issues](#run--api-issues)
- [Permission issues](#permission-issues)
- [Performance issues](#performance-issues)

---

## Page-level issues

### A tab is missing from the top navigation

| Cause | Fix |
| ----- | --- |
| The API is not running, so the SPA fell back to **static** mode. | Start `sma serve --with-api` and reload. See [02. Modes](02-modes.md). |
| You opened the static report directly from disk (`file://`). | Static mode never shows Run / Runs / Diff / Configuration. Use `sma serve` to get the control plane. |

### A tab opens to a blank panel — no data, no spinner

| Cause | Fix |
| ----- | --- |
| The active run id was lost during navigation (pre-2.0 bug). | Upgrade to **2.0.0+**. The 2.0 fix mirrors the run id between `#run=…` in the URL and `sessionStorage`. |
| You deep-linked to a run id that no longer exists on the server. | Pick a valid run from [10. Runs history](10-runs-history.md). |
| The module that backs that page didn't run. | Open the [Dashboard](04-dashboard.md) → *Inputs analyzed* card and verify the module is listed. Re-run with the module enabled if needed. |

### *Loading…* never goes away

| Cause | Fix |
| ----- | --- |
| The module's JSON file is missing for this run. | Check the run folder on disk. Re-run the relevant module. |
| The browser cannot reach `/api/...` (control plane only). | Open dev-tools → Network. Look for 401 / 404 / blocked CORS. Confirm the FastAPI server log shows the request. |

---

## Data quality issues

### "Storage shows 0 tables / 0 rows for a pool I know has data"

| Cause | Fix |
| ----- | --- |
| Pre-1.2.1 bug where `sys.pdw_table_mappings` was joined incorrectly. | Upgrade to **1.2.1+** (the join was rewritten). |
| The pool is **paused**. The catalog views return zero rows when the pool is offline. | Resume the pool, re-run `dedicated_pools` + `storage`. |
| The principal lacks **VIEW SERVER STATE** / **VIEW DATABASE STATE**. | Grant on the pool's master + user database. |

### "Code objects shows only views — no procedures / functions"

| Cause | Fix |
| ----- | --- |
| The principal lacks **VIEW DEFINITION** on the pool. The body of `sys.sql_modules` requires that grant; objects without a readable body are filtered out. | `GRANT VIEW DEFINITION ON DATABASE :: <db> TO <principal>;` and re-run. |

### "Pipeline activity section is empty"

| Cause | Fix |
| ----- | --- |
| The Synapse workspace has **no pipeline runs** recorded by Azure Monitor in the inspected window. | Trigger a run, then re-run the analyzer. |
| The principal lacks **Reader** on the workspace's Activity Log. | Add the role at the workspace scope. |

### "Diff page returns 500"

| Cause | Fix |
| ----- | --- |
| Pre-2.0 bug where the endpoint crashed for runs with no `fabric_mapping.json`. | Upgrade to **2.0.0+**. |
| One of the two runs is corrupt (missing `manifest.json`). | Delete the bad run folder; re-run. |

### "Dashboard headline cards say *No fabric_mapping data*"

| Cause | Fix |
| ----- | --- |
| `fabric_mapping` module wasn't selected. | Re-run with `fabric_mapping` enabled. It's free — it only consumes the other modules' output. |
| All upstream modules failed. | Check *Errors* on [10. Runs history](10-runs-history.md). |

### "Switching workspaces in Configuration doesn't change the next run"

| Cause | Fix |
| ----- | --- |
| Pre-2.1 bug where `load_dotenv()` was called without `override=True`, so `.env` edits were ignored by the long-lived `sma serve` process. | Upgrade to **2.1.0+**. The server now reloads `.env` with `override=True` for every analyzer invocation, so saved Configuration edits take effect on the next run with no restart. |
| You edited `.env` on disk while a run is in flight. | The active run keeps its original config. The change applies on the next run. |

### "Storage scan returns every account in the subscription"

| Cause | Fix |
| ----- | --- |
| `SMA_STORAGE_INCLUDE_ALL` is set to `1` / `true` / `yes`. | Remove the variable from `.env` (or set it to `0`) to restrict the inventory to accounts attached to the workspace — the default ADLS Gen2 plus any account referenced by a linked service. |
| You're on pre-2.1 and the scope filter doesn't exist yet. | Upgrade to **2.1.0+**. |

### "Storage scan is missing an account I know is used by a pipeline"

| Cause | Fix |
| ----- | --- |
| The linked service uses a managed-identity / shared-access-signature URL the parser can't extract a host from. | Set `SMA_STORAGE_INCLUDE_ALL=1` in `.env` to fall back to the legacy subscription-wide scan. File an issue with the linked-service definition redacted so the parser can be improved. |

---

## Run / API issues

### "Run page: API not reachable"

| Cause | Fix |
| ----- | --- |
| `sma serve` started without `--with-api`. | Restart with `sma serve --with-api`. |
| The browser cached an old static-mode response. | Hard reload (Ctrl-F5). |
| The server is bound to a different host / port than the SPA expects. | The SPA assumes same-origin. If you changed `--host` / `--port`, open the new URL directly. |

### "A module shows *failed* with a generic message"

The Latest event column is intentionally short. Get the full stack
trace from:

- the terminal output of `sma serve` (it streams every error there), or
- `output/runs/<id>/run.log` if the run was started from the CLI.

### "Cancel button doesn't stop the run"

Modules complete their **current step** before stopping — a long
`pipelines_run_history` query may take 30 s to abort. If a module
hangs longer than that (e.g. the SQL endpoint is unresponsive), kill
the `sma` process; the run will be marked **cancelled** the next time
the API restarts.

---

## Permission issues

### "azure.auth fails on Validate"

| Cause | Fix |
| ----- | --- |
| Wrong tenant id. | Tenant id is the **directory** id, not the subscription id. |
| Service principal has no client secret. | Generate one and paste into [Configuration](12-configuration.md). |
| The secret is expired. | Rotate it. |

### "azure.workspace_reachable fails"

| Cause | Fix |
| ----- | --- |
| The service principal has no role on the workspace. | Assign **Reader** at the workspace level (sufficient for inventory). |
| The workspace is in a private VNet. | Run the analyzer from inside the VNet, or open the management endpoint via Private Link. |

### "sql.dedicated_pool_reachable fails — login timeout"

| Cause | Fix |
| ----- | --- |
| The pool is paused. | Resume it. |
| Firewall blocks the analyzer host. | Add the host's outbound IP to the workspace SQL firewall. |
| AAD login disabled / no Synapse SQL user. | `CREATE USER [<sp-name>] FROM EXTERNAL PROVIDER;` on the pool, plus role membership. |

### "Dedicated SQL pools module shows 'Pool status is …; skipped DMV collection'"

Starting in **3.4.0**, the dedicated pools analyzer skips any pool
whose status is not `Online` (paused, pausing, resuming, scaling,
creating, deleting, recovering, restoring, disabled, inaccessible)
instead of failing the module. The pool's inventory row is still
captured; the per-pool error list records why DMV collection was
skipped. Resume / wait for the transition to complete and rerun the
module to pick up the data-plane details. The same skip-with-warning
also kicks in if the AAD login fails unexpectedly mid-run (for example
when the pool transitions out of `Online` between the ARM listing and
the SQL connect — visible as a `28000 / 18456 Login failed` ODBC
error).

---

## Performance issues

### "Run takes much longer than expected"

| Cause | Fix |
| ----- | --- |
| Many storage accounts in scope. | Confirm `SMA_STORAGE_INCLUDE_ALL` is **not** set; the default workspace-scoped scan is dramatically faster on subscriptions with many unrelated storage accounts. |
| Many pipelines and pre-2.0 sequential run-history fetch. | Upgrade to **2.0.0+** — pipeline run-history is now batched. |

### "SPA feels sluggish on a large workspace"

The Code-objects table virtualises rows, but the Recommendations and
Runbook tables do not. Workspaces with > 5 000 recommendations may
benefit from filtering aggressively.

---

## Still stuck

1. Run `sma doctor --json` and inspect the output.
2. Run `sma serve --with-api --debug` and watch the terminal — every
   request and error is logged.
3. File an issue with the doctor output and the failing run id.

## Related

- [12. Configuration](12-configuration.md) — Validate button
- [15. FAQ](15-faq.md) — quick answers to common questions
