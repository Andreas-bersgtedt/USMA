# Adding a new source

> Phase-0 stub. Filled in during Phase 1 once the abstractions are wired
> through end-to-end.

This guide walks contributors through adding a new source type (e.g.
Snowflake, Teradata) to the Unified Solution Migration Analyzer.

## Prerequisites

- Read [`feasibility_study.md`](../../feasibility_study.md) §4
  (target architecture).
- Skim [`docs/adr/0001-multi-source-architecture.md`](../adr/0001-multi-source-architecture.md)
  and [`docs/adr/0002-scope-and-credentials-model.md`](../adr/0002-scope-and-credentials-model.md).
- Pick a stable string identifier for the new source (used in
  `SourceType`, manifest files, artifact names, REST payloads).

## High-level steps

1. **Declare the `SourceType`** in
   [`src/usma/sources/__init__.py`](../../src/usma/sources/__init__.py).
2. **Create the provider package** under
   `src/usma/sources/<name>/`. Copy the
   `sap_bw/` or `databricks/` stub as a template.
3. **Implement `discover`, `validate`, `make_clients`** on your
   `BaseSourceProvider` subclass.
4. **Register at import time** in the package `__init__.py`:

   ```python
   from .. import register_provider
   from .provider import MyProvider
   register_provider(MyProvider())
   ```

5. **Update module `supports`** in
   [`src/usma/modules/spec.py`](../../src/usma/modules/spec.py)
   for every module that can analyze this source. For modules with
   source-specific collectors, drop a new collector under e.g.
   `modules/pipelines/sources/<name>_collector.py`.
6. **Add tests + fixtures** under `tests/sources/<name>/` and
   `tests/fixtures/sources/<name>/`.
7. **Add a user-guide page** at `docs/user-guide/<NN>-<name>.md`.
8. **Update [`USMA_planning_Manifest.md`](../../USMA_planning_Manifest.md)**
   with the source rollout phase.

## Worked example

Will be added during Phase 2 once ADF is implemented end-to-end and
serves as a reference.
