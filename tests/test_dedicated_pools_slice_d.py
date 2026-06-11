"""Slice D verification tests for standalone Dedicated SQL pool support.

Proves the existing Estate Overview aggregator and fabric_mapping module
work unchanged on a ``synapse_dedicated_sql`` scope. Slice D added no
new code paths to either of them \u2014 only widened predicates and
labels \u2014 so these tests are guardrails against future regressions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules.fabric_mapping.analyzer import FabricMappingAnalyzer
from usma.modules.spec import MODULE_SPECS
from usma.sources import SourceType
from usma.web.estate import _cloud_for
from usma.web.storage import _SCOPE_DIR_RE


def test_fabric_mapping_supports_standalone_dedicated_sql() -> None:
    spec = MODULE_SPECS["fabric_mapping"]
    assert spec.supports_source(SourceType.SYNAPSE_DEDICATED_SQL)
    # And the dedicated_pools module already does.
    assert MODULE_SPECS["dedicated_pools"].supports_source(
        SourceType.SYNAPSE_DEDICATED_SQL
    )


def test_scope_dir_re_accepts_synapse_dedicated_sql() -> None:
    m = _SCOPE_DIR_RE.fullmatch("synapse_dedicated_sql__examplesqlserver")
    assert m is not None
    assert m.group("source") == "synapse_dedicated_sql"
    assert m.group("slug") == "examplesqlserver"
    # Path-traversal still rejected.
    assert _SCOPE_DIR_RE.fullmatch("synapse_dedicated_sql__..") is None
    assert _SCOPE_DIR_RE.fullmatch("synapse_dedicated_sql__") is None


def test_estate_cloud_for_synapse_dedicated_sql_is_azure() -> None:
    # Standalone DWU is Azure-native, so the Estate Overview must
    # bucket it under "azure" (same cloud chip as Synapse + ADF).
    assert _cloud_for("synapse_dedicated_sql", None, None) == "azure"
    assert _cloud_for("synapse_dedicated_sql", None, "any.host.example") == "azure"


def _cfg(out: Path) -> AppConfig:
    return AppConfig(
        azure=AzureConfig(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            subscription_id="sub",
            resource_group="rg",
            workspace_name="examplesqlserver",
        ),
        sql=SqlConfig(),
        output_dir=out,
    )


def test_fabric_mapping_consumes_standalone_dedicated_pools_unchanged(
    tmp_path: Path,
) -> None:
    """A ``dedicated_pools.json`` produced from a standalone DWU server
    has the same shape as one produced from a Synapse-workspace pool;
    fabric_mapping must emit identical recommendation titles for the
    same findings regardless of topology (the analyzer reads the
    artifact, not the source type)."""
    payload = {
        # The "workspace_name" key carries the SQL server name when
        # the source type is synapse_dedicated_sql; fabric_mapping
        # treats it as an opaque label.
        "workspace_name": "examplesqlserver",
        "pools": [
            {
                "inventory": {
                    "name": "edw",
                    "status": "Online",
                    "collation": "SQL_Latin1_General_CP1_CI_AS",
                },
                "tables": [
                    {
                        "schema_name": "dbo",
                        "table_name": "big_replicate",
                        "distribution_policy": "REPLICATE",
                        "row_count": 200_000_000,
                        "index_type": "CCI",
                    },
                ],
                "workload_groups": [{"name": "wg1"}],
                "code_objects": [
                    {
                        "schema_name": "dbo",
                        "object_name": "upsert",
                        "object_type": "SQL_STORED_PROCEDURE",
                        "definition": "MERGE INTO dbo.t USING s ON 1=1 WHEN MATCHED THEN UPDATE SET a=1;",
                    },
                ],
            },
        ],
    }
    (tmp_path / "dedicated_pools.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    report = FabricMappingAnalyzer(_cfg(tmp_path)).run()
    titles = {r.title for r in report.recommendations}
    # The same Synapse-workspace assertions hold verbatim.
    assert any("REPLICATE" in t for t in titles)
    assert any("MERGE" in t for t in titles)
    assert any("collation" in t.lower() for t in titles)
