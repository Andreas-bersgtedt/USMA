from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from ..modules.dedicated_pools.models import PoolAnalysis, WorkspaceAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Dedicated Pools</title>
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
 details[open] { background: #fff; }
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
 .reasons { font-size: 11px; color:#555; }
 .footer { margin: 2rem 0 0; padding-top: .5rem; border-top: 1px solid var(--bd); color: #888; font-size: 11px; }
</style>
</head>
<body>
<h1>Unified Solution Migration Analyzer &mdash; Dedicated SQL Pools</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Subscription:</strong> {{ r.subscription_id }}</div>
 <div><strong>Resource group:</strong> {{ r.resource_group }}</div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
 <div><strong>Pools analyzed:</strong> {{ r.pools|length }}</div>
</div>

{% if r.pools|length > 1 %}
<div class="toc">
 <strong>Pools:</strong>
 {% for pool in r.pools %}<a href="#pool-{{ loop.index0 }}">{{ pool.inventory.name }}</a>{% endfor %}
</div>
{% endif %}

{% for pool in r.pools %}
{% set pix = loop.index0 %}
{% set ext = ext_per_pool[pix] %}
<h2 id="pool-{{ pix }}">Pool: {{ pool.inventory.name }}</h2>
<div class="badge-row">
 <span class="pill {{ 'warn' if (pool.inventory.status or '')|lower == 'paused' else 'ok' }}">{{ pool.inventory.status or 'Unknown' }}</span>
 <span class="pill info">{{ pool.inventory.sku_name or '?' }}</span>
 <span class="pill info">DWU {{ pool.inventory.sku_capacity or '?' }}</span>
 <span class="pill info">{{ pool.inventory.location }}</span>
 {% if pool.inventory.collation %}<span class="pill {{ 'ok' if pool.inventory.collation == 'Latin1_General_100_BIN2_UTF8' else 'warn' }}">collation: {{ pool.inventory.collation }}</span>{% endif %}
</div>

{% if pool.errors %}
<details open>
 <summary class="err">Collection errors ({{ pool.errors|length }})</summary>
 <ul class="err">{% for e in pool.errors %}<li>{{ e }}</li>{% endfor %}</ul>
</details>
{% endif %}

<details open>
 <summary>Schemas ({{ pool.schemas|length }})</summary>
 <table><tr><th>Schema</th><th class="num">Objects</th></tr>
 {% for s in pool.schemas %}<tr><td>{{ s.schema_name }}</td><td class="num">{{ s.object_count }}</td></tr>{% endfor %}
 </table>
</details>

<details open>
 <summary>Tables ({{ pool.tables|length }})
  &mdash; <span class="small muted">click a row to drill into columns + stats + collations</span></summary>
 <input class="filter" type="text" placeholder="Filter tables (e.g. dbo.fact)" oninput="smaFilter(this, 'tbl-{{ pix }}')"/>
 <table id="tbl-{{ pix }}">
 <tr>
  <th>Schema.Table</th><th>Distribution</th><th>Dist Col</th><th>Partitioned</th>
  <th class="num">Rows</th><th class="num">Reserved MB</th><th>Index Type</th><th class="num">Cols</th>
 </tr>
 {% for t in pool.tables %}
 {% set tk = (t.schema_name, t.table_name) %}
 {% set cols = ext.cols_by_table.get(tk, []) %}
 {% set advice = ext.advice_by_table.get(tk, []) %}
 {% set stats_for_table = ext.stats_by_table.get(tk, []) %}
 <tr>
  <td>
   <details>
    <summary><code>{{ t.schema_name }}.{{ t.table_name }}</code></summary>
    <h4>Columns ({{ cols|length }})</h4>
    {% if cols %}
    <table>
     <tr>
      <th>Column</th><th>Type</th><th class="num">Max len</th><th>Nullable</th>
      <th>Collation</th><th class="num">Rows</th><th class="num">Distinct</th>
      <th class="num">Selectivity</th><th class="num">Nulls %</th><th class="num">Skew</th>
     </tr>
     {% for c in cols %}
     <tr>
      <td><code>{{ c.column_name }}</code></td>
      <td>{{ c.data_type or '' }}</td>
      <td class="num">{{ c.max_length if c.max_length is not none else '' }}</td>
      <td>{{ '' if c.is_nullable is none else ('yes' if c.is_nullable else 'no') }}</td>
      <td>
       {% if c.collation_name %}{{ c.collation_name }}
       {% if c.differs_from_db %}<span class="pill warn">differs</span>{% endif %}
       {% endif %}
      </td>
      <td class="num">{{ c.row_count if c.row_count is not none else '' }}</td>
      <td class="num">{{ c.distinct_count if c.distinct_count is not none else '' }}</td>
      <td class="num">{{ '%.3f'|format(c.selectivity) if c.selectivity is not none else '' }}</td>
      <td class="num">{{ '%.0f'|format(c.null_pct) ~ '%' if c.null_pct is not none else '' }}</td>
      <td class="num">{{ '%.0f'|format(c.skew_pct) ~ '%' if c.skew_pct is not none else '' }}</td>
     </tr>
     {% endfor %}
    </table>
    {% else %}
    <p class="muted small">No column-level data captured (column_collation / column_stats collectors returned no rows for this table).</p>
    {% endif %}

    {% if advice %}
    <h4>Distribution-key candidates</h4>
    <table>
     <tr><th>Column</th><th class="num">Score</th><th>Reasons</th></tr>
     {% for a in advice %}
     <tr>
      <td><code>{{ a.column_name }}</code></td>
      <td class="num"><strong>{{ a.score }}</strong></td>
      <td class="reasons">{{ a.reasons|join('; ') }}</td>
     </tr>
     {% endfor %}
    </table>
    {% endif %}

    {% if stats_for_table %}
    <h4>Statistics ({{ stats_for_table|length }})</h4>
    <table>
     <tr><th>Stat</th><th>User</th><th>Auto</th><th class="num">Days since update</th><th class="num">Rows</th><th class="num">Modifications</th></tr>
     {% for s in stats_for_table %}
     <tr>
      <td>{{ s.stat_name }}</td>
      <td>{{ 'yes' if s.user_created else '' }}</td>
      <td>{{ 'yes' if s.auto_created else '' }}</td>
      <td class="num">{{ s.days_since_update if s.days_since_update is not none else '' }}{% if s.days_since_update and s.days_since_update > 14 %} <span class="pill warn">stale</span>{% endif %}</td>
      <td class="num">{{ s.rows if s.rows is not none else '' }}</td>
      <td class="num">{{ s.modification_counter if s.modification_counter is not none else '' }}</td>
     </tr>
     {% endfor %}
    </table>
    {% endif %}
   </details>
  </td>
  <td>{{ t.distribution_policy or '' }}</td>
  <td>{{ t.distribution_column or '' }}</td>
  <td>{{ 'yes' if t.is_partitioned else '' }}</td>
  <td class="num">{{ t.row_count if t.row_count is not none else '' }}</td>
  <td class="num">{{ '%.1f'|format(t.reserved_space_mb) if t.reserved_space_mb is not none else '' }}</td>
  <td>{{ t.index_type or '' }}</td>
  <td class="num">{{ cols|length }}</td>
 </tr>
 {% endfor %}
 </table>
</details>

<details>
 <summary>Indexes ({{ pool.indexes|length }})</summary>
 <table><tr><th>Schema.Table</th><th>Index</th><th>Type</th><th>Unique</th><th>PK</th><th>Leading key</th></tr>
 {% for i in pool.indexes %}
 <tr><td><code>{{ i.schema_name }}.{{ i.table_name }}</code></td><td>{{ i.index_name or '' }}</td>
  <td>{{ i.index_type }}</td><td>{{ 'yes' if i.is_unique else '' }}</td>
  <td>{{ 'yes' if i.is_primary_key else '' }}</td><td>{{ i.first_key_column or '' }}</td></tr>
 {% endfor %}
 </table>
</details>

<details>
 <summary>Usage ({{ pool.usage|length }})</summary>
 <table><tr><th>Metric</th><th class="num">Value</th><th>Unit</th></tr>
 {% for u in pool.usage %}<tr><td>{{ u.metric }}</td><td class="num">{{ u.value }}</td><td>{{ u.unit or '' }}</td></tr>{% endfor %}
 </table>
</details>

<details>
 <summary>Workload groups ({{ pool.workload_groups|length }})</summary>
 <table>
  <tr><th>Name</th><th>Importance</th><th class="num">Min %</th><th class="num">Cap %</th><th class="num">Min Grant %</th><th class="num">Classifiers</th></tr>
  {% for w in pool.workload_groups %}
  <tr><td>{{ w.name }}</td><td>{{ w.importance or '' }}</td>
   <td class="num">{{ w.min_resource_pct or '' }}</td><td class="num">{{ w.cap_resource_pct or '' }}</td>
   <td class="num">{{ w.request_min_resource_grant_pct or '' }}</td><td class="num">{{ w.classifier_count }}</td></tr>
  {% endfor %}
 </table>
</details>

<details>
 <summary>Security ({{ pool.security|length }})</summary>
 <table><tr><th>Name</th><th>Type</th><th>Roles</th></tr>
 {% for sp in pool.security %}
 <tr><td>{{ sp.name }}</td><td>{{ sp.type }}</td><td>{{ sp.role_memberships|join(', ') }}</td></tr>
 {% endfor %}
 </table>
</details>

{% if pool.column_collations %}
<details>
 <summary>Column collations ({{ pool.column_collations|length }};
  {{ ext.collation_diff_count }} differ from DB default)</summary>
 <input class="filter" type="text" placeholder="Filter (e.g. SQL_Latin1)" oninput="smaFilter(this, 'col-{{ pix }}')"/>
 <table id="col-{{ pix }}">
  <tr><th>Schema.Table</th><th>Column</th><th>Type</th><th>Collation</th><th>DB collation</th><th>Differs</th></tr>
  {% for c in pool.column_collations %}
  <tr>
   <td><code>{{ c.schema_name }}.{{ c.table_name }}</code></td>
   <td>{{ c.column_name }}</td>
   <td>{{ c.data_type or '' }}</td>
   <td>{{ c.collation_name or '' }}</td>
   <td>{{ c.db_collation or '' }}</td>
   <td>{% if c.differs_from_db %}<span class="pill warn">yes</span>{% endif %}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endif %}

{% if pool.materialized_views %}
<details>
 <summary>Materialized views ({{ pool.materialized_views|length }})</summary>
 <table>
  <tr><th>Schema.View</th><th>Created</th><th>Modified</th></tr>
  {% for v in pool.materialized_views %}
  <tr>
   <td><details><summary><code>{{ v.schema_name }}.{{ v.view_name }}</code></summary>
    {% if v.definition %}<pre>{{ v.definition }}</pre>{% else %}<p class="muted small">No definition captured.</p>{% endif %}
   </details></td>
   <td>{{ v.create_date.isoformat() if v.create_date else '' }}</td>
   <td>{{ v.modify_date.isoformat() if v.modify_date else '' }}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endif %}

{% if pool.statistics %}
<details>
 <summary>Statistics freshness ({{ pool.statistics|length }};
  {{ ext.stale_stat_count }} stale &gt; 14 d)</summary>
 <input class="filter" type="text" placeholder="Filter table or stat" oninput="smaFilter(this, 'stat-{{ pix }}')"/>
 <table id="stat-{{ pix }}">
  <tr><th>Schema.Table</th><th>Stat</th><th>User</th><th>Auto</th><th class="num">Days</th><th class="num">Rows</th><th class="num">Mods</th></tr>
  {% for s in pool.statistics %}
  <tr>
   <td><code>{{ s.schema_name }}.{{ s.table_name }}</code></td>
   <td>{{ s.stat_name }}</td>
   <td>{{ 'yes' if s.user_created else '' }}</td>
   <td>{{ 'yes' if s.auto_created else '' }}</td>
   <td class="num">{{ s.days_since_update if s.days_since_update is not none else '' }}{% if s.days_since_update and s.days_since_update > 14 %} <span class="pill warn">stale</span>{% endif %}</td>
   <td class="num">{{ s.rows if s.rows is not none else '' }}</td>
   <td class="num">{{ s.modification_counter if s.modification_counter is not none else '' }}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endif %}

{% if pool.distribution_candidates %}
<details>
 <summary>Distribution-key candidates ({{ pool.distribution_candidates|length }})</summary>
 <table>
  <tr><th>Schema.Table</th><th>Column</th><th class="num">Score</th><th>Reasons</th></tr>
  {% for c in pool.distribution_candidates %}
  <tr>
   <td><code>{{ c.schema_name }}.{{ c.table_name }}</code></td>
   <td><code>{{ c.column_name }}</code></td>
   <td class="num"><strong>{{ c.score }}</strong></td>
   <td class="reasons">{{ c.reasons|join('; ') }}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endif %}

{% if pool.code_objects %}
<details>
 <summary>Code objects ({{ pool.code_objects|length }})
 {% if pool.code_object_summary and pool.code_object_summary.compatibility_pct is not none %}
 &mdash;
  {% set pct = pool.code_object_summary.compatibility_pct %}
  {% if pct >= 80 %}<span class="pill ok">Fabric-compatible: {{ pct }}%</span>
  {% elif pct >= 50 %}<span class="pill warn">Fabric-compatible: {{ pct }}%</span>
  {% else %}<span class="pill err">Fabric-compatible: {{ pct }}%</span>{% endif %}
 {% endif %}
 </summary>
 {% if pool.code_object_summary %}
 {% set sumr = pool.code_object_summary %}
 <h4>SQL-plane inventory</h4>
 <table>
  <tr>
   <th class="num">Procedures</th>
   <th class="num">Scalar UDFs</th>
   <th class="num">Inline TVFs</th>
   <th class="num">Multi-stmt TVFs</th>
   <th class="num">Views</th>
   <th class="num">Compatible</th>
   <th class="num">Needs review</th>
   <th class="num">Incompatible</th>
  </tr>
  <tr>
   <td class="num">{{ sumr.by_type.get('procedure', 0) }}</td>
   <td class="num">{{ sumr.by_type.get('scalar_function', 0) }}</td>
   <td class="num">{{ sumr.by_type.get('inline_tvf', 0) }}</td>
   <td class="num">{{ sumr.by_type.get('multi_stmt_tvf', 0) }}</td>
   <td class="num">{{ sumr.by_type.get('view', 0) }}</td>
   <td class="num">{% set v = sumr.by_compatibility.get('compatible', 0) %}{% if v %}<span class="pill ok">{{ v }}</span>{% else %}0{% endif %}</td>
   <td class="num">{% set v = sumr.by_compatibility.get('needs_review', 0) %}{% if v %}<span class="pill warn">{{ v }}</span>{% else %}0{% endif %}</td>
   <td class="num">{% set v = sumr.by_compatibility.get('incompatible', 0) %}{% if v %}<span class="pill err">{{ v }}</span>{% else %}0{% endif %}</td>
  </tr>
 </table>
 {% endif %}
 <input class="filter" type="text" placeholder="Filter (schema, name, id, type, compatibility)" oninput="smaFilter(this, 'code-{{ pix }}')"/>
 <table id="code-{{ pix }}">
  <tr><th>Object</th><th>Type</th><th>Compatibility</th><th class="num">Lines</th><th class="num">Params</th><th>Stable id</th><th class="num">T-SQL gaps</th></tr>
  {% for o in ext.sorted_code_objects %}
  {% set gaps = ext.gaps_by_object.get(o.code_object_id, []) %}
  <tr>
   <td>
    <details>
     <summary><code>{{ o.schema_name }}.{{ o.object_name }}</code></summary>
     {% if o.parameters %}
     <h4>Parameters ({{ o.parameter_count }})</h4>
     <table>
      <tr><th class="num">#</th><th>Name</th><th>Type</th><th class="num">Max len</th><th>Output</th><th>Default</th></tr>
      {% for p in o.parameters %}
      <tr>
       <td class="num">{{ p.ordinal }}</td>
       <td><code>{{ p.parameter_name }}</code></td>
       <td>{{ p.data_type or '' }}</td>
       <td class="num">{{ p.max_length if p.max_length is not none else '' }}</td>
       <td>{{ 'yes' if p.is_output else '' }}</td>
       <td>{{ 'yes' if p.has_default else '' }}</td>
      </tr>
      {% endfor %}
     </table>
     {% endif %}
     {% if o.create_date or o.modify_date or o.line_count %}
     <p class="small muted">
      {% if o.create_date %}Created: {{ o.create_date.isoformat() if o.create_date.isoformat is defined else o.create_date }} &middot; {% endif %}
      {% if o.modify_date %}Modified: {{ o.modify_date.isoformat() if o.modify_date.isoformat is defined else o.modify_date }} &middot; {% endif %}
      {% if o.line_count %}{{ o.line_count }} line(s){% endif %}
      {% if o.definition_length %} &middot; {{ o.definition_length }} chars{% endif %}
     </p>
     {% endif %}
     {% if gaps %}
     <h4>T-SQL surface gaps</h4>
     <table>
      <tr><th>Rule</th><th>Severity</th><th class="num">Matches</th><th>Fabric action</th></tr>
      {% for g in gaps %}
      <tr>
       <td>{{ g.label }} <span class="muted small">({{ g.rule_id }})</span></td>
       <td><span class="pill {{ {'blocker':'err','warning':'warn','info':'info'}.get(g.severity,'info') }}">{{ g.severity }}</span></td>
       <td class="num">{{ g.matches }}</td>
       <td class="small">{{ g.fabric_action or '' }}</td>
      </tr>
      {% endfor %}
     </table>
     {% endif %}
     {% if o.definition %}
     <h4>Definition <span class="muted small">(truncated to 50 KB by collector)</span></h4>
     <pre>{{ o.definition }}</pre>
     {% endif %}
    </details>
   </td>
   <td>{{ o.object_type }}</td>
   <td>
    {% set cls = {'compatible':'ok','needs_review':'warn','incompatible':'err'}.get(o.compatibility, 'info') %}
    <span class="pill {{ cls }}">{{ o.compatibility or 'compatible' }}</span>
   </td>
   <td class="num">{{ o.line_count if o.line_count is not none else '' }}</td>
   <td class="num">{{ o.parameter_count }}</td>
   <td class="small"><code>{{ o.code_object_id or '' }}</code></td>
   <td class="num">{{ gaps|length }}{% if gaps %} <span class="pill warn">!</span>{% endif %}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endif %}

{% if pool.tsql_surface_gaps %}
<details>
 <summary>T-SQL surface gaps &mdash; rule rollup ({{ pool.tsql_surface_gaps|length }} findings across
  {{ ext.gap_object_count }} object(s))</summary>
 <table>
  <tr><th>Rule</th><th>Severity</th><th class="num">Findings</th><th class="num">Objects</th><th>Fabric action</th></tr>
  {% for row in ext.gap_rule_rollup %}
  <tr>
   <td>{{ row.label }} <span class="muted small">({{ row.rule_id }})</span></td>
   <td><span class="pill {{ {'blocker':'err','warning':'warn','info':'info'}.get(row.severity,'info') }}">{{ row.severity }}</span></td>
   <td class="num">{{ row.findings }}</td>
   <td class="num">{{ row.objects }}</td>
   <td class="small">{{ row.fabric_action or '' }}</td>
  </tr>
  {% endfor %}
 </table>
</details>
{% endif %}

{% endfor %}

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


def _build_extras(pool: PoolAnalysis) -> dict[str, Any]:
    """Pre-compute per-pool aggregations the template uses for the drill-down views.

    Done in Python (not Jinja) to keep the template readable and to centralize the
    join logic between collations / column stats / advisor output / surface gaps.
    """
    # 1. Per-table column-level rows (collations + column stats merged on column name).
    cols_by_table: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for c in pool.column_collations:
        key = (c.schema_name, c.table_name)
        cols_by_table[key][c.column_name] = {
            "column_name": c.column_name,
            "data_type": c.data_type,
            "max_length": c.max_length,
            "is_nullable": None,
            "collation_name": c.collation_name,
            "differs_from_db": c.differs_from_db,
            "row_count": None,
            "distinct_count": None,
            "selectivity": None,
            "null_pct": None,
            "skew_pct": None,
        }
    for s in pool.column_stats:
        key = (s.schema_name, s.table_name)
        row = cols_by_table[key].setdefault(s.column_name, {
            "column_name": s.column_name,
            "data_type": s.data_type,
            "max_length": s.max_length,
            "is_nullable": s.is_nullable,
            "collation_name": None,
            "differs_from_db": False,
            "row_count": None,
            "distinct_count": None,
            "selectivity": None,
            "null_pct": None,
            "skew_pct": None,
        })
        row["data_type"] = row["data_type"] or s.data_type
        row["max_length"] = row["max_length"] if row["max_length"] is not None else s.max_length
        row["is_nullable"] = s.is_nullable
        row["row_count"] = s.row_count
        row["distinct_count"] = s.distinct_count
        if s.row_count and s.distinct_count is not None:
            row["selectivity"] = s.distinct_count / s.row_count
        if s.row_count and s.null_count is not None:
            row["null_pct"] = (s.null_count / s.row_count) * 100
        if s.row_count and s.max_frequency:
            row["skew_pct"] = (s.max_frequency / s.row_count) * 100
    # Order columns deterministically.
    cols_by_table_list: dict[tuple[str, str], list[dict[str, Any]]] = {
        k: sorted(v.values(), key=lambda r: r["column_name"]) for k, v in cols_by_table.items()
    }

    # 2. Distribution candidates per (schema, table), sorted by score desc.
    advice_by_table: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for a in pool.distribution_candidates:
        advice_by_table[(a.schema_name, a.table_name)].append(a)
    for v in advice_by_table.values():
        v.sort(key=lambda x: x.score, reverse=True)

    # 3. Statistics per (schema, table).
    stats_by_table: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for s in pool.statistics:
        stats_by_table[(s.schema_name, s.table_name)].append(s)

    # 4. T-SQL surface gaps grouped by code_object_id and by rule_id.
    gaps_by_object: dict[str, list[Any]] = defaultdict(list)
    rule_agg: dict[str, dict[str, Any]] = {}
    for g in pool.tsql_surface_gaps:
        gaps_by_object[g.code_object_id].append(g)
        rb = rule_agg.setdefault(g.rule_id, {
            "rule_id": g.rule_id, "label": g.label, "severity": g.severity,
            "fabric_action": g.fabric_action, "findings": 0, "_objs": set(),
        })
        rb["findings"] += 1
        rb["_objs"].add(g.code_object_id)
    rule_rollup = sorted(
        ({**v, "objects": len(v["_objs"])} for v in rule_agg.values()),
        key=lambda r: ({"blocker": 0, "warning": 1, "info": 2}.get(r["severity"], 3), -r["findings"]),
    )

    return {
        "cols_by_table": cols_by_table_list,
        "advice_by_table": dict(advice_by_table),
        "stats_by_table": dict(stats_by_table),
        "gaps_by_object": dict(gaps_by_object),
        "gap_rule_rollup": rule_rollup,
        "gap_object_count": len(gaps_by_object),
        "collation_diff_count": sum(1 for c in pool.column_collations if c.differs_from_db),
        "stale_stat_count": sum(
            1 for s in pool.statistics if (s.days_since_update or 0) > 14
        ),
        # Code objects sorted: incompatible -> needs_review -> compatible,
        # then by gap_count desc, then by qualified name. Surfaces problematic
        # procs / functions at the top of the drill-down table.
        "sorted_code_objects": sorted(
            pool.code_objects,
            key=lambda o: (
                {"incompatible": 0, "needs_review": 1, "compatible": 2}.get(
                    o.compatibility or "compatible", 3,
                ),
                -(o.gap_count or 0),
                (o.schema_name or "").lower(),
                (o.object_name or "").lower(),
            ),
        ),
    }


def write_html(result: WorkspaceAnalysis, out_dir: Path) -> Path:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(_TEMPLATE)
    ext_per_pool = [_build_extras(p) for p in result.pools]
    html = template.render(r=result, ext_per_pool=ext_per_pool)
    path = out_dir / "dedicated_pools.html"
    path.write_text(html, encoding="utf-8")
    return path
