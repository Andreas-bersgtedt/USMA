"""HTML report writer for the cost module."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from ...reporting.html_common import SHARED_CSS, SHARED_FILTER_JS
from .models import CostAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Cost</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Cost</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Resource group:</strong> {{ r.resource_group }}</div>
 <div><strong>Subscription:</strong> <code>{{ r.subscription_id }}</code></div>
 <div><strong>Window:</strong> {{ r.window_start.date().isoformat() }} &rarr; {{ r.window_end.date().isoformat() }}</div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
</div>

<div class="grid-2">
 <div class="stat"><div class="label">Cost rows</div><div class="value">{{ r.rows|length }}</div></div>
 <div class="stat"><div class="label">Months</div><div class="value">{{ r.monthly_totals|length }}</div></div>
 <div class="stat"><div class="label">Resource kinds</div><div class="value">{{ r.by_resource_kind|length }}</div></div>
 <div class="stat"><div class="label">Findings</div><div class="value">{{ r.findings|length }}</div></div>
 <div class="stat"><div class="label">Synapse avg/mo</div>
  <div class="value">{% if extras.avg_monthly is not none %}{{ '%.0f'|format(extras.avg_monthly) }}{% else %}&mdash;{% endif %}</div></div>
 <div class="stat"><div class="label">Fabric SKU</div>
  <div class="value">{{ r.fabric_comparison.fabric_capacity_sku if r.fabric_comparison else '&mdash;'|safe }}</div></div>
</div>

<div class="toc">
 <strong>Sections:</strong>
 {% if r.findings %}<a href="#findings">Findings</a>{% endif %}
 {% if r.monthly_totals %}<a href="#months">Monthly totals</a>{% endif %}
 {% if r.by_resource_kind %}<a href="#kinds">By resource kind</a>{% endif %}
 {% if r.by_resource_name %}<a href="#names">By resource</a>{% endif %}
 {% if r.fabric_comparison %}<a href="#fabric">Fabric comparison</a>{% endif %}
 {% if r.errors %}<a href="#errors">Errors</a>{% endif %}
</div>

{% if r.errors %}
<h2 id="errors">Collection errors</h2>
<details open><summary class="err">{{ r.errors|length }} error(s)</summary>
 <ul class="err">{% for e in r.errors %}<li>{{ e }}</li>{% endfor %}</ul></details>
{% endif %}

{% if r.findings %}
<h2 id="findings">Findings</h2>
<input class="filter" type="text" placeholder="Filter findings" oninput="smaFilter(this,'tbl-find')"/>
<table id="tbl-find">
 <tr><th>Severity</th><th>Rule</th><th>Title</th><th>Detail</th></tr>
 {% for f in extras.sorted_findings %}
 <tr>
  <td><span class="pill {{ extras.sev_pill[f.severity] }}">{{ f.severity }}</span></td>
  <td><code>{{ f.rule_id }}</code></td>
  <td>{{ f.title }}</td>
  <td class="small">{{ f.detail or '' }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.monthly_totals %}
<h2 id="months">Monthly totals</h2>
<table>
 <tr><th>Month</th><th class="num">Cost</th><th class="num">&Delta; vs prior</th></tr>
 {% for row in extras.month_rows %}
 <tr>
  <td>{{ row.month }}</td>
  <td class="num">{{ '%.2f'|format(row.cost) }}</td>
  <td class="num">{% if row.delta_pct is not none %}
    {% if row.delta_pct >= 0.25 %}<span class="pill warn">{{ '%+.0f'|format(row.delta_pct*100) }}%</span>
    {% elif row.delta_pct <= -0.25 %}<span class="pill ok">{{ '%+.0f'|format(row.delta_pct*100) }}%</span>
    {% else %}{{ '%+.0f'|format(row.delta_pct*100) }}%{% endif %}
  {% else %}&mdash;{% endif %}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.by_resource_kind %}
<h2 id="kinds">By resource kind</h2>
<table>
 <tr><th>Kind</th><th class="num">Cost</th><th class="num">Share</th></tr>
 {% for row in extras.kind_rows %}
 <tr>
  <td>{{ row.kind }}</td>
  <td class="num">{{ '%.2f'|format(row.cost) }}</td>
  <td class="num">{{ '%.0f'|format(row.share*100) }}%</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.by_resource_name %}
<h2 id="names">By resource</h2>
<input class="filter" type="text" placeholder="Filter resources" oninput="smaFilter(this,'tbl-res')"/>
<table id="tbl-res">
 <tr><th>Resource</th><th class="num">Cost</th></tr>
 {% for row in extras.name_rows %}
 <tr><td><code>{{ row.name }}</code></td><td class="num">{{ '%.2f'|format(row.cost) }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.fabric_comparison %}
<h2 id="fabric">Fabric capacity comparison</h2>
{% set fc = r.fabric_comparison %}
<table>
 <tr><th>Synapse latest/mo</th><td class="num">{{ '%.2f'|format(fc.synapse_avg_monthly_cost) }}</td></tr>
 <tr><th>Fabric SKU</th><td><code>{{ fc.fabric_capacity_sku or 'unknown' }}</code></td></tr>
 <tr><th>Fabric estimate/mo</th><td class="num">{% if fc.fabric_estimated_monthly_cost is not none %}{{ '%.2f'|format(fc.fabric_estimated_monthly_cost) }}{% else %}&mdash;{% endif %}</td></tr>
 <tr><th>Delta</th><td class="num">
  {% if fc.delta_abs is not none %}
   {% if fc.delta_abs < 0 %}<span class="pill ok">{{ '%+.0f'|format(fc.delta_abs) }}</span>
   {% else %}<span class="pill warn">{{ '%+.0f'|format(fc.delta_abs) }}</span>{% endif %}
   {% if fc.delta_pct is not none %} ({{ '%+.0f'|format(fc.delta_pct*100) }}%){% endif %}
  {% else %}&mdash;{% endif %}
 </td></tr>
</table>
{% if fc.notes %}<p class="small muted">{{ fc.notes }}</p>{% endif %}
{% endif %}

<div class="footer">Generated by Unified Solution Migration Analyzer.</div>
<script>{{ js }}</script>
</body></html>
"""


_SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_SEV_PILL = {"high": "err", "medium": "warn", "low": "warn", "info": "info"}


def write_html(result: CostAnalysis, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_findings = sorted(
        result.findings,
        key=lambda f: (_SEV_ORDER.get(f.severity, 99), f.rule_id),
    )

    months = sorted(result.monthly_totals)
    month_rows: list[dict[str, Any]] = []
    prev_cost: float | None = None
    for m in months:
        cost = result.monthly_totals[m]
        delta_pct: float | None = None
        if prev_cost is not None and prev_cost > 0:
            delta_pct = (cost - prev_cost) / prev_cost
        month_rows.append({"month": m, "cost": cost, "delta_pct": delta_pct})
        prev_cost = cost

    total_kind = sum(result.by_resource_kind.values()) or 1.0
    kind_rows = [
        {"kind": k, "cost": c, "share": c / total_kind}
        for k, c in sorted(result.by_resource_kind.items(), key=lambda kv: -kv[1])
    ]

    name_rows = [
        {"name": n, "cost": c}
        for n, c in sorted(result.by_resource_name.items(), key=lambda kv: -kv[1])
    ]

    avg_monthly: float | None = None
    if result.monthly_totals:
        avg_monthly = sum(result.monthly_totals.values()) / len(result.monthly_totals)

    extras = {
        "sorted_findings": sorted_findings,
        "sev_pill": _SEV_PILL,
        "month_rows": month_rows,
        "kind_rows": kind_rows,
        "name_rows": name_rows,
        "avg_monthly": avg_monthly,
    }

    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    tpl = env.from_string(_TEMPLATE)
    html = tpl.render(r=result, extras=extras, css=SHARED_CSS, js=SHARED_FILTER_JS)
    out_path.write_text(html, encoding="utf-8")
    return out_path
