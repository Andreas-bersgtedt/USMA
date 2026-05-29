"""Tests for pipelines fabric_compat helpers."""
from usma.modules.pipelines import fabric_compat


def test_classify_activity_levels() -> None:
    assert fabric_compat.classify_activity("ExecuteDataFlow") == "unsupported"
    assert fabric_compat.classify_activity("Copy") == "partial"
    assert fabric_compat.classify_activity("MyCustomThing") == "supported"
    assert fabric_compat.classify_activity(None) == "unknown"


def test_linked_service_supported() -> None:
    assert fabric_compat.linked_service_supported("AzureSqlDatabase") is True
    assert fabric_compat.linked_service_supported("HDInsight") is False
    assert fabric_compat.linked_service_supported(None) is True
