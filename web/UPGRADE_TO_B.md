# Upgrade path: Option A → Option B

This document describes how the static SPA scaffolded in `web/` evolves
into a **local web app with on-demand analyzer runs** (FastAPI + the same
SPA). The promise is: **the SPA does not need to be rewritten.** Only the
data base URL changes, and a few UI affordances are added on top.

## End state for Option B

```
   ┌──────────────────────┐
   │ Browser (analyst)    │
   │ React SPA            │ ◀──────── HTTP / SSE
   └──────────┬───────────┘
              │ /api/runs, /api/runs/{id}, /api/runs/{id}/modules/...
              ▼
   ┌──────────────────────┐    spawns   ┌────────────────────────┐
   │ FastAPI (uvicorn)    │ ──────────▶ │ analyzer worker        │
   │ - /api/* endpoints   │             │ (synapse_migration_    │
   │ - SSE / WebSocket    │ ◀────────── │  analyzer.cli funcs)   │
   │   for run progress   │  artefacts  └────────────────────────┘
   └──────────┬───────────┘
              │ reads/writes
              ▼
   ┌──────────────────────┐
   │ runs/                │   <- per-run directory tree
   │   ├ <id>/             │      identical to today's output_dir
   │   │   ├ fabric_mapping.json
   │   │   ├ dedicated_pools.json
   │   │   └ ...
   │   └ <id>/run.json    │      run metadata: started_at, status, config hash
   └──────────────────────┘
```

## What carries over unchanged

- **Every page in `web/src/pages/`.** They each call functions in
  `src/api/loader.ts`; only that file changes.
- **`types.ts`** — same Pydantic-mirrored DTOs.
- **`styles.css`**, layout, atoms.
- **Build pipeline** (`npm run build` -> `web/dist/`).

## What changes

### 1. Data loader becomes run-aware

`src/api/loader.ts` is the only file that changes structurally. Today:

```ts
const BASE = import.meta.env.VITE_SMA_DATA_BASE ?? "./";
async function fetchJson<T>(name: string) { ... fetch(BASE + name) ... }
export const loadFabricMapping = () => fetchJson<...>("fabric_mapping.json");
```

Becomes:

```ts
const API = import.meta.env.VITE_SMA_API_BASE ?? "/api";

export async function listRuns(): Promise<RunMeta[]> { ... }

export function loadFabricMapping(runId = "latest") {
  return fetchJson<FabricMappingReport>(`${API}/runs/${runId}/modules/fabric_mapping`);
}
```

The page components call `loadFabricMapping()` exactly like today and
default to `"latest"`. To browse historical runs the SPA gets a run
selector in the topbar (one new `<select>`), passing the chosen run id
through React Router state or URL params.

### 2. New backend (Python, FastAPI)

A new package `src/usma/web/` provides:

```
web/
  __init__.py
  app.py            # FastAPI app factory
  api/
    runs.py         # POST /runs (start), GET /runs, GET /runs/{id}
    modules.py      # GET /runs/{id}/modules/{module}
    schema.py       # GET /schema/{module}  -> Pydantic JSON schema
    health.py       # GET /healthz
  jobs.py           # in-process job queue (asyncio + threadpool)
  storage.py        # filesystem-backed run repo (no DB needed in B)
```

Endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/runs` | List runs (newest first), paged. |
| `GET` | `/api/runs/{id}` | Run metadata (status, config hash, errors). |
| `POST` | `/api/runs` | Start a run with a JSON body that maps to `AppConfig`. Returns `{ id, status: "queued" }`. |
| `GET` | `/api/runs/{id}/events` | Server-Sent Events stream of progress updates while a run is in flight. |
| `GET` | `/api/runs/{id}/modules/{module}` | Returns the JSON for that module. Equivalent to today's `<output_dir>/<module>.json`. |
| `GET` | `/api/schema/{module}` | Pydantic `model_json_schema()` for that module. Useful for SPA tooling. |
| `GET` | `/healthz` | Liveness. |

#### Reusing the existing analyzer

The CLI already has a thin entry point per module (`analyzer.run()` on
each module's `Analyzer` class) and an aggregator `FabricMappingAnalyzer`.
The web backend will:

1. Build an `AppConfig` from the request body (Pydantic validation = free).
2. Call the same module functions inside a `run_in_threadpool` to keep the
   event loop responsive.
3. Emit SSE messages as each module completes (the analyzer already logs
   per-module progress; we wrap that with a `Queue` + adapter logger).
4. Persist outputs under `runs/<id>/`.

No analyzer code is rewritten — we add a `web/` wrapper that imports the
existing CLI/analyzer functions.

### 3. CLI hook

```bash
sma serve --runs-dir .\runs --port 8080 [--host 127.0.0.1]
```

Starts uvicorn with the FastAPI app and serves `web/dist/` at `/`. Single
command, single port, no reverse proxy.

### 4. New SPA features unlocked

These are additive — they don't replace any existing page.

- **Run selector** in the topbar (URL: `/?run=<id>` etc.).
- **Run history** page listing all `runs/*` with status + readiness score
  trend (sparkline from each run's `fabric_mapping.json#readiness.score`).
- **"New run" wizard** that posts to `POST /api/runs`. Driven entirely by
  the schemas served from `/api/schema/{module}`.
- **Live progress panel** subscribing to `/api/runs/{id}/events`.
- **Diff view** between two runs by feeding the existing `run_delta`
  generator with two run ids server-side.

## Security & secret handling for Option B

The leap from Option A to B is that the backend now needs **credentials**
to call Azure (Synapse, ARM, Cost Management, SQL DMVs).

- **Local-only by default.** `sma serve` binds `127.0.0.1`. No CORS, no
  external network.
- **Re-use existing auth flow.** The analyzer already supports
  `DefaultAzureCredential` (managed identity / az CLI / VS Code account)
  and explicit SP creds via env. The web backend reads the same env vars.
  **Never accept credentials in the request body.** The "New run" wizard
  only collects subscription / RG / workspace / module flags; auth is
  done by the server-side credential chain.
- **No persistence of secrets.** `AppConfig` is hashed (sans secrets) per
  run for diffing, but the secret values never land in `runs/*`.
- **CSRF.** Default uvicorn config + same-origin SPA + SameSite=Strict
  cookies (if we add session cookies later for an Entra-ID hosted
  variant) handle this. For pure localhost, CSRF is moot.
- **Concurrency.** One run at a time per worker (analyzer is IO-heavy and
  rate-limited by ARM); enforce with an `asyncio.Semaphore(1)`.
- **Path traversal** in the static-asset and module routes — use
  `pathlib.Path` + `is_relative_to(runs_dir)` checks. Never join
  user-supplied strings into filesystem paths without validation.

## Migration steps

Each step is small and shippable.

1. **Schema export endpoint** (no UI change).
   Add `GET /api/schema/{module}` that returns Pydantic JSON Schema for
   all module DTOs. Useful even before the SPA changes; lets us
   auto-generate `types.ts` in CI.

2. **Read-only `runs/` browser**.
   FastAPI app exposing only `GET /api/runs` and
   `GET /api/runs/{id}/modules/{module}`. SPA gains a topbar run selector
   and the loader switches to the API. No "new run" yet — analyzers are
   still triggered via CLI. Buys most of the perceived value.

3. **`POST /api/runs` with SSE progress**.
   New "Run analyzer" page in the SPA. Backend spawns a thread, streams
   progress via SSE. Analyzer code unchanged.

4. **Run history + diffing UI**.
   Two-run picker that calls `GET /api/runs/{a}/diff/{b}` (server-side
   wraps the existing `run_manifest` delta logic).

5. **Optional: Entra ID hosted mode**.
   Drop the SPA + FastAPI behind an Azure Container App with Easy Auth.
   This is the bridge to Option C; requires per-tenant managed identity
   work that we deliberately keep separate.

## What this **won't** give you

These are Option C territory and shouldn't be hacked into B:

- Multi-tenant isolation.
- Persistent (database-backed) run history with retention policies.
- Scheduled runs and notifications.
- Customer-managed identity per tenant.
- SLA / uptime guarantees.

If those become requirements, jump to Option C — don't keep growing B.

## TL;DR

| Concern | Option A (today) | Option B |
| --- | --- | --- |
| Trigger runs from UI | ❌ | ✅ |
| Run history | ❌ (latest only) | ✅ (filesystem) |
| Auth | n/a | localhost-only or Entra ID |
| Backend | none | FastAPI single-process |
| New code | none | `src/usma/web/` (~ small) |
| SPA changes | none | one new loader, one run selector, optional new pages |
| Time-to-ship | days | weeks |
