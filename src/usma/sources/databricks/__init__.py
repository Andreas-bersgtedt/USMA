"""Databricks source provider — Phase 4 (Azure) + Phase 4.7 (AWS).

The Azure provider is registered as the default for
:attr:`usma.sources.SourceType.DATABRICKS`. AWS support is dispatched
internally via ``descriptor.extras['platform']`` — use
:func:`provider_for_platform` (or :func:`usma.sources.databricks_platform`)
to pick the right provider. See ADR-0005.
"""
from __future__ import annotations

from .. import (
    SourceDescriptor,
    SourceProvider,
    databricks_platform,
    register_provider,
)
from .aws_provider import DatabricksAwsProvider, aws_host_to_descriptor
from .provider import DATABRICKS_AAD_RESOURCE, DatabricksClientBundle, DatabricksProvider

# Azure remains the default registered provider for the SourceType so
# legacy callers and the SOURCE_REGISTRY lookup keep resolving to it.
register_provider(DatabricksProvider())

# Per-platform dispatcher used by analyzers + the modules registry.
_BY_PLATFORM: dict[str, SourceProvider] = {
    "azure": DatabricksProvider(),
    "aws": DatabricksAwsProvider(),
}


def provider_for_platform(platform: str) -> SourceProvider:
    """Return the Databricks provider for ``platform`` (``azure`` | ``aws``).

    Raises :class:`KeyError` for unknown platforms. ``gcp`` is reserved
    for a future phase and not registered yet.
    """
    try:
        return _BY_PLATFORM[platform]
    except KeyError as exc:
        raise KeyError(
            f"No Databricks provider registered for platform {platform!r}. "
            f"Known platforms: {sorted(_BY_PLATFORM)}",
        ) from exc


def provider_for_descriptor(descriptor: SourceDescriptor) -> SourceProvider:
    """Pick the right Databricks provider for ``descriptor``."""
    return provider_for_platform(databricks_platform(descriptor))


__all__ = [
    "DatabricksProvider",
    "DatabricksAwsProvider",
    "DatabricksClientBundle",
    "DATABRICKS_AAD_RESOURCE",
    "aws_host_to_descriptor",
    "databricks_platform",
    "provider_for_platform",
    "provider_for_descriptor",
]
