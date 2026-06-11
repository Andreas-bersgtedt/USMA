# Unified Solution Migration Analyzer — UI User Guide (v2.0)

This guide is for analysts and migration leads who drive the analyzer from
the browser. It covers every page of the SPA, every column / pill / stat
card, and the end-to-end workflow for kicking off runs, comparing them and
exporting the results to a static deliverable.

For installation, service-principal setup and CLI flags see
[QUICKSTART.md](../../QUICKSTART.md). For module / data internals see
[README.md](../../README.md).

## Audience

You should read this if you are **driving** the analyzer (not extending
it). The CLI experience is documented in QUICKSTART; everything below
assumes you have already run `quickstart.ps1` (or its manual equivalent)
and have either an `output/` directory or a live `sma serve --with-api`
session in front of you.

## How the guide is organised

Two surfaces, one set of pages. The SPA renders different navigation
depending on whether it detects a live API:

- **Static deliverable mode** (`sma serve` over `output/`) — Dashboard,
  Code objects, Recommendations, Runbook, Delta. Read-only.
- **Control plane mode** (`sma serve --with-api`) — adds Run, Runs,
  Diff, Configuration. Writes go through the local API.

Every page chapter follows the same shape: *Purpose → How to open →
Inputs → Layout → Field reference → Common tasks → Empty / error states*.
Skip ahead to whichever page you are looking at; each chapter is
self-contained.

## Table of contents

### Orientation

- [01. Getting started](01-getting-started.md)
- [02. Static vs. control-plane mode](02-modes.md)
- [03. The run picker](03-run-picker.md)

### Pages — read-only (both modes)

- [04. Dashboard](04-dashboard.md)
- [05. Code objects](05-code-objects.md)
- [06. Recommendations](06-recommendations.md)
- [07. Runbook](07-runbook.md)
- [08. Delta](08-delta.md) (static mode only — replaced by *Diff* in control-plane mode)

### Pages — control plane only

- [09. Run](09-run-page.md)
- [10. Runs (history)](10-runs-history.md)
- [11. Diff](11-diff-page.md)
- [12. Configuration](12-configuration.md)
- [16. Estate overview](16-overview.md)

### Reference

- [13. Troubleshooting](13-troubleshooting.md)
- [14. Security posture](14-security.md)
- [15. FAQ](15-faq.md)
- [17. Tool access & security implications](17-access-and-security.md)
- [18. Cost of running the analyzer](18-cost.md)
- [19. Effort estimates & rate card](19-effort.md)
- [20. Data Factory (ADF) sources](20-data-factory.md)
- [21. Databricks sources](21-databricks.md)
- [21a. Databricks on AWS *(alpha)*](21a-databricks-aws.md)
- [22. Google BigQuery sources](22-bigquery.md)
- [23. Snowflake sources](23-snowflake.md)
- [24. Standalone Dedicated SQL pool (formerly SQL DW) *(alpha)*](24-standalone-dedicated-sql.md)
- [Glossary — acronyms & abbreviations](glossary.md)
- [99. Adding a new source](99-adding-a-source.md)

## Conventions used in this guide

- `code spans` are CLI commands, file paths, JSON keys or HTTP
  endpoints — anything you would type literally.
- **Bold** marks UI labels exactly as they appear in the SPA.
- *Italics* highlight a state or mode (*loading*, *empty*).
- ASCII layout diagrams indicate where elements live on the page; they
  are intentionally not pixel-accurate but reflect the current SPA.

## Versioning

This guide tracks the current release of **Unified Solution Migration Analyzer**
(see [CHANGELOG.md](../../CHANGELOG.md) for the exact version). Changes
to navigation, pages or fields are reflected here in lock-step with the
changelog.

> The SPA also surfaces additional pages — **Cost**, **Governance**,
> **Security**, **Help** (both modes) and **Overview** (control-plane
> only) — that are not yet covered chapter-by-chapter in this guide.
> Each of those pages renders the corresponding module's JSON directly
> and links back to its module HTML report under `./output/`.
