"""Governance module.

Mid-term roadmap scaffolding (v0): collects workspace- and resource-level RBAC,
managed private endpoints, customer-managed-key configuration, and (where a
Microsoft Purview account is provided) lineage capture for the analyzed workspace.

The collectors here are *best-effort* and fail soft — empty results + a recorded
error string are preferred to a hard exception so that `analyze-all` keeps
running. Live Azure SDK calls are isolated in `arm_client.py` and gated behind
SMA_GOVERNANCE_* environment switches; the analyzer skeleton itself runs without
any network access (returns an empty `GovernanceAnalysis`).
"""
