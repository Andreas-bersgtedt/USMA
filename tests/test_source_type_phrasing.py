"""Source-aware phrasing in fabric_mapping rules + runbook.

Pinning the wording so an ADF scope never sees Synapse-only recommendation
text and a Synapse scope keeps the historical wording.
"""
from __future__ import annotations

from usma.modules.fabric_mapping.models import Recommendation
from usma.modules.fabric_mapping.rules import rules_for_pipelines
from usma.modules.fabric_mapping.runbook import _rollback_hint_for


def _payload() -> dict:
    return {"pipelines": [{"name": "p1"}, {"name": "p2"}]}


def test_pipelines_inventory_default_uses_synapse_wording() -> None:
    recs = rules_for_pipelines(_payload())
    inv = next(r for r in recs if r.id == "pl.pipelines_inventory")
    assert "Synapse pipeline(s)" in inv.title
    assert "Synapse pipelines" in inv.detail


def test_pipelines_inventory_synapse_source_keeps_synapse_wording() -> None:
    recs = rules_for_pipelines(_payload(), source_type="synapse_workspace")
    inv = next(r for r in recs if r.id == "pl.pipelines_inventory")
    assert "Synapse pipeline(s)" in inv.title
    assert "Synapse pipelines" in inv.detail


def test_pipelines_inventory_adf_source_uses_adf_wording() -> None:
    recs = rules_for_pipelines(_payload(), source_type="adf")
    inv = next(r for r in recs if r.id == "pl.pipelines_inventory")
    assert "Azure Data Factory pipeline(s)" in inv.title
    assert "Azure Data Factory pipelines" in inv.detail
    assert "Synapse" not in inv.title
    assert "Synapse" not in inv.detail


def test_rollback_hint_for_pipelines_synapse() -> None:
    rec = Recommendation(
        id="x", area="pipelines.activities", title="t", detail="d",
        source_type="synapse_workspace",
    )
    hint = _rollback_hint_for(rec)
    assert hint is not None
    assert "Synapse pipeline" in hint
    assert "Data Factory" not in hint


def test_rollback_hint_for_pipelines_adf() -> None:
    rec = Recommendation(
        id="x", area="pipelines.activities", title="t", detail="d",
        source_type="adf",
    )
    hint = _rollback_hint_for(rec)
    assert hint is not None
    assert "Azure Data Factory pipeline" in hint
    assert "Synapse pipeline" not in hint


def test_recommendation_source_type_defaults_to_none() -> None:
    """Legacy artefacts deserialise without the new field."""
    rec = Recommendation(id="x", area="dedicated_pools.tables", title="t", detail="d")
    assert rec.source_type is None
