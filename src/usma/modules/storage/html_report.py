"""HTML report writer for the storage module."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, select_autoescape

from ...reporting.html_common import SHARED_CSS, SHARED_FILTER_JS
from .models import StorageAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Storage</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Storage</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Subscription:</strong> <code>{{ r.subscription_id }}</code></div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
</div>

<div class="grid-2">
 <div class="stat"><div class="label">Storage accounts</div><div class="value">{{ r.accounts|length }}</div></div>
 <div class="stat"><div class="label">ADLS Gen2 accounts</div><div class="value">{{ adls_count }}</div></div>
 <div class="stat"><div class="label">Capacity samples</div><div class="value">{{ r.capacities|length }}</div></div>
 <div class="stat"><div class="label">Total used capacity</div><div class="value">{{ '%.2f' % total_used_gb }} GB</div></div>
 <div class="stat"><div class="label">Dedicated pools sized</div><div class="value">{{ r.dedicated_pool_storage|length }}</div></div>
 <div class="stat"><div class="label">Total dedicated pool data</div><div class="value">{{ '%.2f' % total_pool_data_gb }} GB</div></div>
</div>

<div class="toc">
 <strong>Sections:</strong>
 {% if r.dedicated_pool_storage %}<a href="#pools">Dedicated SQL pool storage</a>{% endif %}
 {% if r.accounts %}<a href="#accounts">Storage accounts</a>{% endif %}
 {% if r.capacities %}<a href="#capacity">Capacity (Azure Monitor)</a>{% endif %}
 {% if r.errors %}<a href="#errors">Errors</a>{% endif %}
</div>

{% if r.errors %}
<h2 id="errors">Collection errors</h2>
<details open><summary class="err">{{ r.errors|length }} error(s)</summary>
 <ul class="err">{% for e in r.errors %}<li>{{ e }}</li>{% endfor %}</ul></details>
{% endif %}

{% if r.dedicated_pool_storage %}
<h2 id="pools">Dedicated SQL pool storage ({{ r.dedicated_pool_storage|length }})</h2>
<input class="filter" type="text" placeholder="Filter pools" oninput="smaFilter(this,'tbl-pools')"/>
<table id="tbl-pools">
 <tr>
  <th>Pool</th><th class="num">Tables</th><th class="num">Rows</th>
  <th class="num">Reserved (MB)</th><th class="num">Reserved (GB)</th>
  <th class="num">Data (GB)</th><th class="num">Index (GB)</th>
  <th class="num">Max (GB)</th><th class="num">% of max</th><th>Notes</th>
 </tr>
 {% for p in r.dedicated_pool_storage %}
 <tr>
  <td><code>{{ p.pool_name }}</code></td>
  <td class="num">{{ '{:,}'.format(p.table_count) }}</td>
  <td class="num">{{ '{:,}'.format(p.row_count) }}</td>
  <td class="num">{{ '{:,.2f}'.format(p.reserved_space_mb) }}</td>
  <td class="num">{{ '{:,.2f}'.format(p.reserved_space_gb) }}</td>
  <td class="num">{{ '{:,.2f}'.format(p.data_space_gb) }}</td>
  <td class="num">{{ '{:,.2f}'.format(p.index_space_gb) }}</td>
  <td class="num">{{ '{:,.2f}'.format(p.max_size_gb) if p.max_size_gb is not none else '' }}</td>
  <td class="num">
   {% if p.used_pct_of_max is not none %}
    {% if p.used_pct_of_max >= 85 %}<span class="pill err">{{ '%.2f' % p.used_pct_of_max }} %</span>
    {% elif p.used_pct_of_max >= 50 %}<span class="pill warn">{{ '%.2f' % p.used_pct_of_max }} %</span>
    {% else %}<span class="pill ok">{{ '%.2f' % p.used_pct_of_max }} %</span>{% endif %}
   {% endif %}
  </td>
  <td class="small muted">{{ '; '.join(p.notes) }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.accounts %}
<h2 id="accounts">Storage accounts ({{ r.accounts|length }})</h2>
<input class="filter" type="text" placeholder="Filter accounts" oninput="smaFilter(this,'tbl-accts')"/>
<table id="tbl-accts">
 <tr>
  <th>Name</th><th>Kind</th><th>SKU</th><th>Access tier</th>
  <th>HNS (ADLS Gen2)</th><th>Workspace default</th><th>Location</th>
 </tr>
 {% for a in r.accounts %}
 <tr>
  <td><code>{{ a.name }}</code></td>
  <td>{{ a.kind or '' }}</td>
  <td>{{ a.sku or '' }}</td>
  <td>{{ a.access_tier or '' }}</td>
  <td>{% if a.is_hns_enabled %}<span class="pill ok">yes</span>{% else %}<span class="pill info">no</span>{% endif %}</td>
  <td>{% if a.is_workspace_default %}<span class="pill ok">yes ({{ a.default_filesystem or '' }})</span>{% else %}<span class="muted">no</span>{% endif %}</td>
  <td class="small muted">{{ a.location or '' }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.capacities %}
<h2 id="capacity">Capacity (latest sample, Azure Monitor)</h2>
<input class="filter" type="text" placeholder="Filter capacity" oninput="smaFilter(this,'tbl-cap')"/>
<table id="tbl-cap">
 <tr>
  <th>Account</th>
  <th class="num">Used (GB)</th><th class="num">Used (MB)</th>
  <th class="num">Blob (GB)</th><th class="num">Containers</th><th class="num">Blobs</th>
  <th class="num">Files</th><th class="num">File capacity (GB)</th>
 </tr>
 {% for c in r.capacities %}
 <tr>
  <td><code>{{ c.account_name }}</code></td>
  <td class="num">{{ '{:,.2f}'.format(c.used_capacity_gb) if c.used_capacity_gb is not none else '' }}</td>
  <td class="num">{{ '{:,.2f}'.format(c.used_capacity_mb) if c.used_capacity_mb is not none else '' }}</td>
  <td class="num">{{ '{:,.2f}'.format(c.blob_capacity_gb) if c.blob_capacity_gb is not none else '' }}</td>
  <td class="num">{{ '{:,}'.format(c.container_count) if c.container_count is not none else '' }}</td>
  <td class="num">{{ '{:,}'.format(c.blob_count) if c.blob_count is not none else '' }}</td>
  <td class="num">{{ '{:,}'.format(c.file_count) if c.file_count is not none else '' }}</td>
  <td class="num">
   {% if c.file_capacity_bytes is not none %}{{ '%.2f' % (c.file_capacity_bytes / 1073741824.0) }}{% endif %}
  </td>
 </tr>
 {% endfor %}
</table>
{% endif %}

<div class="footer">Generated by Unified Solution Migration Analyzer.</div>
<script>{{ js }}</script>
</body></html>
"""


def write_html(result: StorageAnalysis, out_dir: Path) -> Path:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    tmpl = env.from_string(_TEMPLATE)
    adls_count = sum(1 for a in result.accounts if a.is_hns_enabled)
    total_used_gb = sum((c.used_capacity_gb or 0.0) for c in result.capacities)
    total_pool_data_gb = sum((p.data_space_gb or 0.0) for p in result.dedicated_pool_storage)
    html = tmpl.render(
        r=result,
        css=SHARED_CSS,
        js=SHARED_FILTER_JS,
        adls_count=adls_count,
        total_used_gb=total_used_gb,
        total_pool_data_gb=total_pool_data_gb,
    )
    path = out_dir / "storage.html"
    path.write_text(html, encoding="utf-8")
    return path
