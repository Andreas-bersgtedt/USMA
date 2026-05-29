"""Pure-Python rules engine for the cost module.

Operates over a populated :class:`CostAnalysis` and emits severity-tagged
findings. No live calls — entirely deterministic and unit-testable.
"""
from __future__ import annotations

import os
import re

from .models import CostAnalysis, CostFinding


# Thresholds (kept module-level so tests can override via monkeypatch).
SAVINGS_PCT = 0.10  # |delta_pct| >= 10% → meaningful delta
SPIKE_PCT = 0.25    # month-over-month >= +25% → spike
DOMINANT_KIND_PCT = 0.70  # one resource_kind > 70% of total → concentration risk


def evaluate(result: CostAnalysis) -> list[CostFinding]:
    findings: list[CostFinding] = []
    findings.extend(_no_data_finding(result))
    findings.extend(_snowflake_credits_only_finding(result))
    findings.extend(_fabric_findings(result))
    findings.extend(_concentration_findings(result))
    findings.extend(_month_over_month_findings(result))
    return findings


def _snowflake_credits_only_finding(result: CostAnalysis) -> list[CostFinding]:
    """When ``USAGE_IN_CURRENCY_DAILY`` is unavailable in the account
    the cost client falls back to credits-only rows (cost=0,
    usage_unit='credits'). Flag it as info so the SPA doesn't render
    a misleading ``$0`` total without context."""
    source_type = (getattr(result, "source_type", "") or "azure").lower()
    if source_type != "snowflake" or not result.rows:
        return []
    has_credits = any(
        (r.usage_unit or "").lower() == "credits" and (r.usage_quantity or 0) > 0
        for r in result.rows
    )
    has_cost = any((r.cost or 0) > 0 for r in result.rows)
    if not has_credits or has_cost:
        return []
    return [CostFinding(
        rule_id="cost.snowflake_credits_only",
        severity="info",
        title="Snowflake cost reported in credits (no $ conversion)",
        detail=(
            "Credit consumption was captured from "
            "SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY, but "
            "SNOWFLAKE.ACCOUNT_USAGE.USAGE_IN_CURRENCY_DAILY is not "
            "available in this account (typical for Standard Edition or "
            "accounts without org-level billing visibility). All cost "
            "figures are therefore 0 — multiply credits by your "
            "contracted $/credit rate to estimate spend, or run on an "
            "Enterprise+ account with USAGE_IN_CURRENCY_DAILY exposed "
            "to get automatic conversion."
        ),
    )]


def _no_data_finding(result: CostAnalysis) -> list[CostFinding]:
    if result.rows:
        return []
    status = getattr(result, "collection_status", "ok")
    source_type = (getattr(result, "source_type", "") or "azure").lower()
    err_tail = ""
    if result.errors:
        first = result.errors[0]
        # ``errors[0]`` is already ``"<stage>: <ExcType>: <msg>"`` from format_error.
        err_tail = f" Underlying error: {first}"

    if status == "sdk_missing":
        if source_type == "bigquery":
            return [CostFinding(
                rule_id="cost.sdk_missing",
                severity="medium",
                title="google-cloud-billing is not installed",
                detail=("The GCP billing-export SDK is not importable, so no "
                        "live cost data could be collected. Install it with "
                        "`pip install usma[gcp]` and re-run the cost module."),
            )]
        if source_type == "snowflake":
            return [CostFinding(
                rule_id="cost.sdk_missing",
                severity="medium",
                title="snowflake-connector-python is not installed",
                detail=("The Snowflake connector is not importable, so no live "
                        "cost data could be collected. Install it with "
                        "`pip install usma[snowflake]` and re-run the cost "
                        "module."),
            )]
        return [CostFinding(
            rule_id="cost.sdk_missing",
            severity="medium",
            title="azure-mgmt-costmanagement is not installed",
            detail=("The optional Cost Management SDK is not importable, so no "
                    "live cost data could be collected. Install it with "
                    "`pip install azure-mgmt-costmanagement` and re-run "
                    "`sma analyze-cost`."),
        )]

    if status == "live_disabled":
        return [CostFinding(
            rule_id="cost.live_disabled",
            severity="info",
            title="Live cost queries are disabled (SMA_COST_DISABLE_LIVE)",
            detail=("The SMA_COST_DISABLE_LIVE environment variable is set, so "
                    "the cost collector returned no rows by design. Unset it "
                    "to enable live cost queries."),
        )]

    if status == "missing_config":
        if source_type == "snowflake":
            return [CostFinding(
                rule_id="cost.missing_config",
                severity="medium",
                title="Snowflake account is not configured",
                detail=("SNOWFLAKE_ACCOUNT is not set, so the Snowflake cost "
                        "client cannot connect. Set it via the Configuration "
                        "page or the .env file."),
            )]
        if source_type == "bigquery":
            return [CostFinding(
                rule_id="cost.missing_config",
                severity="medium",
                title="GCP billing project is not configured",
                detail=("No billing project is configured; the GCP cost "
                        "client cannot run. Set GOOGLE_CLOUD_PROJECT / "
                        "SMA_GCP_BILLING_DATASET and re-run."),
            )]
        return [CostFinding(
            rule_id="cost.missing_config",
            severity="medium",
            title="Required cost configuration is missing",
            detail="See errors[] for the missing keys.",
        )]

    if status == "error":
        if source_type == "snowflake":
            current_role = (os.environ.get("SNOWFLAKE_ROLE") or "").strip().upper()
            err_text = " ".join(result.errors or [])
            # Did the cost client tag the error with the active session
            # role? (See snowflake_cost_client._probe_session_role_context.)
            active_match = re.search(r"active_role=([A-Z0-9_]+)", err_text)
            active_role = active_match.group(1).upper() if active_match else ""
            role_warning = ""
            if active_role and current_role and active_role != current_role:
                role_warning = (
                    "\n\n⚠ ROLE MISMATCH: SNOWFLAKE_ROLE is "
                    f"'{current_role}' but the live Snowflake session is "
                    f"running as '{active_role}'. This is the smoking "
                    "gun: Snowflake OAuth refresh tokens are bound to "
                    "the role chosen at consent time, so changing "
                    "SNOWFLAKE_ROLE in the Configuration page is not "
                    "enough — you must click 'Sign in to Snowflake' "
                    "AGAIN to mint a new refresh token bound to "
                    f"'{current_role}'."
                )
            elif active_role == "PUBLIC" or current_role in ("", "PUBLIC"):
                role_warning = (
                    "\n\n⚠ Your active Snowflake role is "
                    f"'{active_role or current_role or '<unset>'}'. "
                    "Snowflake does NOT allow IMPORTED PRIVILEGES ON "
                    "DATABASE SNOWFLAKE to be granted to PUBLIC, so the "
                    "fix below will fail until you switch to a "
                    "non-PUBLIC role such as USMA_RO."
                )
            elif active_role:
                # Active role known and matches config but still failing
                # — the grant itself is missing.
                role_warning = (
                    f"\n\n⚠ Session role '{active_role}' is the role you "
                    "configured, so the OAuth token is fine. The "
                    "remaining cause is that this role has not been "
                    "granted IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE "
                    "(or is missing USAGE on the warehouse). Run the "
                    "bootstrap SQL in docs/user-guide/23-snowflake.md "
                    "as ACCOUNTADMIN."
                )
            return [CostFinding(
                rule_id="cost.collection_error",
                severity="medium",
                title="Snowflake ACCOUNT_USAGE query failed",
                detail=(
                    "The Snowflake cost collector raised an exception while "
                    "reading SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY / "
                    "USAGE_IN_CURRENCY_DAILY. The most common cause is the "
                    "configured role missing IMPORTED PRIVILEGES ON DATABASE "
                    "SNOWFLAKE." + role_warning + "\n\n"
                    "As ACCOUNTADMIN, run the full role bootstrap from "
                    "docs/user-guide/23-snowflake.md (creates USMA_RO, "
                    "grants ACCOUNT_USAGE access, binds the role to the "
                    "OAuth user). Then on the Configuration page:\n"
                    "  1. Set SNOWFLAKE_ROLE = USMA_RO and Save.\n"
                    "  2. Click 'Sign in to Snowflake' AGAIN — Snowflake "
                    "OAuth refresh tokens are bound to the role at "
                    "consent time, so an existing token minted for "
                    "PUBLIC will keep failing even after the role grant."
                    + err_tail
                ),
            )]
        if source_type == "bigquery":
            return [CostFinding(
                rule_id="cost.collection_error",
                severity="medium",
                title="GCP billing-export query failed",
                detail=(
                    "The GCP cost collector raised an exception while "
                    "reading the billing-export dataset. Verify "
                    "GOOGLE_APPLICATION_CREDENTIALS, the billing dataset id, "
                    "and that the service account has BigQuery Data Viewer "
                    "on the dataset." + err_tail
                ),
            )]
        return [CostFinding(
            rule_id="cost.collection_error",
            severity="medium",
            title="Cost Management query failed",
            detail=("The Cost Management API call raised an exception. See the "
                    "errors[] list on this report for the underlying message — "
                    "common causes are missing Cost Management Reader on the "
                    "resource group or an invalid scope." + err_tail),
        )]

    # status == "empty_window" or "ok" with no rows
    if source_type == "snowflake":
        return [CostFinding(
            rule_id="cost.no_data",
            severity="info",
            title="No cost rows captured",
            detail=("SNOWFLAKE.ACCOUNT_USAGE returned no rows for the "
                    "configured window. Verify the role has IMPORTED "
                    "PRIVILEGES ON DATABASE SNOWFLAKE, that the warehouse / "
                    "storage has actually been used in this window, and "
                    "that SMA_COST_DISABLE_LIVE is not set. Try widening "
                    "the window with SMA_COST_MONTHS=6."),
        )]
    if source_type == "bigquery":
        return [CostFinding(
            rule_id="cost.no_data",
            severity="info",
            title="No cost rows captured",
            detail=("GCP billing export returned no rows for the configured "
                    "window. Verify BigQuery Data Viewer on the billing "
                    "dataset and that SMA_COST_DISABLE_LIVE is not set. "
                    "Try widening the window with SMA_COST_MONTHS=6."),
        )]
    return [CostFinding(
        rule_id="cost.no_data",
        severity="info",
        title="No cost rows captured",
        detail=("Cost Management returned no rows for the configured window. "
                "Verify Cost Management Reader on the resource group, that the "
                "azure-mgmt-costmanagement extra is installed, and that "
                "SMA_COST_DISABLE_LIVE is not set. Try widening the window "
                "with SMA_COST_MONTHS=6."),
    )]


def _fabric_findings(result: CostAnalysis) -> list[CostFinding]:
    fc = result.fabric_comparison
    if fc is None or fc.delta_pct is None or fc.fabric_estimated_monthly_cost is None:
        return []
    if fc.delta_pct <= -SAVINGS_PCT:
        # Fabric is cheaper.
        return [CostFinding(
            rule_id="cost.fabric.savings",
            severity="info",
            title=f"Fabric SKU {fc.fabric_capacity_sku} projects ~{fc.delta_pct:+.0%} vs Synapse",
            detail=(f"Synapse latest monthly cost {fc.synapse_avg_monthly_cost:,.0f}; "
                    f"Fabric estimated {fc.fabric_estimated_monthly_cost:,.0f} "
                    f"(delta {fc.delta_abs:+,.0f})."),
        )]
    if fc.delta_pct >= SAVINGS_PCT:
        return [CostFinding(
            rule_id="cost.fabric.increase",
            severity="medium",
            title=f"Fabric SKU {fc.fabric_capacity_sku} projects ~{fc.delta_pct:+.0%} vs Synapse",
            detail=(f"Synapse latest monthly cost {fc.synapse_avg_monthly_cost:,.0f}; "
                    f"Fabric estimated {fc.fabric_estimated_monthly_cost:,.0f} "
                    f"(delta {fc.delta_abs:+,.0f}). Review CU sizing recommendation "
                    "or evaluate reservation pricing."),
        )]
    return []


def _concentration_findings(result: CostAnalysis) -> list[CostFinding]:
    total = sum(result.by_resource_kind.values())
    if total <= 0:
        return []
    findings: list[CostFinding] = []
    for kind, cost in result.by_resource_kind.items():
        share = cost / total
        if share >= DOMINANT_KIND_PCT and kind in {
            "dedicated_pool",
            "spark_pool",
            "adf_factory",
            "databricks_workspace",
        }:
            findings.append(CostFinding(
                rule_id=f"cost.concentration.{kind}",
                severity="info",
                title=f"{kind} accounts for {share:.0%} of workspace spend",
                detail=(f"Resource kind '{kind}' contributes {cost:,.0f} of "
                        f"{total:,.0f} total. Pause / scale-down policies can move "
                        "the needle materially."),
            ))
    return findings


def _month_over_month_findings(result: CostAnalysis) -> list[CostFinding]:
    months = sorted(result.monthly_totals)
    if len(months) < 2:
        return []
    findings: list[CostFinding] = []
    for prev, curr in zip(months, months[1:], strict=False):
        prev_cost = result.monthly_totals[prev]
        curr_cost = result.monthly_totals[curr]
        if prev_cost <= 0:
            continue
        pct = (curr_cost - prev_cost) / prev_cost
        if pct >= SPIKE_PCT:
            findings.append(CostFinding(
                rule_id="cost.month_over_month.spike",
                severity="medium",
                title=f"Month-over-month spike {pct:+.0%} ({prev} → {curr})",
                detail=(f"Spend rose from {prev_cost:,.0f} to {curr_cost:,.0f}. "
                        "Investigate the dominant resource kind for that month."),
            ))
    return findings
