"""Synapse pipeline expression-language compatibility checks for Fabric Data Factory.

Fabric DF inherits most of ADF / Synapse pipelines' expression language but a few
system variables, functions, and pipeline-internal references behave differently
or are not yet available. This module scans expressions found in activity payloads
and emits :class:`ExpressionFinding` records.

We deliberately keep the checks coarse — false positives are preferable to false
negatives because the recommendations are advisory.

Rules:

* **system-var-data-factory** — ``@pipeline().DataFactory`` is renamed in Fabric
  (``@pipeline().Workspace`` style). Flag it.
* **system-var-pipeline-id** — ``@pipeline().GroupId`` is Synapse-only.
* **trigger-output** — ``@trigger().outputs`` payload differs between Synapse and Fabric.
* **getmetadata-childItems** — Fabric `Get Metadata`'s ``childItems`` field shape changed
  (paths vs names). Flag for manual review.
* **lookup-output-shape** — ``@activity('lookup').output.firstRow`` is supported, but
  ``output.value[0]`` semantics for arrays-of-objects sometimes differ.
* **synapse-only-fn** — explicit list of functions known to be Synapse-only.
* **secrets-keyvault** — ``@listSecret`` / ``getSecret`` patterns; Fabric uses workspace
  managed identities and Fabric secrets, not the Synapse linked-service shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

Severity = Literal["info", "warning", "blocker"]


@dataclass(frozen=True)
class ExpressionFinding:
    rule_id: str
    label: str
    severity: Severity
    pipeline: str
    activity: str
    expression: str   # the offending expression (truncated)


_SYNAPSE_ONLY_FUNCTIONS: frozenset[str] = frozenset({
    # These are heuristic; revisit against current Fabric DF function reference.
    "addToTime",     # exists in both, but argument order differed historically — flag for review
    "trigger",       # @trigger().outputs payload differs — flagged separately too
})

# (rule_id, label, severity, regex, fabric_action)
_RULES: tuple[tuple[str, str, Severity, re.Pattern[str], str], ...] = (
    ("system-var-data-factory",
     "@pipeline().DataFactory reference",
     "warning",
     re.compile(r"@pipeline\(\)\.DataFactory\b", re.I),
     "Replace with the Fabric equivalent (`@pipeline().Workspace` / static workspace name)."),

    ("system-var-pipeline-groupid",
     "@pipeline().GroupId reference",
     "warning",
     re.compile(r"@pipeline\(\)\.GroupId\b", re.I),
     "GroupId is Synapse-only. Use `pipeline().RunId` or remove the dependency on group ids."),

    ("trigger-output",
     "@trigger().outputs reference",
     "info",
     re.compile(r"@trigger\(\)\.outputs", re.I),
     "Validate the trigger output shape — Fabric tumbling/event triggers expose different fields."),

    ("getmetadata-childitems",
     "@activity(...).output.childItems reference",
     "info",
     re.compile(r"@activity\([^)]+\)\.output\.childItems", re.I),
     "Verify `childItems` shape; Fabric returns slightly different fields for blob/lake listings."),

    ("lookup-array-output",
     "Lookup output[index] indexing",
     "info",
     re.compile(r"@activity\([^)]+\)\.output\.(?:value|firstRow)\[\d+\]", re.I),
     "Confirm the Lookup `firstRow=false` array shape matches in Fabric."),

    ("secrets-keyvault",
     "@listSecret / getSecret reference",
     "warning",
     re.compile(r"@(?:listSecret|getSecret)\b", re.I),
     "Fabric pipelines retrieve Key Vault secrets via Web Activity + workspace MI, not via this expression."),
)


def fabric_action_for(rule_id: str) -> str | None:
    for rid, _label, _sev, _pat, action in _RULES:
        if rid == rule_id:
            return action
    return None


def scan_expression(expr: str | None, *, pipeline: str, activity: str) -> list[ExpressionFinding]:
    """Run every rule against a single expression string."""
    if not expr:
        return []
    out: list[ExpressionFinding] = []
    for rule_id, label, severity, pattern, _action in _RULES:
        if pattern.search(expr):
            out.append(ExpressionFinding(
                rule_id=rule_id, label=label, severity=severity,
                pipeline=pipeline, activity=activity,
                expression=expr if len(expr) <= 200 else expr[:197] + "...",
            ))
    # Function-name pass — looking for `@<name>(` where name is in the synapse-only set.
    for fn in _SYNAPSE_ONLY_FUNCTIONS:
        if re.search(rf"@{re.escape(fn)}\s*\(", expr, re.I):
            out.append(ExpressionFinding(
                rule_id="synapse-only-fn",
                label=f"Synapse-only function `{fn}()`",
                severity="warning",
                pipeline=pipeline, activity=activity,
                expression=expr if len(expr) <= 200 else expr[:197] + "...",
            ))
    return out


def scan_payload(payload: object, *, pipeline: str, activity: str) -> list[ExpressionFinding]:
    """Walk an arbitrary nested JSON payload (dict/list/scalar) and scan every string
    that looks like an expression (starts with ``@``)."""
    out: list[ExpressionFinding] = []
    _walk(payload, pipeline=pipeline, activity=activity, out=out)
    return out


def _walk(node: object, *, pipeline: str, activity: str, out: list[ExpressionFinding]) -> None:
    if isinstance(node, str):
        if node.lstrip().startswith("@"):
            out.extend(scan_expression(node, pipeline=pipeline, activity=activity))
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk(v, pipeline=pipeline, activity=activity, out=out)
        return
    if isinstance(node, (list, tuple)):
        for v in node:
            _walk(v, pipeline=pipeline, activity=activity, out=out)
        return


def scan_activities(activities: Iterable[dict]) -> list[ExpressionFinding]:
    """Scan every ``activity['type_properties']`` payload, plus any explicit expressions
    we know about (``activity['expression']`` for Until / IfCondition / Switch)."""
    out: list[ExpressionFinding] = []
    for a in activities:
        pipeline = a.get("pipeline") or "?"
        name = a.get("name") or "?"
        for key in ("typeProperties", "type_properties", "userProperties"):
            if key in a:
                out.extend(scan_payload(a[key], pipeline=pipeline, activity=name))
        for key in ("expression", "condition"):
            v = a.get(key)
            if isinstance(v, str):
                out.extend(scan_expression(v, pipeline=pipeline, activity=name))
            elif isinstance(v, dict) and isinstance(v.get("value"), str):
                out.extend(scan_expression(v["value"], pipeline=pipeline, activity=name))
    return out
