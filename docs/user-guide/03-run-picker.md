# 03. The run picker

The run picker is the small dropdown in the top-right of the header
that appears in **control-plane mode**. It selects which run id every
read-only page (Dashboard, Code objects, Recommendations, Runbook,
Delta) reads from. Get used to it: it is the most touched control in
the UI.

## Layout

```
                                                ┌─────────────────────┐
   …  Diff   Configuration                      │ Run: 2026-05-05_14… │▼
                                                └─────────────────────┘
```

The dropdown lists the most recent 50 runs with id and label. Picking a
new entry:

1. Fetches the run's manifest from `/api/runs/<id>`.
2. Updates the URL hash to `#run=<id>`.
3. Mirrors the value into `sessionStorage` (key `sma.runId`).
4. Triggers every visible page to re-load against the new id.

## Why both URL hash and sessionStorage?

Two failure modes hit the original hash-only design:

- Clicking a `<NavLink>` in the nav pushed a new history entry but
  *replaced* the hash with empty, so navigating from Dashboard →
  Recommendations dropped the run id and showed empty pages.
- A hard reload (`F5`) survived the hash but a browser-tab restore
  did not.

The fix: write the id into both. The URL hash makes the run shareable
("send me your link"), and `sessionStorage` is the source of truth for
intra-session navigation. Closing the tab clears `sessionStorage`, so
your selection does not leak into a fresh session.

## Deep links

Anything of the form

```
http://127.0.0.1:8000/recommendations#run=2026-05-05_142233-some-label
```

is a valid deep link. The SPA bootstraps from the hash, populates the
picker, and renders the requested page directly. This is also the
shape of the link emitted by the **Open** button on the
[Runs history](10-runs-history.md) page.

## Static-deliverable mode has no picker

The static SPA does not have multiple runs to choose from — it reads
`./<module>.json` relative to whichever directory it was served from.
Symptoms of accidentally running the static SPA against a control plane
output: the picker is missing, the **Run / Runs / Diff / Configuration**
tabs are missing, and the page silently uses whichever run shipped with
the bundle.

## Common tasks

### Switch to a previous run

Open the picker, pick the older entry. Every page refreshes against
the older run's JSON. Use this to spot-check what an old finding said.

### Compare two runs

Pick the head run in the picker, then go to
[11. Diff](11-diff-page.md) and pick the base in the **Base** dropdown.
The diff endpoint produces a structural delta, not just a file-level
manifest comparison.

### Share a specific run with a colleague

Copy the URL out of the address bar — the hash carries the id. They
need the same `sma serve --with-api` running locally (the run id
references `runs/<id>/`); for a fully shareable artefact, see
[10. Runs (history)](10-runs-history.md) for the export workflow.

## Related

- [02. Static vs. control-plane mode](02-modes.md)
- [10. Runs (history)](10-runs-history.md)
- [11. Diff](11-diff-page.md)
