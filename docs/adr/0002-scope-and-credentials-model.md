# 0002 — Scope + credentials model

**Status:** Accepted (2026-05-19) — `short_scope_id` is a readable fs-safe slug, NOT a hash
**Date:** 2026-05-19
**Decision IDs (feasibility study §9):** D1, D3, D8

## Context

ADR-0001 introduces the source plugin architecture. We still need to
decide:

1. Whether one run can target multiple sources (multi-scope), or one
   source per run with cross-scope reporting only at the estate level.
2. How credentials are managed when sources need different secrets
   (Databricks PAT, SAP RFC password, ...).
3. How scopes are named on disk (artifact files, manifest records).

## Decision

### Multi-scope per run (D1: multi-scope)

A `RunRequest` carries `scopes: list[SourceDescriptor]`. The run planner
iterates the cartesian product `scopes × modules`, filtered by
`ModuleSpec.supports`. A run with a single scope and a run with N scopes
go through the same code path; the single-scope case remains the default
in the UI for Phase 1.

### Shared credentials with per-scope overrides (D3)

`Credentials` carries the base AAD ClientSecretCredential fields
(`tenant_id`, `client_id`, `client_secret`) plus an `extras: dict[str, str]`
for source-specific secrets (Databricks PAT, SAP RFC password). The
service principal is shared across sources by default; per-scope
overrides live alongside the scope in config.

### Artifact naming (D8)

Each artifact file written to a run directory follows the convention:

```
<source_type>__<short_scope_id>__<module>.json
```

- `source_type` — the string value of `SourceType` (e.g. `synapse_workspace`, `adf`).
- `short_scope_id` — produced by `SourceDescriptor.short_id()`. Lowercase,
  filesystem-safe, derived from the canonical id (typically last segment).
- `module` — the `ModuleSpec.name`.

The legacy single-scope layout (`pipelines.json`, `security.json`, ...)
is preserved on read for Phase-1 compatibility — see ADR-0003.

## Consequences

**Positive**
- Estate-wide analysis runs share a single manifest, single Diff, single
  PDF — no cross-tool reconciliation.
- Per-scope secrets stay scoped: a Databricks PAT doesn't leak into the
  Synapse code path.

**Negative**
- Configuration file format and the FastAPI `/api/config` payload both
  need to grow to a `scopes: [...]` list. Legacy single-workspace `.env`
  files are auto-upgraded into a single-scope list on read.

**Neutral**
- Artifact file names change for new (multi-scope) runs. Legacy file
  names continue to work via the manifest-v1 read path (ADR-0003).

## Alternatives considered

- **One run per scope, with an estate-aggregation tool on top.** Rejected
  because it complicates Diff, requires a separate aggregation step, and
  produces N manifests where one would do.
- **Per-source full credential objects (no shared default).** Rejected as
  noisy for the common case (single SP, multiple sources visible to it).
- **Long, fully-qualified artifact names** (e.g. full ARM resource id).
  Rejected because the names become unwieldy and reveal subscription ids
  in path strings.
