# Fabric CU model — documentation vs. this codebase

A review of every published Microsoft Fabric **Capacity Unit (CU)** consumption
rate that is relevant to a Synapse Analytics workspace, side-by-side with the
constants this analyzer uses today. **Documentation only — no code changes
proposed in this file.**

Date of review: 2026-05-11 (current Fabric `learn.microsoft.com` docs).

---

## 1 · Authoritative CU consumption rates (Fabric docs)

### 1.1 Data Factory pipelines

Source: [Pipelines pricing for Data Factory in Microsoft Fabric](https://learn.microsoft.com/fabric/data-factory/pricing-pipelines)

| Pipelines engine | Charge basis | Fabric CU rate |
|---|---|---|
| **Data movement** (Copy activity) | Copy duration (h) × DIUs used | **1.5 CU-h per DIU-h** |
| **Data orchestration** (every other activity run) | Number of non-copy activity runs | **0.0056 CU-h per activity run** |
| **SSIS** (Invoke SSIS Package activity) | SSIS-IR uptime × vCores | **1.35 CU-h per vCore-h** |

Note: when a pipeline triggers Notebook / Dataflow Gen2 / SJD, that downstream
CU is metered separately under its own engine.

### 1.2 Dataflow Gen2 — relevant when Synapse Mapping Data Flows migrate to Fabric Dataflow Gen2

Source: [Dataflow Gen2 pricing for Data Factory in Microsoft Fabric](https://learn.microsoft.com/fabric/data-factory/pricing-dataflows-gen2)

| Engine | Per-second CU rate |
|---|---|
| **Standard Compute (CI/CD)** | 12 CU/s for first 600 s, **1.5 CU/s** thereafter |
| **Standard Compute (non-CI/CD)** | flat **16 CU/s** |
| **High Scale Compute** (staging on) | **6 CU/s** |
| **Fast Copy** (data movement) | **1.5 CU/s** |
| **VNET Data Gateway uptime** | 4 CU/member-s |

### 1.3 Copy Job (newer, standalone item — not produced by a pipeline)

Source: [Copy Job pricing](https://learn.microsoft.com/fabric/data-factory/pricing-copy-job)

| Pattern | Rate |
|---|---|
| Full copy | 1.5 CU-h |
| Incremental copy | **3 CU-h** |

### 1.4 Apache Spark (Notebooks, Spark Job Definitions, lakehouse jobs)

Sources: [Apache Spark billing](https://learn.microsoft.com/fabric/data-engineering/billing-capacity-management-for-spark) · [Concurrency limits](https://learn.microsoft.com/fabric/data-engineering/spark-job-concurrency-and-queueing) · [Fabric operations](https://learn.microsoft.com/fabric/enterprise/fabric-operations)

- **1 CU = 2 Spark vCores** (so **1 vCore-s = 0.5 CU-s**).
- Spark operations are **background** and are **smoothed over 24 hours**.
- Bursting: up to 3× SKU vCores per capacity (F64 → 384 vCores max).

### 1.5 Fabric Data Warehouse

Source: [Billing and utilization reporting in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/usage-reporting) · [Fabric operations](https://learn.microsoft.com/fabric/enterprise/fabric-operations)

- **1 CU = 0.5 Warehouse vCores** (so **1 Warehouse vCore = 2 CUs**).
- F64 → 32 Warehouse vCores.
- Warehouse operations are **background**, **smoothed over 24 h**.

### 1.6 Fabric SQL Database

- **1 CU = 0.383 SQL Database vCores** (interactive).

### 1.7 Real-Time Intelligence (KQL Database / Eventhouse)

- KQL DB: active-seconds × vCores → CU-seconds; **interactive**, no smoothing.

### 1.8 What Microsoft does **not** publish

- **No linear DWU → CU conversion** is published. The Fabric Updates Blog post
  "[Mapping Azure Synapse dedicated SQL pools to Fabric data warehouse compute](https://blog.fabric.microsoft.com/blog/mapping-azure-synapse-dedicated-sql-pools-to-fabric-data-warehouse-compute/)"
  (Hoang & Schacht, 2024) explicitly says a simple resource mapping is **not**
  accurate, and instead publishes empirical TPC-H *performance-parity* peer
  pairs.
- **No linear vCore → CU mapping for Synapse-managed Spark.** Synapse Spark
  pools bill differently (per-vCore-second on the Synapse meter) and Fabric
  Spark bills on capacity (CU). The mapping `1 vCore = 0.5 CU` *only* applies
  when the workload runs natively in Fabric Spark.
- Microsoft's canonical sizing recommendation for migrating dedicated SQL pools
  is the **Fabric SKU Estimator** "Use migrate to Fabric experience" mode,
  which takes the source DWU SKU as input and returns a target F SKU — not a
  linear formula.

---

## 2 · What this codebase currently models

### 2.1 Pipelines run-stats (`src/usma/modules/pipelines/run_stats.py`)

| Constant | Value | Used for |
|---|---|---|
| `DIU_TO_CU_HOURS` | **1.5** | Copy-activity DIU-h → Fabric CU-h |
| `ORCHESTRATION_CU_HOURS_PER_ACTIVITY` | **0.0056** | Non-copy activity run count |
| `VCORE_HOURS_TO_CU_HOURS` | **0.5** | Mapping-Dataflow Spark vCore-h → Fabric CU-h (assumes the migration target is a **Fabric Spark job**, not a Fabric Dataflow Gen2) |
| `DEFAULT_DATAFLOW_CORES` | **8** | Fallback when `compute.coreCount` is not present on the activity definition (smallest General Purpose cluster) |

### 2.2 Capacity (F-SKU) projection from monitoring (`src/usma/modules/fabric_mapping/cu_projection.py`)

| Constant | Value | Rationale (documented in the module) |
|---|---|---|
| `DWU_TO_CU` | **0.020** (100 DWU ≈ 2.0 CU) | Heuristic; chosen so that 0.020 CU/DWU + 30 % headroom reproduces the Hoang & Schacht TPC-H performance-parity peers (F32 ≈ DW1000, F64 ≈ DW1500–3000, F128 ≈ 10 TB) across F8 → F2048 |
| Headroom | **30 %** | Configurable starting buffer for a POC sizing |
| `_FSKU_TABLE` | F2 → F2048 | Picks the smallest SKU whose CU count covers the estimate |

### 2.3 Estate aggregation (`src/usma/web/estate.py`)

Per-workspace `projected_fabric_cu` is computed as:

```
projected_fabric_cu
  = capacity_projection.estimated_cu          # DWU-derived steady-state CU
  + (Σ pipelines CU-h-per-day)                # integration daily CU/day
```

where `pipelines CU-h-per-day` = `est_cu_hours_from_diu + est_cu_hours_from_vcore + est_cu_hours_from_orchestration` over the 7-day window.

---

## 3 · Alignment, gaps, and inaccuracies

### 3.1 Exact matches with Microsoft docs

| Codebase value | MS doc | Match |
|---|---|---|
| `DIU_TO_CU_HOURS = 1.5` | Pipeline data-movement meter `1.5 CU-h / DIU-h` | ✅ Exact |
| `ORCHESTRATION_CU_HOURS_PER_ACTIVITY = 0.0056` | Pipeline orchestration `0.0056 CU-h per non-copy activity run` | ✅ Exact |
| `VCORE_HOURS_TO_CU_HOURS = 0.5` (Spark vCore-h → CU-h) | `1 CU = 2 Spark vCores` ⇒ `1 Spark vCore-h = 0.5 CU-h` | ✅ Exact, *but only if the migration target is Fabric Spark* — see §3.3 |

### 3.2 Documented heuristics, not formal Microsoft constants

| Codebase value | Status | Action recommendation |
|---|---|---|
| `DWU_TO_CU = 0.020` + 30 % headroom | Heuristic; module docstring is explicit about this | Keep, but make the headroom + per-tier override more discoverable in the HTML / SPA footnote and reference the official **Fabric SKU Estimator** as the authoritative sizing tool. |
| `DEFAULT_DATAFLOW_CORES = 8` | Reasonable: Synapse "General Purpose 8-core" is the smallest cluster; uses Activity-level `compute.coreCount` when present | Keep. Already runtime-derived per-activity since 2.4.2. |

### 3.3 Gaps and mis-attributions

**G1 — Mapping Data Flow migration target is questionable.**
The HTML footnote and run-stats docstring assume migration to a **Fabric Spark
job** at 0.5 CU/vCore-s. In practice, Microsoft positions Mapping Data Flow →
**Fabric Dataflow Gen2** as the like-for-like migration path (low-code,
mashup-engine). For non-trivial transformations Dataflow Gen2 Standard Compute
charges **12–16 CU/s** of query duration — i.e. **24× – 32× higher** than the
current Spark-target estimate per second of compute. Recommendation:
- Surface *both* projections (Spark-target and Dataflow-Gen2-target) in the
  HTML / SPA so the customer can pick the migration path that matches their
  intent. Document the assumption explicitly on the dashboard tooltip.
- Add a new constant pair: `DATAFLOW_GEN2_STD_CU_PER_SEC = 16` (non-CI/CD) and
  the CI/CD tiered profile (12 CU/s ≤ 600 s, 1.5 CU/s after).
- A simple lower-bound: project Mapping Data Flow runtime-seconds × 16 CU/s
  for non-CI/CD or use the tiered formula for CI/CD.

**G2 — SSIS (`ExecuteSSISPackage` / Invoke SSIS Package) is not modeled.**
Microsoft publishes **1.35 CU-h per SSIS-IR vCore-h**. Today `aggregate_runs`
does not extract SSIS vCores or runtime, and the SSIS contribution to
`projected_fabric_cu` is 0. Synapse customers who run SSIS via the Azure-SSIS
IR will under-size when this analyzer is used. Recommendation: extract
SSIS-IR vCore size from the activity definition (or the linked IR) and apply
**1.35 CU-h / vCore-h × wall-clock-runtime**.

**G3 — Copy Job (Fabric-native item) is not modeled.**
Synapse has no Copy Job equivalent, so this is only relevant when the
migration target replaces Copy activities with a Copy Job. Today the analyzer
keeps the Copy-activity rate (1.5 CU-h/DIU-h) for the target projection,
which is correct *if* Copy stays as a pipeline Copy activity in Fabric. If the
migration plan promotes them to **incremental Copy Jobs**, the target rate is
**3 CU-h/DIU-h** — i.e. **2×** the current estimate.

**G4 — Fabric Spark bursting & smoothing not reflected in capacity sizing.**
Spark CU consumption is **smoothed over 24 hours** on Fabric capacity, and
**bursting** allows a single job to consume up to 3× the base SKU vCores
short-term. The capacity-projection module compares **peak active DWU** to a
**flat** SKU CU table. For workloads dominated by Spark/Dataflow rather than
DW reads, this produces an over-sized SKU (the smoothing window means the
*average*, not the *peak*, sets the floor). Recommendation: add an
information-only note in the HTML / SPA explaining that Spark / Dataflow CU
contributes to the **24-h smoothed average**, not the peak, and link to
[Spark capacity SKU limits](https://learn.microsoft.com/fabric/data-engineering/spark-job-concurrency-and-queueing#spark-capacity-sku-limits).

**G5 — DWU → CU is a linear heuristic, not a Microsoft formula.**
Already documented in the module docstring (Hoang & Schacht peer pairs).
Recommendation: make the **HTML report and SPA Stat-Card** explicitly link to
the [Fabric SKU Estimator](https://learn.microsoft.com/fabric/enterprise/fabric-sku-estimator)
and the
[Mapping Azure Synapse dedicated SQL pools to Fabric data warehouse compute](https://blog.fabric.microsoft.com/blog/mapping-azure-synapse-dedicated-sql-pools-to-fabric-data-warehouse-compute/)
blog as the authoritative final sizing input, with the analyzer's number
called out as a **POC starting point**.

**G6 — Serverless SQL pool → Fabric mapping.**
Serverless SQL bills as `$ per TB scanned`. The natural Fabric equivalent is
the **SQL analytics endpoint of a Lakehouse** (Warehouse CU rate) and/or
**Fabric Data Warehouse**. The current `serverless_pools` module surfaces
TB-scanned but does not project a CU equivalent at all (`projected_fabric_cu`
ignores serverless TB). For estates with heavy serverless usage the projected
CU is materially understated.

**G7 — `est_cu_hours_from_orchestration` heuristic.**
Today the analyzer estimates non-copy activity runs as `static non-copy
activity count × pipeline runs`. In practice a `ForEach`/`Until`/`If` activity
expands at runtime: 1 pipeline run with `ForEach` over 100 items emits **100
extra activity-run charges**, not 1. The estimate is therefore a **lower
bound** for any pipeline containing iterative control-flow activities.
Recommendation: use Synapse `query_activity_runs_by_pipeline_run` to count
*actual* terminal activity-run records on the sampled runs and extrapolate,
instead of relying on the static count.

### 3.4 Cross-cutting recommendations

1. **Surface the CU model in the HTML / SPA** — a dedicated "CU model"
   accordion that lists each constant with its Microsoft source URL.
2. **Make the migration-target assumption explicit per activity type**:
   - Copy → Pipeline Copy activity (1.5 CU-h/DIU-h) — default
   - Copy (alternate) → Copy Job incremental (3 CU-h/DIU-h)
   - ExecuteDataFlow → Fabric Dataflow Gen2 non-CI/CD (16 CU/s) — default
   - ExecuteDataFlow (alternate) → Fabric Spark job (0.5 CU per vCore-s)
   - ExecuteSSISPackage → Fabric pipeline SSIS (1.35 CU-h/vCore-h)
   - SynapseNotebook / SparkJobDefinition → Fabric Spark (0.5 CU/vCore-s)
3. **Link the canonical sizing tools** in the report footer: Fabric SKU
   Estimator, Capacity Metrics app, Migration Assistant.
4. **Document the smoothing assumption** in the projected SKU stat-card.

---

## 4 · Authoritative references

| Topic | URL |
|---|---|
| Pipelines pricing (Fabric) | https://learn.microsoft.com/fabric/data-factory/pricing-pipelines |
| Dataflow Gen2 pricing | https://learn.microsoft.com/fabric/data-factory/pricing-dataflows-gen2 |
| Copy Job pricing | https://learn.microsoft.com/fabric/data-factory/pricing-copy-job |
| Fabric Data Warehouse usage reporting | https://learn.microsoft.com/fabric/data-warehouse/usage-reporting |
| Spark billing & utilization | https://learn.microsoft.com/fabric/data-engineering/billing-capacity-management-for-spark |
| Spark concurrency limits / bursting | https://learn.microsoft.com/fabric/data-engineering/spark-job-concurrency-and-queueing |
| Fabric operations (per-experience CU rates) | https://learn.microsoft.com/fabric/enterprise/fabric-operations |
| Optimize / evaluate Fabric capacity | https://learn.microsoft.com/fabric/enterprise/optimize-capacity |
| Synapse DWU → Fabric DW compute mapping (blog) | https://blog.fabric.microsoft.com/blog/mapping-azure-synapse-dedicated-sql-pools-to-fabric-data-warehouse-compute/ |
| Fabric SKU Estimator | https://learn.microsoft.com/fabric/enterprise/fabric-sku-estimator |
| Synapse SQL resource consumption (DWU / cDWU) | https://learn.microsoft.com/azure/synapse-analytics/sql/resource-consumption-models |
| Migration: dedicated SQL pool → Fabric DW | https://learn.microsoft.com/fabric/data-warehouse/migration-synapse-dedicated-sql-pool-warehouse |

---

## 5 · Summary

The three rates the analyzer treats as "Microsoft-published" — **1.5 CU-h per
DIU-h** (Copy), **0.0056 CU-h per activity run** (Orchestration), and
**0.5 CU-h per Spark vCore-h** (Spark target) — are correct and verifiable
against current Fabric documentation.

The main *gaps* in the model are:

1. **Mapping Data Flow** is projected against a Fabric Spark target; the
   like-for-like target (Fabric Dataflow Gen2) is 24–32× more expensive per
   compute-second.
2. **SSIS** (`ExecuteSSISPackage`) is unmodeled (`1.35 CU-h/vCore-h`).
3. **Serverless SQL** (TB scanned) is unmodeled in the projected-CU number.
4. **DWU → CU** is a heuristic, not a Microsoft formula; should link to the
   Fabric SKU Estimator more prominently.
5. **Orchestration activity count** uses a static count; under-counts
   iterative `ForEach` / `Until` expansion.
6. **Smoothing & bursting** for Spark / Dataflow CU is not surfaced in the
   capacity-sizing logic.

All six are documentation / model gaps — none are bugs in the existing
computations.
