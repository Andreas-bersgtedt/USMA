"""Source-aware module specs — Phase-0 additive scaffolding.

This module defines :class:`ModuleSpec`, a richer descriptor for each
analysis module that declares which :class:`~..sources.SourceType` values
it supports. It is **additive** in Phase 0: the existing
:data:`~.MODULE_REGISTRY` continues to drive all callers. Phase 1 will
replace callers with :data:`MODULE_SPECS` and make ``supports`` a true
gate on the run planner.

The factory references intentionally point at the existing private
``_*`` functions in :mod:`~modules.__init__` so no analyzer code moves
during Phase 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..sources import SourceType
from . import (
    MODULE_REGISTRY,
    ModuleFactory,
)


@dataclass(frozen=True)
class ModuleSpec:
    """Declarative metadata for a single analyzer module.

    ``supports`` declares which source types this module can analyze. The
    run planner intersects ``supports`` with the user-selected scopes to
    decide whether the module runs at all (per scope).

    Phase-0 default: every existing module supports only
    :attr:`SourceType.SYNAPSE_WORKSPACE`. Phases 2+ extend ``supports``
    on relevant modules as new sources land.
    """

    name: str
    factory: ModuleFactory
    supports: frozenset[SourceType] = field(
        default_factory=lambda: frozenset({SourceType.SYNAPSE_WORKSPACE}),
    )
    description: str = ""

    def supports_source(self, source_type: SourceType) -> bool:
        return source_type in self.supports


def _spec(
    name: str,
    *,
    supports: frozenset[SourceType] | None = None,
    description: str = "",
) -> ModuleSpec:
    return ModuleSpec(
        name=name,
        factory=MODULE_REGISTRY[name],
        supports=supports or frozenset({SourceType.SYNAPSE_WORKSPACE}),
        description=description,
    )


# Phase-0 declarations. Phases 2/4/5 will widen ``supports`` for modules
# that the new sources are compatible with (mainly ``pipelines``, ``cost``,
# and ``fabric_mapping``). Keep this dict in lock-step with
# :data:`MODULE_REGISTRY`'s ordering.
MODULE_SPECS: dict[str, ModuleSpec] = {
    "dedicated_pools": _spec(
        "dedicated_pools",
        # Both Synapse-workspace pools and standalone Dedicated SQL
        # pools (formerly SQL DW) share the same data-plane analyzer
        # (DMVs, distribution advisor, T-SQL gap rollup); only ARM
        # discovery differs (see ADR-0009).
        supports=frozenset({
            SourceType.SYNAPSE_WORKSPACE,
            SourceType.SYNAPSE_DEDICATED_SQL,
        }),
        description="Dedicated SQL pool inventory and Fabric readiness.",
    ),
    "serverless_pools": _spec(
        "serverless_pools",
        description="Serverless SQL pool usage and Fabric mapping.",
    ),
    "spark_pools": _spec(
        "spark_pools",
        description="Spark pool inventory and Fabric capacity projection.",
    ),
    "pipelines": _spec(
        "pipelines",
        # Phase 2 widens to {SYNAPSE_WORKSPACE, ADF}. Phase 4 may rename
        # the module to ``orchestration`` (per D5) when Databricks
        # workflows / SAP process chains join.
        supports=frozenset({SourceType.SYNAPSE_WORKSPACE, SourceType.ADF}),
        description="Synapse + ADF pipelines + activities + linked services + triggers.",
    ),
    "monitoring": _spec(
        "monitoring",
        description="Monitor/Log Analytics signals and run-history rollups.",
    ),
    "storage": _spec(
        "storage",
        description="Linked storage accounts and access patterns.",
    ),
    "governance": _spec(
        "governance",
        description="Workspace governance, RBAC, and policy posture.",
    ),
    "security": _spec(
        "security",
        description="Network, encryption, and identity surface.",
    ),
    "cost": _spec(
        "cost",
        # Phase 2 widened to ADF. Phase 4 Slice 4-C adds DATABRICKS —
        # Cost Management is keyed on the subscription + resource group,
        # so the existing collector covers Databricks workspaces too
        # once the resource-id classifier knows the Databricks namespace.
        # Phase 5 Slice 5-H adds BIGQUERY via ``gcp_cost_client`` reading
        # the BigQuery billing-export tables.
        # Phase 7 Slice 7-H adds SNOWFLAKE via ``snowflake_cost_client``
        # reading ``ACCOUNT_USAGE.METERING_HISTORY`` joined to
        # ``USAGE_IN_CURRENCY_DAILY`` for credit-to-currency conversion.
        supports=frozenset({
            SourceType.SYNAPSE_WORKSPACE,
            SourceType.ADF,
            SourceType.DATABRICKS,
            SourceType.BIGQUERY,
            SourceType.SNOWFLAKE,
        }),
        description="Cost attribution and Fabric CU projection.",
    ),
    "fabric_validation": _spec(
        "fabric_validation",
        description="Fabric Warehouse / Lakehouse readiness checks.",
    ),
    "databricks_workflows": _spec(
        "databricks_workflows",
        # Databricks-only by design (D5). Synapse pipelines and ADF stay
        # on the ``pipelines`` module.
        supports=frozenset({SourceType.DATABRICKS}),
        description="Databricks workflows (jobs) + clusters inventory and Fabric mapping.",
    ),
    "bigquery_workloads": _spec(
        "bigquery_workloads",
        # BigQuery-only by design (mirrors ``databricks_workflows`` per D5).
        # Slice 5-C will widen ``cost`` + ``fabric_mapping`` to also
        # support BIGQUERY so a BigQuery scope gets the same three-module
        # treatment Databricks does.
        supports=frozenset({SourceType.BIGQUERY}),
        description="BigQuery datasets / tables / routines / scheduled queries / jobs inventory.",
    ),
    "snowflake_workloads": _spec(
        "snowflake_workloads",
        # Snowflake-only by design (mirrors ``databricks_workflows`` /
        # ``bigquery_workloads`` per D5). Phase 7 Slice 7-D widens
        # ``cost`` + ``fabric_mapping`` to also support SNOWFLAKE so a
        # Snowflake scope gets the same three-module treatment.
        supports=frozenset({SourceType.SNOWFLAKE}),
        description="Snowflake warehouses / databases / schemas / tables / routines / tasks / pipes / jobs inventory.",
    ),
    "fabric_mapping": _spec(
        "fabric_mapping",
        # Phase 2 widened to ADF; Phase 4 Slice 4-C adds DATABRICKS
        # (consumes ``databricks_workflows.json`` via the new rule);
        # Phase 5 Slice 5-C adds BIGQUERY (consumes ``bigquery_workloads.json``);
        # Phase 7 Slice 7-D adds SNOWFLAKE (consumes ``snowflake_workloads.json``).
        # Phase 6 Slice D (ADR-0009) adds SYNAPSE_DEDICATED_SQL — the
        # standalone topology emits the same ``dedicated_pools.json`` shape
        # as workspace-attached pools, so the existing rules apply unchanged.
        supports=frozenset({
            SourceType.SYNAPSE_WORKSPACE,
            SourceType.SYNAPSE_DEDICATED_SQL,
            SourceType.ADF,
            SourceType.DATABRICKS,
            SourceType.BIGQUERY,
            SourceType.SNOWFLAKE,
        }),
        description="Source artifact -> Fabric target mapping and effort.",
    ),
}


def applicable_modules(source_type: SourceType) -> list[ModuleSpec]:
    """Return all module specs that support ``source_type``."""
    return [spec for spec in MODULE_SPECS.values() if spec.supports_source(source_type)]


__all__ = ["ModuleSpec", "MODULE_SPECS", "applicable_modules"]
