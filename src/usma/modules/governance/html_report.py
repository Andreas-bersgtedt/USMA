"""HTML report writer for the governance module."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from ...reporting.html_common import SHARED_CSS, SHARED_FILTER_JS
from .models import GovernanceAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Governance</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Governance</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Resource group:</strong> {{ r.resource_group }}</div>
 <div><strong>Subscription:</strong> <code>{{ r.subscription_id }}</code></div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
 {% if r.purview_account %}
 <div><strong>Purview account:</strong> <code>{{ r.purview_account }}</code></div>
 {% else %}
 <div><strong>Purview account:</strong> <span class="muted">(not configured)</span></div>
 {% endif %}
</div>

<div class="grid-2">
 <div class="stat"><div class="label">Role assignments</div><div class="value">{{ r.role_assignments|length }}</div></div>
 <div class="stat"><div class="label">Managed PEs</div><div class="value">{{ r.managed_private_endpoints|length }}</div></div>
 <div class="stat"><div class="label">CMK records</div><div class="value">{{ r.customer_managed_keys|length }}</div></div>
 <div class="stat"><div class="label">Findings</div><div class="value">{{ r.findings|length }}</div></div>
</div>

<div class="toc">
 <strong>Sections:</strong>
 {% if r.findings %}<a href="#findings">Findings</a>{% endif %}
 {% if r.role_assignments %}<a href="#rbac">Role assignments</a>{% endif %}
 {% if r.managed_private_endpoints %}<a href="#mpe">Managed PEs</a>{% endif %}
 {% if r.customer_managed_keys %}<a href="#cmk">Customer-managed keys</a>{% endif %}
 {% if r.purview_lineage %}<a href="#lineage">Purview lineage</a>{% endif %}
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
 <tr><th>Severity</th><th>Rule</th><th>Title</th><th>Resource</th><th>Detail</th></tr>
 {% for f in extras.sorted_findings %}
 <tr>
  <td><span class="pill {{ extras.sev_pill[f.severity] }}">{{ f.severity }}</span></td>
  <td><code>{{ f.rule_id }}</code></td>
  <td>{{ f.title }}</td>
  <td class="small"><code>{{ f.resource_id or '' }}</code></td>
  <td class="small">{{ f.detail or '' }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.role_assignments %}
<h2 id="rbac">Role assignments</h2>
<input class="filter" type="text" placeholder="Filter role assignments" oninput="smaFilter(this,'tbl-rbac')"/>
<table id="tbl-rbac">
 <tr><th>Scope</th><th>Role</th><th>Principal type</th><th>Principal id</th><th>Plane</th></tr>
 {% for ra in r.role_assignments %}
 <tr>
  <td class="small"><span class="pill info">{{ ra.scope_kind }}</span><br><code>{{ ra.scope }}</code></td>
  <td>{{ ra.role_name }}</td>
  <td>{{ ra.principal_type or '' }}</td>
  <td class="small"><code>{{ ra.principal_id }}</code></td>
  <td>{{ ra.plane }}</td>
 </tr>
 {% endfor %}
</table>
{% if extras.role_summary %}
<h3>Roles by scope kind</h3>
<table>
 <tr><th>Scope kind</th><th>Role</th><th class="num">Count</th></tr>
 {% for row in extras.role_summary %}
 <tr><td>{{ row.scope_kind }}</td><td>{{ row.role_name }}</td>
  <td class="num">{{ row.count }}</td></tr>
 {% endfor %}
</table>
{% endif %}
{% endif %}

{% if r.managed_private_endpoints %}
<h2 id="mpe">Managed private endpoints</h2>
<table id="tbl-mpe">
 <tr><th>Name</th><th>Group</th><th>Provisioning</th><th>Connection</th><th>Target</th></tr>
 {% for m in r.managed_private_endpoints %}
 <tr>
  <td><code>{{ m.name }}</code></td>
  <td>{{ m.group_id or '' }}</td>
  <td>{{ m.provisioning_state or '' }}</td>
  <td>
    {% if m.connection_state == 'Approved' %}<span class="pill ok">{{ m.connection_state }}</span>
    {% elif m.connection_state %}<span class="pill warn">{{ m.connection_state }}</span>
    {% endif %}
  </td>
  <td class="small"><code>{{ m.target_resource_id or '' }}</code></td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.customer_managed_keys %}
<h2 id="cmk">Customer-managed keys</h2>
<table>
 <tr><th>Resource</th><th>Enabled</th><th>Key vault</th><th>Key</th></tr>
 {% for c in r.customer_managed_keys %}
 <tr>
  <td>{{ c.resource_kind }}<br><code class="small">{{ c.resource_id }}</code></td>
  <td>{% if c.enabled %}<span class="pill ok">enabled</span>
      {% else %}<span class="pill warn">disabled</span>{% endif %}</td>
  <td class="small">{{ c.key_vault_uri or '' }}</td>
  <td>{{ c.key_name or '' }}{% if c.key_version %}<br><span class="small muted">{{ c.key_version }}</span>{% endif %}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.purview_lineage %}
<h2 id="lineage">Purview lineage</h2>
<table>
 <tr><th>Source</th><th>Target</th><th>Process</th></tr>
 {% for e in r.purview_lineage %}
 <tr>
  <td class="small"><code>{{ e.source_qualified_name }}</code><br>
      <span class="muted">{{ e.source_type }}</span></td>
  <td class="small"><code>{{ e.target_qualified_name }}</code><br>
      <span class="muted">{{ e.target_type }}</span></td>
  <td class="small">{{ e.process_qualified_name or '' }}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

<div class="footer">Generated by Unified Solution Migration Analyzer.</div>
<script>{{ js }}</script>
</body></html>
"""


_SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_SEV_PILL = {"high": "err", "medium": "warn", "low": "warn", "info": "info"}


def write_html(result: GovernanceAnalysis, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_findings = sorted(
        result.findings,
        key=lambda f: (_SEV_ORDER.get(f.severity, 99), f.rule_id),
    )

    # Roll up roles by (scope_kind, role_name) for a quick at-a-glance view.
    roll: dict[tuple[str, str], int] = defaultdict(int)
    for ra in result.role_assignments:
        roll[(ra.scope_kind, ra.role_name)] += 1
    role_summary = [
        {"scope_kind": sk, "role_name": rn, "count": c}
        for (sk, rn), c in sorted(roll.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    extras: dict[str, Any] = {
        "sorted_findings": sorted_findings,
        "sev_pill": _SEV_PILL,
        "role_summary": role_summary,
    }

    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    tpl = env.from_string(_TEMPLATE)
    html = tpl.render(r=result, extras=extras, css=SHARED_CSS, js=SHARED_FILTER_JS)
    out_path.write_text(html, encoding="utf-8")
    return out_path
