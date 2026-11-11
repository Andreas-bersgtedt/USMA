---
name: exported-backup-assessment
description: Analyze a locally saved migration-analyzer backup and produce evidence-based compute or storage findings. Use when reviewing an exported run archive, checking collection coverage, comparing resource snapshots, or preparing a report or dashboard from saved results.
---

# Assessing an exported backup

Use this workflow for read-only analysis of a saved assessment archive. Follow the user's requested scope. Do not expand a compute-and-storage review into unrelated migration, governance, or security findings.

## Safety and scope

- Analyze files already present in the supplied archive. Do not start a live cloud scan, change resources, or send backup contents to an external service.
- Do not read `.env` files, dotfiles, tokens, private keys, or credentials. Do not copy secrets into reports, examples, logs, or generated pages.
- Treat run labels, resource IDs, hostnames, file paths, and exported records as potentially sensitive. Include only identifiers the user needs to interpret the requested findings.
- Keep analysis artifacts within the requested locations. Do not commit or push unless the user asks.

## Inspect the archive

1. Read the export manifest, if present. Record the export timestamp, application version, declared run/file counts, excluded paths, and whether event records were exported.
2. Inventory the archive's actual run folders and files. Compare actual counts with the manifest, and report differences.
3. Read each run metadata file. Capture the run ID, source resource ID, source type, label, status, timestamps, module states, and error counts.
4. Report raw run counts and distinct source counts separately. Deduplicate resource IDs case-insensitively. Where multiple runs exist for one resource, use the latest completed run for summary metrics and disclose the rule. Keep unfinished runs, failed runs, and resources without a completed replacement visible as coverage gaps.
5. Distinguish similarly named scopes from identical resources. A workspace scan and a dedicated-server scan can describe the same pool; match by stable IDs and pool names before combining counts.

## Analyze compute

- Use the saved capacity projection as the source for estimated capacity, suggested SKU, headroom, workload contributions, and projection notes.
- Count positive, zero, and missing estimates separately. Missing is not zero, and a model-produced zero is not proof of no demand.
- Report the number of scopes analyzed alongside any ranking or aggregate. Do not present a sum of per-scope SKU recommendations as an estate procurement recommendation unless shared-capacity use and overlapping workloads have been resolved.
- Separate measured source telemetry from modeled equivalents. Name the observation window, conversion basis, and any missing executor, allocation, concurrency, or run-history data.
- Treat sizing as a planning estimate. Do not describe estimated target capacity or cost as observed billing.

## Analyze storage

- Keep storage-account capacity, SQL reserved space, SQL data space, row counts, and target-storage estimates as distinct measures.
- When a storage account appears in multiple snapshots, deduplicate using a stable account ID when available. Otherwise use a case-insensitive account name, keep the latest capture timestamp, and disclose that fallback.
- Do not add repeated snapshots for the same account. Report the reading count, unique-resource count, and capture time range.
- Keep workspace pool results separate from dedicated-server scans. Identify overlapping pool readings and compare timestamps and values; never add two scans of the same pool.
- Do not treat reserved SQL space as exported data size or destination capacity. Use the report's units as given and state any conversion, such as decimal GB to TB.
- Report resources with missing or failed readings separately from those with a numeric zero. A missing reading does not establish an empty resource.

## Check evidence quality

- Read the source JSON for totals and row-level values. Use Markdown and HTML summaries as navigation and cross-checks, not as the only evidence.
- Inspect nested module errors even when the run-level status says `ok` or the run-level error count is zero. Group repeated failures by module and cause, and identify affected scopes.
- Check for throttling, truncated history, paused resources, authentication failures, empty inventory results, and incomplete-collection flags. Explain which conclusions those conditions limit.
- Validate the totals in the written report against the underlying records. For deduplicated totals, reproduce the selection rule before summing.
- Link findings to the specific local manifest or run artifacts that support them. Clearly label archived observations and their timestamps; do not imply they describe current live resources.

## Write the requested deliverable

Use concise, descriptive headings and concrete findings. State the method, scope, units, date range, coverage, and material caveats. Separate observed values from estimates and recommendations. Avoid claims that exceed the exported evidence.

For an interactive HTML deliverable:

- Embed the relevant derived data so the page works offline without loading libraries or services.
- Provide useful filtering or sorting for tables and charts, and make missing values distinguishable from zeros.
- Keep category totals separate when they describe different resources or overlapping scans.
- Follow the repository's applicable design and accessibility rules. Verify the page opens and its controls update the displayed records.

Before finishing, check that the requested files exist, their figures match the source records, local evidence links resolve, and no secret or unrelated finding has been included.
