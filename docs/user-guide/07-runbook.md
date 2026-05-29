# 07. Runbook

## Purpose

A sequenced, phased view of the same recommendations — what to do
first, second, third, and how to roll back if a step fails.

## How to open

- URL: `/runbook`.
- Modes: both static and control plane.

## Inputs

`fabric_mapping.json` — specifically the `runbook[]` array, generated
by `fabric_mapping.runbook_builder` from the recommendations plus a
phase / dependency heuristic.

## Layout

```
Migration runbook                                       [ Copy whole runbook as Markdown ]

Estimated effort: 184.0 h P50 / 264.0 h P90 — 27 P50 / 38 P90 resource-days
 · with 2 workers in parallel: 112.0 h P50 / 162.0 h P90 (17 / 24 calendar-days)

┌─────────────────────────┬───────┬────────┬────────┬────────┬────────┬─────────┬─────────┐
│ Phase                   │ Steps │ P50 h  │ P90 h  │ P50 d  │ P90 d  │ ∥ P50 h │ ∥ P90 h │
├─────────────────────────┼───────┼────────┼────────┼────────┼────────┼─────────┼─────────┤
│ Foundation              │   4   │  16.0  │  24.0  │   2    │   3    │  10.0   │  14.0   │
│ Data plane prep         │   5   │  32.0  │  48.0  │   5    │   7    │  18.0   │  26.0   │
│ Ingest shortcuts        │   3   │  18.0  │  24.0  │   3    │   4    │  10.0   │  14.0   │
│ Compute migration       │  10   │  60.0  │  84.0  │   9    │  12    │  36.0   │  50.0   │
│ Orchestration migration │   6   │  42.0  │  60.0  │   6    │   9    │  26.0   │  36.0   │
│ Verification            │   2   │  16.0  │  24.0  │   2    │   3    │  12.0   │  22.0   │
└─────────────────────────┴───────┴────────┴────────┴────────┴────────┴─────────┴─────────┘

Foundation — 1/4 done · 1 skipped · est. 12.0 h P50 remaining   [ Copy phase as Markdown ]
┌───┬──────────────────┬───────────────────────────────────────┬──────────┬────────┬─────────┬─────────┬─────────────────────┐
│ # │ Status           │ Step                                  │ Severity │ Effort │ P50 (h) │ P90 (h) │ Target              │
├───┼──────────────────┼───────────────────────────────────────┼──────────┼────────┼─────────┼─────────┼─────────────────────┤
│ 1 │ Done ▾           │ Refresh statistics on all tables    ▾ │ ◐ warn   │ Low    │ 2.0     │ 3.0     │ dwpool01            │
│ 2 │ In progress ▾    │ Backfill VIEW DEFINITION grant      ▾ │ ●        │ Low    │ 2.0     │ 3.0     │ dwpool01            │
│ 3 │ Skipped (reason) │ Storage account at 90 % used        ▾ │ ◐ warn   │ Medium │ 6.0     │ 8.0     │ saraw01             │
│ 4 │ Not started ▾    │ Confirm capacity sizing (F128)      ▾ │ ○ info   │ Medium │ 6.0     │ 10.0    │ dwpool01            │
└───┴──────────────────┴───────────────────────────────────────┴──────────┴────────┴─────────┴─────────┴─────────────────────┘
…
```

Each phase is its own table; rows are sorted by `order`. Clicking the
**Step** title expands an inline panel with the full detail, the
**Rollback** note (when present), and the effort breakdown.

## Field reference

### Sections (one per phase)

The phase string is rendered verbatim from `phase`. The analyzer
emits six phases in this order:

1. **Foundation** — capacity sizing, workspace provisioning, role
   assignments, storage account capacity / cost decisions.
2. **Data plane prep** — schema-level decisions (collation,
   distribution, materialised views), statistics refresh, grant
   backfills.
3. **Ingest shortcuts** — OneLake shortcuts and external-table /
   storage-account work needed before data lands in Fabric.
4. **Compute migration** — T-SQL rewrites, Spark notebook conversion,
   serverless query remappings.
5. **Orchestration migration** — Synapse pipelines → Fabric Data
   Factory: activities, linked services, triggers, expressions, plus
   run-history-driven cleanup (idle / chronically failing pipelines).
6. **Verification** — object / row count parity, monitoring rewiring,
   final cutover sign-off.

Each phase header shows a live progress summary:
`X/Y done · Z skipped · est. P50 hours remaining`. The numbers
update as soon as you change a step's status.

### Effort summary card

The card at the top of the page rolls per-step P50 / P90 estimates
into per-phase and project-level totals. Two parallel projections are
shown alongside:

- **Sequential** — what one person doing every step in order would
  take (this is what `total_p50_hours` / `total_p90_hours` measure).
- **Parallel (with N workers)** — assumes phases stay sequential
  (you cannot start *Compute migration* before *Data plane prep*),
  but steps inside a phase are split across N workers. The phase
  finishes in `max(longest_step, phase_total / N)` — whichever is the
  bottleneck. The default `N = 2` reflects how most migrations
  actually run (a lead engineer plus one contributor).

Days are computed as `ceil((hours / 8) × 1.15)` — 8-hour day, 15 %
spillage budget. The parallel days assume the same workday but apply
the parallel hours instead of sequential.

### Step status

Each step has a status dropdown:

- **Not started** (default)
- **In progress**
- **Done** — row is dimmed so you can scan what is left.
- **Skipped…** — you are prompted for a free-text reason. The reason
  is shown below the dropdown and included in the Markdown export.

Status is persisted in your browser (`localStorage`) and keyed on
either `source_recommendation_id` or `phase + order`. It survives
page reloads and runbook regenerations as long as the rec ids stay
stable. To start over, clear the `sma.runbook.status.*` keys for the
workspace.

### Copy as Markdown

Two buttons emit GitHub-flavoured Markdown tables for paste into a
ticket or change record:

- **Copy phase as Markdown** (per-phase header) — just that phase.
- **Copy whole runbook as Markdown** (page header) — every phase.

The exported table includes status and (for skipped steps) the skip
reason inline.

### Table columns

| Column     | Field      | Notes |
| ---------- | ---------- | ----- |
| **#**      | `order`    | 1-based step number within the phase. |
| **Status** | (browser)  | See *Step status* above. |
| **Step**   | `title`    | Click to expand `detail`, `rollback`, and effort breakdown. |
| **Severity** | `severity` | Same pill semantics as [Recommendations](06-recommendations.md). |
| **Effort** | `effort`   | `Low`, `Medium`, `High`. |
| **P50 (h)** | `effort_hours_p50` | Estimator output for this step. |
| **P90 (h)** | `effort_hours_p90` | Estimator output for this step. |
| **Target** | `target`   | The object / pool / pipeline this step modifies. |

### Detail panel

- `detail` — multi-paragraph description of the step, often pasted
  verbatim into a change ticket.
- `rollback` — when present, a one-paragraph note describing how to
  reverse the step if it fails (e.g. *"Revert the column collation
  with `ALTER COLUMN ... COLLATE <prev>` and rerun the dependent
  ETL."*). Steps without a rollback note are typically idempotent
  (e.g. statistics refresh).

## Common tasks

### "Hand the runbook to a junior engineer"

Print or copy the `runbook.md` file (it is the same content rendered
as Markdown) — the rendered Markdown ships in `output/`. The HTML
report (`fabric_mapping.html`) renders the same data with a TOC.

### "I want to skip a step"

Set the step status to **Skipped** and supply a free-text reason —
the SPA requires one and stores it in `localStorage` keyed to the
step. The reason is included verbatim in the Markdown export so
reviewers can see *why* steps were dropped. The next analyzer run
will re-emit the step if the underlying recommendation still
exists; your skip note persists locally.

### "The order seems wrong"

The order is heuristic — the analyzer cannot know your specific
deployment cadence. Treat it as a starting point. The phase
boundaries are usually correct (you genuinely cannot migrate schema
before fixing collation), but cross-phase reordering may make sense
for your release window.

## Empty / error states

- *No runbook generated* — `fabric_mapping.json` has no `runbook[]`
  entries. Either the run was very small (no recommendations → no
  steps), or the aggregator failed. Check the
  [Inputs analyzed](04-dashboard.md#inputs-analyzed) card on the
  Dashboard for module status.

## Related

- [06. Recommendations](06-recommendations.md) — flat triage list
- [08. Delta](08-delta.md) — what changed between two runs
