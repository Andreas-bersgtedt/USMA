"""SapBwProvider — Phase-5 stub.

SAP BW connectivity will use either OData (HTTP) or native RFC via
``pyrfc`` (gated behind the ``[sapbw]`` install extra). Credentials live
in ``creds.extras`` (``SAP_SYSTEM_ID``, ``SAP_HOSTNAME``, ``SAP_USER``,
``SAP_PASSWORD``, ``SAP_CLIENT``, ...).
"""
from __future__ import annotations

from typing import Any

from .. import ConfigCheck, Credentials, SourceDescriptor, SourceType
from ..base import BaseSourceProvider


class SapBwProvider(BaseSourceProvider):
    """Discovers SAP BW systems."""

    type = SourceType.SAP_BW
    display_name = "SAP BW"
    required_env: tuple[str, ...] = (
        "SAP_SYSTEM_ID",
        "SAP_HOSTNAME",
        "SAP_USER",
        "SAP_PASSWORD",
        "SAP_CLIENT",
    )

    def discover(
        self, creds: Credentials, subscription_id: str | None = None,
    ) -> list[SourceDescriptor]:
        raise NotImplementedError(
            "SapBwProvider.discover lands in Phase 5. "
            "SAP BW does not auto-discover via ARM; the user supplies system "
            "coordinates and discover() validates connectivity.",
        )

    def validate(
        self, descriptor: SourceDescriptor, creds: Credentials,
    ) -> list[ConfigCheck]:
        return []

    def make_clients(
        self, descriptor: SourceDescriptor, creds: Credentials,
    ) -> Any:
        raise NotImplementedError(
            "SapBwProvider.make_clients lands in Phase 5.",
        )
