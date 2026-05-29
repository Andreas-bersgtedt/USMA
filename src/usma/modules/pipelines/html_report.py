"""HTML report writer for the pipelines module."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from ...reporting.html_common import SHARED_CSS, SHARED_FILTER_JS
from .models import PipelinesAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Pipelines</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Pipelines</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Artifacts endpoint:</strong> <code>{{ r.artifacts_endpoint }}</code></div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
</div>

<div class="grid-2">
 <div class="stat"><div class="label">Pipelines</div><div class="value">{{ r.pipelines|length }}</div></div>
 <div class="stat"><div class="label">Activities</div><div class="value">{{ r.activities|length }}</div></div>
 <div class="stat"><div class="label">Unsupported activities</div><div class="value">{{ extras.unsupported_acts|length }}</div></div>
 <div class="stat"><div class="label">Linked services</div><div class="value">{{ r.linked_services|length }}</div></div>
 <div class="stat"><div class="label">Datasets</div><div class="value">{{ r.datasets|length }}</div></div>
 <div class="stat"><div class="label">Triggers</div><div class="value">{{ r.triggers|length }}</div></div>
 <div class="stat"><div class="label">Integration runtimes</div><div class="value">{{ r.integration_runtimes|length }}</div></div>
 <div class="stat"><div class="label">Expression findings</div><div class="value">{{ r.expression_findings|length }}</div></div>
{% if r.run_history %}
 <div class="stat"><div class="label">Runs ({{ extras.headline_window }}d)</div><div class="value">{{ extras.headline_runs }}</div></div>
 <div class="stat"><div class="label">Failed ({{ extras.headline_window }}d)</div><div class="value">{{ extras.headline_failed }}</div></div>
{% endif %}
</div>

<div class="toc">
 <strong>Sections:</strong>
 <a href="#pipelines">Pipelines</a>
 {% if extras.unsupported_acts %}<a href="#unsup-acts">Unsupported activities</a>{% endif %}
 {% if extras.partial_acts %}<a href="#partial-acts">Partial activities</a>{% endif %}
 {% if extras.unsupported_ls %}<a href="#unsup-ls">Unsupported linked services</a>{% endif %}
 {% if r.linked_services %}<a href="#ls">Linked services</a>{% endif %}
 {% if r.datasets %}<a href="#datasets">Datasets</a>{% endif %}
 {% if r.triggers %}<a href="#triggers">Triggers</a>{% endif %}
 {% if r.schedule_mappings %}<a href="#schedules">Schedule mapping</a>{% endif %}
 {% if r.integration_runtimes %}<a href="#ir">Integration runtimes</a>{% endif %}
 {% if r.expression_findings %}<a href="#expr">Expression findings</a>{% endif %}
 {% if r.run_history %}<a href="#run-stats">Runtime statistics</a>{% endif %}
 {% if r.errors %}<a href="#errors">Errors</a>{% endif %}
</div>

{% if r.errors %}
<h2 id="errors">Collection errors</h2>
<details open><summary class="err">{{ r.errors|length }} error(s)</summary>
 <ul class="err">{% for e in r.errors %}<li>{{ e }}</li>{% endfor %}</ul></details>
{% endif %}

<h2 id="pipelines">Pipelines ({{ r.pipelines|length }})</h2>
{% if r.pipelines %}
<input class="filter" type="text" placeholder="Filter pipelines" oninput="smaFilter(this,'tbl-pl')"/>
<table id="tbl-pl">
 <tr><th>Name</th><th>Folder</th><th class="num">Activities</th><th class="num">Unsupported</th>
  <th class="num">Partial</th><th>Activity types</th></tr>
 {% for p in r.pipelines %}
 {% set acts = extras.acts_by_pipeline.get(p.name, []) %}
 <tr>
  <td>
   <details><summary><code>{{ p.name }}</code></summary>
    {% if acts %}
    <table>
     <tr><th>Activity</th><th>Type</th><th>Support</th><th>Refs</th><th>Why / what's partial</th></tr>
     {% for a in acts %}
     <tr>
      <td><code>{{ a.name }}</code></td>
      <td>{{ a.type }}</td>
      <td>
       {% if a.support == 'supported' %}<span class="pill ok">{{ a.support }}</span>
       {% elif a.support == 'partial' %}<span class="pill warn">{{ a.support }}</span>
       {% elif a.support == 'unsupported' %}<span class="pill err">{{ a.support }}</span>
       {% else %}<span class="pill info">{{ a.support }}</span>{% endif %}
      </td>
      <td class="small">
       {% if a.references_pipeline %}pl:<code>{{ a.references_pipeline }}</code><br>{% endif %}
       {% if a.references_dataset %}ds:<code>{{ a.references_dataset }}</code><br>{% endif %}
       {% if a.references_linked_service %}ls:<code>{{ a.references_linked_service }}</code>{% endif %}
      </td>
      <td class="small">
       {% if a.fabric_equivalent %}<div><strong>Fabric:</strong> {{ a.fabric_equivalent }}</div>{% endif %}
       {% if a.support_reasons %}<ul class="muted">{% for r in a.support_reasons %}<li>{{ r }}</li>{% endfor %}</ul>{% endif %}
       {% if a.support_caveats %}<ul>{% for c in a.support_caveats %}<li><strong>this instance:</strong> {{ c }}</li>{% endfor %}</ul>{% endif %}
       {% if a.migration_action %}<div><strong>Action:</strong> {{ a.migration_action }}</div>{% endif %}
       {% if a.doc_url %}<div><a href="{{ a.doc_url }}" target="_blank" rel="noopener">docs &rarr;</a></div>{% endif %}
      </td>
     </tr>
     {% endfor %}
    </table>
    {% endif %}
   </details>
  </td>
  <td>{{ p.folder or '' }}</td>
  <td class="num">{{ p.activity_count }}</td>
  <td class="num">{% if p.unsupported_activity_count %}<span class="pill err">{{ p.unsupported_activity_count }}</span>{% endif %}</td>
  <td class="num">{% if p.partial_activity_count %}<span class="pill warn">{{ p.partial_activity_count }}</span>{% endif %}</td>
  <td class="small">{{ ', '.join(p.activity_types) }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if extras.unsupported_acts %}
<h2 id="unsup-acts">Fabric-unsupported activities ({{ extras.unsupported_acts|length }})</h2>
{% for a in extras.unsupported_acts %}
<details class="card err" open>
 <summary><code>{{ a.pipeline }}</code> &rarr; <code>{{ a.name }}</code> &mdash; <strong>{{ a.type }}</strong></summary>
 <div>
  {% if a.fabric_equivalent %}<div><strong>Fabric equivalent:</strong> {{ a.fabric_equivalent }}</div>
  {% else %}<div><strong>Fabric equivalent:</strong> <em>none</em></div>{% endif %}
  {% if a.support_reasons %}<div><strong>Why unsupported:</strong><ul>{% for r in a.support_reasons %}<li>{{ r }}</li>{% endfor %}</ul></div>{% endif %}
  {% if a.support_caveats %}<div><strong>Instance caveats:</strong><ul>{% for c in a.support_caveats %}<li>{{ c }}</li>{% endfor %}</ul></div>{% endif %}
  {% if a.migration_action %}<div><strong>Action:</strong> {{ a.migration_action }}</div>{% endif %}
  {% if a.doc_url %}<div><a href="{{ a.doc_url }}" target="_blank" rel="noopener">Microsoft docs &rarr;</a></div>{% endif %}
 </div>
</details>
{% endfor %}
{% endif %}

{% if extras.partial_acts %}
<h2 id="partial-acts">Partially-compatible activities ({{ extras.partial_acts|length }})</h2>
<p class="muted">These activities exist in Fabric Data Factory but with reduced functionality, narrower connector support, or different config. Each entry lists <em>what specifically</em> is partial, and (when applicable) which property of <em>this instance</em> triggered the warning.</p>
{% for a in extras.partial_acts %}
<details class="card warn">
 <summary><code>{{ a.pipeline }}</code> &rarr; <code>{{ a.name }}</code> &mdash; <strong>{{ a.type }}</strong>
  {% if a.support_caveats %}<span class="pill warn">{{ a.support_caveats|length }} instance caveat{{ 's' if a.support_caveats|length != 1 else '' }}</span>{% endif %}
 </summary>
 <div>
  {% if a.fabric_equivalent %}<div><strong>Fabric equivalent:</strong> {{ a.fabric_equivalent }}</div>{% endif %}
  {% if a.support_reasons %}<div><strong>Why partial (general):</strong><ul>{% for r in a.support_reasons %}<li>{{ r }}</li>{% endfor %}</ul></div>{% endif %}
  {% if a.support_caveats %}<div><strong>Caveats from this instance's properties:</strong><ul>{% for c in a.support_caveats %}<li>{{ c }}</li>{% endfor %}</ul></div>{% endif %}
  {% if a.migration_action %}<div><strong>Action:</strong> {{ a.migration_action }}</div>{% endif %}
  {% if a.doc_url %}<div><a href="{{ a.doc_url }}" target="_blank" rel="noopener">Microsoft docs &rarr;</a></div>{% endif %}
 </div>
</details>
{% endfor %}
{% endif %}

{% if extras.unsupported_ls %}
<h2 id="unsup-ls">Fabric-unsupported linked services ({{ extras.unsupported_ls|length }})</h2>
<table>
 <tr><th>Name</th><th>Type</th><th>Connect via</th></tr>
 {% for ls in extras.unsupported_ls %}
 <tr><td><code>{{ ls.name }}</code></td><td>{{ ls.type }}</td><td>{{ ls.connect_via or '' }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.linked_services %}
<h2 id="ls">Linked services ({{ r.linked_services|length }})</h2>
<input class="filter" type="text" placeholder="Filter linked services" oninput="smaFilter(this,'tbl-ls')"/>
<table id="tbl-ls">
 <tr><th>Name</th><th>Type</th><th>Connect via</th><th>Fabric supported</th></tr>
 {% for ls in r.linked_services %}
 <tr><td><code>{{ ls.name }}</code></td><td>{{ ls.type }}</td><td>{{ ls.connect_via or '' }}</td>
  <td>{% if ls.fabric_supported %}<span class="pill ok">yes</span>{% else %}<span class="pill err">no</span>{% endif %}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.datasets %}
<h2 id="datasets">Datasets ({{ r.datasets|length }})</h2>
<input class="filter" type="text" placeholder="Filter datasets" oninput="smaFilter(this,'tbl-ds')"/>
<table id="tbl-ds">
 <tr><th>Name</th><th>Type</th><th>Linked service</th><th>Folder</th></tr>
 {% for d in r.datasets %}
 <tr><td><code>{{ d.name }}</code></td><td>{{ d.type }}</td><td>{{ d.linked_service or '' }}</td><td>{{ d.folder or '' }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.triggers %}
<h2 id="triggers">Triggers ({{ r.triggers|length }})</h2>
<table>
 <tr><th>Name</th><th>Type</th><th>Runtime state</th><th>Pipelines</th></tr>
 {% for t in r.triggers %}
 <tr><td><code>{{ t.name }}</code></td><td>{{ t.type }}</td>
  <td>{% if t.runtime_state == 'Started' %}<span class="pill ok">{{ t.runtime_state }}</span>
   {% elif t.runtime_state %}<span class="pill warn">{{ t.runtime_state }}</span>{% endif %}</td>
  <td class="small">{{ ', '.join(t.pipelines) }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.schedule_mappings %}
<h2 id="schedules">Schedule mapping</h2>
<table>
 <tr><th>Trigger</th><th>Synapse type</th><th>Fabric kind</th><th>Summary</th><th>Notes</th></tr>
 {% for m in r.schedule_mappings %}
 <tr><td><code>{{ m.trigger_name }}</code></td><td>{{ m.trigger_type }}</td>
  <td><span class="pill info">{{ m.fabric_kind }}</span></td>
  <td class="small">{{ m.summary or '' }}</td>
  <td class="small muted">{{ '; '.join(m.notes) }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.integration_runtimes %}
<h2 id="ir">Integration runtimes ({{ r.integration_runtimes|length }})</h2>
<table>
 <tr><th>Name</th><th>Type</th><th>Description</th></tr>
 {% for i in r.integration_runtimes %}
 <tr><td><code>{{ i.name }}</code></td>
  <td>{% if i.type == 'SelfHosted' %}<span class="pill warn">{{ i.type }}</span>{% else %}<span class="pill ok">{{ i.type }}</span>{% endif %}</td>
  <td class="small muted">{{ i.description or '' }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.expression_findings %}
<h2 id="expr">Expression findings ({{ r.expression_findings|length }})</h2>
<input class="filter" type="text" placeholder="Filter findings" oninput="smaFilter(this,'tbl-expr')"/>
<table id="tbl-expr">
 <tr><th>Severity</th><th>Pipeline</th><th>Activity</th><th>Rule</th><th>Expression</th></tr>
 {% for f in r.expression_findings %}
 <tr>
  <td>{% set sev = f.severity %}<span class="pill {{ 'err' if sev == 'blocker' else ('warn' if sev == 'warning' else 'info') }}">{{ sev }}</span></td>
  <td><code>{{ f.pipeline }}</code></td>
  <td><code>{{ f.activity }}</code></td>
  <td><code>{{ f.rule_id }}</code> {{ f.label }}</td>
  <td><code class="small">{{ f.expression }}</code></td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.run_history %}
<h2 id="run-stats">Pipeline runtime statistics</h2>
<p class="muted">
 Window: {{ r.run_history.window_start.isoformat() }} &rarr; {{ r.run_history.window_end.isoformat() }}.
 Runs collected: <strong>{{ r.run_history.fetched_run_count }}</strong>{% if r.run_history.truncated %}
  <span class="pill warn">truncated</span>{% endif %}.
 Activity runs collected: {{ r.run_history.fetched_activity_run_count }}.
 Success-rate pill: <span class="pill ok">&ge;99%</span>
 <span class="pill warn">95&ndash;99%</span> <span class="pill err">&lt;95%</span>.
 Data movement is reported for pipelines that statically contain a Copy / Dataflow / Lookup activity.
 Est. CU-hrs (DM) projects Azure-IR DIU-hours to Fabric CU-hours at <strong>1.5 CU-hr / DIU-hr</strong> (heuristic).
 Est. CU-hrs (DF) projects Mapping Data Flow Spark vCore-hours (derived from the executing Spark cluster shape × wall-clock runtime, not service billing) to Fabric Spark CU-hours at <strong>1 vCore-sec = 0.5 CU-sec</strong> (assumes migration to a Fabric Spark job).
 Est. CU-hrs (Orch) charges <strong>0.0056 CU-hr per non-copy activity run</strong> (Microsoft-published Fabric meter), estimated as static non-copy activity count × pipeline runs.
</p>
<input class="filter" type="text" placeholder="Filter pipelines" oninput="smaFilter(this,'tbl-runs')"/>
<table id="tbl-runs">
 <tr>
  <th>Pipeline</th><th>Last run</th><th>Window</th>
  <th class="num">Runs</th><th class="num">Succeeded</th><th class="num">Failed</th>
  <th class="num">Other</th><th class="num">Success rate</th>
  <th class="num">Avg duration (ms)</th><th class="num">p95 (ms)</th>
  <th class="num">Avg MB / run</th><th class="num">Total MB</th>
  <th class="num">DIU-hrs</th><th class="num">Est. CU-hrs (DM)</th>
  <th class="num">vCore-hrs</th><th class="num">Est. CU-hrs (DF)</th>
  <th class="num">Non-copy runs</th><th class="num">Est. CU-hrs (Orch)</th>
 </tr>
 {% for s in r.run_history.by_pipeline %}
  {% for w in s.windows %}
  <tr>
   {% if loop.first %}
   <td rowspan="{{ s.windows|length }}"><code>{{ s.pipeline }}</code>
    {% if not s.has_data_movement %}<br><span class="pill info">no data movement</span>{% endif %}
   </td>
   <td rowspan="{{ s.windows|length }}" class="small">
    {% if s.last_run_at %}{{ s.last_run_at.isoformat() }}<br>
     {% set st = (s.last_run_status or '').lower() %}
     <span class="pill {{ 'ok' if st == 'succeeded' else ('err' if st == 'failed' else 'info') }}">{{ s.last_run_status or '—' }}</span>
    {% else %}<span class="muted">—</span>{% endif %}
   </td>
   {% endif %}
   <td class="num">{{ w.window_days }}d</td>
   <td class="num">{{ w.run_count }}</td>
   <td class="num">{{ w.succeeded }}</td>
   <td class="num">{% if w.failed %}<span class="pill err">{{ w.failed }}</span>{% else %}{{ w.failed }}{% endif %}</td>
   <td class="num">{{ w.other }}</td>
   <td class="num">
    {% if w.success_rate is none %}<span class="muted">—</span>
    {% else %}
     {% set pct = (w.success_rate * 100) %}
     {% set cls = 'ok' if pct >= 99 else ('warn' if pct >= 95 else 'err') %}
     <span class="pill {{ cls }}">{{ '%.1f'|format(pct) }}%</span>
    {% endif %}
   </td>
   <td class="num">{% if w.avg_duration_ms is none %}—{% else %}{{ '%.0f'|format(w.avg_duration_ms) }}{% endif %}</td>
   <td class="num">{% if w.p95_duration_ms is none %}—{% else %}{{ '%.0f'|format(w.p95_duration_ms) }}{% endif %}</td>
   <td class="num">{% if w.avg_data_moved_mb_per_run is none %}—{% else %}{{ '%.2f'|format(w.avg_data_moved_mb_per_run) }}{% endif %}</td>
   <td class="num">{% if w.total_data_moved_mb is none %}—{% else %}{{ '%.2f'|format(w.total_data_moved_mb) }}{% endif %}</td>
   <td class="num">{% if w.total_diu_hours is none %}—{% else %}{{ '%.3f'|format(w.total_diu_hours) }}{% endif %}</td>
   <td class="num">{% if w.est_cu_hours_from_diu is none %}—{% else %}{{ '%.3f'|format(w.est_cu_hours_from_diu) }}{% endif %}</td>
   <td class="num">{% if w.total_vcore_hours is none %}—{% else %}{{ '%.3f'|format(w.total_vcore_hours) }}{% endif %}</td>
   <td class="num">{% if w.est_cu_hours_from_vcore is none %}—{% else %}{{ '%.3f'|format(w.est_cu_hours_from_vcore) }}{% endif %}</td>
   <td class="num">{{ w.est_non_copy_activity_runs }}</td>
   <td class="num">{{ '%.3f'|format(w.est_cu_hours_from_orchestration) }}</td>
  </tr>
  {% endfor %}
 {% endfor %}
</table>
{% endif %}

<div class="footer">Generated by Unified Solution Migration Analyzer.</div>
<script>{{ js }}</script>
</body></html>
"""


def _build_extras(result: PipelinesAnalysis) -> dict[str, Any]:
    acts_by_pipeline: dict[str, list[Any]] = defaultdict(list)
    for a in result.activities:
        acts_by_pipeline[a.pipeline].append(a)

    headline_window = 28
    headline_runs = 0
    headline_failed = 0
    if result.run_history:
        for stats in result.run_history.by_pipeline:
            for w in stats.windows:
                if w.window_days == headline_window:
                    headline_runs += w.run_count
                    headline_failed += w.failed

    return {
        "acts_by_pipeline": dict(acts_by_pipeline),
        "unsupported_acts": [a for a in result.activities if a.support == "unsupported"],
        "partial_acts": [a for a in result.activities if a.support == "partial"],
        "unsupported_ls": [ls for ls in result.linked_services if not ls.fabric_supported],
        "headline_window": headline_window,
        "headline_runs": headline_runs,
        "headline_failed": headline_failed,
    }


def write_html(result: PipelinesAnalysis, out_dir: Path) -> Path:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(_TEMPLATE)
    html = template.render(r=result, extras=_build_extras(result), css=SHARED_CSS, js=SHARED_FILTER_JS)
    path = out_dir / "pipelines.html"
    path.write_text(html, encoding="utf-8")
    return path
