"""Base helpers shared by source provider implementations.

Provides a minimal ``BaseSourceProvider`` mixin that fills in safe
defaults (empty ``required_env``, empty validate, ``NotImplementedError``
discover/make_clients) so concrete providers only need to override the
methods that matter for them.
"""
from __future__ import annotations

from typing import Any

from . import ConfigCheck, Credentials, SourceDescriptor, SourceProvider, SourceType


class BaseSourceProvider:
    """Default-noop base for source providers.

    Concrete subclasses MUST set ``type``, ``display_name``, and override
    ``discover``. ``validate`` and ``make_clients`` can be overridden as
    needed.
    """

    type: SourceType
    display_name: str = ""
    required_env: tuple[str, ...] = ()

    def discover(
        self, creds: Credentials, subscription_id: str | None = None,
    ) -> list[SourceDescriptor]:
        raise NotImplementedError(
            f"{self.__class__.__name__}.discover is not yet implemented",
        )

    def validate(
        self, descriptor: SourceDescriptor, creds: Credentials,
    ) -> list[ConfigCheck]:
        return []

    def make_clients(
        self, descriptor: SourceDescriptor, creds: Credentials,
    ) -> Any:
        raise NotImplementedError(
            f"{self.__class__.__name__}.make_clients is not yet implemented",
        )


# Sanity check at import time: every subclass we ship should satisfy the
# runtime-checkable ``SourceProvider`` protocol. This is exercised by
# ``tests/sources/test_registry.py``.
__all__ = ["BaseSourceProvider", "SourceProvider"]
