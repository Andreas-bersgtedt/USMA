# Security Policy

## Reporting a vulnerability

If you discover a security issue in **Unified Solution Migration Analyzer**, please **do not
open a public GitHub issue**. Instead, report it privately so we can investigate and
ship a fix before details become public.

Use one of the following channels:


- Email: open a confidential GitHub issue requesting contact and the maintainer will
  reach out off-band.

Please include, where possible:

- A description of the issue and its impact (data exposure, privilege escalation,
  denial of service, etc.).
- Reproduction steps or a minimal proof-of-concept.
- The affected version (`pip show synapse-migration-analyzer`) and Python version.
- Any suggested mitigation.

## Response expectations

- **Acknowledgement:** within 5 business days of receipt.
- **Triage + initial assessment:** within 10 business days.
- **Fix or mitigation timeline:** communicated after triage, depending on severity.
- **Public disclosure:** coordinated with the reporter; we credit reporters in the
  advisory unless asked otherwise.

## Scope

In scope:

- Code in this repository (the `usma` Python package and its
  CLI, reports, and bundled SQL queries).
- Default configuration patterns documented in `README.md` / `QUICKSTART.md`.

Out of scope:

- Vulnerabilities in upstream dependencies (Azure SDKs, `pyodbc`, etc.) — please
  report those to the respective maintainers. We will track and bump versions
  reactively.
- The Azure services this tool reads from (Synapse, Storage, Monitor) — those are
  Microsoft's responsibility.
- Issues that require the attacker to already have privileged Azure access
  equivalent to what the tool itself needs to function (Reader + DMV access).

## Web control plane (`sma serve --with-api`)

The optional control plane shipped in v1.3 is a **local-only**, **single-user**
convenience layer. It has no authentication and is not designed to be exposed
on a network. The threat model and mitigations are:

- **Bind address.** Loopback (`127.0.0.1`) only by default. Non-loopback binds
  require an explicit `--i-know-this-is-not-auth` flag.
- **CSRF.** All state-changing requests (`POST` / `PUT` / `DELETE` / `PATCH`)
  must include the `X-SMA-API: 1` header, which is set by the SPA. Browsers
  strip custom headers from cross-origin form posts, so a drive-by site cannot
  forge a request that mutates state.
- **CORS.** Disabled (`allow_origins=[]`); same-origin only.
- **Path traversal.** Run identifiers must match `^\d{8}T\d{6}Z-[0-9a-f]{8}$`,
  module names must match `^[a-z][a-z0-9_]*$`, and every filesystem join is
  resolved under the configured `--runs-dir`.
- **Secret handling.** Reading `/api/config` never returns the client secret —
  only its presence (`set` / `unset`). Writes accept a plaintext secret only
  via `PUT /api/config` and persist it to the same `.env` file the CLI uses,
  with permissions tightened to `0o600` on POSIX hosts.
- **No auth.** There is no user / session model. If you need remote access,
  put the control plane behind your own authenticating reverse proxy or VPN —
  do not expose it directly.

Vulnerabilities in this surface are in scope for [the report process above](#reporting-a-vulnerability).
Deployment scenarios that involve exposing `--with-api` on a shared host without
additional auth are explicitly out of scope; that is documented as unsafe.

## Access surface and reviewer reports

For an exhaustive, version-stamped list of every Azure / Synapse / SQL surface
the analyzer touches — including per-module RBAC, what ends up in output, and
what does not leave the host — see the user-guide chapter
[**17. Tool access & security implications**](docs/user-guide/17-access-and-security.md).

A reviewer-ready Markdown report can be generated locally with no Azure
access:

```
sma access-report --out access-report.md
```

Attach this report to change-advisory tickets when running SMA against a
production workspace.

## Supported versions

Only the latest released version on `main` receives security fixes.
