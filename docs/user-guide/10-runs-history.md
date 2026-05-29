# 10. Runs history

## Purpose

A reverse-chronological list of every run available on this server.
The single place to switch the active run, see at a glance which runs
failed, and copy a run id into a deep link.

## How to open

- URL: `/runs`.
- Modes: control plane only.

## Inputs

The page calls `GET /api/runs?limit=50` — the server enumerates the
`output/runs/` folder (or whichever directory the active config
points at) and returns a manifest entry per run.

## Layout

```
Runs (50 most recent)

┌──────────────────────────┬───────────────────────────┬──────────┬─────────────────────┬──────────┬────────┬───────┬─────────┐
│ ID                       │ Label                     │ Status   │ Started             │ Duration │ Errors │ Score │         │
├──────────────────────────┼───────────────────────────┼──────────┼─────────────────────┼──────────┼────────┼───────┼─────────┤
│ 2026-05-05T12-04-11Z     │ syn-prod-eu               │ ● ok     │ 5 May 2026 12:04:11 │ 02:14    │   0    │  84   │ Open · Delete │
│ 2026-05-04T18-22-09Z     │ syn-prod-eu               │ ● ok     │ 4 May 2026 18:22:09 │ 01:58    │   0    │  82   │ Open · Delete │
│ 2026-05-04T09-10-00Z     │ first attempt             │ ◐ partial│ 4 May 2026 09:10:00 │ 00:42    │   2    │  76   │ Open · Delete │
│ 2026-05-03T17-01-15Z     │ smoke                     │ ● failed │ 3 May 2026 17:01:15 │ 00:08    │   1    │   —   │ Open · Delete │
└──────────────────────────┴───────────────────────────┴──────────┴─────────────────────┴──────────┴────────┴───────┴─────────┘
```

## Field reference

| Column      | Field                                | Notes |
| ----------- | ------------------------------------ | ----- |
| **ID**      | `id`                                 | The folder name under `output/runs/`. ISO-8601 timestamp with `:` replaced by `-`. |
| **Label**   | `label`                              | Free-text from the [Run page](09-run-page.md). Empty when the run was started from the CLI without `--label`. |
| **Status**  | `status`                             | Pill: ● ok / ◐ partial / ● failed / ◐ running / ○ cancelled. |
| **Started** | `started_at` (UTC)                   | Rendered in your browser's locale. |
| **Duration**| `finished_at - started_at`           | `mm:ss` for runs ≤ 1 h, `h:mm:ss` otherwise. Blank while still running. |
| **Errors**  | `error_count`                        | Module-level errors; click **Open** then the [Dashboard](04-dashboard.md) → *Inputs analyzed* card to see which module(s) failed. |
| **Score**   | `summary.readiness_score`            | Same value as the headline card on the Dashboard. `—` if the run has no `fabric_mapping.json`. |
| **Open**    | button                               | Sets `#run=<id>` in the URL and navigates to `/`. Equivalent to picking the run in the run picker. |
| **Delete**  | button                               | Deletes the run folder on disk after a confirmation prompt. Disabled while the run is `queued` or `running` (the API responds `409`). If the deleted run is currently active, the page clears `#run=<id>` so the next page load picks a different run. |

## Common tasks

### "Show me only the failed runs"

The page does not filter, but the **Status** column is sortable —
click it to group failures together.

### "Get a shareable link to a specific run"

Click **Open** on the run, then copy the URL from the address bar.
The hash (`#run=<id>`) is part of the link. Anyone with access to the
same server (or the same set of run folders) will see the same data.

### "Why does my latest run not show up?"

Three common reasons:

1. The run is still in progress — refresh after it finishes.
2. The run wrote to a different `output_dir` than the API is reading
   from. Check `sma serve --output-dir <path>` against what the CLI
   used.
3. The run folder lacks a `manifest.json`. Older runs (pre-1.0) had
   no manifest and are silently skipped.

### "Clean up old runs"

Click **Delete** on the row, confirm the prompt, and the run folder
is removed via `DELETE /api/runs/{id}/data`. The list refreshes
automatically. Active runs (`queued` / `running`) cannot be deleted
per-row — cancel them first.

The analyzer never deletes runs automatically; deleting in the UI is
the equivalent of `Remove-Item -Recurse output/runs/<id>` and is
irreversible.

### "Wipe everything, including stalled runs"

Use the **Delete all runs** button above the table. It calls
`DELETE /api/runs`, which:

1. Best-effort cancels every run still flagged `queued` or `running`
   (this handles stalled runs left behind by a server restart that
   never transitioned out of `running`).
2. Force-deletes every run directory on disk regardless of status.
3. Returns a count of how many were deleted and how many stalled
   runs were cancelled, which the page surfaces in the status line.

You must type `DELETE ALL` into the confirmation prompt — the action
is irreversible. The currently selected run is also cleared from the
URL hash so other pages stop trying to load a deleted run.

## Empty / error states

- *No runs found* — the `output/runs/` directory is empty or missing.
  Start a run from [09. Run page](09-run-page.md) or from the CLI.
- *Failed to load runs* — the API returned a non-2xx. Check the
  server log; common cause is a permission error reading the output
  directory.

## Related

- [03. Run picker](03-run-picker.md) — switch runs without leaving the current page
- [09. Run page](09-run-page.md) — kick off a new run
- [11. Diff page](11-diff-page.md) — compare any two runs
