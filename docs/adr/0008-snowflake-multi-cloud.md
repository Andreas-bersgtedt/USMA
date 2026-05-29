# ADR-0008: Snowflake multi-cloud via `CURRENT_REGION()` prefix sniffing

* **Status:** Accepted (Phase 7)
* **Date:** 2026-05
* **Supersedes:** —
* **Superseded-by:** —

## Context

Snowflake runs on AWS, Azure and GCP. Unlike Databricks (ADR-0005)
where the workspace host is a hard-coded `<x>.azuredatabricks.net` /
`<x>.cloud.databricks.com` per cloud, Snowflake account locators are
cloud-agnostic — the same `acme-prod` account looks identical from
the connector's perspective regardless of which hyperscaler hosts the
data plane underneath.

We need to know the hyperscaler for two downstream consumers:

1. **Estate Overview hyperscaler grouping** (`_cloud_for` in
   `web/estate.py`) buckets workspaces under Azure / AWS / GCP /
   on-prem columns.
2. **Cost narrative.** A future Snowflake-on-AWS scope eventually
   correlates Snowflake spend with the underlying S3 egress + EC2
   support reservations (out of scope today but the data model
   needs to leave room).

## Decision

**Sniff the hyperscaler from `SELECT CURRENT_REGION()` and persist it
as `extras["platform"]`.** Snowflake's region locators have a stable
documented prefix:

| Prefix | Hyperscaler |
|---|---|
| `AWS_*` (e.g. `AWS_US_WEST_2`) | `aws` |
| `AZURE_*` (e.g. `AZURE_WESTEUROPE`) | `azure` |
| `GCP_*` (e.g. `GCP_EUROPE_WEST4`) | `gcp` |
| anything else | `aws` (conservative default) |

`SnowflakeProvider.discover()` runs `CURRENT_REGION()` once at
discovery time and stamps both `extras["region"]` (verbatim, for the
report) and `extras["platform"]` (one of the three normalised
values). The `SMA_SNOWFLAKE_PLATFORM` env var overrides the sniff
for cases where the operator wants to pin the bucket without a
discovery roundtrip (e.g. CI smoke tests against a regionless
mock).

The SPA exposes a small **Cloud** select in the Snowflake config card
that writes `SMA_SNOWFLAKE_PLATFORM` to `.env`, mirroring the
Databricks `Cloud` select introduced in ADR-0005.

## Why not parse the account locator?

Snowflake account locators **do not encode the cloud** in any
documented suffix. Two accounts named `xy12345` and `xy67890` can
live on different hyperscalers; the only sanctioned discriminator is
`CURRENT_REGION()` or `SHOW REGIONS`. URL-style locators
(`<acct>.<region>.<cloud>.snowflakecomputing.com`) do encode the
cloud, but the connector accepts the bare account locator and the
display-name form so we can't rely on the URL-style form being
present.

## Why not require the operator to set it?

Mandatory would work, but the same Snowflake account auth UI used
for sign-in already has a live data-plane connection at discovery
time. Running one extra `SELECT CURRENT_REGION()` is free, and a
zero-config UX matches the Databricks ADR-0005 pattern where the
platform is auto-detected from the workspace host. The env var
override is preserved for the CI / regression-test path.

## Consequences

### Positive

* **No new manifest fields.** `extras["platform"]` already exists
  (Phase 4.7 added it for Databricks). The Estate Overview's
  `_cloud_for` resolver consumes it transparently — the
  `databricks_platform` parameter is misnamed but accepts any
  scope's `extras["platform"]`.
* **Single-place upgrade.** When Snowflake adds a fourth hyperscaler
  (Oracle? IBM?), `SnowflakeProvider._platform_from_region` and the
  SPA dropdown are the only two places to touch.
* **Override-able for CI.** `SMA_SNOWFLAKE_PLATFORM=azure` pins the
  bucket without a live connection, useful for fixture-driven tests
  and air-gapped demo runs.

### Negative

* **`CURRENT_REGION()` is one query at discovery time.** Adds
  ~50 ms to the first analyzer cycle. Negligible vs the rest of
  discovery; mitigated by caching the result in the descriptor.
* **Unknown prefix defaults to AWS.** A truly new Snowflake hyperscaler
  (not yet announced) would silently bucket under AWS until the
  prefix table is updated. We accept this because it's strictly
  cosmetic (Estate Overview grouping) and the override env var
  exists.
