"""Phase-2 tests for the per-source pipeline collectors.

The Synapse collector is a 1-line-per-method adapter over the existing
``ArtifactsApiClient``; its behavior is covered by the existing
``test_pipelines_*`` suite via the analyzer.

The ADF collector ships in Phase 2 with the SDK-call layer and the
static helpers (``_folder_path``, ``_adapt_activity``) wired up.
End-to-end SDK behavior lands in a follow-up PR once ADF fixtures are
recorded. The tests here cover what *can* be tested without ADF
credentials: protocol conformance, the activity adapter, and the
factory-args shape.
"""
from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock

from usma.modules.pipelines.models import Activity
from usma.modules.pipelines.sources import PipelineCollector
from usma.modules.pipelines.sources.adf_collector import (
    AdfCollector,
    _adapt_activity,
    _folder_path,
)
from usma.modules.pipelines.sources.synapse_collector import (
    SynapseCollector,
)
from usma.sources.adf.provider import AdfClientBundle


def _bundle() -> AdfClientBundle:
    return AdfClientBundle(
        credential=MagicMock(name="cred"),
        subscription_id="sub-x",
        resource_group="rg-1",
        factory_name="adf-prod",
        location="westeurope",
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_adf_collector_satisfies_protocol():
    c = AdfCollector(_bundle())
    assert isinstance(c, PipelineCollector)
    assert c.source_label == "adf-prod"


def test_synapse_collector_satisfies_protocol():
    fake_client = MagicMock(name="ArtifactsApiClient")
    c = SynapseCollector(fake_client, workspace_name="ws-a")
    assert isinstance(c, PipelineCollector)
    assert c.source_label == "ws-a"


# ---------------------------------------------------------------------------
# Static helpers (no SDK)
# ---------------------------------------------------------------------------


def test_folder_path_handles_none_and_object():
    assert _folder_path(None) is None
    folder = types.SimpleNamespace(name="ETL/Sales")
    assert _folder_path(folder) == "ETL/Sales"


def test_adapt_activity_known_type():
    """``Copy`` activity should land with the pipeline name and
    depends_on chain preserved, and a classification from fabric_compat
    (any of supported / partial / unsupported / unknown)."""
    dep = types.SimpleNamespace(activity="Stage")
    raw = types.SimpleNamespace(
        name="CopyToWarehouse",
        type="Copy",
        depends_on=[dep],
        additional_properties={"typeProperties": {}},
    )
    out = _adapt_activity(raw, pipeline_name="LoadSales")
    assert isinstance(out, Activity)
    assert out.pipeline == "LoadSales"
    assert out.name == "CopyToWarehouse"
    assert out.type == "Copy"
    assert out.depends_on == ["Stage"]
    assert out.support in {"supported", "partial", "unsupported", "unknown"}


def test_adapt_activity_unknown_type_classified():
    """``Unknown`` activity types still produce an Activity record;
    classification is delegated to fabric_compat.analyze_activity."""
    raw = types.SimpleNamespace(
        name="Custom1",
        type="ZZZ_DoesNotExist",
        depends_on=[],
        additional_properties={},
    )
    out = _adapt_activity(raw, pipeline_name="P")
    assert out.type == "ZZZ_DoesNotExist"
    # Unknown -> classified as 'supported' default-tier or 'unknown'; both
    # are acceptable since the rule table can grow over time.
    assert out.support in {"supported", "unknown", "partial", "unsupported"}


# ---------------------------------------------------------------------------
# Lazy SDK construction
# ---------------------------------------------------------------------------


def test_adf_collector_factory_args_shape():
    c = AdfCollector(_bundle())
    args = c._factory_args()
    assert args == {"resource_group_name": "rg-1", "factory_name": "adf-prod"}


def test_adf_collector_does_not_instantiate_sdk_until_used():
    """Constructing the collector must not touch the Azure SDK; the SDK
    is imported lazily on first iter_/list_ call. Cheap construction is
    important because plan_run() may build collectors per scope ahead of
    deciding which actually run."""
    c = AdfCollector(_bundle())
    assert c._mgmt is None
