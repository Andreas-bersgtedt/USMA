"""T-SQL surface heuristics for Fabric Warehouse compatibility.

Detects keywords and constructs in stored-procedure / view / function bodies that are
either unsupported or behave differently in Fabric Warehouse. This is a deliberately
*conservative* keyword-scan — false positives are expected and should be reviewed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Keyword -> (severity, short reason)
# severity: blocker | warning | info
_PATTERNS: list[tuple[str, str, str, str]] = [
    # id_suffix, severity, label, regex
    ("merge",        "warning", "MERGE statement",
     r"\bMERGE\s+INTO\b|\bMERGE\s+\["),
    ("cursor",       "warning", "CURSOR usage",
     r"\bDECLARE\s+\w+\s+CURSOR\b"),
    ("temp_table",   "info",    "Local temp table (#tbl)",
     r"\b(FROM|INTO|JOIN|UPDATE)\s+#\w+"),
    ("global_temp",  "warning", "Global temp table (##tbl)",
     r"\b(FROM|INTO|JOIN|UPDATE)\s+##\w+"),
    ("identity",     "info",    "IDENTITY column property",
     r"\bIDENTITY\s*\("),
    ("xml_methods",  "warning", "XML data-type methods",
     r"\.\s*(value|nodes|exist|query|modify)\s*\("),
    ("openrowset",   "info",    "OPENROWSET / OPENJSON",
     r"\bOPENROWSET\s*\(|\bOPENJSON\s*\("),
    ("clr_proc",     "blocker", "External / CLR procedure",
     r"\bEXTERNAL\s+NAME\b"),
    ("triggers",     "blocker", "DML / DDL trigger",
     r"\bCREATE\s+TRIGGER\b"),
    ("user_defined_type", "warning", "User-defined type",
     r"\bCREATE\s+TYPE\b"),
    ("sequence",     "info",    "SEQUENCE object",
     r"\bCREATE\s+SEQUENCE\b|\bNEXT\s+VALUE\s+FOR\b"),
    ("crossdb_3pn",  "warning", "Three-part name (cross-DB) reference",
     r"\b\[?\w+\]?\.\[?\w+\]?\.\[?\w+\]?\b"),
    ("rowversion",   "info",    "ROWVERSION / TIMESTAMP column",
     r"\b(ROWVERSION|TIMESTAMP)\b"),
    ("date_funcs",   "info",    "DATEPART/DATEADD on uncommon parts",
     r"\b(DATEPART|DATEADD)\s*\(\s*(NANOSECOND|MICROSECOND|TIMEZONEOFFSET)\b"),
]

_COMPILED: list[tuple[str, str, str, re.Pattern[str]]] = [
    (sid, sev, label, re.compile(pat, re.IGNORECASE)) for sid, sev, label, pat in _PATTERNS
]


@dataclass(frozen=True)
class TsqlFinding:
    rule_id: str
    severity: str
    label: str
    matches: int


def scan(text: str | None) -> list[TsqlFinding]:
    """Return one finding per matched rule with the count of matches."""
    if not text:
        return []
    out: list[TsqlFinding] = []
    for sid, sev, label, pat in _COMPILED:
        n = len(pat.findall(text))
        if n:
            out.append(TsqlFinding(rule_id=sid, severity=sev, label=label, matches=n))
    return out


def fabric_action_for(rule_id: str) -> str:
    return _ACTIONS.get(rule_id, "Review and rewrite for Fabric Warehouse T-SQL surface.")


_ACTIONS: dict[str, str] = {
    "merge":         "Rewrite MERGE as INSERT + UPDATE (or DELETE + INSERT) — MERGE is not supported in Fabric Warehouse.",
    "cursor":        "Replace cursor with set-based logic; cursors are inefficient and discouraged in Fabric.",
    "temp_table":    "Local temp tables are supported but check for usage in distributed joins; consider CTAS.",
    "global_temp":   "Global temp tables (##) are not supported in Fabric Warehouse — refactor to permanent or local temp.",
    "identity":      "IDENTITY columns work but values are not guaranteed contiguous; verify downstream consumers.",
    "xml_methods":   "XML data-type methods are not supported in Fabric Warehouse; pre-process upstream or in Spark.",
    "openrowset":    "OPENROWSET/OPENJSON syntax differs in Fabric Warehouse; use COPY INTO or OneLake shortcuts.",
    "clr_proc":      "External / CLR procedures are not supported in Fabric Warehouse.",
    "triggers":      "DML and DDL triggers are not supported in Fabric Warehouse.",
    "user_defined_type": "User-defined types are not supported; replace with built-in equivalents.",
    "sequence":      "SEQUENCE objects are supported but verify usage patterns work with Fabric isolation.",
    "crossdb_3pn":   "Cross-database three-part name references are restricted in Fabric Warehouse — restructure schema.",
    "rowversion":    "ROWVERSION/TIMESTAMP behaviour differs; verify CDC strategies after migration.",
    "date_funcs":    "Some DATEPART/DATEADD parts are not supported in Fabric — verify equivalents.",
}


def aggregate_by_rule(findings_per_object: Iterable[tuple[str, list[TsqlFinding]]]) -> dict[str, list[tuple[str, int]]]:
    """Group findings by rule_id -> [(target, match_count), ...]."""
    grouped: dict[str, list[tuple[str, int]]] = {}
    for target, findings in findings_per_object:
        for f in findings:
            grouped.setdefault(f.rule_id, []).append((target, f.matches))
    return grouped
