# 0004 — Package + brand rename

**Status:** Accepted (2026-05-19) — rename to **USMA** in Phase 3
**Date:** 2026-05-19
**Updated:** 2026-05-29 — human-readable brand swapped from *Unified Synapse Migration Analyzer* to *Unified Solution Migration Analyzer* (see footnote at end). Acronym `USMA` and all package / CLI / env identifiers preserved.
**Decision IDs (feasibility study §9):** D2

## Context

The Python package, repo, CLI entry point, and product name are all
`synapse_migration_analyzer` / `SynapseMigrationAnalyzer`. Once ADF,
Databricks, and SAP BW ship, the name becomes actively misleading:
users will look for "ADF migration analyzer" and not find this tool.

## Decision (proposed — needs sign-off)

Rename for Phase 3 with a deprecation shim for one minor release.

**Candidate names** (final pick deferred to a separate decision):

- **USMA — Unified Solution Migration Analyzer.** Working name used in
  branch `unified_SMA` and [`USMA_planning_Manifest.md`](../../USMA_planning_Manifest.md).
  Pros: continuity with existing brand recognition. Cons: still has
  "Synapse" in the expansion despite covering ADF/Databricks/SAP BW.
- **EMA — Estate Migration Analyzer.** Pros: source-agnostic, accurate.
  Cons: full rebrand, loses search-engine equity.
- **FEA — Fabric Estate Analyzer.** Pros: centers the target (Fabric).
  Cons: doesn't immediately convey "assesses pre-Fabric estate".

### Migration plan (whichever name wins)

1. **Phase 3 PR #1**: rename Python package via a thin
   `synapse_migration_analyzer/__init__.py` shim that re-exports the new
   package with a `DeprecationWarning`.
2. **Phase 3 PR #2**: rename CLI entry point in `pyproject.toml`,
   register both old and new commands.
3. **Phase 3 PR #3**: GitHub repo rename (single click; old URL keeps
   redirecting).
4. **Next minor release after Phase 3**: drop the shim.

## Consequences

**Positive**
- Tool name reflects scope.
- New users searching for "ADF Fabric migration tool" can find it.

**Negative**
- Customers with embedded CLI calls need to update commands within the
  deprecation window.
- Documentation cross-references need a sweep.

**Neutral**
- Internal Python imports are touched everywhere, but a single PR with
  a search-and-replace handles it; tests catch breakage.

## Alternatives considered

- **Keep the name forever.** Rejected — see Context.
- **Major-version bump with hard rename and no shim.** Rejected — too
  disruptive for a tool used in customer engagements.

## Decision needed before Phase 3 starts

This ADR is *Proposed* and is the only one of the four currently
blocking actual implementation work (Phase 3, well downstream). Phases
0–2 are unaffected by the rename outcome.

---

## 2026-05-29 update — brand swap to "Unified Solution Migration Analyzer"

The "Synapse" in the original expansion (preserved for continuity in
the 2026-05-19 sign-off) became actively misleading once Phase 4
(Databricks), Phase 5 (BigQuery + GCP IaaS), and Phase 7 (Snowflake)
shipped — the analyzer now covers five sources, only one of which is
Synapse. Renamed the human-readable brand to **Unified Solution
Migration Analyzer** so the expansion matches the actual coverage.

**Preserved (zero churn):**
- Acronym `USMA` (still parses as U**nified** **S**olution **M**igration **A**nalyzer)
- Python package directory `src/usma/` and every `from usma...` import
- CLI entry points `usma` and `synapse-migration-analyzer`
- Env-var prefixes (`SMA_*`), config schema field names
- ADR filenames (this one stays `0004-package-rename.md`)
- Repo name `USMA`, runtime path `C:\SMA\Private8\USMA`
- The `synapse_migration_analyzer/` deprecation shim (historical SMA
  package, unrelated to the new brand)

**Changed:** 88 string occurrences across 55 files — titles, `<h1>`
banners, HTML/Markdown report headers, docstrings, FastAPI app title,
SPA top-bar, `<title>`, package descriptions, user-guide chapters, and
the two test assertions that pin the brand string.
