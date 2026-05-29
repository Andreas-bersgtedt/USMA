# Source plugin layer

> Phase-1 — provider implemented for `synapse_workspace`. ADF, Databricks,
> and SAP BW remain stubs until their respective phases.

The `sources/` package
([`src/usma/sources/`](../../src/usma/sources/))
defines the plugin interface every source must implement and a global
`SOURCE_REGISTRY` mapping each `SourceType` to its provider.

See:

- [`../adr/0001-multi-source-architecture.md`](../adr/0001-multi-source-architecture.md) — the architecture decision.
- [`../../src/usma/sources/README.md`](../../src/usma/sources/README.md) — contributor checklist.
- [`../user-guide/99-adding-a-source.md`](../user-guide/99-adding-a-source.md) — end-to-end "add a source" tutorial (Phase 1).

## Provider contract

```python
class SourceProvider(Protocol):
    type: SourceType
    display_name: str
    required_env: tuple[str, ...]

    def discover(self, creds: Credentials,
                 subscription_id: str | None = None) -> list[SourceDescriptor]: ...
    def validate(self, descriptor: SourceDescriptor,
                 creds: Credentials) -> list[ConfigCheck]: ...
    def make_clients(self, descriptor: SourceDescriptor,
                     creds: Credentials) -> Any: ...
```

## Lifecycle

1. **Import time** — each `sources/<name>/__init__.py` calls
   `register_provider(MyProvider())`. No I/O.
2. **Configuration page (frontend)** — user picks a source type; the SPA
   calls `apiDiscoverByType(source_type)` which dispatches to
   `provider.discover()`.
3. **Validate** — when the user saves a scope, the API calls
   `provider.validate()`, returning a list of `ConfigCheck`.
4. **Run time** — for each `(scope, module)` pair the planner emits, the
   module asks the registry for `provider.make_clients(scope, creds)`
   and uses that bundle to collect.

## Implementation status

| Source | Provider | Status |
|---|---|---|
| Synapse workspace | [`sources/synapse_workspace/provider.py`](../../src/usma/sources/synapse_workspace/provider.py) | **Implemented (Phase 1)** — `discover`, `validate`, `make_clients` |
| ADF | [`sources/adf/provider.py`](../../src/usma/sources/adf/provider.py) | Stub (impl in Phase 2) |
| Databricks | [`sources/databricks/provider.py`](../../src/usma/sources/databricks/provider.py) | Stub (impl in Phase 4) |
| SAP BW | [`sources/sap_bw/provider.py`](../../src/usma/sources/sap_bw/provider.py) | Stub (impl in Phase 5) |

## Synapse workspace provider — client bundle

`SynapseWorkspaceProvider.make_clients` returns a frozen `SynapseClientBundle`:

```python
@dataclass(frozen=True)
class SynapseClientBundle:
    credential: ClientSecretCredential
    subscription_id: str
    resource_group: str
    workspace_name: str
    location: str | None = None
```

Modules construct their own ARM / artifacts / Spark / Monitor SDK
clients on demand from this bundle. `make_clients` itself does no I/O,
so it's safe to call once per scope at the start of a run.

## Single-scope shim

[`web/config_io.discover_workspaces`](../../src/usma/web/config_io.py)
is now a thin shim that delegates to
`SOURCE_REGISTRY[SourceType.SYNAPSE_WORKSPACE].discover(...)` and adapts
the returned `SourceDescriptor` list back into the legacy
`WorkspaceSummary` shape the SPA's Configuration page consumes. The
shim is the temporary glue that keeps the UX unchanged in Phase 1; the
SPA itself will migrate to consuming `SourceDescriptor` directly in
Phase 1.5 (frontend SourcePicker wiring).
