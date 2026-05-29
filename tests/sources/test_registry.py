"""Tests for the source-plugin registry (Phase 0)."""
from __future__ import annotations

import pytest

from usma.sources import (
    SOURCE_REGISTRY,
    SourceProvider,
    SourceType,
    get_provider,
    register_provider,
)
from usma.sources.base import BaseSourceProvider


# Import the source sub-packages so they self-register.
import usma.sources.adf  # noqa: F401
import usma.sources.bigquery  # noqa: F401
import usma.sources.databricks  # noqa: F401
import usma.sources.sap_bw  # noqa: F401
import usma.sources.synapse_workspace  # noqa: F401


def test_phase0_providers_registered():
    """Every Phase-0 stub provider is discoverable via the registry."""
    for st in (
        SourceType.SYNAPSE_WORKSPACE,
        SourceType.ADF,
        SourceType.DATABRICKS,
        SourceType.BIGQUERY,
        SourceType.SAP_BW,
    ):
        assert st in SOURCE_REGISTRY, f"{st} not registered"


def test_providers_satisfy_protocol():
    """Each registered provider satisfies the SourceProvider Protocol."""
    for st, provider in SOURCE_REGISTRY.items():
        assert isinstance(provider, SourceProvider), (
            f"provider for {st} does not satisfy SourceProvider protocol"
        )
        assert provider.type == st
        assert provider.display_name, f"display_name missing on {st}"


def test_get_provider_returns_registered():
    assert get_provider(SourceType.SYNAPSE_WORKSPACE).type == SourceType.SYNAPSE_WORKSPACE


def test_get_provider_unknown_raises():
    class _Fake(BaseSourceProvider):
        type = SourceType.SNOWFLAKE  # not yet registered in Phase 0
        display_name = "x"

    # Save and restore to keep test isolation
    snowflake_was = SOURCE_REGISTRY.pop(SourceType.SNOWFLAKE, None)
    try:
        with pytest.raises(KeyError):
            get_provider(SourceType.SNOWFLAKE)
    finally:
        if snowflake_was is not None:
            SOURCE_REGISTRY[SourceType.SNOWFLAKE] = snowflake_was


def test_register_provider_is_idempotent():
    class _Fake(BaseSourceProvider):
        type = SourceType.SYNAPSE_WORKSPACE  # collides with real one
        display_name = "fake"

    original = SOURCE_REGISTRY[SourceType.SYNAPSE_WORKSPACE]
    try:
        register_provider(_Fake())
        assert SOURCE_REGISTRY[SourceType.SYNAPSE_WORKSPACE].display_name == "fake"
    finally:
        register_provider(original)
    assert SOURCE_REGISTRY[SourceType.SYNAPSE_WORKSPACE] is original


def test_unimplemented_stubs_raise_on_discover():
    """SAP-BW provider intentionally raises NotImplementedError until
    its phase ships.

    Synapse workspace is real as of Phase 1 — see
    ``tests/sources/synapse_workspace/test_provider.py``.
    ADF is real as of Phase 2 — see ``tests/sources/adf/test_provider.py``.
    Databricks is real as of Phase 4 — see
    ``tests/sources/databricks/test_provider.py``.
    """
    creds_stub = object()  # not used because discover() raises before touching it
    for st in (
        SourceType.SAP_BW,
    ):
        provider = SOURCE_REGISTRY[st]
        with pytest.raises(NotImplementedError):
            provider.discover(creds_stub)  # type: ignore[arg-type]
