"""Tests for ModuleSpec + MODULE_SPECS (Phase 0)."""
from __future__ import annotations

from usma.modules import MODULE_REGISTRY, KNOWN_MODULES
from usma.modules.spec import (
    MODULE_SPECS,
    ModuleSpec,
    applicable_modules,
)
from usma.sources import SourceType


def test_specs_cover_every_registry_entry():
    assert set(MODULE_SPECS) == set(MODULE_REGISTRY)
    # Same order — diff/report code relies on this.
    assert tuple(MODULE_SPECS) == KNOWN_MODULES


def test_every_spec_default_supports_synapse():
    # Phase 4 introduces ``databricks_workflows`` as a Databricks-only
    # module (per ADR D5); Phase 5 Slice 5-B adds ``bigquery_workloads``
    # as the equivalent BigQuery-only module; Phase 7 Slice 7-C adds
    # ``snowflake_workloads`` as the equivalent Snowflake-only module.
    # All other modules still cover Synapse.
    for name, spec in MODULE_SPECS.items():
        if name in ("databricks_workflows", "bigquery_workloads", "snowflake_workloads"):
            assert SourceType.SYNAPSE_WORKSPACE not in spec.supports
            continue
        assert SourceType.SYNAPSE_WORKSPACE in spec.supports, (
            f"{name} must support Synapse in Phase 0"
        )


def test_every_spec_carries_factory_and_description():
    for name, spec in MODULE_SPECS.items():
        assert spec.name == name
        assert callable(spec.factory)
        assert spec.factory is MODULE_REGISTRY[name]
        assert spec.description, f"{name} missing description"


def test_supports_source_method():
    spec = MODULE_SPECS["pipelines"]
    assert spec.supports_source(SourceType.SYNAPSE_WORKSPACE)
    # Phase 2: ADF now in supports for pipelines, cost, and fabric_mapping.
    assert spec.supports_source(SourceType.ADF)
    # ``pipelines`` stays Synapse + ADF only; Databricks gets its own
    # ``databricks_workflows`` module per ADR D5.
    assert not spec.supports_source(SourceType.DATABRICKS)
    assert not spec.supports_source(SourceType.SAP_BW)
    dbx_spec = MODULE_SPECS["databricks_workflows"]
    assert dbx_spec.supports == frozenset({SourceType.DATABRICKS})


def test_applicable_modules_filters_by_source():
    synapse_apps = applicable_modules(SourceType.SYNAPSE_WORKSPACE)
    # Every module except the source-specific ones is applicable to Synapse.
    assert {m.name for m in synapse_apps} == set(MODULE_SPECS) - {
        "databricks_workflows", "bigquery_workloads", "snowflake_workloads",
    }
    # Phase 2: pipelines, cost, and fabric_mapping support ADF.
    adf_apps = applicable_modules(SourceType.ADF)
    assert {m.name for m in adf_apps} == {"pipelines", "cost", "fabric_mapping"}
    # Phase 4 Slice 4-B introduced databricks_workflows; Slice 4-C widened
    # cost + fabric_mapping so Databricks now has three applicable modules.
    dbx_apps = applicable_modules(SourceType.DATABRICKS)
    assert {m.name for m in dbx_apps} == {"databricks_workflows", "cost", "fabric_mapping"}
    # Phase 5 Slice 5-B: BigQuery starts BigQuery-only; Slice 5-C widens
    # fabric_mapping to include BIGQUERY; Slice 5-H widens cost via
    # ``gcp_cost_client`` reading the BigQuery billing-export tables.
    bq_apps = applicable_modules(SourceType.BIGQUERY)
    assert {m.name for m in bq_apps} == {"bigquery_workloads", "cost", "fabric_mapping"}
    # Phase 7 Slice 7-C: Snowflake starts Snowflake-only; Slice 7-D
    # widens ``fabric_mapping`` to include SNOWFLAKE; Slice 7-H widens
    # ``cost`` via ``snowflake_cost_client`` reading
    # ``ACCOUNT_USAGE.METERING_HISTORY`` joined to
    # ``USAGE_IN_CURRENCY_DAILY``.
    sf_apps = applicable_modules(SourceType.SNOWFLAKE)
    assert {m.name for m in sf_apps} == {"snowflake_workloads", "cost", "fabric_mapping"}

def test_module_spec_is_frozen():
    spec = MODULE_SPECS["pipelines"]
    try:
        spec.name = "x"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ModuleSpec must be frozen")


def test_module_spec_supports_is_frozenset():
    for spec in MODULE_SPECS.values():
        assert isinstance(spec.supports, frozenset)
