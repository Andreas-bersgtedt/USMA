"""Unit tests for the governance module (pure-Python helpers)."""
from __future__ import annotations

from usma.modules.governance.arm_client import (
    _classify_scope,
    _resource_type,
)


def test_classify_scope_levels() -> None:
    assert _classify_scope("/subscriptions/abc") == "subscription"
    assert _classify_scope("/subscriptions/abc/resourceGroups/rg") == "resource_group"
    assert _classify_scope(
        "/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Synapse/workspaces/ws"
    ) == "workspace"
    assert _classify_scope(
        "/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Synapse/"
        "workspaces/ws/sqlPools/p1"
    ) == "pool"
    assert _classify_scope(
        "/subscriptions/abc/resourceGroups/rg/providers/Microsoft.Storage/"
        "storageAccounts/sa1"
    ) == "storage"
    assert _classify_scope("") == "other"


def test_resource_type_extracts_provider() -> None:
    arm = ("/subscriptions/abc/resourceGroups/rg/providers/"
           "Microsoft.Storage/storageAccounts/sa1")
    assert _resource_type(arm) == "Microsoft.Storage/storageAccounts"
    assert _resource_type("") is None
