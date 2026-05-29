"""Snowflake workloads (databases / schemas / tables / views / routines / tasks / jobs) analysis module.

Phase 7 Slice 7-C. Sibling of :mod:`~..databricks_workflows` and
:mod:`~..bigquery_workloads` (per ADR D5). Snowflake's tasks, pipes,
streams, and dynamic tables each get their own first-class shape on
``SnowflakeWorkloadsAnalysis`` so the SPA Dashboard + Fabric mapping
pipeline can fan in without bespoke widgets per object kind.

This package contains:

* :mod:`.models` — Pydantic shapes for ``snowflake_workloads.json``.
* :mod:`.fabric_compat` — coarse object-kind → Fabric support labels.
* :mod:`.collector` — duck-typed wrapper around a ``snowflake.connector``
  connection (the same bundle produced by ``sources/snowflake/provider.py``).
* :mod:`.analyzer` — orchestrator with ``SnowflakeWorkloadsAnalyzer.for_descriptor()``.
* :mod:`.reporting` — JSON / CSV / Markdown writers.

Credit → vCore-hour → CU rollup (warehouse-size → vCPU proxy), 7/14/28/90-day
window stats, ``MODULE_SPECS`` widening for ``cost`` + ``fabric_mapping``,
and the ``rules_for_snowflake_workloads`` factory land in Slice 7-D.
"""
