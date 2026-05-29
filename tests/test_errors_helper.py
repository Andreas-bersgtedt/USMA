"""Unit tests for the format_error helper that decorates Azure SDK errors."""
from __future__ import annotations

from usma.errors import format_error


def test_format_error_passthrough_for_unrelated_error() -> None:
    msg = format_error("list_pools", RuntimeError("boom"))
    assert msg == "list_pools: boom"
    assert "Synapse RBAC" not in msg


def test_format_error_appends_hint_for_synapse_rbac_unauthorized() -> None:
    raw = (
        "(Unauthorized) The principal 'a6aa3158-f305-4032-97cd-c2ee0f620959' does not "
        "have the required Synapse RBAC permission to perform this action. Required "
        "permission: Action: Microsoft.Synapse/workspaces/artifacts/read, "
        "Scope: workspaces/absynapsemig001."
    )
    msg = format_error("notebooks", RuntimeError(raw))
    assert msg.startswith("notebooks: (Unauthorized)")
    assert "Synapse Artifact User" in msg
    assert "QUICKSTART.md" in msg


def test_format_error_no_hint_when_unauthorized_but_unrelated() -> None:
    # Unauthorized for a different action should NOT receive the artifacts hint.
    raw = "(Unauthorized) Missing role for storageaccounts/read"
    msg = format_error("blob", RuntimeError(raw))
    assert "QUICKSTART.md" not in msg


def test_format_error_appends_hint_for_spark_useCompute_unauthorized() -> None:
    """The verbatim 403 returned by `azure.synapse.spark` Livy when the SP lacks
    `Microsoft.Synapse/workspaces/bigDataPools/useCompute/action` (i.e. the
    Synapse Compute Operator role)."""
    raw = (
        "(Unauthorized) The principal 'a6aa3158-f305-4032-97cd-c2ee0f620959' does not "
        "have the required Synapse RBAC permission to perform this action. Required "
        "permission: Action: Microsoft.Synapse/workspaces/bigDataPools/useCompute/action, "
        "Scope: workspaces/absynapsemig001/bigDataPools/sparkpool001."
    )
    msg = format_error("spark_history_batches[sparkpool001]", RuntimeError(raw))
    assert msg.startswith("spark_history_batches[sparkpool001]: (Unauthorized)")
    assert "Synapse Compute Operator" in msg
    # Must NOT also tack on the Artifact-User hint (different action — the
    # Spark hint deliberately mentions "Synapse Artifact User is NOT sufficient",
    # so we look for a phrase unique to the artifacts hint instead).
    assert "Grant 'Synapse Artifact User'" not in msg
