# Web control plane — detailed plan (Option B, Phase 5)

> **Status: shipped.** Landed on `main` in commit `26acc27` as a single
> 9-PR rollout (FastAPI backend at `/api/*`, four new SPA pages, SSE
> progress, run repository, run-id-aware loaders). This document is kept
> as the design record; user-facing instructions now live in
> [`../README.md`](../README.md#web-control-plane-opt-in),
> [`../QUICKSTART.md`](../QUICKSTART.md#optional--browser-driven-control-plane),
> and [`./README.md`](./README.md#control-plane-mode-sma-serve---with-api).
> The threat model is in [`../SECURITY.md`](../SECURITY.md#web-control-plane-sma-serve---with-api).

> Companion to [`PLAN.md`](./PLAN.md) and
> [`UPGRADE_TO_B.md`](./UPGRADE_TO_B.md). This file is the working
> breakdown for converting the shipped static SPA into a local control
> plane that can **trigger analyzer runs and manage configuration** from
> the browser. Treat it as a checklist that PRs land against.

## TL;DR

Stand up a small **local-only** FastAPI app inside the existing CLI
(`sma serve --with-api`), expose the existing analyzer functions as
HTTP endpoints, and grow the SPA with three new surfaces:

1. **Configuration page** — read and edit `AppConfig`, validate it
   against the live host (ODBC driver, Azure auth, SQL reachability).
2. **Run page** — choose modules, kick off a run, watch SSE progress.
3. **Run history + diff** — list past runs, drill into any one, diff
   two runs.

No new third-party services. No data egress. No new auth surface for the
local mode. Same Pydantic models everywhere — the JSON schema becomes
the contract between SPA, CLI, and tests.

## Constraints, ranked

1. **Local-first.** Bind `127.0.0.1` by default. Bypass CSRF/CORS by
   refusing non-loopback hosts unless an explicit `--bind` flag is set.
2. **No new persisted secrets.** The browser never sees
   `AZURE_CLIENT_SECRET`. Edits to credentials write through to a
   chosen `.env` file with file permissions tightened (best-effort on
   Windows, `chmod 600` on POSIX).
3. **Re-use the existing analyzer.** Every endpoint is a thin adapter
   over a function already used by [`cli.py`](../src/usma/cli.py).
   No business logic moves to `web/`.
4. **Backwards compatible.** `sma serve` without `--with-api`,
   `sma analyze-*`, and the static SPA continue to work unchanged. The
   API + control plane is opt-in until it ships behind the default.
5. **Observability.** Every API call logs through the existing rich/JSON
   logger; every job writes a `runs/<id>/run.json` manifest including
   the redacted config hash, started_at, finished_at, status, errors.

## Architecture

```
   ┌──────────────────────────┐
   │ Browser (analyst)        │
   │ React SPA (web/dist)     │
   └──────────┬───────────────┘
              │ same-origin GET/POST/SSE
              ▼
   ┌──────────────────────────────────────────────┐
   │ uvicorn + FastAPI (sma serve --with-api)     │
   │  GET  /                       -> SPA shell   │
   │  GET  /webui/* + /assets/*    -> SPA assets  │
   │  GET  /api/healthz                            │
   │  GET  /api/schema/{module}                    │
   │  GET  /api/config                              │
   │  PUT  /api/config                              │
   │  POST /api/config/validate                     │
   │  GET  /api/runs                                │
   │  POST /api/runs                                │
   │  GET  /api/runs/{id}                           │
   │  GET  /api/runs/{id}/events           (SSE)    │
   │  GET  /api/runs/{id}/modules/{module}          │
   │  GET  /api/runs/{id}/diff/{other}              │
   └──────────┬─────────────────┬──────────────────┘
              │                 │
              │ in-process      │ filesystem
              ▼                 ▼
   ┌──────────────────────┐  ┌────────────────────────┐
   │ JobRunner            │  │ runs/                   │
   │ - asyncio.Semaphore  │  │   <id>/                 │
   │ - thread pool        │  │     run.json            │
   │ - progress queue     │  │     fabric_mapping.json │
   │ - cancel cookie      │  │     dedicated_pools.json│
   └──────────────────────┘  │     ...                 │
                             └────────────────────────┘
```

## Repository layout

```
src/usma/
  web/
    __init__.py
    app.py                # FastAPI factory, error handlers, CORS-off
    deps.py               # auth_guard, runs_dir, settings injectors
    api/
      __init__.py
      healthz.py
      schema.py           # GET /api/schema/{module}
      config.py           # GET / PUT / POST /api/config
      runs.py             # POST /runs, GET /runs[/id], SSE events
      modules.py          # GET /runs/{id}/modules/{module}
      diff.py             # GET /runs/{id}/diff/{other}
    jobs.py               # JobRunner, RunMeta, progress events
    storage.py            # FilesystemRunRepo
    config_io.py          # safe load/save of .env and AppConfig overlays
    static.py             # mount + path-traversal-safe asset serving
    sse.py                # SSE helpers (heartbeat, JSON encode)
tests/
  web/
    test_app_smoke.py
    test_config_endpoints.py
    test_runs_endpoints.py
    test_runs_sse.py
    test_storage_repo.py
    test_path_traversal.py
web/src/
  api/
    loader.ts             # rewritten to be run-aware (preserves
                          #   existing exports for back-compat)
    runs.ts               # listRuns / startRun / streamEvents
    config.ts             # getConfig / putConfig / validateConfig
    schema.ts             # cached fetch of module schemas
  pages/
    Configuration.tsx     # NEW
    Run.tsx               # NEW
    Runs.tsx              # NEW (history)
    RunDiff.tsx           # NEW
  hooks/
    useRunSelector.ts     # NEW (URL ?run=<id>)
    useSseProgress.ts     # NEW
  components/
    RunPicker.tsx         # NEW (topbar)
    SchemaForm.tsx        # NEW (json-schema-driven form)
```

## Endpoint contracts

All payloads are Pydantic-validated; the schemas served by `/api/schema/*`
are the source of truth for the SPA forms.

### Configuration

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| `GET` | `/api/config` | — | `AppConfigPublic` (secrets redacted: `AZURE_CLIENT_SECRET` returned as `"set" \| "unset"`, never the real value) |
| `PUT` | `/api/config` | `AppConfigUpdate` | `{ "saved_to": "<path>", "warnings": [...] }`. Writes through to the active `.env` file (chosen via constructor or env). Refuses to write a value that fails Pydantic validation. |
| `POST` | `/api/config/validate` | `AppConfigUpdate` | `{ "ok": bool, "checks": [{"name": "odbc_driver", "ok": true, "detail": "ODBC Driver 18 found"}, ...] }`. Wraps the existing [`doctor.py`](../src/usma/doctor.py) checks plus a SQL reachability probe per dedicated pool. Never calls Azure if `--offline` is set. |

Secret handling rules:

- `AZURE_CLIENT_SECRET` is **write-only** through this API; reads return
  the literal string `"set"` or `"unset"`. The SPA renders a "rotate"
  affordance, never a value.
- The browser never sees `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` either
  unless a query flag `?reveal=ids` is set; documented as a
  troubleshooting affordance.
- `PUT /api/config` writes to the file path resolved by the same chain
  that the CLI uses (`--env-file` flag, then `./.env`). The endpoint
  returns the absolute resolved path so the analyst sees what changed.
- After write, the running app reloads `AppConfig` so the next run picks
  up the new values without a restart.

### Runs

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| `GET` | `/api/runs` | — | `[{ id, status, started_at, finished_at, modules, readiness_score?, errors_count }]` newest first, paged via `?limit=&before=`. |
| `POST` | `/api/runs` | `{ modules: ["dedicated_pools","pipelines",...], options: {...}, label?: string }` | `{ id, status: "queued" }`. 409 if a run is already in flight (semaphore). |
| `GET` | `/api/runs/{id}` | — | Full `RunMeta` including per-module status. |
| `GET` | `/api/runs/{id}/events` | — | SSE stream: `{type:"module_started"\|"module_finished"\|"log"\|"done"\|"error", data:{...}}`. Heartbeats every 15s. Disconnect-safe — sends the full event log on subscribe so a refresh doesn't lose state. |
| `DELETE` | `/api/runs/{id}` | — | Cancels an in-flight run (sets a cancel flag the analyzer's per-module loop polls); returns `{status:"cancelled"}`. Idempotent. |

Run-id format: `YYYYMMDDTHHMMSSZ-<8 hex>` (UTC, sortable, unique). Run
metadata path: `runs/<id>/run.json`. Module outputs land in
`runs/<id>/<module>.json` so the existing reporting layer is reused
verbatim — `runs/<id>/` is identical to today's `output_dir`.

### Module fetch

| Method | Path | Response |
| --- | --- | --- |
| `GET` | `/api/runs/{id}/modules/{module}` | The module's JSON, content-type `application/json`. Path-traversal guarded. |
| `GET` | `/api/runs/{id}/diff/{other}` | `RunDelta` from the existing `run_manifest` logic. |

### Schema

| Method | Path | Response |
| --- | --- | --- |
| `GET` | `/api/schema/{module}` | Pydantic `model_json_schema()` for the module's top-level model. Cached in-process. |
| `GET` | `/api/schema/config` | Schema for `AppConfigUpdate` (drives the Configuration page form). |

## Security model

- **Bind.** Default `127.0.0.1`. To bind another address, the operator
  must pass `--bind <addr>` and acknowledge `--i-know-this-is-not-auth`.
  Without that ack, the server refuses to start on a non-loopback bind.
- **Auth.** None for local mode. A future Entra ID hosted variant adds
  it via Easy Auth (Container App) — not in scope here.
- **CORS.** Disabled. Same-origin only.
- **CSRF.** Same-origin + no cookies = not exploitable. State-changing
  endpoints (`PUT /api/config`, `POST /api/runs`, `DELETE /api/runs/*`)
  require an `X-SMA-API: 1` header that browsers can send but cross-site
  forms cannot, as a defence-in-depth marker.
- **Path traversal.** All filesystem joins go through `pathlib.Path` +
  `is_relative_to(runs_dir)`. Module names are validated against the
  `_schema_registry()` keys, never accepted as free-form strings.
- **DoS.** `asyncio.Semaphore(1)` for run concurrency. Body size cap
  (1 MB) on config endpoints. SSE heartbeats prevent zombie streams.
- **Logs.** No secret values are logged. The redaction rule lives in
  `config_io.py` and is unit-tested.

## Data model deltas (Pydantic)

```python
# src/usma/web/jobs.py

class ModuleStatus(BaseModel):
    name: str
    state: Literal["queued", "running", "ok", "failed", "skipped"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None

class RunMeta(BaseModel):
    id: str
    label: str | None = None
    status: Literal["queued", "running", "ok", "failed", "cancelled"]
    started_at: datetime
    finished_at: datetime | None = None
    config_hash: str          # sha256 of redacted AppConfig
    modules: list[ModuleStatus]
    readiness_score: float | None = None  # mirrors fabric_mapping.json
    errors_count: int = 0
```

```python
# src/usma/web/api/config.py

class AppConfigPublic(BaseModel):
    azure: AzureConfigPublic                # secret -> "set"|"unset"
    sql: SqlConfig
    output_dir: Path

class AppConfigUpdate(BaseModel):
    azure: AzureConfigUpdate                # secret optional, write-only
    sql: SqlConfig | None = None
    output_dir: Path | None = None
```

## SPA work breakdown

### A. Loader rewrite (back-compatible)

`web/src/api/loader.ts` keeps its current named exports
(`loadFabricMapping`, `loadDedicatedPools`, `loadRunDelta`) but routes
through:

```ts
const RUN_BASE = (id: string) =>
  `${import.meta.env.VITE_SMA_API_BASE ?? "/api"}/runs/${id}/modules`;
const STATIC_BASE = import.meta.env.VITE_SMA_DATA_BASE ?? "./";

function url(name: string, runId?: string) {
  return runId ? `${RUN_BASE(runId)}/${name}` : `${STATIC_BASE}${name}.json`;
}
```

If `VITE_SMA_API_BASE` is unset (the static-SPA-only deployment), the
loader stays in today's filesystem-fetch mode. The single `runId` arg
defaults to `"latest"` when the API is present.

### B. Run picker

A topbar `<select>` populated from `GET /api/runs?limit=20`. Selecting
a run updates `?run=<id>` in the URL and re-renders pages. Persisted in
`localStorage` so the analyst's last run survives a refresh.

### C. Configuration page

- Form is generated from `GET /api/schema/config` via a small
  `SchemaForm.tsx` that maps JSON Schema → controlled inputs (string,
  number, boolean, enum). Avoids react-hook-form for now to keep deps
  lean.
- A "Validate" button calls `POST /api/config/validate` and shows the
  per-check pass/fail rows (ODBC, Azure auth, per-pool SQL probe).
- "Save" calls `PUT /api/config`. Disabled until validation passes.
- Secrets render a read-only "set" / "unset" badge with a "rotate"
  field that POSTs the new secret without ever GETting it back.

### D. Run page

- Module checklist defaulted from the last successful run.
- "Start" calls `POST /api/runs` and navigates to the run detail page.
- Live progress = `useSseProgress(runId)` — streams events into a
  module table (state, duration, last log line).
- Cancel button → `DELETE /api/runs/{id}`.
- On completion, links to each module's existing module page (which
  already loads from the loader and now shows the freshly written
  data).

### E. Run history + diff

- `Runs.tsx` table: id, label, started, status, readiness sparkline,
  modules-with-errors badge.
- Two-run picker → `RunDiff.tsx` reuses the existing `Delta` page logic
  but pulls from `/api/runs/{a}/diff/{b}` instead of the static
  `run_delta.json`.

### F. Vitest coverage

- Mock-server tests for the new loader fns (`runs.ts`, `config.ts`)
  using `msw`.
- Component tests for the run picker and the schema-driven form
  (renders a fixture schema, type-checks the values it emits).

## Backend test matrix

| Test file | Covers |
| --- | --- |
| `tests/web/test_app_smoke.py` | Boot the FastAPI app, hit `/api/healthz` and `/api/schema/*`, assert 200 + content-type. |
| `tests/web/test_config_endpoints.py` | `GET /api/config` redacts secrets; `PUT` writes to a tmp `.env`; `POST /validate` returns deterministic check rows when wrapped against an offline doctor. |
| `tests/web/test_runs_endpoints.py` | `POST /api/runs` enqueues a fake job (analyzer module patched), waits for completion via `GET /runs/{id}`, asserts `runs/<id>/run.json` shape. |
| `tests/web/test_runs_sse.py` | Subscribe with `httpx.AsyncClient`, assert heartbeat + module event ordering. |
| `tests/web/test_storage_repo.py` | Path-traversal blocked; idempotent run id generation; concurrent writes don't corrupt `run.json`. |
| `tests/web/test_path_traversal.py` | `GET /api/runs/{id}/modules/..%2Fetc%2Fpasswd` returns 400, never 200. |

Run with the same `pytest` we already use; FastAPI dependency makes
this an `extras-dev` install (`fastapi`, `uvicorn`, `httpx[http2]`,
`sse-starlette`, `pydantic-settings`).

## CLI surface change

`sma serve` grows three optional flags:

```
sma serve [...existing flags...]
          [--with-api]                    # mount FastAPI at /api
          [--runs-dir PATH]               # default: ./runs
          [--env-file PATH]               # default: ./.env
          [--bind 127.0.0.1]              # default loopback; explicit
                                          # opt-in via --i-know-this-is-not-auth
                                          # to bind elsewhere
```

`sma analyze-*` commands keep working. New: when `--with-api` is on,
the analyzer modules log through a `RunRecorder` adapter so SSE
subscribers see the same per-module progress as the rich-console run.
The CLI behaviour is unchanged when the recorder is absent.

## Rollout

| PR | Title | Verifies |
| --- | --- | --- |
| **PR-1** | `web/api`: backend skeleton (no SPA changes) | `tests/web/test_app_smoke.py`, `test_path_traversal.py`. `sma serve --with-api` boots; `/api/healthz` 200; `/api/schema/dedicated_pools` returns valid JSON Schema. |
| **PR-2** | run repo + `POST /api/runs` (single module) | `test_runs_endpoints.py`, `test_storage_repo.py`. CLI parity test: a run kicked via API produces the same `dedicated_pools.json` as `sma analyze-dedicated-pools`. |
| **PR-3** | SSE progress + cancel | `test_runs_sse.py`. Manual: kick a run, refresh tab, see existing event log replay; cancel mid-run leaves a `cancelled` `run.json`. |
| **PR-4** | `GET/PUT/POST /api/config` + redaction | `test_config_endpoints.py`. Manual: edit `.env` via UI, confirm secret never appears in network traffic. |
| **PR-5** | SPA loader rewrite + run picker | Vitest. Manual: existing pages keep working with `VITE_SMA_API_BASE` unset; setting it surfaces the picker and routes fetches through `/api`. |
| **PR-6** | Configuration page (`SchemaForm`) | Vitest snapshots. Manual: edit + validate + save round-trip. |
| **PR-7** | Run page + live progress | Vitest. Manual: run a 2-module scan from the UI, watch progress, land on the module page when done. |
| **PR-8** | Run history + diff | Vitest. Manual: select two runs, diff produces same JSON as `sma run-delta` between equivalent `output_dir`s. |
| **PR-9** | docs + quickstart wiring | `quickstart.ps1` learns `--with-api` flag and prints the right URL; README + QUICKSTART link the new pages. |

Each PR is small enough to land in a day or two; nothing requires the
next PR to be merged before the previous one ships.

## Out of scope (still)

- Multi-user. Local mode is single-analyst-per-port.
- Database-backed run history. Filesystem repo is sufficient at the
  scale the analyzer is run.
- Hosting/identity. Entra ID + Container Apps is the Option C bridge,
  separate plan.
- Migrating the existing module HTML reports into the SPA. They stay
  side-by-side; the SPA links to them.
- Editing analyzer rule files (compatibility, mapping) from the UI.
  Stays Git-driven.

## Open questions to resolve before PR-1

1. **`.env` write-back UX.** Do we want PUT to *replace* the file (safer
   diff) or *patch* it line-by-line (preserves comments)? Recommendation:
   patch with `python-dotenv`'s `set_key`, fall back to replace if the
   file has merge conflict markers.
2. **Where does the SPA get the API base URL?** Options:
   (a) `VITE_SMA_API_BASE` baked at build time;
   (b) probe `/api/healthz` at startup and toggle dynamically.
   Recommendation: (b) for the bundled flow + (a) for the dev server.
3. **Module rerun granularity.** Should we let the user re-run a single
   failed module against an existing run id (cheap retry), or always
   start a new run? Recommendation: new run only in PR-2; sticky
   "re-run failed modules" as a follow-up after PR-7.

## Definition of done for Phase 5

- [ ] FastAPI backend mounted via `sma serve --with-api`.
- [ ] All endpoints in the contract table above implemented + tested.
- [ ] SPA gains a Configuration page, Run page, Runs (history) page,
      RunDiff page; existing pages unchanged.
- [ ] No secret values ever returned by GET endpoints; verified by
      contract test.
- [ ] CI runs the new `tests/web/` suite + the existing 198-test
      Python suite + Vitest, all green.
- [ ] `quickstart.ps1` documents `--with-api`.
- [ ] CHANGELOG entry under `[Unreleased]` summarising the Option B
      rollout.
