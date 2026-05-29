"""Phase 7 — SnowflakeProvider registry hygiene.

Slice 7-A landed the stub; Slice 7-B turned discover/make_clients into
real implementations; Slice 7-E.1 rewired auth from key-pair JWT to
built-in Snowflake OAuth (refresh-token grant). The registry-hygiene
assertions stay here so the provider's enum binding + display name +
required_env contract is covered alongside the behavioural tests in
``test_provider_slice_7b.py``.
"""
from __future__ import annotations

from usma.sources import SourceType, get_provider
from usma.sources.snowflake import SnowflakeProvider


def test_snowflake_provider_registered() -> None:
    provider = get_provider(SourceType.SNOWFLAKE)
    assert isinstance(provider, SnowflakeProvider)
    assert provider.type is SourceType.SNOWFLAKE
    assert provider.display_name == "Snowflake"


def test_snowflake_required_env_lists_oauth_fields() -> None:
    provider = get_provider(SourceType.SNOWFLAKE)
    required = set(provider.required_env)
    assert {
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_OAUTH_CLIENT_ID",
        "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "SNOWFLAKE_OAUTH_REFRESH_TOKEN",
        "SNOWFLAKE_WAREHOUSE",
    }.issubset(required)
