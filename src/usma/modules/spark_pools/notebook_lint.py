"""Notebook lint pass for Synapse-only patterns that need rewriting for Fabric.

This is a pure-python regex pass over the notebook *source* captured by
``artifacts_client``. It does not parse cells; we operate on the concatenated source
because that's what the v1 ``Notebook.source_size_chars`` field already represents.

Findings are emitted as :class:`NotebookLintFinding` records with a stable ``rule_id``
so the fabric_mapping aggregator can group them.

Rules (initial set — extend as Fabric Spark coverage evolves):

* **mssparkutils-import** — ``from notebookutils import mssparkutils`` or ``import mssparkutils``.
  In Fabric, use ``notebookutils`` (drop the ``mssparkutils`` alias).
* **mssparkutils-call** — any ``mssparkutils.*`` reference. Same fix.
* **synapsesql** — ``synapsesql`` connector usage; replace with Fabric Lakehouse / Warehouse table refs.
* **linked-service-mount** — ``mssparkutils.fs.mount(... 'linkedService' ...)`` — Fabric uses OneLake shortcuts.
* **synapse-config** — ``spark.synapse.*`` Spark configs.
* **livy-only-magic** — ``%%configure`` / ``%%spark`` magics that Fabric does not understand the same way.
* **legacy-mssparkutils-credentials** — ``mssparkutils.credentials.*`` is replaced by Fabric workspace identities.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

Severity = Literal["info", "warning", "blocker"]


@dataclass(frozen=True)
class NotebookLintFinding:
    rule_id: str
    label: str
    severity: Severity
    line: int  # 1-based; 0 if unknown
    snippet: str


# (rule_id, label, severity, regex)
_RULES: tuple[tuple[str, str, Severity, re.Pattern[str]], ...] = (
    ("mssparkutils-import", "import of mssparkutils", "warning",
     re.compile(r"^\s*(?:from\s+notebookutils\s+import\s+mssparkutils|import\s+mssparkutils)\b", re.M)),
    ("mssparkutils-call", "mssparkutils API call", "warning",
     re.compile(r"\bmssparkutils\.[A-Za-z_][\w.]*", re.M)),
    ("synapsesql", "synapsesql connector", "warning",
     re.compile(r"\bsynapsesql\b", re.I)),
    ("linked-service-mount", "linkedService mount", "blocker",
     re.compile(r"mssparkutils\.fs\.mount\([^)]*linkedService", re.I | re.S)),
    ("synapse-config", "spark.synapse.* configuration", "info",
     re.compile(r"\bspark\.synapse\.[A-Za-z0-9_.]+", re.I)),
    ("livy-only-magic", "Synapse %%configure / %%spark magic", "info",
     re.compile(r"^\s*%%(?:configure|spark)\b", re.M)),
    ("legacy-mssparkutils-credentials", "mssparkutils.credentials usage", "warning",
     re.compile(r"\bmssparkutils\.credentials\.[A-Za-z_][\w]*", re.M)),
)

_FABRIC_ACTIONS: dict[str, str] = {
    "mssparkutils-import":
        "Drop the `mssparkutils` alias; in Fabric notebooks use `import notebookutils` directly.",
    "mssparkutils-call":
        "Replace the `mssparkutils` namespace with `notebookutils` (most APIs are 1:1; "
        "verify file-system, credentials, and runtime APIs).",
    "synapsesql":
        "Read/write via Fabric Lakehouse tables or COPY INTO Fabric Warehouse instead of "
        "the Synapse `synapsesql` connector.",
    "linked-service-mount":
        "Replace the linked-service mount with a OneLake shortcut to the same storage location, "
        "then read it as a regular path (no mount needed).",
    "synapse-config":
        "Synapse-specific Spark configs (`spark.synapse.*`) are no-ops in Fabric; review and remove.",
    "livy-only-magic":
        "`%%configure` is honored in Fabric only at session start; `%%spark` Livy-style magics "
        "are not supported. Move config into the Fabric notebook environment or first cell.",
    "legacy-mssparkutils-credentials":
        "`mssparkutils.credentials.*` does not exist in Fabric; use `notebookutils.credentials` "
        "or workspace-identity authentication.",
}


def fabric_action_for(rule_id: str) -> str | None:
    return _FABRIC_ACTIONS.get(rule_id)


def lint_source(source: str | None) -> list[NotebookLintFinding]:
    """Run every rule against ``source``. Returns at most one finding per rule per match."""
    if not source:
        return []
    out: list[NotebookLintFinding] = []
    line_starts = _line_starts(source)
    for rule_id, label, severity, pattern in _RULES:
        for m in pattern.finditer(source):
            line = _line_of(line_starts, m.start())
            snippet = source[max(0, m.start() - 10):m.end() + 30].replace("\n", " ")
            out.append(NotebookLintFinding(
                rule_id=rule_id, label=label, severity=severity,
                line=line, snippet=snippet[:160],
            ))
    return out


def lint_notebooks(
    notebooks: Iterable[dict],
    source_provider,
) -> dict[str, list[NotebookLintFinding]]:
    """Run :func:`lint_source` over each notebook.

    ``source_provider(notebook)`` must return the raw notebook source string, or None
    if the source could not be loaded. Failures from the provider are tolerated:
    the affected notebook is silently skipped (the analyzer logs separately).
    """
    out: dict[str, list[NotebookLintFinding]] = {}
    for nb in notebooks:
        name = nb.get("name") or "?"
        try:
            src = source_provider(nb)
        except Exception:  # noqa: BLE001 - source loading is best-effort
            continue
        findings = lint_source(src)
        if findings:
            out[name] = findings
    return out


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_of(starts: list[int], pos: int) -> int:
    # Binary search would be tidier; linear is fine for typical notebook sizes.
    line = 1
    for s in starts:
        if s > pos:
            break
        line = starts.index(s) + 1 if False else line  # keep type checker happy
    # Simple iterative version:
    line = 1
    for s in starts:
        if s <= pos:
            line += 1
        else:
            break
    return max(1, line - 1)
