"""Tests for the spark_pools runtime compatibility table."""
from usma.modules.spark_pools import runtime_compat


def test_unknown_version_is_unknown_status():
    assert runtime_compat.map_runtime("9.9").status == "unknown"


def test_none_version_is_unknown():
    assert runtime_compat.map_runtime(None).status == "unknown"


def test_known_versions_have_status():
    for v in ("2.4", "3.1", "3.2", "3.3", "3.4"):
        m = runtime_compat.map_runtime(v)
        assert m.status in {"matches", "upgrade", "deprecated"}
        if m.status != "deprecated":
            assert m.fabric_runtime is not None
