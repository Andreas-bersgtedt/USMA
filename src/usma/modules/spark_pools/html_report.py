"""HTML report writer for the spark_pools module."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from ...reporting.html_common import SHARED_CSS, SHARED_FILTER_JS
from .models import SparkAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Apache Spark Pools</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Apache Spark Pools</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Subscription:</strong> {{ r.subscription_id }}</div>
 <div><strong>Resource group:</strong> {{ r.resource_group }}</div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
</div>

<div class="grid-2">
 <div class="stat"><div class="label">Pools</div><div class="value">{{ r.pools|length }}</div></div>
 <div class="stat"><div class="label">Notebooks</div><div class="value">{{ r.notebooks|length }}</div></div>
 <div class="stat"><div class="label">Spark job defs</div><div class="value">{{ r.spark_job_definitions|length }}</div></div>
 <div class="stat"><div class="label">Lint findings</div><div class="value">{{ r.notebook_lint_findings|length }}</div></div>
 <div class="stat"><div class="label">Libraries</div><div class="value">{{ r.libraries|length }}</div></div>
 <div class="stat"><div class="label">Spark runs (Livy)</div><div class="value">{{ r.spark_runs|length }}</div></div>
 <div class="stat"><div class="label">Est. CU-h (Fabric Spark)</div><div class="value">{{ "%.1f"|format(total_cu_hours) }}</div></div>
</div>

<div class="toc">
 <strong>Sections:</strong>
 <a href="#pools">Pools</a>
 {% if r.runtime_mappings %}<a href="#runtime">Runtime mapping</a>{% endif %}
 {% if r.notebooks %}<a href="#notebooks">Notebooks</a>{% endif %}
 {% if r.notebook_lint_findings %}<a href="#lint">Lint findings</a>{% endif %}
 {% if r.spark_job_definitions %}<a href="#sjd">Spark job defs</a>{% endif %}
 {% if r.libraries %}<a href="#libs">Libraries</a>{% endif %}
 {% if r.run_stats %}<a href="#runstats">Spark execution</a>{% endif %}
 {% if r.job_runs %}<a href="#runs">Job runs</a>{% endif %}
 {% if r.errors %}<a href="#errors">Errors</a>{% endif %}
</div>

{% if r.errors %}
<h2 id="errors">Collection errors</h2>
<details open><summary class="err">{{ r.errors|length }} error(s)</summary>
 <ul class="err">{% for e in r.errors %}<li>{{ e }}</li>{% endfor %}</ul></details>
{% endif %}

<h2 id="pools">Pools ({{ r.pools|length }})</h2>
{% if r.pools %}
<input class="filter" type="text" placeholder="Filter pools" oninput="smaFilter(this,'tbl-pools')"/>
<table id="tbl-pools">
 <tr><th>Name</th><th>Spark</th><th>Node size</th><th class="num">Nodes</th><th>Autoscale</th>
  <th>Auto-pause</th><th>Isolated</th><th>Status</th><th>Location</th></tr>
 {% for p in r.pools %}
 <tr>
  <td><code>{{ p.name }}</code></td>
  <td>{{ p.spark_version or '' }}</td>
  <td>{{ p.node_size or '' }}{% if p.node_size_family %} <span class="muted small">({{ p.node_size_family }})</span>{% endif %}</td>
  <td class="num">{{ p.node_count if p.node_count is not none else '' }}</td>
  <td>{% if p.auto_scale_enabled %}<span class="pill ok">{{ p.min_node_count }}-{{ p.max_node_count }}</span>{% else %}<span class="pill muted">off</span>{% endif %}</td>
  <td>{% if p.auto_pause_enabled %}<span class="pill ok">{{ p.auto_pause_delay_minutes }}m</span>{% else %}<span class="pill warn">off</span>{% endif %}</td>
  <td>{% if p.isolated_compute_enabled %}<span class="pill info">yes</span>{% endif %}</td>
  <td>{{ p.provisioning_state or '' }}</td>
  <td>{{ p.location or '' }}</td>
 </tr>
 {% endfor %}
</table>
{% else %}<p class="muted small">No Spark pools defined in the workspace.</p>{% endif %}

{% if r.runtime_mappings %}
<h2 id="runtime">Runtime mapping (Synapse Spark -> Fabric runtime)</h2>
<table>
 <tr><th>Pool</th><th>Synapse Spark</th><th>Fabric runtime</th><th>Fabric Spark</th><th>Status</th><th>Note</th></tr>
 {% for m in r.runtime_mappings %}
 <tr>
  <td><code>{{ m.pool_name }}</code></td>
  <td>{{ m.synapse_version or '' }}</td>
  <td>{{ m.fabric_runtime or '' }}</td>
  <td>{{ m.fabric_spark or '' }}</td>
  <td>
   {% if m.status == 'matches' %}<span class="pill ok">{{ m.status }}</span>
   {% elif m.status == 'upgrade' %}<span class="pill info">{{ m.status }}</span>
   {% elif m.status == 'deprecated' %}<span class="pill err">{{ m.status }}</span>
   {% else %}<span class="pill warn">{{ m.status }}</span>{% endif %}
  </td>
  <td class="small muted">{{ m.note or '' }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.notebooks %}
<h2 id="notebooks">Notebooks ({{ r.notebooks|length }})</h2>
<input class="filter" type="text" placeholder="Filter notebooks" oninput="smaFilter(this,'tbl-nb')"/>
<table id="tbl-nb">
 <tr><th>Name</th><th>Folder</th><th>Language</th><th>Attached pool</th>
  <th class="num">Cells</th><th class="num">Source chars</th><th class="num">Lint findings</th></tr>
 {% for nb in r.notebooks %}
 {% set nbf = lint_by_nb.get(nb.name, []) %}
 <tr>
  <td>
   {% if nbf %}
   <details><summary><code>{{ nb.name }}</code></summary>
    <table><tr><th>Severity</th><th>Rule</th><th class="num">Line</th><th>Snippet</th></tr>
    {% for f in nbf %}
    <tr>
     <td>{% set sev = f.severity %}<span class="pill {{ 'err' if sev == 'blocker' else ('warn' if sev == 'warning' else 'info') }}">{{ sev }}</span></td>
     <td><code>{{ f.rule_id }}</code> {{ f.label }}</td>
     <td class="num">{{ f.line or '' }}</td>
     <td><code class="small">{{ f.snippet }}</code></td>
    </tr>
    {% endfor %}
    </table>
   </details>
   {% else %}<code>{{ nb.name }}</code>{% endif %}
  </td>
  <td>{{ nb.folder or '' }}</td>
  <td>{{ nb.language or '' }}</td>
  <td>{{ nb.attached_spark_pool or '' }}</td>
  <td class="num">{{ nb.cell_count }}</td>
  <td class="num">{{ nb.source_size_chars }}</td>
  <td class="num">{{ nbf|length }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.notebook_lint_findings %}
<h2 id="lint">Lint findings ({{ r.notebook_lint_findings|length }})</h2>
<input class="filter" type="text" placeholder="Filter findings" oninput="smaFilter(this,'tbl-lint')"/>
<table id="tbl-lint">
 <tr><th>Severity</th><th>Notebook</th><th>Rule</th><th class="num">Line</th><th>Snippet</th></tr>
 {% for f in r.notebook_lint_findings %}
 <tr>
  <td>{% set sev = f.severity %}<span class="pill {{ 'err' if sev == 'blocker' else ('warn' if sev == 'warning' else 'info') }}">{{ sev }}</span></td>
  <td><code>{{ f.notebook }}</code></td>
  <td><code>{{ f.rule_id }}</code> {{ f.label }}</td>
  <td class="num">{{ f.line or '' }}</td>
  <td><code class="small">{{ f.snippet }}</code></td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.spark_job_definitions %}
<h2 id="sjd">Spark job definitions ({{ r.spark_job_definitions|length }})</h2>
<table>
 <tr><th>Name</th><th>Folder</th><th>Language</th><th>Target pool</th><th>Main file</th><th>Class</th></tr>
 {% for s in r.spark_job_definitions %}
 <tr><td><code>{{ s.name }}</code></td><td>{{ s.folder or '' }}</td><td>{{ s.language or '' }}</td>
  <td>{{ s.target_spark_pool or '' }}</td><td><code class="small">{{ s.main_definition_file or '' }}</code></td>
  <td>{{ s.class_name or '' }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.libraries %}
<h2 id="libs">Libraries ({{ r.libraries|length }})</h2>
<table>
 <tr><th>Scope</th><th>Name</th><th>Version</th><th>Type</th></tr>
 {% for l in r.libraries %}
 <tr><td>{{ l.scope }}</td><td><code>{{ l.name }}</code></td><td>{{ l.version or '' }}</td><td>{{ l.package_type or '' }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.run_stats %}
<h2 id="runstats">Spark execution (Livy job history)</h2>
<p class="muted small">vCore-seconds × 0.5 = Fabric CU-seconds (1 CU = 2 Spark vCores).
Pulled from the Synapse Spark Livy API; covers both notebook (interactive)
sessions and pipeline/SJD-triggered (scheduled) batch jobs.</p>
<table>
 <tr><th>Pool</th><th>Kind</th><th>Window</th><th class="num">Runs</th><th class="num">Succeeded</th>
  <th class="num">Failed</th><th class="num">Duration h</th><th class="num">vCore-h</th>
  <th class="num">Est. CU-h (Fabric)</th><th class="num">Avg vCore-h/run</th></tr>
 {% for s in r.run_stats %}
  {% for w in s.windows %}
  <tr>
   <td><code>{{ s.pool }}</code></td>
   <td>{% if s.kind == 'interactive' %}<span class="pill info">interactive</span>{% else %}<span class="pill ok">scheduled</span>{% endif %}</td>
   <td>{{ w.window_days }}d</td>
   <td class="num">{{ w.run_count }}</td>
   <td class="num">{{ w.succeeded }}</td>
   <td class="num">{{ w.failed }}</td>
   <td class="num">{{ "%.2f"|format(w.total_duration_hours) }}</td>
   <td class="num">{{ "%.2f"|format(w.total_vcore_hours) }}</td>
   <td class="num">{{ "%.2f"|format(w.est_cu_hours_fabric_spark) }}</td>
   <td class="num">{{ "%.2f"|format(w.avg_vcore_hours_per_run) if w.avg_vcore_hours_per_run is not none else '' }}</td>
  </tr>
  {% endfor %}
 {% endfor %}
</table>
{% endif %}

{% if r.job_runs %}
<h2 id="runs">Job runs ({{ r.job_runs|length }})</h2>
<table>
 <tr><th>Job</th><th>Submission</th><th>Pool</th><th>State</th><th>Submitted</th><th class="num">Duration (s)</th><th>Error</th></tr>
 {% for j in r.job_runs %}
 <tr><td><code>{{ j.job_name }}</code></td><td class="small">{{ j.submission_id or '' }}</td>
  <td>{{ j.target_spark_pool or '' }}</td><td>{{ j.state or '' }}</td>
  <td class="nowrap small">{{ j.submitted_at.isoformat() if j.submitted_at else '' }}</td>
  <td class="num">{{ j.duration_seconds if j.duration_seconds is not none else '' }}</td>
  <td class="small err">{{ j.error_summary or '' }}</td></tr>
 {% endfor %}
</table>
{% endif %}

<div class="footer">Generated by Unified Solution Migration Analyzer.</div>
<script>{{ js }}</script>
</body></html>
"""


def write_html(result: SparkAnalysis, out_dir: Path) -> Path:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(_TEMPLATE)
    lint_by_nb: dict[str, list[Any]] = defaultdict(list)
    for f in result.notebook_lint_findings:
        lint_by_nb[f.notebook].append(f)
    total_cu_hours = sum(
        (r.est_cu_hours_fabric_spark or 0.0) for r in result.spark_runs
    )
    html = template.render(
        r=result,
        lint_by_nb=dict(lint_by_nb),
        css=SHARED_CSS,
        js=SHARED_FILTER_JS,
        total_cu_hours=total_cu_hours,
    )
    path = out_dir / "spark_pools.html"
    path.write_text(html, encoding="utf-8")
    return path
