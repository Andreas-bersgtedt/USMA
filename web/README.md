# web/ — Unified Solution Migration Analyzer SPA

An interactive web front end for the analyzer's JSON outputs. Built as a
single-page app (Vite + React + TypeScript). It runs in two modes:

1. **Static / deliverable mode** (default). The SPA is served alongside
   the JSON files the CLI already produces (e.g. via `sma analyze-all
   --with-webui` + `sma serve` or any static server). No backend, no
   auth, no data egress.
2. **Control-plane mode.** When booted by `sma serve --with-api`, the SPA
   detects the FastAPI backend on the same origin via `GET /api/healthz`
   and unlocks the **Configuration**, **Run**, **Runs**, and **Diff**
   pages. The backend is loopback-only by default; see
   [`PLAN_CONTROL_PLANE.md`](./PLAN_CONTROL_PLANE.md) for the full design.

See [`PLAN.md`](./PLAN.md) for the original Option-A plan and
[`UPGRADE_TO_B.md`](./UPGRADE_TO_B.md) for the historical migration path.

## Quick start

```powershell
# 1. From the repo root, generate analyzer outputs (skip if you already have a run)
.\.venv\Scripts\python.exe -m usma analyze-all `
    --output-dir .\output

# 2. Install dependencies (one-off)
cd web
npm install

# 3. Dev mode: serves the SPA on http://localhost:5173 and exposes JSON
#    files from $SMA_DATA_DIR (defaults to ../output) as Vite's publicDir.
$env:SMA_DATA_DIR = (Resolve-Path ..\output).Path
npm run dev
```

## Production build

```powershell
cd web
npm run build           # emits web/dist/
```

To ship the bundle to an analyst, copy the contents of `web/dist/` into the
analyzer's `output_dir` (next to `fabric_mapping.json`,
`dedicated_pools.json`, `run_delta.json`, …) and serve the directory with
any static server. `file://` is **not** supported because browsers block
`fetch` for local files.

```powershell
# Example: serve <output_dir> on http://localhost:8000
cd .\output
python -m http.server 8000
```

Then open <http://localhost:8000/index.html>.

### Build-time data base

The SPA fetches JSON relative to its own URL by default (`./fabric_mapping.json`,
etc.). If you need a different prefix — for example serving the SPA from
`/ui` and the data from `/api/runs/latest` — set `VITE_SMA_DATA_BASE` at
build time:

```powershell
$env:VITE_SMA_DATA_BASE = "/api/runs/latest/"
npm run build
```

## What it shows today

| Page | Source file | Notes |
| --- | --- | --- |
| Dashboard | `fabric_mapping.json` | readiness score, % T-SQL compatible, capacity SKU, top blockers |
| Code objects | `dedicated_pools.json` | per-object compatibility / parameters / definition / matched rules; client-side filter + sort |
| Recommendations | `fabric_mapping.json` | filter by severity / area, expand for detail |
| Runbook | `fabric_mapping.json` | grouped by phase, ordered by step number |
| Delta | `run_delta.json` | added / removed / changed artefacts and record-count deltas |

Modules without a typed loader yet (governance, security, cost, monitoring,
pipelines, serverless, spark, storage, fabric_validation) can be wired up
by adding a page and calling `loadModule<T>("<name>.json")` from
`src/api/loader.ts`.

## Project layout

```
web/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── PLAN.md                 # full design + scope for Option A
├── UPGRADE_TO_B.md         # migration path to a FastAPI-backed app
└── src/
    ├── main.tsx            # React + router bootstrap
    ├── App.tsx             # nav shell
    ├── styles.css          # tokens + layout (no CSS framework)
    ├── types.ts            # TS DTOs mirroring Pydantic models
    ├── api/loader.ts       # fetch helpers (fabric_mapping / dedicated_pools / run_delta)
    ├── hooks/useAsync.ts   # tiny suspense-free async loader
    ├── components/Atoms.tsx
    └── pages/              # Dashboard / CodeObjects / Recommendations / Runbook / Delta
```

## Security posture

- No data ever leaves the user's machine in this option.
- The SPA performs read-only `fetch`es over the same origin it loads from.
- No cookies, no localStorage of credentials, no telemetry.

## Known limits (static mode)

- No live re-run of the analyzer; the user has to re-run the CLI and refresh.
- No multi-run history; only the current `output_dir` is shown.
- No auth; assumes the local file system is the trust boundary.
- Tables render fully client-side, so very large datasets (>50k rows) will
  be slower than a server-paginated view.

The first three are addressed by **control-plane mode** (`sma serve
--with-api`); see [`PLAN_CONTROL_PLANE.md`](./PLAN_CONTROL_PLANE.md). The
client-side rendering trade-off is shared by both modes.

## Control-plane mode (`sma serve --with-api`)

```powershell
# From the repo root, install the [web] extras (one-off)
pip install -e ".[web]"

# Build the SPA bundle
cd web
npm install
npm run build
cd ..

sma serve --with-api                       # http://127.0.0.1:8000/
```

When the API is reachable, the SPA navigation grows four extra pages:

- **Configuration** — read / write `.env` from the browser. The client
  secret is never returned (only `set` / `unset`).
- **Run** — module checklist + live SSE progress.
- **Runs** — history of all runs with status / duration / readiness.
- **Diff** — compare any two runs using the existing `run_manifest`
  engine.

The selected run id is stored in the URL hash (`#run=<id>`); switching
runs in the topbar picker reloads the SPA so cached loaders refetch from
`/api/runs/<id>/modules/<module>` instead of `./<module>.json`.

Do **not** expose the API on a shared host: it has no authentication. See
the [Security posture in `SECURITY.md`](../SECURITY.md#web-control-plane-sma-serve---with-api).
