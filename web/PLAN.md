# Option A — Static SPA over existing JSON outputs

> **Goal:** Give analysts an interactive front end on top of the JSON the
> Unified Solution Migration Analyzer already produces, without introducing a
> backend, authentication surface, or data egress path. Ship a static
> bundle that can be dropped next to the analyzer's `output_dir` and
> opened in any modern browser.

## Why "static-only" first

- **Zero new infra.** Today the analyzer is a CLI. Adding a server doubles
  the operational footprint and pulls auth, secrets, and identity into
  scope. We defer that to Option B.
- **Re-uses the existing data contract.** Every analyzer module already
  emits a Pydantic model serialised as JSON. Those files are stable enough
  to be the SPA's "API". No new schemas, no DB.
- **Trust boundary stays at the file system.** If the analyst can read
  `output_dir`, they can use the SPA. No new secrets to manage.
- **Composable upgrade path.** The same SPA, unchanged, can be served by
  the FastAPI backend in Option B; only the data base URL changes.

## Scope

### In scope

1. Vite + React + TypeScript SPA.
2. Pages:
   - **Dashboard** — readiness score, % T-SQL compatible, capacity SKU,
     top blockers, inputs analysed.
   - **Code objects** — per-object Fabric compatibility verdict, parameters,
     T-SQL gap drill-down, truncated definition. Filter + sort + free-text.
   - **Recommendations** — severity/area filter, full detail on demand.
   - **Runbook** — grouped by phase, in order, with rollback notes.
   - **Delta** — added/removed/changed artefacts and record-count deltas.
3. Read-only fetch of:
   - `fabric_mapping.json`
   - `dedicated_pools.json`
   - `run_delta.json`
4. Build-time `VITE_SMA_DATA_BASE` to point the SPA at any data location.
5. CLI hook: optional `--with-webui` flag on `analyze-all` that copies the
   pre-built SPA into `output_dir` so the analyst gets a one-folder
   deliverable. (Not yet wired; see "Phase 2" below.)

### Out of scope (deliberately)

- Triggering analyzer runs from the UI.
- Authentication / RBAC.
- Persistent run history beyond what's already on disk.
- Editing the analyzer config from the UI.
- Server-side pagination, search index, or aggregate APIs.
- Multi-tenant.
- Real-time collaboration.

All of the above are addressed by Options B and C.

## Architecture

```
   ┌───────────────────────┐        static fetch (read-only)
   │  Synapse Migration    │  emits JSON
   │  Analyzer (CLI)       │ ─────────────────┐
   └───────────────────────┘                  │
                                              ▼
                                  ┌──────────────────────────┐
                                  │  output_dir/             │
                                  │   ├ fabric_mapping.json  │
                                  │   ├ dedicated_pools.json │
                                  │   ├ run_delta.json       │
                                  │   └ index.html (SPA)     │
                                  └──────────────────────────┘
                                              ▲
                                              │ same-origin GET
                                              │
                                  ┌──────────────────────────┐
                                  │  Browser (analyst)       │
                                  │  React SPA (this folder) │
                                  └──────────────────────────┘
```

### Data flow

1. CLI writes JSON to `output_dir`.
2. Analyst opens the SPA. In production, `web/dist/` is copied next to the
   JSON files in `output_dir` and served by any static HTTP server (e.g.
   `python -m http.server`). In dev, `npm run dev` configures Vite to use
   `$SMA_DATA_DIR` (default `../output`) as its `publicDir`, so JSON files
   are served from the same origin as the SPA.
3. SPA does plain `fetch("./fabric_mapping.json")` etc. on mount — the
   request resolves to a sibling file in both modes.
4. All filtering, sorting, and drill-downs happen client-side.

### Why no `file://`

Modern Chromium / Firefox refuse `fetch()` against `file://` URLs for
security reasons (the response would otherwise leak arbitrary local
files). Therefore the SPA mandates an HTTP origin. `python -m http.server`
is sufficient and documented in the README.

## Tech choices and rationale

| Choice | Rationale |
| --- | --- |
| **Vite** | Fast dev loop; simple config; no Webpack baggage. |
| **React 18 + react-router-dom** | Familiar, hireable, MIT, plays well with TanStack Table. |
| **TypeScript strict** | DTOs mirror Pydantic; catches drift early when the analyzer evolves. |
| **TanStack Table** (headless) | Sort + filter + virtualization-ready without locking us into a styled grid. |
| **No CSS framework** | One small `styles.css` keeps the bundle <200 KB gzipped and avoids design dependencies. |
| **No charting library yet** | Stat tiles + tables cover the v1 dashboards; Recharts/ECharts can be added later if needed. |
| **No state library** | Each page loads its own JSON via `useAsync`. No global mutable state. |
| **No auth** | Out of scope for static deliverable. |

## Type contract

`web/src/types.ts` is a hand-written mirror of the Pydantic models in
`src/usma/modules/**/models.py`. It is intentionally
**permissive** — almost every field is optional — so older analyzer
outputs still render. When the analyzer adds a field, add it here. When
it renames or removes a field, treat it as a breaking change and bump a
`schema_version` constant (TODO: emit one from the analyzer).

A future improvement: emit `model.model_json_schema()` per module from a
dedicated `sma export-schema` CLI subcommand and run
`json-schema-to-typescript` (or `quicktype`) at build time to generate
`types.ts`. This stays compatible with Option B because both options use
the same Pydantic models.

## Performance budget

| Metric | Budget |
| --- | --- |
| Initial bundle (gz) | < 200 KB |
| First contentful paint (loaded LAN) | < 500 ms |
| Time to interactive on Dashboard | < 1 s |
| Code-objects table responsiveness | 5k rows without virtualization (current). 50k+ rows would require adding `@tanstack/react-virtual` (not yet a dependency). |

## Roll-out phases

### Phase 1 — Scaffold (this PR)
- `web/` project structure with Vite + React + TS.
- Loader, router, layout, theming.
- Dashboard, Code objects, Recommendations, Runbook, Delta pages.
- README + PLAN + UPGRADE_TO_B docs.

### Phase 2 — CLI integration (in progress)
- [x] `sma analyze-all --with-webui` copies `web/dist/` into `output_dir`,
      mirrors module JSON files alongside, and re-runs `index.html` so the
      analyzer's landing page surfaces an "Open web UI" banner.
- [x] `sma serve --output-dir <path> --port 8000 --host 127.0.0.1` thin
      wrapper around `http.server`. Auto-redirects to the SPA when
      `webui/index.html` is present, otherwise to the analyzer's
      `index.html`. Loopback-only by default.
- [x] `index.html` (from `index_report.py`) now renders an interactive
      banner linking to `webui/index.html` when the SPA is installed.
      (Auto-redirect was deliberately downgraded to a banner so analysts
      keep access to the per-module HTML cards.)
- [x] CI step under `.github/workflows/ci.yml` running `npm ci` (or
      `npm install` until a lockfile is committed), `npm run typecheck`,
      `npm test` (Vitest), and `npm run build`. The built `web/dist/` is
      uploaded as a workflow artefact rather than committed to the repo.

### Phase 3 — Coverage extension
Add pages for the modules not in v1:
- Pipelines (Synapse-vs-Fabric expression compatibility, schedule mapping).
- Serverless pools (cost attribution by schema/login).
- Spark pools (runtime compatibility, libraries).
- Storage (linked accounts, ABFS migration plan).
- Monitoring (DWU-hours, time series mini-charts).
- Cost (Cost Mgmt vs Fabric SKU compare).
- Governance / Security (rule findings).
- Fabric validation (post-migration row/schema diff).

Most are 1–2 day adds: declare a TS interface for the module's payload,
add a page, drop in a TanStack table.

### Phase 4 — Polish
- Accessibility audit (axe), keyboard nav for tables, focus management.
- Print-friendly layout for handing reports to stakeholders.
- Light theme toggle.
- Permalink support (URL state for filters / expanded rows).
- Optional: replace `useAsync` with TanStack Query for staleness/retry
  semantics if a backend ever appears (Option B).

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Analyzer schema drift breaks the SPA silently | Treat `types.ts` as a public contract; add a Vitest smoke test that loads each module's JSON fixture from `tests/` and asserts a non-zero `keys()` count for documented fields. Bump `schema_version` on breaking change. |
| Large datasets slow the table | Wire `useVirtualizer` on the Code objects + Recommendations pages when row counts exceed 5k. |
| Analyst opens the SPA via `file://` | Documented in README; we could ship a `serve.ps1` helper in `output_dir`. |
| Stale `web/dist/` in repo | Either `.gitignore` it and rebuild in CI, or commit it but enforce a release-only update. Recommendation: gitignore + CI release step. |

## Definition of done for Option A

- [x] Project scaffolded under `web/` with Vite + React + TS.
- [x] Five pages implemented against the JSON contracts above.
- [x] README explaining dev + production flows.
- [x] Dev mode wired via Vite `publicDir` (no proxy, no separate server).
- [x] In-memory loader cache so navigation doesn't re-fetch JSON.
- [x] Vitest smoke test loading committed fixture JSON.
- [x] CI step running `npm install && npm run build && npm run typecheck`
      (plus `npm test`).
- [x] `--with-webui` flag (Phase 2).
- [x] `sma serve` command (Phase 2).
- [x] Index banner pointing at SPA (Phase 2).

Phase 2 is complete. Phases 3 (additional module pages) and 4 (a11y,
permalink, theme toggle, virtualization) remain future work.

---

## Phase 5 — Local control plane (Option B, sequenced)

> **Trigger:** stakeholder request to "add a web UI that can trigger
> scans and manage configuration." This phase implements the Option B
> design captured in [`UPGRADE_TO_B.md`](./UPGRADE_TO_B.md) on top of the
> shipped static SPA, with a strong preference for incremental,
> independently shippable PRs. Detailed task breakdown lives in
> [`PLAN_CONTROL_PLANE.md`](./PLAN_CONTROL_PLANE.md); this section is
> the index.

**Deliverables, in order:**

1. **Backend skeleton** (`src/usma/web/`) — FastAPI
   app, in-process job runner, filesystem run repo, schema endpoint.
2. **`sma serve` evolution** — same command grows a `--with-api` flag
   that mounts the FastAPI app at `/api` and serves `web/dist/` at `/`.
   Default behaviour (read-only static) is unchanged for the existing
   user base.
3. **Run-aware loader + run selector** — only [`web/src/api/loader.ts`](src/api/loader.ts)
   and a new topbar widget change. Existing pages keep working.
4. **Configuration page** — read / edit / validate `AppConfig` via
   the new `/api/config` endpoints. Secrets stay out of the response
   body and out of `runs/`.
5. **Run wizard + live progress** — `POST /api/runs` + SSE stream
   bound to a new "Run" page. Driven by per-module schemas.
6. **Run history + diff UI** — list, status, readiness sparkline,
   two-run picker that wraps the existing `run_manifest` delta logic.

Each deliverable is independently mergeable and behind a feature flag
(`SMA_WEB_API=1`) until the full set lands. Until then the analyzer
remains CLI-first, and `sma serve` continues to behave exactly as
documented in Phase 2.
