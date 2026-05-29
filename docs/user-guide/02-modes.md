# 02. Static vs. control-plane mode

The same React SPA serves two very different audiences. The first time
you open the UI, the SPA pings `GET /api/healthz`; if the call succeeds
it switches into **control-plane mode**, otherwise it stays in
**static-deliverable mode**. Everything visible in the browser flows
from that decision.

## Decision tree

```
Do you have output/ and want to read it offline?
   └─ yes → sma serve --output-dir output    (static mode)

Do you want to start runs / edit .env from the browser?
   └─ yes → pip install -e ".[web]"
            sma serve --with-api             (control-plane mode)

Are you handing the analysis to someone else?
   └─ yes → sma analyze-all --with-webui     (static mode, copies SPA bundle)
            zip output → email
```

## Side-by-side

| Surface                         | Static (`sma serve`)         | Control plane (`sma serve --with-api`) |
| ------------------------------- | ---------------------------- | -------------------------------------- |
| Source of data                  | `output/<module>.json`       | `runs/<id>/<module>.json` via API      |
| Run picker                      | not shown                    | dropdown in top-right, hash-persisted  |
| Top-level pages                 | 9 (Dashboard, Code objects, Recommendations, Runbook, Delta, Cost, Governance, Security, Help) | 13 (Overview, Dashboard, Code objects, Recommendations, Runbook, Cost, Governance, Security, Run, Runs, Diff, Configuration, Help — Delta is replaced by Diff, Overview is the new landing page) |
| Can start runs?                 | no                           | yes (`POST /api/runs`)                 |
| Can edit `.env`?                | no                           | yes (`PUT /api/config`)                |
| Live progress                   | n/a                          | Server-Sent Events at `/api/runs/<id>/events` |
| Diff endpoint                   | reads `run_delta.json`       | full pairwise via `/api/runs/<a>/diff/<b>` |
| Refresh-safe deep links         | yes                          | yes (URL hash + sessionStorage)        |
| Authentication                  | n/a (read-only files)        | none — loopback-only by default        |
| Bind address default            | `127.0.0.1`                  | `127.0.0.1`                            |
| Pip extras required             | none                         | `[web]`                                |
| Node.js / npm required          | only to build the SPA bundle | same                                   |

## Layout: top nav

Static deliverable mode:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Unified Solution Migration Analyzer                                 │
│                                                                     │
│  [ Dashboard ] [ Code objects ] [ Recommendations ] [ Runbook ]     │
│  [ Delta ]     [ Cost ]         [ Governance ]      [ Security ]    │
│  [ Help ]                                                           │
└─────────────────────────────────────────────────────────────────────┘
```

Control plane mode:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Unified Solution Migration Analyzer                      [run picker]│
│                                                                     │
│  [ Overview ]  [ Dashboard ]    [ Code objects ] [ Recommendations ]│
│  [ Runbook ]   [ Cost ]         [ Governance ]   [ Security ]       │
│  [ Run ]       [ Runs ]         [ Diff ]         [ Configuration ]  │
│  [ Help ]                                                           │
└─────────────────────────────────────────────────────────────────────┘
```

Notes:
- Control-plane mode replaces the static **Delta** page with the live
  **Diff** page (full pairwise diff via the API).
- **Overview** is the new landing page in control-plane mode and rolls
  up *every run on disk* across workspaces / subscriptions / tenants.
  See [16. Estate overview](16-overview.md).

The four control-plane-only routes (`/run`, `/runs`, `/diff`,
`/configuration`) are **not registered** when the API is missing — if
you bookmark them and reload against a static server they fall back to
the Dashboard.

## When to pick which

- **Doing remediation work day-to-day on a single Synapse workspace** →
  control-plane mode. You will start runs frequently, you want
  side-by-side diffs, and editing `.env` from the browser is faster than
  reopening the file.
- **Sharing the analysis with stakeholders who do not have Azure
  access** → static mode. `analyze-all --with-webui` produces a fully
  self-contained directory; zip it, hand it over, no install required.
- **Both** — there is no conflict. The control-plane SPA writes the
  exact same JSON shape into `runs/<id>/`; copy that directory to
  produce a static deliverable for any specific run.

## Mode detection details

The SPA caches the API ping for the lifetime of the page load. If you
start the API while the SPA is open, refresh the page (or hard reload —
`Ctrl+Shift+R`) for the new pages to appear in the nav. Conversely, if
the API stops responding mid-session the existing pages keep working
from their already-fetched JSON; navigation to a control-plane page
will surface an *Error: …* card.

## Related

- [01. Getting started](01-getting-started.md)
- [03. The run picker](03-run-picker.md)
- [14. Security posture](14-security.md)
