# 15. FAQ

## Why are the **Run / Runs / Diff / Configuration** tabs missing?

You are in [static-deliverable mode](02-modes.md). Those four pages
require the FastAPI control plane. Either:

- start the server with `sma serve --with-api`, or
- if you only have an `output/` directory, you cannot start runs from
  the browser — use the CLI (`sma analyze-all`) and refresh.

## The Dashboard is empty / shows "No data"

The SPA needs `fabric_mapping.json` to render. Run the aggregator:

```powershell
sma analyze-all                       # writes fabric_mapping.json
sma map-to-fabric                     # rebuild only the aggregator from existing module JSON
```

If only `fabric_mapping.json` is missing the page shows an *Empty*
card; if the analyzer ran but produced no recommendations the cards
show zeros (which is itself meaningful).

## The Storage / Pipeline-activity sections do not appear on the Dashboard

Those sections render only when `storage.json` and `pipelines.json`
exist for the selected run. If you ran a partial subset (e.g.
`sma analyze-dedicated-pools` only), nothing went wrong — there is
just no data to show. Run the missing modules:

```powershell
sma analyze-storage
sma analyze-pipelines
```

## My run finished but the pages are still empty

You probably did not pick the new run in the [run picker](03-run-picker.md).
The Run page auto-selects the new run id, but if you navigated away
mid-run and came back, the picker still points at whatever was
selected before. Open the picker and pick the latest entry.

## The browser shows "Connection refused" / blank page

Check that the server is still running:

```powershell
curl.exe http://127.0.0.1:8000/api/healthz
# {"status":"ok","version":"<current release>"}
```

If `quickstart.ps1` was your launcher, look in the foreground terminal
where you ran it — the server runs there until you Ctrl+C.

## Can I expose the UI on my LAN?

Technically yes, with `--i-know-this-is-not-auth --host 0.0.0.0`. You
should not — there is no authentication. Read
[14. Security posture](14-security.md) before you do.

## How do I share a specific run with someone?

The fastest path: copy the run directory to a stand-alone deliverable
and serve it statically.

```powershell
Copy-Item -Recurse runs\2026-05-05_142233-some-label output_export
Copy-Item -Recurse web\dist output_export\webui
sma serve --output-dir output_export
```

Or rerun the analyzer with `--with-webui` and zip the resulting
`output/`.

## Where are credentials stored?

`.env` at the path shown on the [Configuration page](12-configuration.md).
The control plane never returns the client secret over the wire — you
will only ever see `set` / `unset`.

## Can I use the SPA without the API for a hosted demo?

Yes. The static deliverable mode is the answer:

```powershell
sma analyze-all --with-webui
sma serve --output-dir output           # or your own static host
```

The result is a directory of plain HTML / JS / JSON. Drop it on
GitHub Pages, Azure Static Web Apps, an internal blob, anything that
serves files. The SPA has no API requirements in that mode.

## How long are runs retained?

Forever, until you delete them. Runs live under `--runs-dir` (default
`./runs/`). Use the **Delete** action on the
[Runs history page](10-runs-history.md) to remove individual entries,
or `Remove-Item -Recurse runs\<id>` from PowerShell.

## How do I bump versions / regenerate types after pulling?

```powershell
git pull
.\quickstart.ps1 -SkipClone -SkipDoctor
# rebuilds .venv, reinstalls all extras, rebuilds web/dist
```

If you only want to refresh the SPA bundle:

```powershell
cd web; npm install; npm run build; cd ..
```

## Is there an API doc?

Yes — `web/PLAN_CONTROL_PLANE.md` is the reference. Live OpenAPI is
also exposed at <http://127.0.0.1:8000/api/docs> when the API is
running.

## Related

- [13. Troubleshooting](13-troubleshooting.md) — diagnostic recipes
- [14. Security posture](14-security.md)
