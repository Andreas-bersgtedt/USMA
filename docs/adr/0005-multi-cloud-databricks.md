# ADR-0005: Multi-cloud Databricks support (Option A — descriptor extras)

* **Status:** Accepted (Phase 4.7)
* **Date:** 2026-05
* **Supersedes:** —
* **Superseded-by:** —

## Context

Databricks runs on Azure, AWS and GCP. Up to v0.7-rc1, USMA's
`DatabricksProvider` was implicitly Azure-only: it ARM-probed the
workspace via `azure-mgmt-databricks`, exchanged an Entra SP for an
AAD bearer token (audience
`2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`), and stored the workspace
identifier as a full ARM resource id.

We want to add AWS Databricks (and later GCP) without:

* Forking `SourceType.DATABRICKS` into `DATABRICKS_AZURE` /
  `DATABRICKS_AWS` (would break every manifest written so far, all
  cross-source dispatch maps in `modules/__init__.py`, the SPA's
  source-type radio, and the analyzer's wave logic).
* Duplicating the entire `modules/databricks_workflows/` tree — the
  collector, run-stats, Fabric mapping, reporting and analyzer
  modules all talk to the `databricks-sdk` `WorkspaceClient`, which
  is itself cloud-agnostic.

## Decision

**Keep `SourceType.DATABRICKS` stable. Carry the cloud as a string
discriminator in two places:**

1. **Runtime:** `SourceDescriptor.extras["platform"]` ∈
   `{"azure", "aws", "gcp"}`. Missing key ⇒ `"azure"` (back-compat
   with pre-4.7 descriptors and v2 manifests).
2. **Configuration:** `SMA_DATABRICKS_PLATFORM` env var with the same
   value set. Missing ⇒ `"azure"`.

A new helper `usma.sources.databricks_platform(descriptor)` is the
single source of truth for reading the discriminator and applying the
default.

`DatabricksAwsProvider` sits next to the existing `DatabricksProvider`
under `src/usma/sources/databricks/`. A small dispatcher
(`provider_for_descriptor`) routes by platform. The Azure provider
remains the `SOURCE_REGISTRY` default so anything that resolves by
`SourceType` alone keeps working.

The CLI gains a `databricks-aws:<host>` `--scope` shortcut that maps
to `SourceType.DATABRICKS` with `extras["platform"]="aws"`. The SPA
Configuration page renders a sub-radio (Azure | AWS) only when the
source-type is Databricks.

## Why not Option B (subtype enum)?

We considered splitting `SourceType` into per-cloud variants. Rejected
because:

* Every manifest ever written has `source_type: "databricks"`. A
  v2→v3 upgrade with implicit-Azure default fanned out into every
  call site that switches on `SourceType`.
* The SPA, the run-plan, the `modules/__init__.py` dispatch table,
  the doctor command and the cost module all key on `SourceType`. A
  rename forces a coordinated change across all of them, with no
  semantic gain — the modules underneath genuinely don't care which
  cloud the workspace lives in.
* Future Databricks-on-GCP gets a third enum member to chase.

Option A localises the new code to (a) the provider layer and (b) the
three backend entry points (`config.py`, `cli.py`, `doctor.py`), which
matches our existing pattern for BigQuery (where `SMA_SOURCE_TYPE=bigquery`
similarly relaxes the AAD env-var gate).

## Consequences

### Positive

* Zero manifest-schema churn. Existing manifests load unchanged with
  the Azure default applied at read time.
* `databricks_workflows` analyzer, collector and reporting paths are
  reused 1:1 — the `WorkspaceClient` factory is the only seam.
* The SPA Configuration page surfaces the new option as a sub-radio
  rather than a fourth top-level radio, which keeps the
  Synapse/ADF/Databricks/BigQuery mental model intact.

### Negative

* `databricks_platform(descriptor)` must be called wherever the cloud
  affects behaviour (currently: config gate, doctor probes, provider
  dispatch). Forgetting to call it silently produces an "Azure" code
  path against AWS coords — the test suite gates this for the three
  current call sites; future callers need to be careful.
* The `databricks-aws:` CLI scope-prefix is a second way to express
  "AWS"; the canonical store is still `extras["platform"]`. Tests
  enforce that the prefix expands to the descriptor form.

### Not addressed in this ADR

* **AWS cost attribution.** Azure's Cost Management API does not see
  AWS-billed DBUs/EC2. Hooking the Databricks Usage API is scoped to
  Phase 4.7.5.
* **GCP Databricks.** `platform="gcp"` is reserved and validated, but
  no provider is implemented in 0.7.
* **PrivateLink reachability.** The validator currently lets network
  errors bubble up as `requests.ConnectionError`; a dedicated probe
  is Phase 4.7.5.

## References

* `usma.sources.databricks_platform()` — `src/usma/sources/__init__.py`
* `DatabricksAwsProvider` — `src/usma/sources/databricks/aws_provider.py`
* User guide — [`21a-databricks-aws.md`](../user-guide/21a-databricks-aws.md)
* Planning manifest §4.7 — [`USMA_planning_Manifest.md`](../../USMA_planning_Manifest.md)
