"""Phase 4.7-F smoke test for the ``[databricks]`` extra.

Guards two contracts that the AWS Databricks path depends on:

1. ``databricks.sdk.AccountClient`` must be importable. The Account-API
   discovery path in :class:`DatabricksAwsProvider` would otherwise
   fail at runtime with a confusing ``ImportError`` rather than a
   missing-extra hint.
2. :class:`DatabricksAwsProvider` must be constructible and expose the
   ``SourceType.DATABRICKS`` discriminator + ``aws_host_to_descriptor``
   helper without touching the network.

Both are pure-Python invariants, so the test is intentionally
network-free — it does NOT call ``discover`` or ``validate``.
"""
from __future__ import annotations


def test_account_client_importable() -> None:
    """``databricks-sdk>=0.30`` ships ``AccountClient`` at the top level."""
    from databricks.sdk import AccountClient

    assert AccountClient.__module__.startswith("databricks.sdk")


def test_aws_provider_constructs_and_advertises_databricks_source_type() -> None:
    from usma.sources import SourceType
    from usma.sources.databricks.aws_provider import (
        DatabricksAwsProvider,
        aws_host_to_descriptor,
    )

    provider = DatabricksAwsProvider()
    assert provider.type is SourceType.DATABRICKS
    assert "AWS" in provider.display_name

    desc = aws_host_to_descriptor("dbc-abc12345-6789.cloud.databricks.com")
    assert desc.type is SourceType.DATABRICKS
    assert desc.extras.get("platform") == "aws"
