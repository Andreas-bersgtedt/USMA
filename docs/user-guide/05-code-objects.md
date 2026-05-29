# 05. Code objects

## Purpose

Inspect every stored procedure, view, function and trigger across all
dedicated SQL pools, and drill into the T-SQL surface gaps that block
their move to Fabric Warehouse.

## How to open

- URL: `/code-objects`.
- Modes: both static and control plane.

## Inputs

Single source: `dedicated_pools.json`. The page reads each pool's
`code_objects[]` and joins them to `tsql_surface_gaps[]` via
`code_object_id`.

## Layout

```
Code objects (SQL plane)
128 object(s) across 1 pool(s)

[ Filter ………………………… ] [ All compatibility ▾ ] [ All types ▾ ]   118 match(es)

┌────────────┬─────────────────────────────┬──────┬───────────────┬───────┬────────┬───────────┬─────────┐
│ Pool       │ Object                      │ Type │ Compatibility │ Lines │ Params │ T-SQL gaps│         │
├────────────┼─────────────────────────────┼──────┼───────────────┼───────┼────────┼───────────┼─────────┤
│ testpool01 │ dbo.UpsertCustomer          │ P    │ ● incompatible│  44   │   3    │     2     │ Details │
│ testpool01 │ dbo.RebuildIndexes          │ P    │ ● incompatible│ 112   │   0    │     1     │ Details │
│ testpool01 │ dbo.GetActiveCustomers      │ V    │ ● needs_review│  18   │   0    │     1     │ Details │
│ testpool01 │ dbo.fn_NormalizeEmail       │ FN   │ ○ compatible  │   8   │   1    │     0     │ Details │
└────────────┴─────────────────────────────┴──────┴───────────────┴───────┴────────┴───────────┴─────────┘
```

Clicking **Details** expands an inline panel showing parameter
signatures, the per-rule T-SQL gaps, the offending line ranges, and
metadata (created / modified date, ANSI-NULLS state).

## Field reference

### Toolbar

| Control            | Effect                                          |
| ------------------ | ----------------------------------------------- |
| **Filter** input   | Free-text filter across pool, schema, name, code-object id, type, compatibility. |
| **Compatibility** dropdown | Restrict to `incompatible`, `needs_review` or `compatible`. |
| **Type** dropdown  | Restrict to a specific object type (`P`, `V`, `FN`, `IF`, `TF`, `TR`, …). |

The match counter on the right shows the filtered row count.

### Table columns

| Column         | Field                                | Notes |
| -------------- | ------------------------------------ | ----- |
| **Pool**       | `<pool>.inventory.name`              | The dedicated pool the object belongs to. |
| **Object**     | `<schema>.<object_name>`             | Quoted in `<code>` style. |
| **Type**       | `object_type`                        | `P` = procedure, `V` = view, `FN` = scalar UDF, `IF` = inline TVF, `TF` = multi-statement TVF, `TR` = trigger. |
| **Compatibility** | `compatibility`                   | `compatible` / `needs_review` / `incompatible`. Sort uses the worst-first order `incompatible → needs_review → compatible`. |
| **Lines**      | `line_count`                         | From `sys.sql_modules.definition`. `NULL` when the body is hidden (encrypted / restricted), shown blank. |
| **Params**     | `parameter_count`                    | From `sys.parameters`. |
| **T-SQL gaps** | `gap_count`                          | Count of `tsql_surface_gaps[]` entries linked by `code_object_id`. |

### Detail panel (when expanded)

- **Identity**: `code_object_id` (stable across runs), `object_id`
  (catalog id), schema, name, type, created / modified timestamps.
- **Settings**: `is_ansi_nulls_on`, `is_quoted_identifier_on`,
  `definition_length` (chars).
- **Parameters**: ordered list of `{ name, type, length, is_output }`.
- **T-SQL gaps**: each row shows the rule id (e.g. `tsql.merge`,
  `tsql.cursor`, `tsql.global_temp`, `tsql.three_part_name`),
  severity, and the line range in the procedure where it was detected.

## Common tasks

### "Show me every blocker T-SQL gap from a specific schema"

1. Compatibility = **Incompatible**.
2. Filter = `dbo` (or your schema).
3. Sort by **T-SQL gaps** descending.

The top of the list is your remediation queue.

### "Are any procedures missing a body?"

Filter for **Lines = empty**. If you see any rows here that should
have a body (i.e. you know the proc has code), the analyzer's principal
is missing **VIEW DEFINITION** on the pool. See the troubleshooting
note in [13. Troubleshooting](13-troubleshooting.md).

### "Find every object that uses MERGE"

Filter free-text = `merge`. The text search matches the rule ids in
the gap list, so any object whose details contain a `tsql.merge` gap
will surface.

### "Was an object compatible last run?"

Switch the [run picker](03-run-picker.md) to the previous run id and
look up the same object — the `code_object_id` is stable, so it survives
across runs. The [Diff page](11-diff-page.md) gives a structural diff
of the same set.

## Empty / error states

- *No code objects collected. Run the dedicated_pools module.* —
  `dedicated_pools.json` is missing or has no `code_objects[]`. The
  most common reason in 1.2.x and earlier was the views-only join bug
  (fixed) — if you see this on 2.0.0, the principal lacks
  **VIEW DEFINITION**.
- Empty filter result — sub-text just shows `0 match(es)`. Clear the
  filter to recover.

## Top consumed tables / views

A separate, collapsible section below the code-objects table lists the
tables and views most frequently referenced by the recent workload on
each dedicated pool. Use it to pick the first tables to migrate, the
ones to materialize as Lakehouse Delta, or the ones that warrant
attention from a capacity-planning perspective.

### How it's built

1. The `top_consumed_objects` collector pulls the most recent ~5000
   submitted commands from `sys.dm_pdw_exec_requests` (look-back 14
   days, but the DMV itself is a rolling buffer so the *effective*
   look-back is whatever the buffer holds).
2. Each command is parsed in Python with [`sqlglot`](https://github.com/tobymao/sqlglot)
   (T-SQL dialect). Every `Table` node in the AST is cross-checked
   against `INFORMATION_SCHEMA.TABLES`/`VIEWS` for that pool — names
   that aren't in the catalog (CTEs, temp tables, system DMVs, typos)
   are dropped.
3. Parsed requests are persisted to a per-pool workload cache at
   `output/.cache/dedicated_pools_workload/<workspace>__<pool>.json`
   (default **30-day TTL**). Each run merges new request ids into the
   cache, ages out stale entries, and writes the file back atomically.
4. The page aggregates *the full cache window* (not just the current
   run's DMV snapshot) to produce `usage_count` and `elapsed_time_ms`
   per object. That side-steps the rolling-buffer roll-off that made
   the old SQL-side LIKE-join "hit and miss" between runs.

### Ranking

A **Rank by** control above the table switches between:

- **Elapsed time** *(default)* — sum of `total_elapsed_time` for
  every cached request that touched the object. Better answer to
  "where does the pool actually spend time?"; less skewed by tiny
  metadata queries that fan out to every table.
- **Usage count** — distinct cached requests that referenced the
  object. Better answer to "what gets touched the most?".

### Match-kind column

| Badge                  | Meaning |
| ---------------------- | ------- |
| `qualified`            | 2- or 3-part name (`schema.name`) resolved cleanly in the catalog. Trustworthy. |
| `unqualified`          | 1-part name (`SELECT * FROM fact_sales`) where exactly one schema owns that name — attributed to that owner heuristically. |
| `ambiguous`            | 1-part name found in **more than one** schema. One hit is recorded *for each candidate* and the badge flags the cluster as suspect. |

Non-qualified rows are dimmed so the eye is drawn to the trustworthy
counts first.

### Capture footnote

Below the table, a muted footnote summarizes the capture-health stats
aggregated across pools (hover for per-pool detail):

```
Capture: 12,348 request(s) in the 30-day cache · this run: 4,921 DMV row(s),
3,890 parsed, 12 failed, 1,019 empty · window 2024-09-12 → 2024-10-12 UTC
```

- **cached requests** — total distinct `request_id`s in the on-disk cache (the universe the ranking aggregates over).
- **DMV rows** — rows pulled from `sys.dm_pdw_exec_requests` *this run*.
- **parsed / failed / empty** — sqlglot outcomes for the *new* commands this run (already-cached requests are skipped).

A capture row with thousands of DMV rows but `0 parsed` almost
certainly hit a sqlglot bug — please open an issue with a redacted
sample command.

### Cache management

The cache lives under `output/.cache/`. To force a fresh start
(e.g. after a pool rename or major schema overhaul), delete the
matching JSON file — the next run rebuilds from scratch. The 30-day
TTL is hard-coded; over time only the most recent 30 days of
parseable requests contribute to the ranking.

## Related

- [06. Recommendations](06-recommendations.md) — see what each gap means for migration
- [07. Runbook](07-runbook.md) — sequenced remediation
- [13. Troubleshooting](13-troubleshooting.md) — VIEW DEFINITION grant
