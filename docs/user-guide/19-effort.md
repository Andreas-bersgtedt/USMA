# 19. Effort estimates & rate card

## Purpose

Turn the sequenced runbook into a defensible **person-hour budget** —
P50 and P90 — that a delivery manager can show to a steering committee
without hand-waving. Every figure is the product of a coefficient in a
small JSON file (the **rate card**) and a count that came from the
analyser's own artefacts. No black boxes.

## What this is (and is not)

This **is**:

- A configurable cost model. You see every coefficient, you can edit
  every coefficient.
- A way to capture "our team is 1.5× faster than your defaults" or "we
  bill 4 h per linked-service port" without forking the analyser.
- An auditable explanation behind each step: the SPA shows you
  *"12 tables × 0.25 h + capped at 80 h"*.

This **is not**:

- A statistical model trained on real migrations.
- A guarantee. The numbers move with the rate card; if you don't tune
  it, treat the output as a starting point and refine after sprint 1.
- A substitute for sizing the data plane. Capacity sizing lives on the
  [Capacity page](04-dashboard.md), not here.

## Where the numbers come from

For each :doc:`runbook step <07-runbook>`:

1. The analyser groups all recommendations by their ``area`` (e.g.
   ``dedicated_pools.tables``).
2. For each area it sums the rule coefficients × the matching counts:

       hours = sum(coefficient_i × count_i)

   The counts come from the JSON outputs the other analysers already
   wrote (e.g. ``dedicated_pools.json`` → ``pools[*].tables``). The
   coefficient names are documented per rule below.
3. The area total is capped at ``cap_hours`` if specified.
4. The area total is then split evenly among the runbook steps that
   live in that area, so a step with two siblings shares the same
   budget.
5. The phase's ``base_hours`` (kickoff, runbook authoring, etc.) is
   split evenly across every step in the phase and added to each
   step's figure.
6. The whole step is divided by ``team_velocity`` (1.0 = the shipped
   default, 0.5 = "this team is half as fast", 2.0 = "twice as fast").
7. **P90** is **P50 × confidence_p50_to_p90_multiplier** (default 1.8).

When no rule fires *and* the phase has no base hours, the step falls
back to the **qualitative** map keyed by its existing
``low / medium / high`` effort label.

## Where to edit it

The card lives at ``effort-card.json`` in the same directory as your
``.env`` file. Three ways to manage it, in increasing order of
permanence:

1. **SPA editor — recommended for tuning.** Open the
   [Configuration page](12-configuration.md), expand the **Effort
   card (advanced)** panel, edit the JSON, click **Save**. The
   collapsed panel keeps the page clean for users who never touch it.
2. **CLI — for committing a tuned card to the repo.**
   - ``sma effort-card`` writes the shipped defaults to
     ``./effort-card.json`` so you have a starting point.
   - ``sma map-to-fabric --effort-card path/to/card.json`` or
     ``sma analyze-all --effort-card …`` runs against an override.
   - ``SMA_EFFORT_CARD=path/to/card.json`` is honoured by every
     subcommand that builds the runbook.
3. **Disable / revert.** Click **Reset to shipped defaults** in the SPA panel,
   or simply delete ``effort-card.json``. The shipped defaults take
   over again.

## Rate card schema

The card is a single JSON object with five top-level keys.

```json
{
  "version": 1,
  "team_velocity": 1.0,
  "confidence_p50_to_p90_multiplier": 1.8,
  "qualitative": { "low": 2.0, "medium": 8.0, "high": 24.0 },
  "phases": {
    "foundation":              { "base_hours":  8.0 },
    "data_plane_prep":         { "base_hours": 16.0 },
    "ingest_shortcuts":        { "base_hours":  4.0 },
    "compute_migration":       { "base_hours":  8.0 },
    "orchestration_migration": { "base_hours":  8.0 },
    "verification":            { "base_hours": 16.0 }
  },
  "rules": {
    "dedicated_pools.tables":          { "hours_per_table": 0.25, "cap_hours": 80.0 },
    "dedicated_pools.indexes":         { "hours_per_index_recommendation": 0.5, "cap_hours": 40.0 },
    "dedicated_pools.tsql_surface":    { "hours_per_blocker": 2.0, "hours_per_warning": 0.5, "cap_hours": 120.0 },
    "spark_pools.notebooks":           { "hours_per_notebook": 1.5, "cap_hours": 80.0 },
    "pipelines.activities":            { "hours_per_pipeline": 0.75, "hours_per_blocker_activity": 1.0, "cap_hours": 120.0 },
    "pipelines.linked_services":       { "hours_per_linked_service": 0.5, "hours_per_inline_secret": 0.5, "cap_hours": 40.0 }
    /* … see `sma effort-card` for the full default list … */
  }
}
```

### Recognised unit keys

The estimator only knows about a small, fixed set of unit names. Any
other key falls back to *"one per recommendation in this area"*.

| Unit key                          | Counts                                                                  |
|-----------------------------------|-------------------------------------------------------------------------|
| `hours_per_table`                 | Tables across all dedicated pools                                       |
| `hours_per_index_recommendation`  | Recommendations in the area                                             |
| `hours_per_recommendation`        | Recommendations in the area                                             |
| `hours_per_distribution_change`   | Recommendations in the area                                             |
| `hours_per_blocker`               | Recommendations with `severity = blocker` in the area                   |
| `hours_per_warning`               | Recommendations with `severity = warning` in the area                   |
| `hours_per_view`                  | Recommendations in the area                                             |
| `hours_per_stale_stat`            | Recommendations in the area                                             |
| `hours_per_notebook`              | Notebooks from `spark_pools.json`                                       |
| `hours_per_pool`                  | Spark pools from `spark_pools.json`                                     |
| `hours_per_library`               | Recommendations in the area                                             |
| `hours_per_pipeline`              | Pipelines from `pipelines.json`                                         |
| `hours_per_blocker_activity`      | Recommendations with `severity = blocker` in the area                   |
| `hours_per_linked_service`        | Linked services from `pipelines.json`                                   |
| `hours_per_inline_secret`         | Recommendations in the area                                             |
| `hours_per_trigger`               | Triggers from `pipelines.json`                                          |
| `hours_per_expression_blocker`    | Recommendations with `severity = blocker` in the area                   |
| `hours_per_runtime`               | Integration runtimes from `pipelines.json`                              |
| `hours_per_external_table`        | External tables from `serverless_pools.json`                            |
| `hours_per_query`                 | Queries from `serverless_pools.json`                                    |
| `cap_hours`                       | Upper bound on the area total                                           |

### Top-level knobs

| Field                                | Effect                                                                                                          |
|--------------------------------------|------------------------------------------------------------------------------------------------------------------|
| `version`                            | Must be `1`. Reserved for future schema migrations.                                                              |
| `team_velocity`                      | Divides every step's P50. `2.0` ⇒ this team is twice as fast as the defaults.                                    |
| `confidence_p50_to_p90_multiplier`   | Step P90 = P50 × this. Default `1.8`.                                                                            |
| `qualitative`                        | Fallback hours when no rule fires. Keyed by `low / medium / high`.                                              |
| `phases.<phase>.base_hours`          | Fixed per-phase overhead, split across the phase's runbook steps.                                               |

### Where the result shows up

- **Runbook page** in the SPA — header summary pill ("Total: 142 h
  P50 / 256 h P90"), per-phase breakdown table, and two new columns
  `P50 (h)` / `P90 (h)` on every step row. Hover the cells for the
  human-readable component breakdown.
- **`fabric_mapping.md`** — a new "Estimated effort" section with the
  same totals and per-phase rollup.
- **`fabric_mapping.html`** — same, rendered into the existing report.
- **`fabric_mapping.json`** — each `runbook[*]` now carries
  `effort_hours_p50`, `effort_hours_p90`, and `effort_breakdown`. A
  new top-level `effort_summary` object carries the totals.

## Calibration loop

The defaults are conservative middle-of-the-road numbers from
typical mid-size migrations. Treat them as a starting point:

1. Run sprint 1 against the shipped defaults.
2. At sprint review, compare actuals to the analyser's P50 per
   recommendation area.
3. Adjust the offending coefficient or `team_velocity` in the SPA
   editor.
4. Save. Subsequent runs pick up the new card automatically (because
   the JobRunner reads `effort-card.json` from the working dir).

> **Tip.** If you find yourself routinely overrunning P90 in one
> particular phase, increase that phase's `base_hours` rather than
> globally tanking `team_velocity` — phase base hours represent the
> fixed overhead (kickoff, runbook authoring, sign-off) that the
> per-unit coefficients don't capture.
