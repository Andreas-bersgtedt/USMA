"""Tests for the T-SQL surface scanner used by fabric_mapping rules."""
from usma.modules.fabric_mapping import tsql_surface


def test_scan_detects_merge_and_cursor() -> None:
    code = """
    CREATE PROCEDURE dbo.upsert_customer AS
    BEGIN
        MERGE INTO dbo.customer AS tgt
        USING dbo.staging_customer AS src
        ON tgt.id = src.id
        WHEN MATCHED THEN UPDATE SET tgt.name = src.name;

        DECLARE c1 CURSOR FOR SELECT id FROM dbo.customer;
    END
    """
    findings = {f.rule_id: f for f in tsql_surface.scan(code)}
    assert "merge" in findings
    assert "cursor" in findings
    assert findings["merge"].severity == "warning"
    assert findings["merge"].matches >= 1


def test_scan_detects_global_temp_and_xml() -> None:
    code = "SELECT * FROM ##GlobalTemp; SELECT x.value('/a', 'int') FROM @v x;"
    findings = {f.rule_id for f in tsql_surface.scan(code)}
    assert "global_temp" in findings
    assert "xml_methods" in findings


def test_scan_returns_nothing_for_simple_select() -> None:
    assert tsql_surface.scan("SELECT 1") == []
    assert tsql_surface.scan("") == []
    assert tsql_surface.scan(None) == []


def test_fabric_action_known_and_unknown() -> None:
    assert "MERGE" in tsql_surface.fabric_action_for("merge")
    # Unknown rule id falls back to a generic action.
    assert "Review" in tsql_surface.fabric_action_for("does_not_exist")
