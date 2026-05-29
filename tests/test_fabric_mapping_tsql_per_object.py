"""Tests for the fabric_mapping T-SQL compatibility rollup that surfaces the
SQL-plane analysis (stored procedures + functions + views) in the readiness
summary.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from usma.config import AppConfig, AzureConfig, SqlConfig
from usma.modules.fabric_mapping.analyzer import FabricMappingAnalyzer


def _write_dedicated_pools_payload(out_dir, pool_name="pool1", *, objects):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pools": [
            {
                "inventory": {"name": pool_name, "location": "westeurope"},
                "schemas": [],
                "tables": [],
                "code_objects": objects,
                "errors": [],
            }
        ],
    }
    (out_dir / "dedicated_pools.json").write_text(json.dumps(payload), encoding="utf-8")


def _make_cfg(tmp_path):
    return AppConfig(
        azure=AzureConfig(
            tenant_id="t", client_id="c", client_secret="s",
            subscription_id="sub", resource_group="rg", workspace_name="ws",
        ),
        sql=SqlConfig(),
        output_dir=tmp_path,
    )


def test_fabric_mapping_tsql_compatibility_rollup(tmp_path):
    objs = [
        {
            "schema_name": "dbo", "object_name": "p_clean",
            "object_type": "SQL_STORED_PROCEDURE",
            "code_object_id": "dbo.p_clean.sql_stored_procedure",
            "compatibility": "compatible",
        },
        {
            "schema_name": "dbo", "object_name": "p_warn",
            "object_type": "SQL_STORED_PROCEDURE",
            "code_object_id": "dbo.p_warn.sql_stored_procedure",
            "compatibility": "needs_review",
        },
        {
            "schema_name": "dbo", "object_name": "p_block",
            "object_type": "SQL_STORED_PROCEDURE",
            "code_object_id": "dbo.p_block.sql_stored_procedure",
            "compatibility": "incompatible",
        },
        {
            "schema_name": "dbo", "object_name": "v1", "object_type": "VIEW",
            "code_object_id": "dbo.v1.view", "compatibility": "compatible",
        },
    ]
    _write_dedicated_pools_payload(tmp_path, objects=objs)

    report = FabricMappingAnalyzer(_make_cfg(tmp_path)).run()

    assert report.readiness is not None
    rd = report.readiness
    assert rd.tsql_objects_total == 4
    assert rd.tsql_objects_incompatible == 1
    assert rd.tsql_objects_needs_review == 1
    # 2 / 4 compatible -> 50.0%
    assert rd.tsql_compatibility_pct == 50.0


def test_fabric_mapping_tsql_rollup_without_code_objects(tmp_path):
    # No payload at all -> readiness still produced, but T-SQL fields stay zero.
    report = FabricMappingAnalyzer(_make_cfg(tmp_path)).run()
    assert report.readiness is not None
    assert report.readiness.tsql_objects_total == 0
    assert report.readiness.tsql_compatibility_pct is None
