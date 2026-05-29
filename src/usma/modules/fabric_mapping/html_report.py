"""HTML report writer for the fabric_mapping module."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from ...reporting.html_common import SHARED_CSS, SHARED_FILTER_JS
from .models import FabricMappingReport

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Fabric Migration Plan</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Fabric Migration Plan</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name or '(unknown)' }}</div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
</div>

{% if r.readiness %}
{% set rd = r.readiness %}
<div class="grid-2">
 <div class="stat"><div class="label">Readiness score</div>
  <div class="value">
   {% if rd.score >= 80 %}<span class="pill ok">{{ rd.score }}</span>
   {% elif rd.score >= 50 %}<span class="pill warn">{{ rd.score }}</span>
   {% else %}<span class="pill err">{{ rd.score }}</span>{% endif %}
  </div></div>
 <div class="stat"><div class="label">Bucket</div><div class="value">{{ rd.bucket }}</div></div>
 <div class="stat"><div class="label">Recommendations</div><div class="value">{{ r.recommendations|length }}</div></div>
 <div class="stat"><div class="label">Blockers</div>
  <div class="value">{% if extras.sev.blocker %}<span class="pill err">{{ extras.sev.blocker }}</span>{% else %}0{% endif %}</div></div>
 <div class="stat"><div class="label">Warnings</div>
  <div class="value">{% if extras.sev.warning %}<span class="pill warn">{{ extras.sev.warning }}</span>{% else %}0{% endif %}</div></div>
 <div class="stat"><div class="label">Info</div><div class="value">{{ extras.sev.info or 0 }}</div></div>
{% if rd.tsql_objects_total %}
 <div class="stat"><div class="label">T-SQL compatible</div>
  <div class="value">
   {% set pct = rd.tsql_compatibility_pct %}
   {% if pct >= 80 %}<span class="pill ok">{{ pct }}%</span>
   {% elif pct >= 50 %}<span class="pill warn">{{ pct }}%</span>
   {% else %}<span class="pill err">{{ pct }}%</span>{% endif %}
  </div>
  <div class="small muted">{{ rd.tsql_objects_total }} objects &middot;
   {{ rd.tsql_objects_incompatible }} incompatible &middot;
   {{ rd.tsql_objects_needs_review }} needs review</div>
 </div>
{% endif %}
</div>
{% endif %}

{% if r.capacity_projection %}
{% set cp = r.capacity_projection %}
<h2 id="capacity">Capacity projection</h2>
<table>
 <tr><th class="num">Peak DWU</th><th class="num">Peak DWU + headroom</th><th class="num">Estimated CU</th>
  <th>Recommended SKU</th><th class="num">Headroom %</th><th>Notes</th></tr>
 <tr>
  <td class="num">{{ '%.0f'|format(cp.peak_dwu) }}</td>
  <td class="num">{{ '%.0f'|format(cp.peak_dwu_with_headroom) }}</td>
  <td class="num">{{ '%.1f'|format(cp.estimated_cu) }}</td>
  <td><span class="pill info">{{ cp.recommended_sku }}</span></td>
  <td class="num">{{ cp.headroom_pct }}%</td>
  <td class="small muted">{{ '; '.join(cp.notes) }}</td>
 </tr>
</table>
{% endif %}

<div class="toc">
 <strong>Sections:</strong>
 {% if r.readiness %}<a href="#readiness">Readiness</a>{% endif %}
 {% if r.capacity_projection %}<a href="#capacity">Capacity</a>{% endif %}
 {% if r.inputs %}<a href="#inputs">Inputs analyzed</a>{% endif %}
 {% if r.recommendations %}<a href="#recs">Recommendations</a>{% endif %}
 {% if r.runbook %}<a href="#runbook">Runbook</a>{% endif %}
</div>

{% if r.readiness and r.readiness.top_blockers %}
<h2 id="readiness">Top blockers</h2>
<table>
 <tr><th>Severity</th><th>Area</th><th>Target</th><th>Title</th><th>Action</th></tr>
 {% for b in r.readiness.top_blockers %}
 <tr>
  <td><span class="pill err">{{ b.severity }}</span></td>
  <td><code>{{ b.area }}</code></td>
  <td><code class="small">{{ b.target or '' }}</code></td>
  <td>{{ b.title }}</td>
  <td class="small">{{ b.fabric_action or '' }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.inputs %}
<h2 id="inputs">Inputs analyzed</h2>
<table>
 <tr><th>Module</th><th>Source file</th><th>Counts</th><th>Notes</th></tr>
 {% for s in r.inputs %}
 <tr>
  <td><code>{{ s.module }}</code></td>
  <td><code class="small">{{ s.source_file }}</code></td>
  <td class="small">{% for k, v in s.counts.items() %}<code>{{ k }}</code>={{ v }}{% if not loop.last %}, {% endif %}{% endfor %}</td>
  <td class="small muted">{{ '; '.join(s.notes) }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.recommendations %}
<h2 id="recs">Recommendations ({{ r.recommendations|length }})</h2>
{% for area, recs in extras.by_area.items() %}
<details {% if loop.first %}open{% endif %}>
 <summary><code>{{ area }}</code> &mdash; {{ recs|length }} recommendation(s)</summary>
 <input class="filter" type="text" placeholder="Filter" oninput="smaFilter(this,'tbl-rec-{{ loop.index }}')"/>
 <table id="tbl-rec-{{ loop.index }}">
  <tr><th>Severity</th><th>Effort</th><th>Title</th><th>Target</th><th>Detail</th><th>Fabric action</th></tr>
  {% for rec in recs %}
  <tr>
   <td>{% set sev = rec.severity %}<span class="pill {{ 'err' if sev == 'blocker' else ('warn' if sev == 'warning' else 'info') }}">{{ sev }}</span></td>
   <td>{{ rec.effort }}</td>
   <td>{{ rec.title }}</td>
   <td><code class="small">{{ rec.target or '' }}</code></td>
   <td class="small">{{ rec.detail }}</td>
   <td class="small">{{ rec.fabric_action or '' }}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endfor %}
{% endif %}

{% if r.runbook %}
<h2 id="runbook">Runbook ({{ r.runbook|length }} steps)</h2>
{% if r.effort_summary %}
<p class="muted small">
 Estimated effort: <strong>{{ '%.1f'|format(r.effort_summary.total_p50_hours) }} h P50</strong>
 / <strong>{{ '%.1f'|format(r.effort_summary.total_p90_hours) }} h P90</strong>
 {% if r.effort_summary.total_p50_days is not none and r.effort_summary.total_p90_days is not none -%}
 &mdash; <strong>{{ r.effort_summary.total_p50_days }} P50</strong> /
 <strong>{{ r.effort_summary.total_p90_days }} P90</strong> resource-days
 (8 h/day &times; 1.15 spillage, rounded up)
 {%- endif %}
 &mdash; rate card: <code>{{ r.effort_summary.card_source }}</code> (v{{ r.effort_summary.card_version }}).
 {% if r.effort_summary.per_phase %}
 Per phase:
 {% for p in r.effort_summary.per_phase -%}
  <code>{{ p.phase }}</code> {{ '%.1f'|format(p.p50_hours) }}/{{ '%.1f'|format(p.p90_hours) }} h{% if p.p50_days is not none %} ({{ p.p50_days }}/{{ p.p90_days }} d){% endif %}{% if not loop.last %}, {% endif %}
 {%- endfor %}.
 {% endif %}
</p>
{% endif %}
{% for phase, steps in extras.runbook_by_phase.items() %}
<details {% if loop.first %}open{% endif %}>
 <summary><strong>Phase: {{ phase }}</strong> &mdash; {{ steps|length }} step(s)</summary>
 <table>
  <tr><th class="num">#</th><th>Title</th><th>Severity</th><th>Effort</th><th>P50 (h)</th><th>P90 (h)</th><th>Target</th><th>Detail</th><th>Rollback</th></tr>
  {% for s in steps %}
  <tr>
   <td class="num">{{ s.order }}</td>
   <td>{{ s.title }}</td>
   <td>{% set sv = s.severity %}<span class="pill {{ 'err' if sv == 'blocker' else ('warn' if sv == 'warning' else 'info') }}">{{ sv }}</span></td>
   <td>{{ s.effort }}</td>
   <td class="num">{{ '%.1f'|format(s.effort_hours_p50) if s.effort_hours_p50 is not none else '' }}</td>
   <td class="num">{{ '%.1f'|format(s.effort_hours_p90) if s.effort_hours_p90 is not none else '' }}</td>
   <td><code class="small">{{ s.target or '' }}</code></td>
   <td class="small">{{ s.detail }}</td>
   <td class="small muted">{{ s.rollback or '' }}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endfor %}
{% endif %}

<div class="footer">Generated by Unified Solution Migration Analyzer.</div>
<script>{{ js }}</script>
</body></html>
"""


def _build_extras(result: FabricMappingReport) -> dict[str, Any]:
    sev = Counter(r.severity for r in result.recommendations)
    sev_obj = type("S", (), {"blocker": sev.get("blocker", 0),
                              "warning": sev.get("warning", 0),
                              "info": sev.get("info", 0)})()
    by_area: dict[str, list[Any]] = defaultdict(list)
    sev_order = {"blocker": 0, "warning": 1, "info": 2}
    for rec in sorted(result.recommendations, key=lambda r: (sev_order.get(r.severity, 3), r.area)):
        by_area[rec.area].append(rec)
    runbook_by_phase: dict[str, list[Any]] = defaultdict(list)
    for step in sorted(result.runbook, key=lambda s: (s.phase, s.order)):
        runbook_by_phase[step.phase].append(step)
    return {
        "sev": sev_obj,
        "by_area": dict(by_area),
        "runbook_by_phase": dict(runbook_by_phase),
    }


def write_html(result: FabricMappingReport, out_dir: Path) -> Path:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(_TEMPLATE)
    html = template.render(r=result, extras=_build_extras(result),
                           css=SHARED_CSS, js=SHARED_FILTER_JS)
    path = out_dir / "fabric_mapping.html"
    path.write_text(html, encoding="utf-8")
    return path
