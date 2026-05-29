"""Run manifest + delta-report support (mid-term v1).

A *run manifest* is a small JSON file persisted next to the module reports that
records, per collector, a content hash of its output, the timestamp it was
produced, and a record-count peek (top-level ``findings`` / ``rows`` length).

Subsequent ``analyze-all`` invocations:

  1. Load the previous manifest.
  2. Hash + peek each collector's *new* output.
  3. Produce a side-by-side delta report against the prior run, in
     Markdown, HTML, and JSON forms (``run_delta.md`` / ``.html`` / ``.json``).
  4. Tag each artifact as **added / removed / changed / unchanged** so callers
     can decide whether to re-render downstream artifacts in a future commit.

This module is pure-Python and fully unit-tested.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Environment, select_autoescape
from pydantic import BaseModel, Field

from .html_common import SHARED_CSS


MANIFEST_FILENAME = "run_manifest.json"
DELTA_FILENAME = "run_delta.md"
DELTA_HTML_FILENAME = "run_delta.html"
DELTA_JSON_FILENAME = "run_delta.json"


class ArtifactRecord(BaseModel):
    name: str  # logical name, e.g. "dedicated_pools"
    file_name: str  # produced JSON file name
    sha256: str
    size_bytes: int
    generated_at: datetime
    record_count: int | None = None  # findings / rows count when peekable
    # --- Schema v2 additions (optional for backwards compat) -------------
    source_type: str | None = None  # e.g. "synapse_workspace", "adf"
    scope_id: str | None = None     # display name of the originating scope


class ScopeRecord(BaseModel):
    """A single scope (source instance) that contributed to this run.

    Added in schema v2 alongside ``RunManifest.scopes``. v1 manifests
    are read-upgraded on load: a single synthetic ``ScopeRecord`` is
    materialized from the top-level ``workspace_name`` /
    ``resource_group`` / ``subscription_id`` fields.

    ``platform`` (added in Phase 4.7) carries the cloud discriminator
    for Databricks scopes: ``"azure"`` / ``"aws"`` / ``"gcp"``. It is
    ``None`` for non-Databricks scopes and for pre-4.7 manifests; the
    v1\u2192v2 upgrader leaves it as ``None``.
    """

    source_type: str  # SourceType.value
    scope_id: str  # human-readable id (workspace name for Synapse)
    display_name: str
    subscription_id: str | None = None
    resource_group: str | None = None
    location: str | None = None
    platform: str | None = None  # Databricks: "azure" | "aws" | "gcp"; else None


# Current on-disk schema version. Bumped to 2 in Phase 1 to add
# per-artifact ``source_type``/``scope_id`` + top-level ``scopes`` array
# for multi-source runs. v1 manifests are read-upgraded by
# ``load_manifest``; they are never rewritten on disk.
MANIFEST_SCHEMA_VERSION = 2


class RunManifest(BaseModel):
    workspace_name: str
    subscription_id: str
    resource_group: str
    generated_at: datetime
    sma_version: str
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    # --- Schema v2 additions (optional for backwards compat) -------------
    schema_version: int = MANIFEST_SCHEMA_VERSION
    scopes: list[ScopeRecord] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class ManifestDiffEntry:
    name: str
    status: str  # "added" | "removed" | "changed" | "unchanged"
    prev_sha: str | None
    curr_sha: str | None
    prev_size: int | None = None
    curr_size: int | None = None
    prev_generated_at: datetime | None = None
    curr_generated_at: datetime | None = None
    prev_record_count: int | None = None
    curr_record_count: int | None = None

    @property
    def size_delta(self) -> int | None:
        if self.prev_size is None or self.curr_size is None:
            return None
        return self.curr_size - self.prev_size

    @property
    def record_count_delta(self) -> int | None:
        if self.prev_record_count is None or self.curr_record_count is None:
            return None
        return self.curr_record_count - self.prev_record_count


# ---------------------------------------------------------------------------
# Hashing + peeking
# ---------------------------------------------------------------------------

def compute_artifact_hash(payload: Any) -> str:
    """Stable SHA-256 over the JSON-canonical representation of ``payload``."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> tuple[str, int]:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


# Top-level keys we treat as "the record list" when peeking a module JSON.
# Order matters — first match wins.
_RECORD_COUNT_KEYS: tuple[str, ...] = (
    "findings",
    "rows",
    "pools",
    "role_assignments",
    "firewall_rules",
    "credentials",
    "pipelines",
    "notebooks",
    "spark_jobs",
    "external_tables",
)


def peek_record_count(path: Path) -> int | None:
    """Return a representative top-level record count for a module JSON.

    Tolerant of missing files and malformed JSON — returns ``None`` in those
    cases so manifest builds never fail because of a downstream artifact.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    for key in _RECORD_COUNT_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return None


# ---------------------------------------------------------------------------
# Build / save / load
# ---------------------------------------------------------------------------

def build_manifest(
    out_dir: Path,
    *,
    workspace_name: str,
    subscription_id: str,
    resource_group: str,
    sma_version: str,
    file_names: Iterable[str],
    scopes: list[ScopeRecord] | None = None,
) -> RunManifest:
    """Build a manifest by hashing each *.json file in ``file_names``.

    When ``scopes`` is omitted, a single synthetic
    :class:`ScopeRecord` is synthesized from ``workspace_name`` /
    ``resource_group`` / ``subscription_id`` so v2 readers see a
    populated ``scopes`` array even in legacy single-source mode.
    Each artifact is stamped with ``source_type="synapse_workspace"``
    and ``scope_id=workspace_name`` in that case. Multi-source callers
    populate ``scopes`` themselves and use higher-level helpers (TBD in
    Phase 2) to stamp per-artifact provenance.
    """
    out_dir = Path(out_dir)
    if scopes is None:
        scopes = [ScopeRecord(
            source_type="synapse_workspace",
            scope_id=workspace_name,
            display_name=workspace_name,
            subscription_id=subscription_id,
            resource_group=resource_group,
        )]
    default_source_type = scopes[0].source_type if scopes else "synapse_workspace"
    default_scope_id = scopes[0].scope_id if scopes else workspace_name

    artifacts: list[ArtifactRecord] = []
    for name in file_names:
        path = out_dir / name
        if not path.exists():
            continue
        sha, size = hash_file(path)
        artifacts.append(ArtifactRecord(
            name=path.stem,
            file_name=name,
            sha256=sha,
            size_bytes=size,
            generated_at=datetime.now(timezone.utc),
            record_count=peek_record_count(path),
            source_type=default_source_type,
            scope_id=default_scope_id,
        ))
    return RunManifest(
        workspace_name=workspace_name,
        subscription_id=subscription_id,
        resource_group=resource_group,
        generated_at=datetime.now(timezone.utc),
        sma_version=sma_version,
        artifacts=artifacts,
        schema_version=MANIFEST_SCHEMA_VERSION,
        scopes=scopes,
    )


def save_manifest(manifest: RunManifest, out_dir: Path) -> Path:
    path = Path(out_dir) / MANIFEST_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    return path


def load_manifest(out_dir: Path) -> RunManifest | None:
    path = Path(out_dir) / MANIFEST_FILENAME
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    raw = _upgrade_v1_in_memory(raw)
    try:
        return RunManifest.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def _upgrade_v1_in_memory(raw: dict[str, Any]) -> dict[str, Any]:
    """Promote a v1 manifest dict to v2 in memory only.

    v1 (schema_version absent or 1):
      * No top-level ``scopes`` array.
      * No per-artifact ``source_type`` / ``scope_id``.
    v2 upgrade (read-only — never written back to disk):
      * Synthesize a single ``ScopeRecord`` from
        ``workspace_name`` / ``resource_group`` / ``subscription_id``.
      * Stamp each artifact with ``source_type='synapse_workspace'``
        and ``scope_id=workspace_name``.
      * Set ``schema_version = MANIFEST_SCHEMA_VERSION``.
    """
    if not isinstance(raw, dict):
        return raw
    version = raw.get("schema_version")
    if version is not None and int(version) >= MANIFEST_SCHEMA_VERSION:
        return raw  # already v2+ — leave alone

    workspace_name = raw.get("workspace_name") or ""
    subscription_id = raw.get("subscription_id") or ""
    resource_group = raw.get("resource_group") or ""

    if not raw.get("scopes"):
        raw["scopes"] = [{
            "source_type": "synapse_workspace",
            "scope_id": workspace_name,
            "display_name": workspace_name,
            "subscription_id": subscription_id,
            "resource_group": resource_group,
        }]

    artifacts = raw.get("artifacts") or []
    if isinstance(artifacts, list):
        for art in artifacts:
            if not isinstance(art, dict):
                continue
            art.setdefault("source_type", "synapse_workspace")
            art.setdefault("scope_id", workspace_name)

    raw["schema_version"] = MANIFEST_SCHEMA_VERSION
    return raw


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------

def diff_manifests(
    prev: RunManifest | None,
    curr: RunManifest,
) -> list[ManifestDiffEntry]:
    prev_map = {a.name: a for a in (prev.artifacts if prev else [])}
    curr_map = {a.name: a for a in curr.artifacts}
    out: list[ManifestDiffEntry] = []
    for name in sorted(set(prev_map) | set(curr_map)):
        p = prev_map.get(name)
        c = curr_map.get(name)
        if p is None and c is not None:
            status = "added"
        elif p is not None and c is None:
            status = "removed"
        elif p is not None and c is not None:
            status = "unchanged" if p.sha256 == c.sha256 else "changed"
        else:  # pragma: no cover - logically unreachable
            continue
        out.append(ManifestDiffEntry(
            name=name,
            status=status,
            prev_sha=p.sha256 if p else None,
            curr_sha=c.sha256 if c else None,
            prev_size=p.size_bytes if p else None,
            curr_size=c.size_bytes if c else None,
            prev_generated_at=p.generated_at if p else None,
            curr_generated_at=c.generated_at if c else None,
            prev_record_count=p.record_count if p else None,
            curr_record_count=c.record_count if c else None,
        ))
    return out


def diff_summary(diff: list[ManifestDiffEntry]) -> dict[str, int]:
    """Return ``{added, removed, changed, unchanged, total}`` counts."""
    summary = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for e in diff:
        if e.status in summary:
            summary[e.status] += 1
    summary["total"] = len(diff)
    return summary


# ---------------------------------------------------------------------------
# Delta reports
# ---------------------------------------------------------------------------

def _fmt_count_delta(delta: int | None) -> str:
    if delta is None:
        return ""
    if delta == 0:
        return "0"
    return f"{delta:+d}"


def write_delta_report(
    diff: list[ManifestDiffEntry],
    out_dir: Path,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = diff_summary(diff)
    lines = [
        "# Unified Solution Migration Analyzer - Run delta",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        (f"- Artifacts: {summary['total']} (added: {summary['added']}, "
         f"removed: {summary['removed']}, changed: {summary['changed']}, "
         f"unchanged: {summary['unchanged']})"),
        "",
        "| Module | Status | Records (prev -> curr) | Size delta | Previous SHA | Current SHA |",
        "|---|---|---|---:|---|---|",
    ]
    for e in diff:
        prev_n = "-" if e.prev_record_count is None else str(e.prev_record_count)
        curr_n = "-" if e.curr_record_count is None else str(e.curr_record_count)
        rec = f"{prev_n} -> {curr_n}"
        size_delta = e.size_delta
        size_cell = "-" if size_delta is None else f"{size_delta:+,}"
        lines.append(
            f"| {e.name} | {e.status} | {rec} | {size_cell} | "
            f"`{(e.prev_sha or '')[:12]}` | `{(e.curr_sha or '')[:12]}` |"
        )
    lines.append("")
    path = out_dir / DELTA_FILENAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_delta_json(
    diff: list[ManifestDiffEntry],
    out_dir: Path,
    *,
    prev_manifest: RunManifest | None,
    curr_manifest: RunManifest,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "previous": prev_manifest.model_dump(mode="json") if prev_manifest else None,
        "current": curr_manifest.model_dump(mode="json"),
        "summary": diff_summary(diff),
        "entries": [
            {
                "name": e.name,
                "status": e.status,
                "prev_sha": e.prev_sha,
                "curr_sha": e.curr_sha,
                "prev_size": e.prev_size,
                "curr_size": e.curr_size,
                "size_delta": e.size_delta,
                "prev_generated_at": e.prev_generated_at.isoformat() if e.prev_generated_at else None,
                "curr_generated_at": e.curr_generated_at.isoformat() if e.curr_generated_at else None,
                "prev_record_count": e.prev_record_count,
                "curr_record_count": e.curr_record_count,
                "record_count_delta": e.record_count_delta,
            }
            for e in diff
        ],
    }
    path = out_dir / DELTA_JSON_FILENAME
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Run delta</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Run delta</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ curr.workspace_name }}</div>
 <div><strong>Resource group:</strong> {{ curr.resource_group }}</div>
 <div><strong>Subscription:</strong> <code>{{ curr.subscription_id }}</code></div>
 <div><strong>Current run:</strong> {{ curr.generated_at.isoformat() }} &middot;
   <code>v{{ curr.sma_version }}</code></div>
 {% if prev %}
 <div><strong>Previous run:</strong> {{ prev.generated_at.isoformat() }} &middot;
   <code>v{{ prev.sma_version }}</code></div>
 {% else %}
 <div><strong>Previous run:</strong> <span class="muted">(none &mdash; first run)</span></div>
 {% endif %}
</div>

<div class="grid-2">
 <div class="stat"><div class="label">Total</div><div class="value">{{ summary.total }}</div></div>
 <div class="stat"><div class="label">Added</div><div class="value">{{ summary.added }}</div></div>
 <div class="stat"><div class="label">Removed</div><div class="value">{{ summary.removed }}</div></div>
 <div class="stat"><div class="label">Changed</div><div class="value">{{ summary.changed }}</div></div>
 <div class="stat"><div class="label">Unchanged</div><div class="value">{{ summary.unchanged }}</div></div>
</div>

<h2>Artifacts</h2>
<table>
 <tr>
  <th>Module</th><th>Status</th>
  <th class="num">Records prev</th><th class="num">Records curr</th><th class="num">&Delta; records</th>
  <th class="num">Size delta</th>
  <th>Previous SHA</th><th>Current SHA</th>
 </tr>
 {% for row in rows %}
 <tr>
  <td><code>{{ row.name }}</code></td>
  <td><span class="pill {{ row.pill }}">{{ row.status }}</span></td>
  <td class="num">{{ row.prev_records }}</td>
  <td class="num">{{ row.curr_records }}</td>
  <td class="num">{{ row.records_delta }}</td>
  <td class="num">{{ row.size_delta }}</td>
  <td class="small"><code>{{ row.prev_sha or '' }}</code></td>
  <td class="small"><code>{{ row.curr_sha or '' }}</code></td>
 </tr>
 {% endfor %}
</table>

<div class="footer">Generated by Unified Solution Migration Analyzer.</div>
</body></html>
"""


_STATUS_PILL = {
    "added": "info",
    "removed": "warn",
    "changed": "warn",
    "unchanged": "ok",
}


def write_delta_html(
    diff: list[ManifestDiffEntry],
    out_dir: Path,
    *,
    prev_manifest: RunManifest | None,
    curr_manifest: RunManifest,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _fmt_records(n: int | None) -> str:
        return "-" if n is None else str(n)

    def _fmt_size(n: int | None) -> str:
        return "-" if n is None else f"{n:+,}"

    rows: list[dict[str, Any]] = []
    status_order = {"changed": 0, "added": 1, "removed": 2, "unchanged": 3}
    for e in sorted(diff, key=lambda x: (status_order.get(x.status, 9), x.name)):
        rows.append({
            "name": e.name,
            "status": e.status,
            "pill": _STATUS_PILL.get(e.status, "info"),
            "prev_records": _fmt_records(e.prev_record_count),
            "curr_records": _fmt_records(e.curr_record_count),
            "records_delta": _fmt_count_delta(e.record_count_delta) or "-",
            "size_delta": _fmt_size(e.size_delta),
            "prev_sha": (e.prev_sha or "")[:12],
            "curr_sha": (e.curr_sha or "")[:12],
        })

    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    tpl = env.from_string(_HTML_TEMPLATE)
    html = tpl.render(
        css=SHARED_CSS,
        prev=prev_manifest,
        curr=curr_manifest,
        summary=diff_summary(diff),
        rows=rows,
    )
    path = out_dir / DELTA_HTML_FILENAME
    path.write_text(html, encoding="utf-8")
    return path
