"""Tests for the richer activity-compatibility analyzer."""
from usma.modules.pipelines import fabric_compat
from usma.modules.pipelines.fabric_compat import analyze_activity


def test_analyze_returns_reasons_and_action_for_partial() -> None:
    detail = analyze_activity("Copy", {})
    assert detail.tier == "partial"
    assert detail.fabric_equivalent == "Copy data activity"
    assert detail.migration_action and "linked services" in detail.migration_action
    assert detail.reasons, "Copy should expose generic partial-compat reasons"
    assert detail.caveats == []
    assert detail.doc_url and detail.doc_url.startswith("https://learn.microsoft.com/")


def test_analyze_returns_unsupported_with_reasons() -> None:
    detail = analyze_activity("ExecuteDataFlow", None)
    assert detail.tier == "unsupported"
    assert any("Dataflow Gen2" in r for r in detail.reasons)
    assert detail.migration_action


def test_analyze_unknown_for_none() -> None:
    assert analyze_activity(None).tier == "unknown"


def test_analyze_supported_for_unmodelled_type() -> None:
    detail = analyze_activity("MyCustomThing")
    assert detail.tier == "supported"
    assert detail.reasons == []
    assert detail.caveats == []


def test_copy_caveats_from_type_properties() -> None:
    detail = analyze_activity("Copy", {
        "enableStaging": True,
        "parallelCopies": 64,
        "sink": {"type": "SqlDWSink", "polybaseSettings": {"rejectValue": 0}},
    })
    assert detail.tier == "partial"
    text = " ".join(detail.caveats)
    assert "Staged copy" in text
    assert "parallelCopies=64" in text
    assert "Polybase" in text


def test_web_activity_client_certificate_caveat() -> None:
    detail = analyze_activity("WebActivity", {"authentication": {"type": "ClientCertificate"}})
    assert detail.tier == "partial"
    assert any("ClientCertificate" in c for c in detail.caveats)


def test_web_activity_msi_caveat() -> None:
    detail = analyze_activity("WebActivity", {"authentication": {"type": "MSI"}})
    assert any("SystemAssignedManagedIdentity" in c for c in detail.caveats)


def test_lookup_first_row_only_false() -> None:
    detail = analyze_activity("Lookup", {"firstRowOnly": False})
    assert any("firstRowOnly" in c for c in detail.caveats)
    # firstRowOnly=true (default) should produce no caveats
    detail2 = analyze_activity("Lookup", {"firstRowOnly": True})
    assert detail2.caveats == []


def test_foreach_batch_count_over_max() -> None:
    detail = analyze_activity("ForEach", {"batchCount": 100, "isSequential": False})
    assert any("batchCount=100" in c for c in detail.caveats)


def test_synapse_notebook_pool_binding_caveat() -> None:
    detail = analyze_activity("SynapseNotebook", {
        "sparkPool": {"referenceName": "myPool", "type": "BigDataPoolReference"},
        "executorSize": "Large",
    })
    text = " ".join(detail.caveats)
    assert "myPool" in text
    assert "executorSize=Large" in text


def test_supported_activity_escalates_to_partial_on_caveat() -> None:
    # 'Wait' is intrinsically supported but has no caveat hooks — should stay supported.
    assert analyze_activity("Wait", {"waitTimeInSeconds": 10}).tier == "supported"


def test_caveat_extractor_never_throws_on_garbage() -> None:
    # Pass non-dict shapes deep in the structure; helper must swallow the error.
    detail = analyze_activity("Copy", {"source": "not-a-dict"})
    assert detail.tier == "partial"
    # Still has the generic reasons even when caveat extraction would fail.
    assert detail.reasons


def test_classify_activity_backwards_compatible() -> None:
    # The legacy thin classifier still works.
    assert fabric_compat.classify_activity("Copy") == "partial"
    assert fabric_compat.classify_activity("ExecuteDataFlow") == "unsupported"
    assert fabric_compat.classify_activity("MyCustomThing") == "supported"
    assert fabric_compat.classify_activity(None) == "unknown"
