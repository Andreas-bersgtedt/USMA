"""Tests for notebook lint heuristics."""
from usma.modules.spark_pools import notebook_lint


def _ids(findings):
    return {f.rule_id for f in findings}


def test_mssparkutils_call_detected():
    src = "mssparkutils.fs.ls('/tmp')"
    assert "mssparkutils-call" in _ids(notebook_lint.lint_source(src))


def test_synapsesql_detected():
    src = "df.write.synapsesql('schema.table')"
    assert "synapsesql" in _ids(notebook_lint.lint_source(src))


def test_clean_source_emits_no_findings():
    assert notebook_lint.lint_source("import pandas as pd\n# nothing to see here") == []


def test_lint_notebooks_uses_provider():
    nbs = [{"name": "nb1"}, {"name": "nb2"}]

    def provider(nb):
        return "mssparkutils.fs.ls('/')" if nb["name"] == "nb1" else ""

    out = notebook_lint.lint_notebooks(nbs, provider)
    assert "nb1" in out and "nb2" not in out
