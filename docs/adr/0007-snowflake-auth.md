# ADR-0007: Snowflake authentication — OAuth refresh-token over key-pair JWT

* **Status:** Accepted (Phase 7)
* **Date:** 2026-05
* **Supersedes:** —
* **Superseded-by:** —

## Context

Phase 7 adds Snowflake as a first-class USMA source. The
`SnowflakeProvider` needs to open a connection to a Snowflake account
on every analyzer step (`SHOW WAREHOUSES`, `SELECT … FROM
ACCOUNT_USAGE.QUERY_HISTORY`, `METERING_HISTORY` reads in the cost
client, etc.).

Snowflake supports four authentication mechanisms for programmatic
clients:

1. **Username + password** — deprecated 2024-Q4 for service
   identities, blocked by MFA in most enterprise tenants.
2. **Username + password + MFA (Duo push)** — interactive only.
3. **Key-pair JWT** — generate an RSA keypair, register the public key
   on `ALTER USER … SET RSA_PUBLIC_KEY=…`, sign a JWT for each
   connection.
4. **OAuth** — either an external authorization server (Entra,
   Okta, Auth0) via the External-OAuth integration, or Snowflake's
   built-in OAuth integration that ships with every account.

Pre-7-E.1, an early Slice 7-E prototype used **key-pair JWT** because
it's the documented "service account" pattern in the Snowflake
connector docs. That code was removed in Slice 7-E.1 in favour of
the built-in OAuth path.

## Decision

**Use Snowflake's built-in OAuth security integration with the
refresh-token grant.** The provider expects six env vars
(`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_OAUTH_CLIENT_ID`,
`SNOWFLAKE_OAUTH_CLIENT_SECRET`, `SNOWFLAKE_OAUTH_REFRESH_TOKEN`,
plus `SNOWFLAKE_WAREHOUSE` for the run-time warehouse pick) and mints
a fresh access token via the refresh-token grant on every connection.

The SPA Configuration page exposes a **Sign in to Snowflake** button
(Slice 7-E.2) that runs the refresh-token flow against a one-shot
localhost listener at `http://localhost:53682/` (byte-exact match
against `OAUTH_REDIRECT_URI` — Snowflake does not wildcard hosts or
ports). The resulting refresh token is persisted in the same `.env`
that backs the rest of the SPA's config IO.

## Why not key-pair JWT?

Rejected after Slice 7-E.1 review because:

* **Key-rotation hostility.** Rotating an RSA keypair requires
  `ALTER USER`-level access on the service identity, which is
  typically gated behind a 4-eyes change request in the same change
  windows the analyzer is supposed to bypass.
* **Per-platform PEM handling.** PEM files on Windows hosts trip over
  CRLF normalisation; the SPA had to ship a textarea-with-passphrase
  pair whose write-only contract was indistinguishable from a
  password and confused operators.
* **No standard MFA story.** Key-pair JWT skips MFA entirely; many
  enterprise security teams refuse to whitelist the auth method
  because it can't be combined with a step-up challenge.
* **Limited reuse.** The same Snowflake account often already has an
  OAuth integration set up for Streamlit / Tableau / SnowSQL — USMA
  joining that integration is a simpler audit story than introducing
  a new RSA keypair next to it.

## Why not External OAuth (Entra / Okta)?

Deferred. External OAuth has a richer audit trail and works with
existing identity providers, but every Snowflake account needs a
custom security integration object plus IdP-side app registration,
which is far heavier than the "create one OAuth integration once" cost
of the built-in flow. We may revisit in a later phase if customers
ask for SSO-driven scope discovery; the provider's
`_resolve_connect_kwargs` helper is the only seam that would need to
change.

## Why not username + password + MFA?

* **Service identities can't push MFA.** Snowflake 2024-Q4 deprecation
  blocks programmatic password auth for users not flagged
  `SERVICE`. Flagging them `SERVICE` removes the MFA gate, which
  defeats the security posture the customer's IdP team enforces.

## Why not Personal Access Tokens?

Snowflake's PAT preview (2025) is still account-locked to the
issuing user, expires on a non-configurable 90-day rotation, and
ships **no audit metadata** beyond `LAST_USED`. The same data is
available via `OAUTH_INTEGRATION_USAGE_HISTORY` for the OAuth flow
with vendor / client / scope breakdown, which our security review
team preferred.

## Consequences

### Positive

* **One env-var family.** The OAuth client id + secret + refresh
  token are all string-only, persistable in `.env`, no PEM
  text-area UI.
* **Reuses existing OAuth integration object.** Customers who
  already integrated Tableau / Streamlit via Snowflake OAuth point
  USMA at the same integration; no new account-level objects.
* **Browser sign-in works the same as BigQuery's Slice 5-I flow.**
  The user pattern is consistent across BigQuery (Google ADC) and
  Snowflake (Snowflake OAuth) — same SPA chrome, same single-flight
  state machine.
* **Refresh tokens last up to 90 days.** Less rotation churn than
  PATs (also 90 days, but per-user) and far less than key-pair
  expiry policies in regulated tenants.

### Negative

* **Redirect-URI byte-exact match.** Snowflake doesn't wildcard the
  redirect URI, so the listener port is pinned to `53682` (override
  via `SNOWFLAKE_OAUTH_REDIRECT_URI`). The customer must
  `ALTER SECURITY INTEGRATION usma_oauth SET OAUTH_REDIRECT_URI = …`
  to match. This is documented in
  [`docs/user-guide/23-snowflake.md`](../user-guide/23-snowflake.md).
* **Access tokens are short-lived (~10 minutes).** Provider's
  `connect()` callable mints a new token on every call rather than
  caching, so very-frequent (sub-minute) analyzer loops trip the
  Snowflake OAuth rate limit; in practice the cost / workloads
  modules run a few queries per scope per cycle so this hasn't been
  observed.
* **Customer ACCOUNTADMIN involvement once.** Setting up the
  security integration is a one-time ACCOUNTADMIN-grade DDL. We
  document this as part of the [Snowflake user guide](../user-guide/23-snowflake.md#one-time-snowflake-setup-accountadmin).
