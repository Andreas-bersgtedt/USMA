"""Helpers for turning raw Azure SDK exceptions into actionable error strings.

Multiple modules touch the Synapse artifacts plane (pipelines, notebooks, SJDs,
linked services, datasets, triggers) which uses **Synapse RBAC**, separate from
Azure RBAC. When a caller has subscription-level rights but no workspace role,
the SDK surfaces a ``(Unauthorized)`` exception complaining about
``Microsoft.Synapse/workspaces/artifacts/read``. Without context, that message
is hard to act on. ``format_error`` decorates such errors with a short pointer
to the QUICKSTART permissions section.
"""
from __future__ import annotations

# Substrings that, when present together, indicate the caller is missing
# the Synapse Artifact User (or higher) workspace role. Must be specific to
# the artifacts action — generic "Synapse RBAC" wording also appears in
# Spark Livy 403s (which need a different role; see _SYN_SPARK_HINT_TOKENS).
_SYN_RBAC_HINT_TOKENS = (
    "artifacts/read",
)

_SYN_RBAC_HINT = (
    " [hint: missing Synapse RBAC role on the workspace. Grant 'Synapse Artifact "
    "User' (or higher) to this principal at workspace scope. See QUICKSTART.md "
    "section 'Permissions' for details.]"
)

# Substrings indicating the caller is missing the Spark Livy useCompute action,
# i.e. the **Synapse Compute Operator** role on the Spark pool.
_SYN_SPARK_HINT_TOKENS = (
    "bigDataPools/useCompute",
)

_SYN_SPARK_HINT = (
    " [hint: missing Spark RBAC. Grant 'Synapse Compute Operator' (or higher) "
    "to this principal at workspace scope or on the specific Spark pool — "
    "Synapse Artifact User is NOT sufficient for Livy. See QUICKSTART.md "
    "section 'Permissions' for details.]"
)


def format_error(prefix: str, exc: BaseException) -> str:
    """Return ``f"{prefix}: {exc}"`` plus a permission hint when appropriate."""
    msg = f"{prefix}: {exc}"
    text = str(exc)
    if any(tok in text for tok in _SYN_RBAC_HINT_TOKENS) and "Unauthorized" in text:
        msg += _SYN_RBAC_HINT
    if any(tok in text for tok in _SYN_SPARK_HINT_TOKENS) and "Unauthorized" in text:
        msg += _SYN_SPARK_HINT
    return msg
