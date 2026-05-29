# 09. Run page (start a new run)

## Purpose

Kick off a new analyzer run from the browser, watch its progress
live, and cancel it if needed. Control-plane only.

## How to open

- URL: `/run`.
- Modes: control plane only. The link is hidden in static mode.

## Inputs

The page does not consume any analyzer JSON — it talks to the API
(`/api/runs/start`, `/api/runs/{id}/cancel`, and the SSE stream
`/api/runs/{id}/events`).

## Layout

```
Start a new run

Lookback [ 14 days ▾ ]                       ← 1 / 3 / 7 / 14 / 28 / 60 days
Label    [ syn-prod-eu ………………………………… ]      ← defaults to the workspace name

Modules
[x] dedicated_pools     [x] serverless_pools   [x] spark_pools
[x] pipelines           [x] monitoring         [x] storage
[x] fabric_mapping      [x] governance         [x] security
[x] cost                [x] fabric_validation

[ Start run ]            [ Cancel ]   ← (visible while running)

──────────────────────────────────────────────────────────────────────
Progress
┌─────────────────────┬──────────┬────────────────────────────────────┐
│ Module              │ State    │ Latest event                       │
├─────────────────────┼──────────┼────────────────────────────────────┤
│ dedicated_pools     │ ● ok     │ collected 1 pool, 128 code objects │
│ serverless_pools    │ ● ok     │ no serverless pool found           │
│ storage             │ ◐ running│ enumerating containers (3/12)      │
│ fabric_mapping      │ ○ pending│ —                                  │
└─────────────────────┴──────────┴────────────────────────────────────┘
```

The progress table collapses each module to **one row** showing the
latest event. State pill: `pending` (muted), `running` (amber spinner),
`ok` (green), `failed` (red).

## Field reference

### Lookback

Global trailing-days window applied to every analyzer that fetches
run history — **pipelines**, **spark_pools** (Livy history) and
**monitoring** (Azure Monitor metrics).

| Option | Notes |
| ------ | ----- |
| 1 day  | Smoke-test / debug run; finishes fastest. |
| 3 days | Short rolling window for a daily health-check. |
| 7 days | Matches the Dashboard's *last 7 days* cards. |
| 14 days | **Default.** Good signal-to-noise for most assessments. |
| 28 days | Matches the headline pipeline run-stats window. |
| 60 days | Long lookback; expect noticeably longer run time on busy workspaces. |

The selector **overrides** `SMA_PIPELINES_RUN_DAYS`,
`SMA_SPARK_RUN_DAYS` and `SMA_MONITORING_DAYS` for the duration of
the run; previous environment values are restored when the run
finishes (or fails / is cancelled). Analyzers that don't fetch run
history (e.g. `dedicated_pools`, `serverless_pools`, `storage`,
`security`, `governance`, `fabric_mapping`, `cost`,
`fabric_validation`) ignore the selector entirely.

> Spark Livy has no server-side date filter — the client pages
> newest-first and stops once a full page is older than the window
> (`stop_old`). The `SMA_SPARK_RUN_LIMIT` ceiling (default `50000`
> since 3.2.0) is the only other guard.

### Label

Free-text label persisted with the run. Shown in
[10. Runs history](10-runs-history.md) and in the run picker. The
field **defaults to the configured workspace name** so the run list
stays scannable when you have multiple workspaces; type to override.
The field is cleared and respected once you start typing — switching
workspaces in [12. Configuration](12-configuration.md) only updates
the default on a fresh load.

### Modules

All modules are selected by default. Untick the ones you don't need
for a quicker run.

| Module             | Default | Notes |
| ------------------ | :-----: | ----- |
| `dedicated_pools`  |   ✓     | Inventory + code objects + T-SQL surface. |
| `serverless_pools` |   ✓     | Logical / external table inventory. |
| `spark_pools`      |   ✓     | Spark pool inventory. |
| `pipelines`        |   ✓     | Pipelines + activities + linked services. |
| `monitoring`       |   ✓     | DWU usage / log query stats. |
| `storage`          |   ✓     | ADLS accounts attached to the workspace (default ADLS + linked-service references). Set `SMA_STORAGE_INCLUDE_ALL=1` in `.env` to inventory every account in the subscription. |
| `fabric_mapping`   |   ✓     | Aggregates everything into recommendations & runbook. |
| `governance`       |   ✓     | Naming, grants, ownership. |
| `security`         |   ✓     | Logins, roles, masking. |
| `cost`             |   ✓     | DWU-hours and spend attribution. |
| `fabric_validation`|   ✓     | Post-migration parity checks vs a target Fabric warehouse (experimental). |

### Buttons

- **Start run** — submits the form to `/api/runs/start` and
  immediately writes the new run id to the URL hash and
  `sessionStorage`. The run picker switches to the new run on the
  next paint.
- **Cancel** — only visible while a run is in flight. Calls
  `/api/runs/{id}/cancel`. Modules that are mid-execution complete
  their current step before stopping.

## Common tasks

### "Run only pipelines for a quick check"

1. Uncheck every module except `pipelines` (and `fabric_mapping` if
   you want recommendations).
2. Click **Start run**.

A pipeline-only run typically finishes in under a minute.

### "Reproduce yesterday's run"

There is no "rerun with same options" button yet — copy the module /
option selection manually. The run-id of yesterday's run, plus the
[Diff page](11-diff-page.md), is the closest thing.

### "Run finished but the page didn't switch"

Click the run id in [10. Runs history](10-runs-history.md). The Run
page only updates the URL hash on **start**, not on completion — if
you navigated away and back, the page may still be tracking the same
run.

## Empty / error states

- "API not reachable" banner — the FastAPI server isn't running on
  the expected port. Start it with `sma serve --with-api`.
- A module shows **failed** — read the *Latest event* column for the
  short error and see [13. Troubleshooting](13-troubleshooting.md).
- `fabric_mapping` is selected but no upstream module is — it will
  produce an empty recommendations file. Always pair it with at least
  one collector.

## Related

- [02. Modes](02-modes.md) — why this page is hidden in static mode
- [10. Runs history](10-runs-history.md) — see the result
- [12. Configuration](12-configuration.md) — set the analyzer's target workspace
