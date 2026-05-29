"""Tests for pipeline expression-language compatibility scanner."""
from usma.modules.pipelines import expression_compat


def _ids(findings):
    return {f.rule_id for f in findings}


def test_data_factory_system_var_detected():
    out = expression_compat.scan_expression(
        "@pipeline().DataFactory", pipeline="p", activity="a",
    )
    assert "system-var-data-factory" in _ids(out)


def test_recursive_walk_finds_secret_call():
    payload = {"settings": {"connectionString": "@listSecret('kvls', 'connstr')"}}
    out = expression_compat.scan_payload(payload, pipeline="p", activity="a")
    assert "secrets-keyvault" in _ids(out)


def test_clean_expression_emits_nothing():
    assert expression_compat.scan_expression("@string('x')", pipeline="p", activity="a") == []
