# Synapse Spark consumption and Fabric steady-state estimates

## What the estimate means

USMA estimates application resource-time from Synapse Livy history. It does not
read a Synapse billing meter or measure consumption on a Fabric capacity.

For a constant application shape:

```text
estimated vCore-hours = (driver cores + executor cores * executor count)
                       * applicable runtime in hours
estimated Fabric CU-hours = estimated vCore-hours * 0.5
average daily CU-hours = total estimated Fabric CU-hours / observation days
steady-state CU = average daily CU-hours / 24
```

Microsoft documents **two Spark vCores per Fabric CU**. Using source Synapse
resource-time assumes the migrated workload needs comparable resource-time on
Fabric. Confirm this assumption with a representative Fabric workload.

Do not change the 0.5 conversion factor to compensate for an incorrect executor
count or runtime.

## Pool definitions, applications and duplicate observations

A Synapse Spark pool is a configuration. Several independent Spark instances can
run under one pool, and multiple jobs can share an instance. Overlapping time
intervals or matching notebook names therefore do not prove duplicated compute.

USMA removes repeated observations of the same identified application before
calculating consumption. Where application identity is unavailable, Livy endpoint
identity must remain part of the record identity: session ID 1 and batch ID 1
are not automatically the same application. Independent applications remain
additive.

Deduplicating Livy records is not the same as reconstructing shared physical
cluster billing. Do not add a whole-cluster usage total to application usage
representing the same resources. Livy request shapes alone cannot establish
physical-instance sharing or historical allocation changes.

## Requested capacity is not measured allocation

Synapse can reserve executors for future scale-out without using or billing all
of them. Dynamic allocation changes the number of executors during execution.
A single request or application shape cannot capture that timeline.

Where allocation history is unavailable, USMA reports a **fixed-shape estimate**
and exposes its limitations. For example, an 8-core driver and ten 8-core
executors over an hour imply 88 vCore-hours. If only three executors were
allocated throughout that hour, application allocation would instead be
32 vCore-hours. The discrepancy must be resolved using allocation evidence,
not an arbitrary discount.

Likewise, scheduler submission-to-end duration can include queueing before
compute exists. Lifecycle timestamps must be interpreted according to their
meaning; entry into monitoring is not an execution end timestamp. Available
execution lifecycle evidence narrows the estimate, while fallback timing must
remain labeled.

Synapse VM lifecycle billing and Fabric active-session billing are different.
Fabric excludes cluster acquisition and Spark-context initialization. Do not
assume all idle time is free: active sessions can continue to accrue consumption.
Neither task CPU utilization nor reserved maximum cores are substitutes for
allocated resource-time.

## Observation windows and daily averages

New Spark analysis uses complete UTC days and records the observation start
and exclusive end. Resource-time is clipped to that interval and apportioned
across UTC day boundaries. A run starting before the window can still contribute
inside it. Fixed-shape estimates are apportioned uniformly; this does not imply
that their actual allocation was constant.

Use one common window across all pool and trigger groups, preferring seven days
when available. Never add overlapping seven-, fourteen-, twenty-eight- and
ninety-day totals together. Observed zero-usage days belong in the denominator.
Missing records or unknown consumption do not prove zero usage.

The Spark contribution to the capacity recommendation is the daily-average
steady-state value. Headroom is applied once after components are combined.

For **16 CU-hours over seven days**, the baseline is:

- 2.285714 CU-hours/day.
- 0.095238 steady-state CU before headroom.

It is not 16 / 24 = 0.666667 CU: that is the corresponding single-day demand.
Background smoothing over 24 hours does not turn the busiest day into the
observation-period average.

Peak-day usage is a separate diagnostic. Compute it by summing dated usage
across groups before selecting the largest day. A maximum of group peaks misses
concurrent consumption; a sum of peaks on different dates overstates a combined
day. A calendar-day peak is not a rolling-24-hour peak.

Steady state alone is not a concurrency guarantee. Validate burst demand,
executor limits, queueing and the selected Fabric SKU separately.

## Provenance, coverage and old artifacts

The Spark payload reports accounting basis, observation dates, collection
completeness, accounting warnings and known/unknown consumption counts. Reports
must show these qualifiers alongside the estimate.

Historical artifacts remain readable. Their CU-hour totals can be divided by
their declared observation days without inventing a peak-day uplift. However,
those totals do not gain corrected runtime, deduplication or allocation evidence
merely by being loaded into a newer UI. Re-run collection for the new accounting
metadata and corrected normalization.

If groups have no common window, USMA cannot calculate a combined average
without mixing denominators. For new accounting payloads, collection must be
confirmed complete and daily averages must be available. If collection is
incomplete, coverage is unknown or consumption is unknown,
USMA shows collected totals but withholds the Spark steady-state contribution.
A combined recommendation based on other engines explicitly warns that it
excludes Spark; it is not a size for the complete workload.

The estate overview does not add pipeline consumption again when the combined
capacity projection already contains a pipeline contribution. Legacy DW-only
projections retain their separate pipeline fallback.

These changes concern Synapse Spark. Existing dedicated SQL, pipeline peak and
serverless sizing heuristics are not converted into daily-average models by
this change.

## Customer reconciliation

Before accepting a production recommendation:

1. Check repeated application observations, including endpoint identity and
   valid application attempts.
2. Compare the recorded executor shape against actual executor/instance history.
3. Compare scheduler submission with resource acquisition and active-session
   timestamps.
4. Reconcile source resource-time against available instance/billing evidence.
5. Run a representative workload on Fabric and compare consumption and
   concurrency with the estimate.

The application cannot infer unavailable historical allocation or prove the
cause of a specific customer's discrepancy without that evidence.

## Microsoft references

Consulted 2026-09-28:

- [Synapse Spark core concepts](https://learn.microsoft.com/azure/synapse-analytics/spark/apache-spark-concepts):
  pool definitions, shared jobs, independent instances and VM lifecycle billing.
- [Executor reservation and dynamic allocation](https://learn.microsoft.com/azure/synapse-analytics/spark/reservation-of-executors-in-dynamic-allocation):
  reserved cores are not billed as used cores.
- [Synapse Spark autoscaling](https://learn.microsoft.com/azure/synapse-analytics/spark/apache-spark-autoscale):
  changing node/executor allocations.
- [Synapse Spark session REST API](https://learn.microsoft.com/rest/api/synapse/data-plane/spark-session/get-spark-sessions?view=rest-synapse-data-plane-2020-12-01):
  request and lifecycle fields; `appInfo` is not a documented allocation timeline.
- [Fabric Spark billing](https://learn.microsoft.com/fabric/data-engineering/billing-capacity-management-for-spark):
  CU/vCore relationship and active-session billing. Autoscale Billing is a
  separate serverless billing mode; this capacity estimate assumes Spark uses
  the Fabric capacity rather than that separate meter.
- [Fabric capacity optimization](https://learn.microsoft.com/fabric/enterprise/optimize-capacity#fabric-data-engineering-and-fabric-data-science):
  Spark background smoothing and concurrency considerations.
- [Synapse application monitoring](https://learn.microsoft.com/azure/synapse-analytics/monitoring/apache-spark-applications):
  Spark UI, history server and logs for reconciliation.
