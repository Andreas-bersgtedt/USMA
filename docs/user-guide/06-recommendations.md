# 06. Recommendations

## Purpose

The full triage list. Every finding emitted by `fabric_mapping`'s
rules engine, sortable and filterable in one table.

## How to open

- URL: `/recommendations`.
- Modes: both static and control plane.

## Inputs

`fabric_mapping.json` — specifically the `recommendations[]` array.
Each entry has a stable `id`, severity, area, target, title, detail,
effort estimate, and a Fabric-side action hint.

## Layout

```
Recommendations

┌────────────┬────────────┬────────┬──────────────┬─────────┬──────────────────┐
│ Blockers 4 │ Warnings 19 │ Info 11 │ High impact 6 │ Medium 14 │ Low / unknown 14 │
└────────────┴────────────┴────────┴──────────────┴─────────┴──────────────────┘

[ Filter… ] [ All severities ▾ ] [ All impact ▾ ] [ All areas ▾ ] [ Group by … ▾ ]   34 / 47

┌──────────┬──────────┬────────┬────────────────┬─────────────────────────────┬────────────────────────────────┬──────────────────────────────┐
│ Severity │ Impact   │ Effort │ Area           │ Target                      │ Title                          │ Fabric action                │
├──────────┼──────────┼────────┼────────────────┼─────────────────────────────┼────────────────────────────────┼──────────────────────────────┤
│ ●        │ High     │ Medium │ tsql.surface   │ dbo.UpsertCustomer          │ MERGE statement used        ▾ │ Rewrite as INSERT/UPDATE     │
│ ●        │ Medium   │ Low    │ collation      │ dbo.Customer.Notes          │ Column collation mismatch   ▾ │ ALTER COLUMN to UTF-8        │
│ ◐        │ Low      │ Medium │ pl.runs.idle   │ pl_archive_legacy           │ Pipeline has no runs        ▾ │ Decommission or schedule     │
│ ○        │ Unknown  │ Low    │ sizing         │ dwpool01                    │ Downsize candidate (DWU p95)▾ │ Match with Fabric F128       │
└──────────┴──────────┴────────┴────────────────┴─────────────────────────────┴────────────────────────────────┴──────────────────────────────┘
```

Click the title to expand the inline `<details>` element with the full
description and, when present, the `impact_detail` — a short
business-impact explanation (e.g. *"contributes 12.4 % of CPU
elapsed-ms in the workload sample"*).

The rollup tiles at the top recompute against whatever filter is
active so they always describe what you are looking at.

## Field reference

### Toolbar

| Control                | Effect |
| ---------------------- | ------ |
| **Filter** input       | Free-text across `id`, `title`, `detail`, `area`, `target`, `fabric_action`, `impact_detail`. |
| **Severity** dropdown  | `blocker`, `warning`, `info`. |
| **Impact** dropdown    | `high`, `medium`, `low`, `unknown` — see the *Impact pill semantics* table below. |
| **Area** dropdown      | Auto-populated from the data. Common areas: `tsql.surface`, `collation`, `materialized_view`, `statistics`, `distribution`, `sizing`, `pl.activity`, `pl.linked_service`, `pl.runs.idle`, `pl.runs.idle_7d`, `pl.runs.low_success`, `pl.runs.chronic_failure`, `pl.runs.heavy_data_movement`, `monitoring`, `storage.dedicated_pool`, `storage.accounts`, `governance`, `security`, `cost`. |
| **Group by** dropdown  | `None` (default), `Area`, `Target`. When grouping, rows collapse under a header that shows the count and a severity mix. |

The counter on the right shows `<filtered> / <total>`.

### Table columns

| Column          | Field           | Notes |
| --------------- | --------------- | ----- |
| **Severity**    | `severity`      | Pill: ● blocker (red), ◐ warning (amber), ○ info (green). Sort order is blocker → warning → info, then impact high → unknown, then area. |
| **Impact**      | `impact`        | Pill: high (red), medium (amber), low (green), unknown (muted). Business-impact axis — see below. |
| **Effort**      | `effort`        | `Low`, `Medium`, `High`. Heuristic estimate from the rule; used by the runbook step time estimator. |
| **Area**        | `area`          | Stable rule-family id (e.g. `tsql.surface`). Useful for grouping. |
| **Target**      | `target`        | Free-form identifier the rule applies to — pool name, schema-qualified object, pipeline name, storage account, etc. |
| **Title**       | `title`         | One-line summary; click to expand the full `detail` and any `impact_detail`. |
| **Fabric action** | `fabric_action` | Concrete remediation hint for Fabric Warehouse. |

### Severity pill semantics

| Pill     | Meaning |
| -------- | ------- |
| ● blocker | The migration cannot proceed without addressing this. (Examples: column collation mismatch, MERGE in a procedure, unsupported pipeline activity.) |
| ◐ warning | The migration will succeed but with degraded behaviour. (Examples: stale statistics, low-success pipeline, idle pipeline.) |
| ○ info   | Notable but not blocking — sizing hints, governance observations, cost concentration. |

### Impact pill semantics

The **impact** axis answers *"how much does this matter for the
business?"* — independent of how hard it is to fix or how strictly it
blocks migration. Severity says *what kind of problem*, impact says
*how big*.

| Pill     | Meaning |
| -------- | ------- |
| High     | The finding affects a hot object or a workload-critical surface (e.g. a table that holds a large share of dedicated-pool CPU elapsed-ms; a pipeline driving heavy data movement; a pool at ≥ 95 % storage). |
| Medium   | The finding has noticeable workload share or sustained activity, but is not the dominant cost driver. |
| Low      | Cleanup-class items: cold objects, low-success-but-rare pipelines, governance hygiene. Useful to fix, safe to defer. |
| Unknown  | The rule has no workload signal to score against (e.g. structural rules where workload telemetry was unavailable). Treat as *triage manually*. |

When workload metadata is available, the rule also fills in an
`impact_detail` string with the specific evidence — typically a
percentage share or a row-count threshold. The Recommendations page
shows this directly underneath the expanded detail.

## Common tasks

### "Build a sprint of all blockers under T-SQL surface"

Severity = **Blocker**, Area = **tsql.surface**. The visible rows are
your sprint. Pair them with [05. Code objects](05-code-objects.md) for
the line ranges.

### "What pipelines need attention?"

Filter free-text = `pl.`. Three families show up: `pl.activity` (the
activity is unsupported in Fabric), `pl.linked_service` (connector
unsupported), `pl.runs.*` (run-history-driven warnings).

### "Anything new since last run?"

The Recommendations page does not show a per-row delta — use the
[Diff page](11-diff-page.md) for that. The summary tells you how many
recommendations were added / removed; this page shows what they are.

### "Pull the data into a spreadsheet"

The same data is in `output/recommendations.csv` (CSV) and
`output/fabric_mapping.json` (`recommendations[]`). Open the CSV in
Excel for offline triage.

## Empty / error states

- *No recommendations to show* — the rules engine produced zero
  findings. Either the workspace is genuinely clean, or
  `fabric_mapping.json` is empty / missing.
- Filter yields no rows — sub-text shows `0 / N`.

## Related

- [04. Dashboard](04-dashboard.md) — top-blockers card
- [05. Code objects](05-code-objects.md) — the source for `tsql.surface`
- [07. Runbook](07-runbook.md) — recommendations sequenced as steps
