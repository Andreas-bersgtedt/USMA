# 11. Diff page (semantic diff)

## Purpose

Compare two runs *semantically* — not by file SHA but by
recommendation id, code-object id, pipeline name, etc. Answers
questions like *"which recommendations were resolved?"*, *"did the
readiness score move?"*, *"did any new T-SQL gaps appear?"*.

For a file-level delta of a single run see [08. Delta](08-delta.md).

## How to open

- URL: `/diff`.
- Modes: control plane only. The page calls `GET /api/diff?head=<id>&base=<id>`.

## Inputs

Both runs must exist on this server. The default **Base** is "(auto —
previous run)" — the API picks the chronologically previous run.

## Layout

```
Run diff

Head [ 2026-05-05T12-04-11Z (pre-migration baseline) ▾ ]
Base [ (auto — previous run)                          ▾ ]   [ Compare ]

Summary
┌──────────────────────────────┬──────────┬──────────┬──────────┐
│ Metric                       │ Base     │ Head     │   Δ      │
├──────────────────────────────┼──────────┼──────────┼──────────┤
│ readiness_score              │   82     │   84     │   +2     │
│ tsql_surface_findings        │  132     │  128     │   −4     │
│ recommendations.total        │   49     │   47     │   −2     │
│ recommendations.blocker      │    5     │    3     │   −2     │
│ pipelines.run_count_7d       │   30     │   30     │    0     │
└──────────────────────────────┴──────────┴──────────┴──────────┘

Delta
┌─────────────────────────┬──────────────────────────────────┬──────────┬─────────────┬─────────────┬─────────────┐
│ Path                    │ Kind                             │ Before   │ After       │ Detail      │             │
├─────────────────────────┼──────────────────────────────────┼──────────┼─────────────┼─────────────┼─────────────┤
│ rec/tsql.merge.001      │ + added                          │ —        │ blocker     │ ▾           │             │
│ rec/collation.005       │ − removed                        │ warn     │ —           │ ▾           │             │
│ rec/sizing.012          │ ◐ changed                        │ info     │ warning     │ ▾           │             │
│ pool/dwpool01/score     │ ◐ changed                        │  78      │   84        │             │             │
└─────────────────────────┴──────────────────────────────────┴──────────┴─────────────┴─────────────┴─────────────┘
```

## Field reference

### Selectors

| Control      | Effect |
| ------------ | ------ |
| **Head** dropdown | The "after" run. Defaults to the run currently selected in the run picker. |
| **Base** dropdown | The "before" run. Empty value means **auto-previous**. |
| **Compare** button | Re-runs the diff with the chosen pair. The result is cached client-side. |

### Summary card

A flat dictionary of headline metrics, each with its **Base**, **Head**
and signed **Δ**. Common keys:

- `readiness_score`
- `tsql_surface_findings`
- `tsql_compatibility_pct`
- `recommendations.total`
- `recommendations.blocker` / `.warning` / `.info`
- `pipelines.run_count_7d`
- `storage.dedicated_pool_data_gb`

The exact key set depends on which modules the runs share.

### Delta table

| Column     | Field      | Notes |
| ---------- | ---------- | ----- |
| **Path**   | `path`     | Stable identifier, e.g. `rec/tsql.merge.001`, `code_object/<id>/compatibility`, `pool/<name>/score`. |
| **Kind**   | `kind`     | `added`, `removed`, `changed`. |
| **Before** | `before`   | Value in the base run. `—` for `added` rows. |
| **After**  | `after`    | Value in the head run. `—` for `removed` rows. |
| **Detail** | (expandable) | Full JSON of both sides. |

## Common tasks

### "Did fixing collation actually move the needle?"

1. Pick the run after the fix as **Head**, the run before the fix as
   **Base**.
2. Look at `summary.readiness_score` Δ and at the `rec/collation.*`
   rows in the delta table.

### "Has the workspace regressed?"

Any `+ added` rows with severity `blocker` are regressions.

### "I expected zero changes — why is the diff non-empty?"

`pipelines.run_count_7d` and `storage.dedicated_pool_data_gb` move
naturally between runs (background pipeline traffic, daily storage
growth). Filter those out mentally; the per-row delta will show only
real changes.

## Empty / error states

- *Pick two runs to compare* — initial state, no fetch yet.
- *No differences found* — both runs produced byte-identical
  module outputs. Rare but possible (e.g. comparing a run to itself).
- 500 from `/api/diff` — see [13. Troubleshooting](13-troubleshooting.md);
  in pre-2.0 versions this could happen for runs without
  `fabric_mapping.json`. Fixed in 2.0.0.

## Related

- [08. Delta](08-delta.md) — file-level delta
- [10. Runs history](10-runs-history.md) — pick two runs
