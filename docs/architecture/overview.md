# Architecture overview

> This document is a Phase-0 stub. It will be filled in during Phase 1
> as the source-aware abstractions wire into the existing code paths.
> See [`../../feasibility_study.md`](../../feasibility_study.md) for the
> full design rationale and [`../adr/`](../adr/) for the locked decisions.

## Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│                            Frontend (React)                          │
│   Configuration · Run · Dashboard · Recommendations · Runs · Diff   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST + SSE
┌──────────────────────────────▼──────────────────────────────────────┐
│                         FastAPI control plane                        │
│         /api/config · /api/runs · /api/runs/{id}/modules/{m}        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                            Run planner                               │
│        scopes × modules → list[(SourceDescriptor, ModuleSpec)]      │
└──────────┬───────────────────────────────────────────┬──────────────┘
           │                                           │
┌──────────▼─────────────┐                ┌────────────▼─────────────┐
│       sources/         │                │         modules/         │
│  SOURCE_REGISTRY:      │ ───clients───► │  MODULE_SPECS:           │
│   synapse_workspace    │                │   pipelines              │
│   adf                  │                │   cost                   │
│   databricks           │                │   security               │
│   sap_bw               │                │   fabric_mapping  ...    │
└────────────────────────┘                └────────────┬─────────────┘
                                                       │
                                          ┌────────────▼─────────────┐
                                          │       reporting/         │
                                          │  run_manifest (v2)       │
                                          │  json / csv / md / html  │
                                          └──────────────────────────┘
```

## See also

- [`sources.md`](sources.md) — source plugin layer detail.
- [`modules.md`](modules.md) — module + ModuleSpec detail.
- [`../adr/0001-multi-source-architecture.md`](../adr/0001-multi-source-architecture.md)
- [`../adr/0002-scope-and-credentials-model.md`](../adr/0002-scope-and-credentials-model.md)
- [`../adr/0003-manifest-v2-schema.md`](../adr/0003-manifest-v2-schema.md)
