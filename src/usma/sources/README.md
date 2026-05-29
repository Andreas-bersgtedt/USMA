# `sources/` — how to add a new source

This package implements the **source plugin** layer described in
[`feasibility_study.md`](../../../feasibility_study.md) and
[`docs/architecture/sources.md`](../../../docs/architecture/sources.md).

A "source" is a kind of data-estate system the analyzer can collect from
(Synapse workspace, Azure Data Factory, Databricks, SAP BW, …). Each
source is a self-contained Python sub-package under `sources/<name>/`
that registers a `SourceProvider` with the global `SOURCE_REGISTRY`.

## Adding a new source — checklist

1. **Pick a `SourceType` value** — add a new member to `SourceType` in
   [`__init__.py`](__init__.py). The string value is permanent (it ends
   up in manifest files and artifact names).

2. **Create the sub-package** under `sources/<name>/`:
   ```
   sources/<name>/
   ├── __init__.py         # registers the provider on import
   └── provider.py         # implements SourceProvider
   ```

3. **Implement `SourceProvider`** — extend `BaseSourceProvider` from
   [`base.py`](base.py) and override:
   - `discover(creds, subscription_id) -> list[SourceDescriptor]`
   - `validate(descriptor, creds) -> list[ConfigCheck]`
   - `make_clients(descriptor, creds) -> Any` — returns whatever SDK
     bundle the modules will need.

4. **Register at import time** — in `sources/<name>/__init__.py`:
   ```python
   from ..base import BaseSourceProvider
   from .. import register_provider, SourceType
   from .provider import MySourceProvider

   register_provider(MySourceProvider())
   ```

5. **Mark modules `supports`** — open
   [`modules/spec.py`](../modules/spec.py) and add your `SourceType` to
   the `supports` frozenset of every `ModuleSpec` that can analyze this
   source.

6. **Add a collector** under the relevant module(s), e.g.
   `modules/pipelines/sources/<name>_collector.py`. Reuse existing rules
   (`fabric_compat`, `expression_compat`, ...) where the data model is
   compatible.

7. **Tests + fixtures** — drop sample payloads under
   `tests/fixtures/sources/<name>/` and add `tests/sources/<name>/test_provider.py`.

8. **Docs** — add `docs/user-guide/<NN>-<name>.md` and link it from the
   getting-started page.

9. **Front-end** — the `SourcePicker` component picks up new source
   types automatically from `apiListSourceTypes()`; you only need to add
   source-specific config fields if your provider needs more than the
   base `Credentials`.

10. **Update [`USMA_planning_Manifest.md`](../../../USMA_planning_Manifest.md)**
    by checking off the relevant phase items.

## Conventions

- **No I/O at import time.** Providers may be imported in CLI help paths
  or schema generators; defer all Azure / network calls to method bodies.
- **`extras` for source-specific fields.** Don't add new top-level
  fields to `SourceDescriptor` or `Credentials` for one-off needs.
- **`short_id()` must be stable.** It feeds artifact file names; changing
  it for an existing source invalidates run-history comparisons.
- **No mutable global state** beyond `SOURCE_REGISTRY` itself.
