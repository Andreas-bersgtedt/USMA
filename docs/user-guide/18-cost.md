# 18. Cost of running the analyzer

This chapter answers two questions reviewers and FinOps owners typically
ask before approving SMA against a production workspace:

1. **What does running the analyzer itself cost?** (Azure spend the
   *act of running* SMA adds to your bill.)
2. **How does SMA project Fabric cost?** (The methodology behind the
   "Recommended SKU" and "Estimated Fabric monthly cost" numbers on
   the Dashboard.)

These are two different things. The first is essentially free. The
second is a model — useful for sizing, not a guaranteed quote.

## Part A — Azure cost incurred by running SMA

### TL;DR

For a typical workspace (5 dedicated pools, 100 pipelines, 3 Spark
pools, 10 linked storage accounts) a full `sma analyze-all` run costs
**well under USD 1** in Azure consumption — usually rounding to
**$0.00** because every API SMA calls is in a free tier and the DMV
scans run on capacity you are already paying for.

### Per-surface cost

| Surface | Per-call cost | What SMA does | Total per run |
|---|---|---|---|
| Azure Resource Manager (ARM) `list` / `get` | Free (Resource Provider API; no per-call charge) | ~50–200 calls (workspaces, pools, linked services, firewall, RBAC, MPE, keys, storage accounts) | $0 |
| Azure Monitor `metrics.list` | Free for the first 1M metric queries / month per subscription (then $0.10 per million) | ~10–50 metric queries per workspace per run (DWU peak, Spark vCore-hours, storage UsedCapacity) | ≈ $0 |
| Cost Management `query.usage` | Free for read access at the subscription / RG scope | 1 query per `--months` window | $0 |
| Synapse Artifacts (Pipelines / Notebooks / SJDs / LinkedServices / Triggers) | Free Synapse data-plane API | ~20–200 paginated list calls + per-item gets | $0 |
| Synapse Livy job-history list | Free Synapse data-plane API | ~1–10 paginated calls per Spark pool (window-limited) | $0 |
| Dedicated SQL pool DMV scans | DWU-hours you are **already paying for** while the pool is online (the pool must be ACTIVE for SMA to read DMVs at all) | ~14 queries × seconds each per pool; tagged `OPTION (LABEL = 'sma:<query>')` for auditability | Indirect: a few CPU-seconds of the pool's DWU |
| Serverless SQL pool scans | Serverless is billed per TB processed; SMA's DMV / metadata scans process **<1 MB per query** | ~5 metadata queries | ≈ $0 (sub-cent) |
| Storage Account `list-properties` + `UsedCapacity` metric | Free | 1 call + 1 metric per account | $0 |
| ADLS Gen2 list-files | **Not used.** SMA never enumerates blob contents. | 0 | $0 |

### What SMA does NOT do (and therefore does NOT cost you)

- It never starts a Spark session. The `spark_pools` module *lists*
  prior Livy job history; it does not submit work.
- It never executes a pipeline. The `pipelines` module reads pipeline
  *definitions* and *prior run history*; it does not trigger runs.
- It never enumerates ADLS file contents. Storage analysis is
  capacity-only via Azure Monitor's `UsedCapacity` metric.
- It never restores or scales a dedicated pool. Pools that are
  PAUSED are skipped with a one-line warning.
- It never persists query *results* from user tables. The only SQL
  text SMA captures is the top-query previews (~4 KB each), and only
  the query text itself, not the result set.

### Auditing SMA's footprint inside the pool

Every heavy DMV scan that SMA runs against a dedicated pool carries
an `OPTION (LABEL = 'sma:<query-name>')` clause. To see exactly what
SMA did in the last day:

```sql
SELECT label, COUNT(*) AS executions, SUM(total_elapsed_time) AS total_ms
FROM sys.dm_pdw_exec_requests
WHERE submit_time >= DATEADD(DAY, -1, SYSUTCDATETIME())
  AND label LIKE 'sma:%'
GROUP BY label
ORDER BY total_ms DESC;
```

Labels currently emitted: `sma:tables`, `sma:column_stats`,
`sma:top_queries`, `sma:workload_commands`, `sma:workload_catalog`,
`sma:usage`.

### Worked example: 5-pool workspace, weekly run

- 5 dedicated pools × ~14 DMV queries × ~2 s each = ~140 pool-seconds
  per run. On a DW100c (cheapest unit, ~$1.20/h) that's roughly
  $0.05 of *implicit* DWU consumption per run, **only counted if the
  pool was idle and SMA caused it to stay warm a bit longer** (in
  practice the pool is already online for production workloads).
- Serverless metadata scans: <1 MB processed per run. At $5 / TB that
  rounds to $0.000005.
- ARM + Monitor + Cost Management + Artifacts API calls: $0.
- Storage list-properties + UsedCapacity metric: $0.
- **Total per run:** rounds to $0.05 worst-case, $0 in steady state.
- Weekly cadence: <$3 / year.

### Knobs that move the needle (if you care)

These environment variables cap how much DMV history SMA pulls. Lower
them if you want to bound the analyzer's own footprint on the pool;
raise them if you want longer history at marginally higher cost.

| Env var | Default | Effect |
|---|---|---|
| `SMA_PIPELINES_RUN_DAYS` | 90 | Window for pipeline run-history pull. |
| `SMA_PIPELINES_RUN_LIMIT` | 5000 | Hard cap on rows. |
| `SMA_PIPELINES_RUN_HISTORY` | 1 | Set to `0` to skip run-history entirely. |
| `SMA_SPARK_RUN_DAYS` | 90 | Window for Livy job-history pull. |
| `SMA_SPARK_RUN_LIMIT` | 5000 | Hard cap on Livy rows. |
| `SMA_SPARK_RUN_HISTORY` | 1 | Set to `0` to skip Livy history. |
| `SMA_COST_MONTHS` | 6 | Months of Cost Management data to pull. |
| `SMA_COST_DISABLE_LIVE` | 0 | Set to `1` to skip Cost Management entirely. |

## Part B — How SMA projects Fabric cost

The Dashboard's *Recommended SKU* card and the per-workspace
*Estimated Fabric monthly cost* are produced by `fabric_mapping` from
the other modules' JSON outputs. This section documents the model so
its outputs can be reproduced and audited.

### Inputs

- **Dedicated SQL pool DWU** — per-timestamp peak from Azure Monitor
  (`DWUUsedPercent` × `DWULimit`). Already a peak signal.
- **Spark vCore-hours** — derived per Livy run from
  `app_info.driver_vcores + executor_vcores × num_executors`
  multiplied by wall-clock seconds, summed per UTC submission day.
- **Pipelines vCore-hours** — sum of:
  - Data Integration Units (DIU) × seconds for Copy / Lookup activities,
  - mapping-dataflow `compute.coreCount` × `output.executionDuration`,
  - a small constant for orchestration time.

### Conversion factors (documented, hard-coded)

| From | To | Ratio | Source |
|---|---|---|---|
| 1 Spark vCore-hour | Fabric CU-hour | × 0.5 | Microsoft published rate: 1 Fabric CU = 2 Spark vCores. |
| 1 ADF vCore-hour (DIU or MDF) | Fabric CU-hour | × 0.5 | Same conversion (data-integration vCores). |
| 1 DWU100c-hour | Fabric CU-hour | mapped via the SKU lookup table in `fabric_mapping/capacity_projection.py` | Microsoft DWU→CU mapping for the dedicated-pool → Fabric Warehouse migration path. |

### Peak-day rule (since 2.6.3)

Fabric capacity smooths CU-second consumption over a rolling **24-hour
burndown window**, so a one-day spike that exceeds `F-SKU × 24
CU-hours` will throttle even when the weekly average is comfortable.
SMA's Spark + Pipelines contribution to the SKU recommendation is
therefore derived from the **worst single UTC day** inside the
observation window, divided by 24h — not the window total divided by
`window_days × 24`. DWU contribution is unchanged because Monitor
metrics already give per-timestamp peaks.

This is the difference between "the workspace averages 10 CU" and
"the workspace's busiest day was 70 CU" — SMA recommends for the 70
CU peak so you don't get throttled on Tuesdays.

### SKU sizing

After summing the three contributions (DWU, Spark, Pipelines), SMA
applies a configurable **headroom multiplier** (default 1.25× — set
via `SMA_FABRIC_HEADROOM`) and picks the smallest F-SKU that covers
the total:

`F2, F4, F8, F16, F32, F64, F128, F256, F512, F1024, F2048`

### Pricing caveat (READ THIS)

The dollar amounts shown on the Dashboard and in the per-workspace
report use a **hard-coded price list** that ships with the analyzer.
This list is updated when prices change in Microsoft's official
pricing pages, but **always verify on the Microsoft pricing page**
before quoting numbers to procurement / FinOps:

> https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/

Regional variation, reserved-instance discounts, EA / CSP rebates,
and currency are **not** modeled. The number SMA prints is the
**list-price USD pay-as-you-go** rate for the recommended F-SKU,
in the smallest-F-that-covers-peak sense.

## Related

- [04. Dashboard](04-dashboard.md) — where the *Recommended SKU* and
  Fabric-cost numbers are rendered.
- [17. Tool access & security implications](17-access-and-security.md)
  — for what SMA reads (the inputs to the cost model).
- [CHANGELOG.md](../../CHANGELOG.md) — entries 2.6.2 and 2.6.3 record
  the changes to the SKU sizing rules.
