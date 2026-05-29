# 14. Security posture

The web control plane is intentionally narrow in scope. It is **not**
a multi-tenant hosted product — it is a developer-style local server,
designed to run on the same host as the analyst, behind whatever
authentication that host already has (OS login, VPN, etc.).

## What is bound where

- `sma serve` (static mode): binds `127.0.0.1` by default. Pure
  read-only file server. No write endpoints.
- `sma serve --with-api` (control-plane mode): also binds `127.0.0.1`
  by default. The API mounts `/api/*`; the SPA is served from the same
  origin. There is **no authentication**.

## How non-loopback hosts are blocked

The server refuses to bind anything other than `127.0.0.1` /
`localhost` unless you pass `--i-know-this-is-not-auth`. The flag's
name is the warning: there is no auth layer, so any reachable network
peer can read / write your `.env` and start arbitrary analyzer runs.

If you need remote access, terminate it at an authenticating reverse
proxy (e.g. an nginx in front of the loopback bind) and never expose
the port to a shared / multi-user host directly.

## CSRF mitigation

State-changing endpoints (`POST` / `PUT` / `DELETE`) require an
`X-SMA-API: 1` HTTP header. The SPA sets it on every fetch; a
drive-by request from a malicious page in another browser tab cannot
set custom headers cross-origin without preflight, so the request is
rejected before any state changes.

This is defence in depth, not a substitute for authentication. Do not
rely on it for anything beyond local-loopback safety.

## Path traversal

- Run identifiers must match `^[a-zA-Z0-9._\-]+$`.
- Module / artefact names must match the allow-listed module set
  (`dedicated_pools`, `serverless_pools`, …).
- Every filesystem operation resolves the target under `--runs-dir`
  and refuses paths that escape it.

## Secret handling

The Configuration page never returns the client secret over the wire.
`GET /api/config` reports `client_secret = "set"` or `"unset"` only.
`PUT /api/config` accepts a new value; submitting an empty string keeps
the existing secret. The browser shows a `<input type="password">` so
the value is not visible on screen and is excluded from autofill.

The secret is written to whichever `.env` the analyzer is configured
to read. The path is shown on the Configuration page (e.g.
`Reads / writes ./.env`).

## Logs and run files

Run files include the analyzer's outputs (JSON / CSV / Markdown / HTML)
plus a `progress.jsonl` event log. None of these contain the client
secret. They do contain workspace metadata (subscription id,
workspace name, dedicated-pool names, schema / table names) — treat
the runs directory as the same sensitivity tier as your `.env`.

## Recommended deployment checklist

- [ ] Bind to `127.0.0.1` only (default).
- [ ] Run on a single-user host (laptop / personal cloud VM).
- [ ] Restrict OS-level access to `runs/` and `.env`.
- [ ] If you need shareable analysis, regenerate as a static
      deliverable (`sma analyze-all --with-webui`) — the static SPA has
      no API and is safe to share.

## Reporting issues

Security issues — including drive-by injection, traversal escapes, or
information disclosure — should be reported via the procedure in
[SECURITY.md](../../SECURITY.md). Do not file them as public GitHub
issues.

## Related

- [02. Static vs. control-plane mode](02-modes.md)
- [12. Configuration](12-configuration.md)
