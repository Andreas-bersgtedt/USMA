"""Google BigQuery workloads (datasets / tables / routines / jobs) analysis module.

Phase 5 Slice 5-B. Sibling of :mod:`~..databricks_workflows` (per ADR D5).
The Synapse ``pipelines`` and Azure Data Factory analyzers remain
separate so each platform's migration story can evolve independently.

This package contains:

* :mod:`.models` — Pydantic shapes for ``bigquery_workloads.json``.
* :mod:`.fabric_compat` — coarse table/routine/job-type → Fabric support.
* :mod:`.collector` — duck-typed wrapper around ``bigquery.Client`` +
  ``logging_v2.Client`` + (optional) ``bigquery_datatransfer.Client``.
* :mod:`.analyzer` — orchestrator with ``BigQueryWorkloadsAnalyzer.for_descriptor()``.
* :mod:`.reporting` — JSON / CSV / Markdown writers.

Slot-hour → CU-hour math, ``MODULE_SPECS`` widening for ``cost`` +
``fabric_mapping``, and the ``rules_for_bigquery_workloads`` factory
land in Slice 5-C.
"""
