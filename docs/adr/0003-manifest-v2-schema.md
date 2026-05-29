# 0003 — Run manifest schema v2

**Status:** Accepted (2026-05-19) — implemented in Phase 1
**Date:** 2026-05-19
**Decision IDs (feasibility study §9):** D7

## Context

The current run manifest (see [`reporting/run_manifest.py`](../../src/synapse_migration_analyzer/reporting/run_manifest.py))
tracks artifacts by file name + SHA + record count. It has no concept of
which source or scope an artifact came from — it implicitly assumes a
single Synapse workspace per run.

For multi-scope runs (ADR-0002) the manifest needs to record, per
artifact, **what source** and **which instance of that source** the data
came from.

## Decision

Bump the manifest schema to **v2** with the following additions:

### Top-level

```json
{
  "schema_version": 2,
  "run_id": "...",
  "started_at": "...",
  "finished_at": "...",
  "sma_version": "...",
  "scopes": [
    {
      "type": "synapse_workspace",
      "id": "/subscriptions/.../workspaces/foo",
      "display_name": "foo",
      "short_id": "foo",
      "subscription_id": "...",
      "resource_group": "...",
      "location": "westeurope",
      "extras": {}
    }
  ],
  "artifacts": [ ... ]
}
```

### Per-artifact record

```json
{
  "name": "synapse_workspace__foo__pipelines.json",
  "module": "pipelines",
  "source_type": "synapse_workspace",
  "scope_id": "foo",
  "sha": "sha256:...",
  "size": 12345,
  "record_count": 42,
  "generated_at": "..."
}
```

### Backwards compatibility

- **Read path**: a manifest with no `schema_version` or `schema_version: 1`
  is automatically upgraded **in memory** on read. Missing `source_type`
  defaults to `"synapse_workspace"`. Missing `scope_id` defaults to a
  short id derived from the config's `workspace_name`. The on-disk file
  is **never rewritten** during this upgrade — the v1 file stays as it
  was authored, preserving historical accuracy.
- **Write path**: every new run writes a v2 manifest.
- **Diff**: ADR-0002 artifact naming `<source>__<scope>__<module>.json`
  changes the `name` field; the diff layer keys on `(source_type, scope_id, module)`
  going forward, with a fallback that matches v1 `<module>.json` names
  against `(synapse_workspace, <run-scope>, <module>)`.

## Consequences

**Positive**
- Diff (run-vs-run) becomes meaningful across multi-scope runs.
- Per-scope filtering on the UI keys directly off manifest data.
- No data migration step required — v1 manifests are read transparently.

**Negative**
- Diff code path gains a small back-compat branch for v1.
- A consumer that parses manifests by hand needs to handle both versions
  (documented in `docs/architecture/sources.md`).

**Neutral**
- The `record_count_delta` / `size_delta` computed fields on
  `ManifestDiffEntry` stay as they are (see commit `1bb49d8`).

## Alternatives considered

- **In-place v1 rewrite to v2 on first read**: rejected — never mutate
  historical data on read.
- **Separate v2 manifest sidecar file**: rejected — splits the source of
  truth and complicates the read path forever.
- **Make `scopes` an inline field on each artifact only (no top-level list)**:
  rejected — the top-level list lets a manifest reader enumerate scopes
  without scanning every artifact record.
