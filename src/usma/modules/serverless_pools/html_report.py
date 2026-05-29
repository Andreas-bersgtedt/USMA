"""HTML report writer for the serverless_pools module.

Mirrors the dedicated-pools drill-down style: sticky table headers, severity
pills, per-database `<details>` drawers, per-table column-level drawer, and
a `<pre>` drawer for full query text on the Top Queries section.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from .models import ServerlessAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Serverless SQL</title>
<style>
 :root { --brand:#0078d4; --bg:#f8f9fb; --bd:#dfe3e8; --warn:#b07c00; --err:#b00020; --ok:#107c10; }
 body { font-family: 'Segoe UI', Arial, sans-serif; margin: 1.5rem; color: #222; }
 h1 { border-bottom: 2px solid var(--brand); padding-bottom:.25rem; margin-bottom:.5rem; }
 h2 { color: var(--brand); margin-top: 1.5rem; }
 h3 { margin: 1rem 0 .25rem; font-size: 1.05rem; }
 h4 { margin: .25rem 0; font-size: .95rem; color:#555; }
 table { border-collapse: collapse; margin: .25rem 0 .75rem; width:100%; font-size: 13px; }
 th, td { border: 1px solid var(--bd); padding: 5px 8px; text-align:left; vertical-align: top; }
 th { background: #f3f6fa; position: sticky; top: 0; }
 tr:nth-child(even) td { background: #fafbfc; }
 td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
 code, pre { font-family: Consolas, monospace; font-size: 12px; }
 pre { background:#f3f6fa; padding:.5rem; border-left: 3px solid var(--brand); white-space: pre-wrap; word-break: break-word; max-height: 320px; overflow:auto; }
 .meta { background: var(--bg); padding: .75rem 1rem; border-left: 4px solid var(--brand); margin-bottom: 1rem; }
 .meta div { margin: .15rem 0; }
 .toc { background: var(--bg); padding: .5rem 1rem; border:1px solid var(--bd); border-radius: 4px; margin-bottom: 1rem; }
 .toc a { margin-right: 1rem; }
 details { border: 1px solid var(--bd); border-radius: 4px; padding: .25rem .75rem; margin: .25rem 0; background: #fff; }
 details > summary { cursor: pointer; padding: .35rem 0; font-weight: 600; outline: none; user-select: none; }
 details summary::-webkit-details-marker { color: var(--brand); }
 details details { margin: .25rem 0 .25rem 1rem; background: #fafbfc; }
 .pill { display:inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px; font-weight: 600; vertical-align: middle; }
 .pill.ok { background:#e6f4ea; color: var(--ok); }
 .pill.warn { background:#fff4ce; color: var(--warn); }
 .pill.err { background:#fde7e9; color: var(--err); }
 .pill.info { background:#deecf9; color: var(--brand); }
 .badge-row { margin: .15rem 0; }
 .err { color: var(--err); }
 .filter { padding: 4px 8px; margin: .25rem 0 .5rem; width: 280px; border:1px solid var(--bd); border-radius:3px; font-size: 13px; }
 .small { font-size: 12px; color:#555; }
 .muted { color:#777; }
 .nowrap { white-space: nowrap; }
 .footer { margin: 2rem 0 0; padding-top: .5rem; border-top: 1px solid var(--bd); color: #888; font-size: 11px; }
</style>
</head>
<body>
<h1>Unified Solution Migration Analyzer &mdash; Serverless SQL</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Endpoint:</strong> <code>{{ r.endpoint_fqdn }}</code></div>
 <div><strong>Subscription:</strong> {{ r.subscription_id }}</div>
 <div><strong>Resource group:</strong> {{ r.resource_group }}</div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
 <div><strong>Databases:</strong> {{ r.databases|length }}
  &nbsp;<strong>External data sources:</strong> {{ r.external_data_sources|length }}
  &nbsp;<strong>External tables:</strong> {{ r.external_tables|length }}</div>
</div>

<div class="badge-row">
{% if r.cost_estimate %}
 <span class="pill info">cost (last {{ r.cost_estimate.window_days }}d): USD {{ '%.2f'|format(r.cost_estimate.estimated_cost_usd) }}</span>
 <span class="pill info">data scanned: {{ '%.3f'|format(r.cost_estimate.total_data_processed_tb) }} TB</span>
 <span class="pill info">@ USD {{ '%.2f'|format(r.cost_estimate.list_price_usd_per_tb) }}/TB</span>
{% endif %}
{% if extras.unique_storage_accounts %}<span class="pill info">storage accounts: {{ extras.unique_storage_accounts }}</span>{% endif %}
{% if extras.collation_warn_count %}<span class="pill warn">non-UTF8 DB collations: {{ extras.collation_warn_count }}</span>{% endif %}
{% if r.errors %}<span class="pill err">collection errors: {{ r.errors|length }}</span>{% endif %}
</div>

<div class="toc">
 <strong>Sections:</strong>
 <a href="#databases">Databases</a>
 {% if r.external_data_sources %}<a href="#data-sources">External data sources</a>{% endif %}
 {% if r.external_tables %}<a href="#external-tables">External tables</a>{% endif %}
 {% if r.cost_estimate %}<a href="#cost">Cost estimate</a>{% endif %}
 {% if r.storage_account_usage %}<a href="#storage">Storage account usage</a>{% endif %}
 {% if r.daily_usage %}<a href="#daily">Daily usage</a>{% endif %}
 {% if r.top_queries %}<a href="#top-queries">Top queries</a>{% endif %}
 {% if r.usage %}<a href="#usage">Usage stats</a>{% endif %}
 {% if r.errors %}<a href="#errors">Errors</a>{% endif %}
</div>

{% if r.errors %}
<h2 id="errors">Collection errors</h2>
<details open>
 <summary class="err">{{ r.errors|length }} error(s)</summary>
 <ul class="err">{% for e in r.errors %}<li>{{ e }}</li>{% endfor %}</ul>
</details>
{% endif %}

<h2 id="databases">Databases ({{ r.databases|length }})</h2>
{% if not r.databases %}
<p class="muted small">No user databases found on the serverless endpoint.</p>
{% else %}
<input class="filter" type="text" placeholder="Filter databases" oninput="smaFilter(this, 'tbl-databases')"/>
<table id="tbl-databases">
 <tr><th>Database</th><th>Collation</th><th>Created</th>
  <th class="num">Ext. data sources</th><th class="num">Ext. tables</th></tr>
 {% for d in r.databases %}
 {% set ds = extras.eds_by_db.get(d.name, []) %}
 {% set ets = extras.tables_by_db.get(d.name, []) %}
 <tr>
  <td>
   <details>
    <summary><code>{{ d.name }}</code></summary>
    {% if ds %}
    <h4>External data sources ({{ ds|length }})</h4>
    <table>
     <tr><th>Name</th><th>Type</th><th>Location</th></tr>
     {% for s in ds %}
     <tr><td><code>{{ s.name }}</code></td><td>{{ s.type or '' }}</td>
      <td><code class="small">{{ s.location or '' }}</code></td></tr>
     {% endfor %}
    </table>
    {% endif %}
    {% if ets %}
    <h4>External tables ({{ ets|length }})</h4>
    <table>
     <tr><th>Schema.Table</th><th>Data source</th><th>Format</th><th>Location</th><th class="num">Cols</th></tr>
     {% for t in ets %}
     {% set tk = (t.database, t.schema_name, t.table_name) %}
     {% set cols = extras.cols_by_table.get(tk, []) %}
     <tr>
      <td>
       <details>
        <summary><code>{{ t.schema_name }}.{{ t.table_name }}</code></summary>
        {% if cols %}
        <table>
         <tr><th>Column</th><th>Type</th><th class="num">Max len</th><th class="num">Precision</th><th class="num">Scale</th><th>Nullable</th></tr>
         {% for c in cols %}
         <tr>
          <td><code>{{ c.column_name }}</code></td>
          <td>{{ c.data_type or '' }}</td>
          <td class="num">{{ c.max_length if c.max_length is not none else '' }}</td>
          <td class="num">{{ c.precision if c.precision is not none else '' }}</td>
          <td class="num">{{ c.scale if c.scale is not none else '' }}</td>
          <td>{{ 'yes' if c.is_nullable else 'no' }}</td>
         </tr>
         {% endfor %}
        </table>
        {% else %}
        <p class="muted small">No column metadata captured.</p>
        {% endif %}
       </details>
      </td>
      <td>{{ t.data_source or '' }}</td>
      <td>{{ t.file_format or '' }}</td>
      <td><code class="small">{{ t.location or '' }}</code></td>
      <td class="num">{{ cols|length }}</td>
     </tr>
     {% endfor %}
    </table>
    {% endif %}
    {% if not ds and not ets %}
    <p class="muted small">No external data sources or external tables in this database.</p>
    {% endif %}
   </details>
  </td>
  <td>
   {% if d.collation %}{{ d.collation }}
    {% if d.collation.startswith('Latin1_General_100_BIN2_UTF8') %}<span class="pill ok">UTF-8</span>
    {% else %}<span class="pill warn">non-UTF8</span>{% endif %}
   {% endif %}
  </td>
  <td class="nowrap small">{{ d.create_date.isoformat() if d.create_date else '' }}</td>
  <td class="num">{{ ds|length }}</td>
  <td class="num">{{ ets|length }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.external_data_sources %}
<h2 id="data-sources">External data sources ({{ r.external_data_sources|length }})</h2>
<input class="filter" type="text" placeholder="Filter data sources" oninput="smaFilter(this, 'tbl-eds')"/>
<table id="tbl-eds">
 <tr><th>Database</th><th>Name</th><th>Type</th><th>Location</th></tr>
 {% for s in r.external_data_sources %}
 <tr><td>{{ s.database }}</td><td><code>{{ s.name }}</code></td><td>{{ s.type or '' }}</td>
  <td><code class="small">{{ s.location or '' }}</code></td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.external_tables %}
<h2 id="external-tables">External tables ({{ r.external_tables|length }})</h2>
<input class="filter" type="text" placeholder="Filter external tables (e.g. dbo.fact)" oninput="smaFilter(this, 'tbl-extt')"/>
<table id="tbl-extt">
 <tr><th>Database</th><th>Schema.Table</th><th>Data source</th><th>Format</th><th>Location</th><th class="num">Cols</th></tr>
 {% for t in r.external_tables %}
 {% set tk = (t.database, t.schema_name, t.table_name) %}
 {% set cols = extras.cols_by_table.get(tk, []) %}
 <tr>
  <td>{{ t.database }}</td>
  <td>
   <details>
    <summary><code>{{ t.schema_name }}.{{ t.table_name }}</code></summary>
    {% if cols %}
    <table>
     <tr><th>Column</th><th>Type</th><th class="num">Max len</th><th class="num">Precision</th><th class="num">Scale</th><th>Nullable</th></tr>
     {% for c in cols %}
     <tr>
      <td><code>{{ c.column_name }}</code></td>
      <td>{{ c.data_type or '' }}</td>
      <td class="num">{{ c.max_length if c.max_length is not none else '' }}</td>
      <td class="num">{{ c.precision if c.precision is not none else '' }}</td>
      <td class="num">{{ c.scale if c.scale is not none else '' }}</td>
      <td>{{ 'yes' if c.is_nullable else 'no' }}</td>
     </tr>
     {% endfor %}
    </table>
    {% else %}
    <p class="muted small">No column metadata captured.</p>
    {% endif %}
   </details>
  </td>
  <td>{{ t.data_source or '' }}</td>
  <td>{{ t.file_format or '' }}</td>
  <td><code class="small">{{ t.location or '' }}</code></td>
  <td class="num">{{ cols|length }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.cost_estimate %}
<h2 id="cost">Cost estimate</h2>
<table>
 <tr><th>Window (days)</th><th class="num">Data processed (TB)</th><th class="num">List price (USD/TB)</th><th class="num">Estimated cost (USD)</th><th>Notes</th></tr>
 <tr>
  <td class="num">{{ r.cost_estimate.window_days }}</td>
  <td class="num">{{ '%.4f'|format(r.cost_estimate.total_data_processed_tb) }}</td>
  <td class="num">{{ '%.2f'|format(r.cost_estimate.list_price_usd_per_tb) }}</td>
  <td class="num"><strong>{{ '%.2f'|format(r.cost_estimate.estimated_cost_usd) }}</strong></td>
  <td class="small muted">{{ r.cost_estimate.notes or '' }}</td>
 </tr>
</table>
{% endif %}

{% if r.storage_account_usage %}
<h2 id="storage">Storage account usage</h2>
<table>
 <tr><th>Storage account</th><th class="num">Queries</th><th class="num">Data processed (MB)</th><th class="num">Estimated cost (USD)</th></tr>
 {% for s in r.storage_account_usage %}
 <tr>
  <td><code>{{ s.storage_account }}</code></td>
  <td class="num">{{ s.query_count }}</td>
  <td class="num">{{ s.data_processed_mb }}</td>
  <td class="num">{{ '%.2f'|format(s.estimated_cost_usd) }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.daily_usage %}
<h2 id="daily">Daily data processed</h2>
<table>
 <tr><th>Day</th><th class="num">Requests</th><th class="num">Data processed (MB)</th><th class="num">Execution time (s)</th></tr>
 {% for d in r.daily_usage %}
 <tr><td class="nowrap">{{ d.day }}</td><td class="num">{{ d.request_count }}</td><td class="num">{{ d.data_processed_mb }}</td><td class="num">{{ d.duration_seconds }}</td></tr>
 {% endfor %}
</table>
{% endif %}

{% if r.top_queries %}
<h2 id="top-queries">Top queries by data scanned ({{ r.top_queries|length }})</h2>
<input class="filter" type="text" placeholder="Filter queries (login, status, text...)" oninput="smaFilter(this, 'tbl-tq')"/>
<table id="tbl-tq">
 <tr>
  <th>Start</th><th>Login</th><th>Status</th>
  <th class="num">Duration (s)</th><th class="num">Data (MB)</th><th>Command</th>
 </tr>
 {% for q in r.top_queries %}
 <tr>
  <td class="nowrap small">{{ q.start_time.isoformat() if q.start_time else '' }}</td>
  <td>{{ q.login_name or '' }}</td>
  <td>
   {% if q.status %}
    {% set sl = q.status|lower %}
    <span class="pill {{ 'err' if 'fail' in sl or 'error' in sl else ('warn' if 'cancel' in sl else 'ok') }}">{{ q.status }}</span>
   {% endif %}
   {% if q.error_code %}<span class="pill err">err {{ q.error_code }}</span>{% endif %}
  </td>
  <td class="num">{{ q.duration_seconds if q.duration_seconds is not none else '' }}</td>
  <td class="num">{{ q.data_processed_mb if q.data_processed_mb is not none else '' }}</td>
  <td>
   {% if q.command_text %}
   <details><summary class="small muted">show ({{ q.command_text|length }} chars)</summary>
    <pre>{{ q.command_text }}</pre>
   </details>
   {% endif %}
  </td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.usage %}
<h2 id="usage">Usage stats</h2>
<table>
 <tr><th>Metric</th><th>Value</th><th>Unit</th><th>Captured</th></tr>
 {% for u in r.usage %}
 <tr><td>{{ u.metric }}</td><td>{{ u.value if u.value is not none else '' }}</td>
  <td>{{ u.unit or '' }}</td>
  <td class="nowrap small">{{ u.captured_at.isoformat() if u.captured_at else '' }}</td></tr>
 {% endfor %}
</table>
{% endif %}

<div class="footer">
 Generated by Unified Solution Migration Analyzer. Click any &#9656; row to drill in.
</div>

<script>
function smaFilter(input, tableId) {
  const q = input.value.toLowerCase();
  const tbl = document.getElementById(tableId);
  if (!tbl) return;
  const rows = tbl.querySelectorAll('tr');
  rows.forEach((r, i) => {
    if (i === 0) return; // header
    r.style.display = r.innerText.toLowerCase().includes(q) ? '' : 'none';
  });
}
</script>
</body></html>
"""


def _build_extras(result: ServerlessAnalysis) -> dict[str, Any]:
    """Pre-compute aggregations the template uses for drill-down views."""
    eds_by_db: dict[str, list[Any]] = defaultdict(list)
    for s in result.external_data_sources:
        eds_by_db[s.database].append(s)

    tables_by_db: dict[str, list[Any]] = defaultdict(list)
    for t in result.external_tables:
        tables_by_db[t.database].append(t)

    cols_by_table: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for c in result.external_table_columns:
        cols_by_table[(c.database, c.schema_name, c.table_name)].append(c)
    for v in cols_by_table.values():
        v.sort(key=lambda c: c.column_id if c.column_id is not None else 0)

    unique_storage_accounts = len({s.storage_account for s in result.storage_account_usage})

    collation_warn_count = sum(
        1 for d in result.databases
        if d.collation and not d.collation.startswith("Latin1_General_100_BIN2_UTF8")
    )

    return {
        "eds_by_db": dict(eds_by_db),
        "tables_by_db": dict(tables_by_db),
        "cols_by_table": dict(cols_by_table),
        "unique_storage_accounts": unique_storage_accounts,
        "collation_warn_count": collation_warn_count,
    }


def write_html(result: ServerlessAnalysis, out_dir: Path) -> Path:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(_TEMPLATE)
    extras = _build_extras(result)
    html = template.render(r=result, extras=extras)
    path = out_dir / "serverless_pools.html"
    path.write_text(html, encoding="utf-8")
    return path
