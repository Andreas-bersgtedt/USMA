# 16. Estate overview

> **Control plane only.** The estate overview is the new default landing
> page when the SPA is served by `sma serve --with-api`. It rolls up
> *every run on disk* — across every workspace, subscription and
> tenant — into a single estate-wide picture, so you can answer
> "where do we stand across all of Synapse?" without clicking into
> individual runs.

In static / deliverable mode the SPA only ever sees one workspace's
JSON, so this page is hidden — Dashboard remains the landing page
there.

## Purpose

- One screen that summarises the **whole Synapse estate** that this
  analyzer install has scanned.
- Multi-tenant aware: workspaces are grouped by **tenant ·
  subscription** so it reads correctly when you point the analyzer at
  several tenants.
- Both **actual spend** (Synapse, from the Cost module) and
  **projected Fabric spend / capacity** are shown side-by-side and
  clearly labelled — the page never silently mixes the two
  dimensions.

## How to open

Click **Overview** in the top nav (control-plane mode only). The brand
link in the top-left also drops here. Dashboard moved to the
**Dashboard** nav entry and is reachable at `/dashboard`.

## What you see

### Header strip — estate totals

| Card | Source | Notes |
| --- | --- | --- |
| Workspaces | unique `(tenant, subscription, RG, workspace)` keys | a fresh scan with the same identity counts as the same workspace |
| Runs | every run on disk | includes failed and cancelled |
| Tenants / Subscriptions | unique values across workspaces | useful for multi-tenant estates |
| Ready / w-effort / blocked | latest successful run per workspace | matches the readiness bucket on the Dashboard |
| Open blockers | sum of latest-run blockers | sparkline shows trend per workspace |
| Avg T-SQL compatibility | mean across workspaces | each workspace contributes equally |
| Total projected Fabric CU | sum of recommended SKU CU | from `fabric_mapping.capacity_projection` |
| Actual monthly spend (Synapse) | sum across `cost.monthly_totals` | uses the latest month per workspace |
| Projected monthly spend (Fabric) | sum of `fabric_comparison.fabric_estimated_monthly_cost` | what Fabric is *modelled* to cost |

### Estate readiness over time

A line chart of average readiness per calendar day across all
workspaces. Shows up only once at least two distinct days have at
least one successful run.

### Workspaces table

Grouped by **Tenant · Subscription**. Each row is one workspace and
shows that workspace's *most recent successful run* (we ignore the
last failure so a transient outage doesn't wipe the headline numbers,
while still counting it under "Runs").

Notable columns:

- **Score / Bucket** — readiness score and bucket from
  `fabric_mapping.readiness`.
- **Blk / Warn** — blocker / warning counts from the latest
  recommendations.
- **T-SQL %** — `readiness.tsql_compatibility_pct` clamped to 0–100.
- **Actual $/mo (Synapse)** — latest month from the cost
  window. May be partial if the latest month is month-to-date.
- **Projected $/mo (Fabric)** — modelled Fabric capacity cost from
  `cost.fabric_comparison`. Always currency-formatted in the same
  unit as the actual cost so the two numbers are comparable.
- **Fabric SKU / Proj. CU** — the recommended Fabric SKU and total
  capacity units.
- **Trend** — sparkline of readiness scores across the last
  **50 runs** of this workspace, oldest → newest. A slight upward
  trend renders green, otherwise amber.
- **Open latest** — selects the latest run id and jumps to the
  Dashboard so you can drill in.

### Top blockers across the estate

Deduplicates `(area, title)` pairs across every workspace's latest
run. Sorted by **how many workspaces are affected**, then by
occurrence count. Useful for finding cross-cutting platform work
(e.g. "MERGE rewrite hits 7 of 12 workspaces — fix once, ship
everywhere").

## Refreshing

The endpoint is cached based on each `run.json`'s mtime, so the page
updates automatically as runs finish. To force a re-aggregation hit
**Refresh** in your browser; the API also accepts `?refresh=1`.

## Exporting

**Export CSV** in the workspaces toolbar downloads
`/api/estate/export.csv` — one row per workspace, identity columns
plus all the headline metrics. Use this for status reports outside
the SPA.

## API

| Endpoint | Returns |
| --- | --- |
| `GET /api/estate` | full `EstateReport` (totals + workspaces + top blockers). `?refresh=1` invalidates the cache. |
| `GET /api/estate/workspaces/{key}` | a single workspace by composite key (`tenant\|subscription\|rg\|workspace`). |
| `GET /api/estate/export.csv` | flat CSV export of all workspaces. |

## Limits and caveats

- **Identity is best-effort.** Older runs created before the
  workspace identity fields were added on `run.json` fall back to
  `fabric_mapping.workspace_name`; missing tenant / subscription /
  RG show up as `_` in the composite key.
- **Last failure does not poison the headline numbers.** The
  workspace's most recent *successful* run is what populates Score,
  Blockers, Cost, etc. If no run ever succeeded the latest run
  (whatever its status) is shown so the row isn't empty.
- **Currency is per-workspace.** The estate totals add costs as if
  every workspace shares the same currency code — typically true,
  but if you have mixed currencies, sanity-check the totals.
- **History cap.** Each workspace shows at most 50 runs in its
  sparkline. Override with `SMA_ESTATE_MAX_HISTORY`.
