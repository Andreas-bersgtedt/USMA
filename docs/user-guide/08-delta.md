# 08. Delta (file delta)

## Purpose

Show, for the currently selected run, which output files were
**added**, **removed**, **changed** or stayed **unchanged** relative
to the previous run. This is a file-content delta; for a *semantic*
diff (recommendations / readiness numbers) see
[11. Diff page](11-diff-page.md).

## How to open

- URL: `/delta`.
- Modes: both static and control plane.

## Inputs

`run_delta.json` — emitted by the manifest writer when a run finishes
and a previous run is available.

## Layout

```
File delta — run 2026-05-05T12-04-11Z (vs 2026-05-04T18-22-09Z)

┌───────────────────┬──────────────────────────────┬──────────┬──────────┬───────────┬───────────┬────────────────────┐
│ Module            │ Path                         │ Status   │ Records  │ Δ records │ Size      │ SHA-256            │
├───────────────────┼──────────────────────────────┼──────────┼──────────┼───────────┼───────────┼────────────────────┤
│ pipelines         │ pipelines.json               │ ◐ changed│   8      │     +1    │ 412 KB    │ 7c4e…f9            │
│ pipelines         │ pipelines_run_history.json   │ + added  │ 56       │    n/a    │ 184 KB    │ a012…3b            │
│ dedicated_pools   │ dedicated_pools.json         │ ○ unchgd │ —        │     0     │ 1.34 MB   │ 92ab…c1            │
│ monitoring        │ usage.csv                    │ − removed│ —        │    n/a    │ —         │ —                  │
└───────────────────┴──────────────────────────────┴──────────┴──────────┴───────────┴───────────┴────────────────────┘
```

## Field reference

| Column        | Field                             | Notes |
| ------------- | --------------------------------- | ----- |
| **Module**    | `module`                          | The module that produced the file. |
| **Path**      | `path`                            | Relative path inside the run folder (e.g. `pipelines.json`). |
| **Status**    | `status`                          | One of `added`, `removed`, `changed`, `unchanged`. Pill colours: `+` green, `−` red, `◐` amber, `○` muted. |
| **Records**   | `record_count`                    | Logical record count when applicable (e.g. number of rows in a CSV, length of a top-level JSON array). |
| **Δ records** | `record_count - prev_record_count` | Signed delta. `n/a` for `added` / `removed` rows where one side has no record count. |
| **Size**      | `size_bytes`                      | Human-rounded (KB / MB). |
| **SHA-256**   | `sha256[:6]…`                     | Truncated; full hash is in `run_delta.json`. |

## Status semantics

| Status     | Meaning |
| ---------- | ------- |
| **added**  | File exists in the current run but not in the previous one. Example: `pipelines_run_history.json` shows up the first time `--with-run-history` is used. |
| **removed** | File existed in the previous run but is missing now. Example: a module was disabled, or the source resource went away. |
| **changed** | File exists in both runs and the SHA-256 differs. Inspect *Δ records* to see if the change is structural. |
| **unchanged** | File exists in both runs with the same SHA-256. |

## Common tasks

### "Did anything change at all?"

Sort by **Status** — if every row is `unchanged`, the workspace is
stable. (Note: `unchanged` is normal for things like
`dedicated_pool_storage.csv` when the pool is paused.)

### "Why did the recommendations change?"

The file delta tells you *that* `fabric_mapping.json` changed. To see
*what* recommendations were added or removed, switch to the
[11. Diff page](11-diff-page.md).

### "Catch a regression where a module silently stopped running"

Look for unexpected `removed` rows. If `pipelines.json` flips to
`removed`, a previously-running module is now failing — check
[10. Runs history](10-runs-history.md) for the run's error count.

## Empty / error states

- *No delta available* — `run_delta.json` is missing. The most common
  reason: this is the first run, or the previous run folder has been
  deleted. (Retention is not automatic — runs are kept until you
  remove them.)

## Related

- [11. Diff page](11-diff-page.md) — semantic diff between any two runs
- [10. Runs history](10-runs-history.md) — list of available runs
