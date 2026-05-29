"""SnowflakeProvider — Phase-7 Slice 7-E.1 (OAuth rewire).

Discovers a Snowflake account, validates connectivity via a Snowflake
built-in OAuth integration (refresh-token grant), and produces a
:class:`SnowflakeClientBundle` that downstream modules use to mint
short-lived ``snowflake.connector`` connections.

Auth model — Snowflake built-in OAuth (refresh-token grant)
-----------------------------------------------------------
The Snowflake admin creates a custom OAuth security integration
(``CREATE SECURITY INTEGRATION … TYPE = OAUTH OAUTH_CLIENT = CUSTOM
OAUTH_CLIENT_TYPE = 'CONFIDENTIAL' OAUTH_ISSUE_REFRESH_TOKENS = TRUE``).
Out-of-band, a human walks the authorisation-code flow once to mint a
long-lived ``refresh_token``. USMA then runs headlessly: at connect
time we exchange the refresh token for a short-lived access token via
``POST https://<account>.snowflakecomputing.com/oauth/token-request``
and hand the access token to the connector with
``authenticator='oauth'`` + ``token=<access_token>``.

Snowflake's built-in OAuth integration does **not** support the
``client_credentials`` grant — that grant exists only on External
OAuth (Entra / Okta). The refresh-token flow is the canonical
headless pattern for built-in OAuth and is functionally equivalent for
batch analyzers: one-time human consent, then long-lived unattended
operation.

Required credential bits (sourced from ``Credentials.extras`` — the
Azure-shaped ``tenant_id``/``client_id``/``client_secret`` fields are
ignored):

* ``snowflake_account`` — ``<orgname>-<accountname>``
* ``snowflake_user`` — the user the OAuth integration binds tokens to
* ``snowflake_oauth_client_id`` — the integration's client id
* ``snowflake_oauth_client_secret`` — the integration's client secret
* ``snowflake_oauth_refresh_token`` — long-lived refresh token minted
  out-of-band
* ``snowflake_role`` — defaults to ``"PUBLIC"``; production runs should
  use a role with ``USAGE`` on ``SNOWFLAKE.ACCOUNT_USAGE`` (see
  Slice 7-G docs)
* ``snowflake_warehouse`` — warehouse the analyzer runs its own queries
  against

Optional escape hatch:

* ``snowflake_oauth_token`` — a pre-minted short-lived access token. If
  set, we skip the refresh-token exchange and pass the token straight
  through. Short-lived (~10 min); intended for CI / tests / break-glass.

Falls back to the ``SNOWFLAKE_*`` env vars when extras are absent.

Descriptors
~~~~~~~~~~~
The unit of scope for Snowflake is the **account**. ``SourceDescriptor.id``
is the account locator (e.g. ``"acme-prod"``); ``display_name`` mirrors
it; ``subscription_id``/``resource_group``/``location`` stay unset.
``extras`` carries:

* ``platform`` — ``"aws" | "azure" | "gcp"`` (mirrors Databricks 4.7;
  sniffed from ``CURRENT_REGION()`` or pinned via
  ``SMA_SNOWFLAKE_PLATFORM``)
* ``region`` — the raw region string returned by ``CURRENT_REGION()``
  (e.g. ``"AWS_US_EAST_1"``)
* ``edition`` — Snowflake edition pulled from
  ``SNOWFLAKE.ACCOUNT_USAGE.ACCOUNT_PROPERTIES`` when accessible
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider


_REGION_PREFIX_TO_PLATFORM: dict[str, str] = {
    "AWS": "aws",
    "AZURE": "azure",
    "GCP": "gcp",
}

_TOKEN_EXCHANGE_TIMEOUT_SECS = 30.0


@dataclass(frozen=True)
class SnowflakeClientBundle:
    """Connection factory + identity needed by every Snowflake module.

    ``connect`` is a zero-arg callable that opens a fresh
    ``snowflake.connector.SnowflakeConnection`` with a freshly-minted
    OAuth access token, account, user, role, and warehouse. Callers
    are responsible for closing the connection (use
    ``contextlib.closing`` or a ``with`` block).
    """

    connect: Callable[[], Any]
    account: str
    user: str
    role: str
    warehouse: str
    platform: str
    region: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class SnowflakeProvider(BaseSourceProvider):
    """Discovers a Snowflake account and validates OAuth access."""

    type = SourceType.SNOWFLAKE
    display_name = "Snowflake"
    required_env: tuple[str, ...] = (
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_OAUTH_CLIENT_ID",
        "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "SNOWFLAKE_OAUTH_REFRESH_TOKEN",
        "SNOWFLAKE_WAREHOUSE",
    )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(
        self,
        creds: Credentials | None = None,
        subscription_id: str | None = None,  # noqa: ARG002 — no ARM concept
    ) -> list[SourceDescriptor]:
        """Return a single descriptor for the configured Snowflake account.

        Unlike Synapse / ADF / Databricks-on-Azure (which enumerate
        every workspace in a subscription) or BigQuery (which enumerates
        every project visible to ADC), a Snowflake "scope" is the
        **single account** the credentials authorise. We mint an access
        token, open a connection, run ``CURRENT_REGION()`` to fill the
        cloud-platform discriminator, best-effort probe the edition,
        and return one :class:`SourceDescriptor`.
        """
        resolved = _resolve_connect_kwargs(creds)
        account = resolved["account"]
        with _closing(_open_connection(resolved)) as conn:
            region, platform = _probe_region(conn)
            edition = _probe_edition(conn)
        extras: dict[str, Any] = {"platform": platform}
        if region:
            extras["region"] = region
        if edition:
            extras["edition"] = edition
        return [SourceDescriptor(
            type=SourceType.SNOWFLAKE,
            id=account,
            display_name=account,
            extras=extras,
        )]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials | None = None,
    ) -> list[ConfigCheck]:
        """Three live probes: OAuth token exchange, ``CURRENT_VERSION()``,
        and ``ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`` reachability.
        Symmetric with :meth:`BigQueryProvider.validate`.
        """
        checks: list[ConfigCheck] = []

        try:
            resolved = _resolve_connect_kwargs(creds)
            checks.append(ConfigCheck(
                name="Snowflake OAuth token resolution",
                ok=True,
                detail=f"account={resolved['account']}, user={resolved['user']}",
                category="Control plane",
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Snowflake OAuth token resolution",
                ok=False,
                detail=str(exc),
                category="Control plane",
            ))
            return checks

        try:
            conn = _open_connection(resolved)
        except ImportError as exc:
            checks.append(ConfigCheck(
                name="Snowflake connector",
                ok=False,
                detail=(
                    f"snowflake-connector-python not importable: {exc}. "
                    "Install the [snowflake] extra."
                ),
                category="Data plane",
            ))
            return checks
        except Exception as exc:  # noqa: BLE001
            checks.append(ConfigCheck(
                name="Snowflake connector",
                ok=False,
                detail=str(exc),
                category="Data plane",
            ))
            return checks

        with _closing(conn):
            try:
                version = _scalar(conn, "SELECT CURRENT_VERSION()")
                checks.append(ConfigCheck(
                    name="Snowflake CURRENT_VERSION()",
                    ok=True,
                    detail=f"version: {version}",
                    category="Data plane",
                ))
            except Exception as exc:  # noqa: BLE001
                checks.append(ConfigCheck(
                    name="Snowflake CURRENT_VERSION()",
                    ok=False,
                    detail=str(exc),
                    category="Data plane",
                ))

            try:
                _scalar(
                    conn,
                    "SELECT 1 FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY LIMIT 1",
                )
                checks.append(ConfigCheck(
                    name="SNOWFLAKE.ACCOUNT_USAGE access",
                    ok=True,
                    detail="WAREHOUSE_METERING_HISTORY readable",
                    category="Data plane",
                ))
            except Exception as exc:  # noqa: BLE001
                checks.append(ConfigCheck(
                    name="SNOWFLAKE.ACCOUNT_USAGE access",
                    ok=False,
                    detail=str(exc),
                    category="Data plane",
                ))

        return checks

    # ------------------------------------------------------------------
    # Client bundle
    # ------------------------------------------------------------------
    def make_clients(
        self,
        descriptor: SourceDescriptor,
        creds: Credentials | None = None,
    ) -> SnowflakeClientBundle:
        """Return a :class:`SnowflakeClientBundle` whose ``connect()``
        opens a fresh connection on each call. Each call mints a new
        short-lived access token via the refresh-token grant.
        """
        if not descriptor.id:
            raise ValueError(
                f"descriptor {descriptor.display_name!r} is missing id (Snowflake account)",
            )
        platform = str(descriptor.extras.get("platform") or _platform_from_env() or "aws")
        region = descriptor.extras.get("region")
        # Resolve once up-front so missing-creds errors surface here
        # rather than inside the lazy ``connect()`` closure; capture
        # the account locator + user for the bundle metadata.
        snapshot = _resolve_connect_kwargs(creds, account_override=descriptor.id)

        def _connect() -> Any:
            # Re-resolve on every call so the access token is fresh
            # (default Snowflake OAuth access tokens expire after ~10
            # minutes — analyzer passes that span longer than the
            # token TTL still work because each new connection mints a
            # new token).
            fresh = _resolve_connect_kwargs(creds, account_override=descriptor.id)
            return _open_connection(fresh)

        return SnowflakeClientBundle(
            connect=_connect,
            account=snapshot["account"],
            user=snapshot["user"],
            role=snapshot.get("role") or "PUBLIC",
            warehouse=snapshot.get("warehouse") or "",
            platform=platform,
            region=str(region) if region else None,
            extras=dict(descriptor.extras),
        )


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _resolve_connect_kwargs(
    creds: Credentials | None,
    account_override: str | None = None,
) -> dict[str, Any]:
    """Build kwargs for ``snowflake.connector.connect()``.

    Reads ``Credentials.extras`` first; falls back to ``SNOWFLAKE_*``
    env vars. Raises :class:`RuntimeError` listing missing fields when
    the contract isn't satisfied.

    If ``snowflake_oauth_token`` is present (extras or env var) we use
    it directly as the access token and skip the refresh-token
    exchange. Otherwise we POST the refresh token to the account's
    ``/oauth/token-request`` endpoint and use the resulting
    ``access_token``.
    """
    extras: dict[str, Any] = dict(creds.extras) if (creds and creds.extras) else {}

    def _pick(extras_key: str, env_key: str) -> str | None:
        val = extras.get(extras_key)
        if val:
            return str(val).strip() or None
        env_val = os.environ.get(env_key, "").strip()
        return env_val or None

    account = account_override or _pick("snowflake_account", "SNOWFLAKE_ACCOUNT")
    user = _pick("snowflake_user", "SNOWFLAKE_USER")
    warehouse = _pick("snowflake_warehouse", "SNOWFLAKE_WAREHOUSE")
    role = _pick("snowflake_role", "SNOWFLAKE_ROLE") or "PUBLIC"

    pre_minted = _pick("snowflake_oauth_token", "SNOWFLAKE_OAUTH_TOKEN")
    if pre_minted:
        missing = [
            name for name, val in (
                ("SNOWFLAKE_ACCOUNT", account),
                ("SNOWFLAKE_USER", user),
                ("SNOWFLAKE_WAREHOUSE", warehouse),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                "Snowflake OAuth (pre-minted token) auth requires: "
                + ", ".join(missing)
                + " (provide via Credentials.extras snowflake_* keys or env vars).",
            )
        return {
            "account": account,
            "user": user,
            "authenticator": "oauth",
            "token": pre_minted,
            "role": role,
            "warehouse": warehouse,
        }

    client_id = _pick("snowflake_oauth_client_id", "SNOWFLAKE_OAUTH_CLIENT_ID")
    client_secret = _pick("snowflake_oauth_client_secret", "SNOWFLAKE_OAUTH_CLIENT_SECRET")
    refresh_token = _pick("snowflake_oauth_refresh_token", "SNOWFLAKE_OAUTH_REFRESH_TOKEN")

    missing = [
        name for name, val in (
            ("SNOWFLAKE_ACCOUNT", account),
            ("SNOWFLAKE_USER", user),
            ("SNOWFLAKE_OAUTH_CLIENT_ID", client_id),
            ("SNOWFLAKE_OAUTH_CLIENT_SECRET", client_secret),
            ("SNOWFLAKE_OAUTH_REFRESH_TOKEN", refresh_token),
            ("SNOWFLAKE_WAREHOUSE", warehouse),
        ) if not val
    ]
    if missing:
        raise RuntimeError(
            "Snowflake OAuth auth requires: "
            + ", ".join(missing)
            + " (provide via Credentials.extras snowflake_* keys or env vars; "
            "or set SNOWFLAKE_OAUTH_TOKEN to use a pre-minted access token).",
        )

    access_token = _exchange_refresh_token(
        account=account,  # type: ignore[arg-type]
        client_id=client_id,  # type: ignore[arg-type]
        client_secret=client_secret,  # type: ignore[arg-type]
        refresh_token=refresh_token,  # type: ignore[arg-type]
    )
    return {
        "account": account,
        "user": user,
        "authenticator": "oauth",
        "token": access_token,
        "role": role,
        "warehouse": warehouse,
    }


def _exchange_refresh_token(
    *,
    account: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str:
    """Trade a refresh token for a short-lived access token.

    Snowflake's built-in OAuth token endpoint lives at
    ``https://<account>.snowflakecomputing.com/oauth/token-request``;
    the response is JSON with ``access_token`` / ``token_type`` /
    ``expires_in``. Raises :class:`RuntimeError` on HTTP or parse
    failure with the response body included for triage.
    """
    url = f"https://{account}.snowflakecomputing.com/oauth/token-request"
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(  # nosec B310 - https Snowflake endpoint
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(  # nosec B310 - https Snowflake endpoint
            req, timeout=_TOKEN_EXCHANGE_TIMEOUT_SECS,
        ) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Snowflake OAuth token-request failed against {url!r}: {exc}",
        ) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Snowflake OAuth token-request returned non-JSON body: {raw!r}",
        ) from exc

    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError(
            f"Snowflake OAuth token-request returned no access_token: {payload!r}",
        )
    return str(access_token)


def _open_connection(connect_kwargs: dict[str, Any]) -> Any:
    """Thin wrapper around ``snowflake.connector.connect()`` so tests
    can stub it via this single symbol."""
    try:
        import snowflake.connector as sc
    except ImportError as exc:
        raise ImportError(
            "The Snowflake source requires the optional '[snowflake]' "
            "extra. Install it with: pip install -e \".[snowflake]\" "
            "(adds snowflake-connector-python)."
        ) from exc
    return sc.connect(**connect_kwargs)


def _scalar(conn: Any, sql: str) -> Any:
    """Execute ``sql`` and return the first column of the first row."""
    cur = conn.cursor()
    try:
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def _probe_region(conn: Any) -> tuple[str | None, str]:
    """Run ``CURRENT_REGION()``, return ``(region, platform)``.

    ``CURRENT_REGION()`` returns strings like ``"AWS_US_EAST_1"``,
    ``"AZURE_EASTUS2"``, ``"GCP_US_CENTRAL1"``. The prefix maps to the
    cloud-platform discriminator. Falls back to
    ``SMA_SNOWFLAKE_PLATFORM`` (then ``"aws"``) when the probe fails.
    """
    try:
        region = _scalar(conn, "SELECT CURRENT_REGION()")
    except Exception:  # noqa: BLE001
        region = None
    if isinstance(region, str) and region:
        prefix = region.split("_", 1)[0].upper()
        platform = _REGION_PREFIX_TO_PLATFORM.get(prefix, _platform_from_env() or "aws")
        return region, platform
    return None, (_platform_from_env() or "aws")


def _probe_edition(conn: Any) -> str | None:
    """Best-effort probe for Snowflake edition (Standard / Enterprise / …)."""
    try:
        return _scalar(
            conn,
            "SELECT VALUE FROM SNOWFLAKE.ACCOUNT_USAGE.ACCOUNT_PROPERTIES "
            "WHERE PROPERTY = 'EDITION' LIMIT 1",
        )
    except Exception:  # noqa: BLE001
        return None


def _platform_from_env() -> str | None:
    """Read ``SMA_SNOWFLAKE_PLATFORM`` and normalise to aws/azure/gcp."""
    val = os.environ.get("SMA_SNOWFLAKE_PLATFORM", "").strip().lower()
    if val in {"aws", "azure", "gcp"}:
        return val
    return None


@contextmanager
def _closing(resource: Any) -> Iterator[Any]:
    try:
        yield resource
    finally:
        try:
            resource.close()
        except Exception:  # noqa: BLE001
            pass


def host_to_descriptor(account: str, *, platform: str | None = None) -> SourceDescriptor:
    """Build a :class:`SourceDescriptor` for an explicit Snowflake account.

    Mirrors :func:`usma.sources.databricks.aws_host_to_descriptor` —
    used by the CLI ``--scope snowflake:<account>`` shortcut so the user
    can target an account without going through discovery.
    """
    norm_platform = (platform or _platform_from_env() or "aws").lower()
    if norm_platform not in {"aws", "azure", "gcp"}:
        raise ValueError(
            f"Snowflake platform {norm_platform!r} unknown (allowed: aws, azure, gcp)",
        )
    return SourceDescriptor(
        type=SourceType.SNOWFLAKE,
        id=account,
        display_name=account,
        extras={"platform": norm_platform},
    )
