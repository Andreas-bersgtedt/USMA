"""Source plugin framework — Phase 0 scaffolding.

This package defines the abstractions that allow the analyzer to support
multiple kinds of data-estate sources (Synapse workspaces, Azure Data
Factory, Databricks, SAP BW, ...) behind a single uniform interface.

See:

* `feasibility_study.md` (repo root) — design rationale and phasing.
* `USMA_planning_Manifest.md` (repo root) — live progress tracker.
* `docs/architecture/sources.md` — architecture overview.
* `src/usma/sources/README.md` — contributor guide.

**Phase-0 state:** the protocol, dataclasses, and registry are defined
but no production code is wired to them yet. The existing Synapse code
paths in ``web/config_io.py``, ``config.py``, ``cli.py`` etc. continue
to work unchanged. Phase 1 will migrate the Synapse implementation into
``sources/synapse_workspace/provider.py`` and route callers through the
registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "SourceType",
    "SourceDescriptor",
    "Credentials",
    "ConfigCheck",
    "SourceProvider",
    "SOURCE_REGISTRY",
    "register_provider",
    "get_provider",
    "databricks_platform",
]


class SourceType(StrEnum):
    """Canonical identifier for a kind of data-estate source.

    The string value is used as a stable key in config files, manifests,
    artifact file names, and REST payloads. Do not rename existing values
    without a manifest schema bump.
    """

    SYNAPSE_WORKSPACE = "synapse_workspace"
    ADF = "adf"
    DATABRICKS = "databricks"
    BIGQUERY = "bigquery"
    SAP_BW = "sap_bw"
    SQL_SERVER = "sql_server"  # future
    SNOWFLAKE = "snowflake"    # future


@dataclass(frozen=True)
class SourceDescriptor:
    """A single concrete source instance the user has selected for analysis.

    ``id`` is the canonical identifier for the source (an ARM resource id
    for Azure-hosted sources, a workspace URL for Databricks, an SAP system
    id for SAP BW, etc.). ``extras`` carries source-specific fields that
    don't fit the common shape (e.g. ``databricks_url``, ``sap_system_id``).

    **Multi-cloud Databricks contract (Phase 4.7).** When ``type`` is
    :attr:`SourceType.DATABRICKS`, ``extras['platform']`` indicates the
    underlying cloud and takes one of the string values ``"azure"``,
    ``"aws"`` or ``"gcp"``. The key is optional for backwards
    compatibility — readers must treat a missing key as ``"azure"`` so
    pre-4.7 manifests and config files keep resolving to the original
    ARM-backed provider. Use :func:`databricks_platform` to read the
    field rather than open-coding the lookup.
    """

    type: SourceType
    id: str
    display_name: str
    subscription_id: str | None = None
    resource_group: str | None = None
    location: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def short_id(self) -> str:
        """Return a short, filesystem-safe identifier for use in artifact names.

        Defaults to the last segment of ``id`` lowercased; provider
        implementations may override this via ``extras['short_id']``.
        """
        if "short_id" in self.extras:
            return str(self.extras["short_id"])
        tail = self.id.rstrip("/").rsplit("/", 1)[-1]
        return tail.lower().replace(" ", "-")


@dataclass(frozen=True)
class Credentials:
    """Shared Azure-AD credentials for source discovery and data-plane access.

    Per-source secrets (e.g. a Databricks PAT, an SAP RFC password) live
    in ``extras`` keyed by a stable provider-defined name. The base
    ``tenant_id``/``client_id``/``client_secret`` cover the common
    ClientSecretCredential case which all Azure-hosted sources use as
    their default.

    **Non-Azure providers may ignore this object entirely** and accept
    ``Credentials | None`` on their ``discover``/``validate``/``make_clients``
    methods. For example, ``BigQueryProvider`` resolves credentials via
    Google's Application Default Credentials chain (``google.auth.default()``)
    and only inspects ``extras['gcp_service_account_json']`` as an optional
    escape hatch when ADC is not configured. See ``sources/bigquery/`` for
    the canonical non-Azure pattern.
    """

    tenant_id: str
    client_id: str
    client_secret: str
    extras: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigCheck:
    """A single validation result emitted by ``SourceProvider.validate``.

    The shape intentionally matches the existing ``ConfigCheck`` used by
    ``web/config_io.py`` so the Phase-1 migration is a straight import
    swap rather than a refactor. ``category`` is optional and used by
    the SPA to group checks into "Control plane" / "Data plane" sections.
    """

    name: str
    ok: bool
    detail: str = ""
    category: str | None = None


@runtime_checkable
class SourceProvider(Protocol):
    """Protocol every source plugin must implement.

    Implementations live under ``sources/<source_type>/provider.py`` and
    are registered via ``register_provider`` at import time.

    The ``creds`` parameter is typed ``Credentials | None`` because
    non-Azure providers (e.g. BigQuery) resolve credentials out-of-band
    via their own SDK's default-credential chain and may ignore it.
    Azure-backed providers (Synapse / ADF / Databricks) require a
    non-``None`` ``Credentials`` and should fail loudly when given
    ``None``.
    """

    type: SourceType
    display_name: str
    # Env-var names required for this provider beyond the base Credentials
    # (e.g. ``("DATABRICKS_HOST", "DATABRICKS_TOKEN")``). May be empty.
    required_env: tuple[str, ...]

    def discover(
        self, creds: Credentials | None, subscription_id: str | None = None,
    ) -> list[SourceDescriptor]:
        """List discoverable source instances visible to ``creds``."""
        ...

    def validate(
        self, descriptor: SourceDescriptor, creds: Credentials | None,
    ) -> list[ConfigCheck]:
        """Validate that ``creds`` can reach ``descriptor`` for analysis."""
        ...

    def make_clients(
        self, descriptor: SourceDescriptor, creds: Credentials | None,
    ) -> Any:
        """Return whatever SDK client bundle the modules need to call.

        The concrete return type is provider-specific; modules cast it to
        the expected shape. Kept as ``Any`` here to avoid forcing a
        lowest-common-denominator interface.
        """
        ...


SOURCE_REGISTRY: dict[SourceType, SourceProvider] = {}


def register_provider(provider: SourceProvider) -> SourceProvider:
    """Register ``provider`` in :data:`SOURCE_REGISTRY`.

    Idempotent: re-registering the same provider type replaces the prior
    entry (useful for tests that swap in a fake).
    """
    SOURCE_REGISTRY[provider.type] = provider
    return provider


def get_provider(source_type: SourceType) -> SourceProvider:
    """Look up a registered provider; raise ``KeyError`` if missing."""
    try:
        return SOURCE_REGISTRY[source_type]
    except KeyError as exc:
        raise KeyError(
            f"No provider registered for source type {source_type!r}. "
            "Did you forget to import the provider module?",
        ) from exc


# ---------------------------------------------------------------------------
# Multi-cloud helpers
# ---------------------------------------------------------------------------

# Canonical platform values for Databricks descriptors.
_DATABRICKS_PLATFORMS: frozenset[str] = frozenset({"azure", "aws", "gcp"})


def databricks_platform(descriptor: SourceDescriptor) -> str:
    """Return the cloud platform for a Databricks ``descriptor``.

    Phase 4.7 introduces multi-cloud Databricks support without changing
    :class:`SourceType`. The platform is carried on
    ``descriptor.extras['platform']`` and takes one of ``"azure"``,
    ``"aws"`` or ``"gcp"``. Pre-4.7 descriptors and v2 manifests omit
    the key entirely \u2014 those resolve to ``"azure"`` so the legacy ARM /
    AAD provider keeps owning them.

    Raises :class:`ValueError` if called on a non-Databricks descriptor
    (callers should branch on ``type`` first) or if the extras key holds
    an unrecognised value.
    """
    if descriptor.type is not SourceType.DATABRICKS:
        raise ValueError(
            f"databricks_platform() called on non-Databricks descriptor "
            f"(type={descriptor.type.value!r}).",
        )
    platform = descriptor.extras.get("platform", "azure")
    if not isinstance(platform, str):
        raise ValueError(
            f"extras['platform'] must be a string, got {type(platform).__name__}",
        )
    platform = platform.lower()
    if platform not in _DATABRICKS_PLATFORMS:
        raise ValueError(
            f"extras['platform']={platform!r} not in {sorted(_DATABRICKS_PLATFORMS)}",
        )
    return platform


# ---------------------------------------------------------------------------
# Eager provider registration
#
# Each subpackage's ``__init__`` calls :func:`register_provider` at import
# time. Importing them here guarantees that anything that imports
# ``usma.sources`` (e.g. ``get_provider`` callers in
# ``web/config_io.py``, ``modules/run_plan.py``) sees the full registry
# without needing to know which provider modules exist.
#
# Imports are placed at the end of this module so the registry helpers
# above are already defined when the subpackages run their registration.
# Stub providers (Databricks, SAP BW) only raise on actual use, so it is
# safe to import them eagerly even when their optional extras are absent.
# ---------------------------------------------------------------------------
from . import synapse_workspace as _synapse_workspace  # noqa: E402, F401
from . import adf as _adf  # noqa: E402, F401
from . import databricks as _databricks  # noqa: E402, F401
from . import bigquery as _bigquery  # noqa: E402, F401
from . import snowflake as _snowflake  # noqa: E402, F401
from . import sap_bw as _sap_bw  # noqa: E402, F401
