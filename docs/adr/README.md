# Architecture Decision Records (ADRs)

This directory tracks the design decisions for the **Unified Synapse
Migration Analyzer (USMA)** multi-source refactor. Each ADR has a number,
a one-line title, and a status:

- **Proposed** — drafted, not yet accepted.
- **Accepted** — locked in; implementation may proceed.
- **Superseded by NNNN** — replaced by a later ADR.
- **Deprecated** — no longer relevant.

| # | Title | Status |
|---|---|---|
| [0001](0001-multi-source-architecture.md) | Multi-source plugin architecture | Proposed |
| [0002](0002-scope-and-credentials-model.md) | Scope + credentials model | Proposed |
| [0003](0003-manifest-v2-schema.md) | Run manifest schema v2 | Proposed |
| [0004](0004-package-rename.md) | Package + brand rename | Proposed |
| [0005](0005-multi-cloud-databricks.md) | Multi-cloud Databricks support | Accepted |
| [0007](0007-snowflake-auth.md) | Snowflake authentication (OAuth refresh-token) | Accepted |
| [0008](0008-snowflake-multi-cloud.md) | Snowflake multi-cloud via `CURRENT_REGION()` | Accepted |

See [`../../feasibility_study.md`](../../feasibility_study.md) §9 for the
matching open-decision list (D1–D10) and [`../../USMA_planning_Manifest.md`](../../USMA_planning_Manifest.md)
for live progress.

## ADR template

```markdown
# NNNN — <Title>

**Status:** Proposed | Accepted | Superseded by NNNN | Deprecated
**Date:** YYYY-MM-DD
**Decision IDs (feasibility study §9):** Dx, Dy

## Context

What problem are we solving? What constraints apply?

## Decision

What we are going to do.

## Consequences

Positive, negative, and neutral consequences. Migration / back-compat notes.

## Alternatives considered

Brief summary of options not taken and why.
```
