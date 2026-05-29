"""HTML report writer for the security module."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from ...reporting.html_common import SHARED_CSS, SHARED_FILTER_JS
from .models import SecurityAnalysis

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Unified Solution Migration Analyzer &mdash; Security</title>
<style>{{ css }}</style></head><body>
<h1>Unified Solution Migration Analyzer &mdash; Security</h1>
<div class="meta">
 <div><strong>Workspace:</strong> {{ r.workspace_name }}</div>
 <div><strong>Resource group:</strong> {{ r.resource_group }}</div>
 <div><strong>Subscription:</strong> <code>{{ r.subscription_id }}</code></div>
 <div><strong>Generated:</strong> {{ r.generated_at.isoformat() }}</div>
</div>

<div class="grid-2">
 <div class="stat"><div class="label">Findings</div><div class="value">{{ r.findings|length }}</div></div>
 <div class="stat"><div class="label">High severity</div>
  <div class="value">{{ extras.sev_counts.get('high', 0) }}</div></div>
 <div class="stat"><div class="label">Firewall rules</div><div class="value">{{ r.firewall_rules|length }}</div></div>
 <div class="stat"><div class="label">Credentials inventoried</div><div class="value">{{ r.credentials|length }}</div></div>
 <div class="stat"><div class="label">Pools (TDE checked)</div><div class="value">{{ r.pool_tde_status|length }}</div></div>
 <div class="stat"><div class="label">AAD admins</div><div class="value">{{ r.aad_admins|length }}</div></div>
</div>

<div class="toc">
 <strong>Sections:</strong>
 {% if r.findings %}<a href="#findings">Findings</a>{% endif %}
 {% if r.workspace_settings %}<a href="#ws">Workspace settings</a>{% endif %}
 {% if r.firewall_rules %}<a href="#fw">Firewall rules</a>{% endif %}
 {% if r.pool_tde_status %}<a href="#tde">Pool TDE</a>{% endif %}
 {% if r.credentials %}<a href="#creds">Credentials</a>{% endif %}
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

{% if r.workspace_settings %}
<h2 id="ws">Workspace settings</h2>
{% set s = r.workspace_settings %}
<table>
 <tr><th>AAD-only auth</th><th>Public network access</th><th>Min TLS</th>
   <th>Encryption at rest</th><th>Managed VNet</th></tr>
 <tr>
  <td>{% if s.aad_only_authentication %}<span class="pill ok">enabled</span>
      {% else %}<span class="pill err">disabled</span>{% endif %}</td>
  <td>{% if s.public_network_access == 'Disabled' %}<span class="pill ok">disabled</span>
      {% elif s.public_network_access %}<span class="pill warn">{{ s.public_network_access }}</span>
      {% else %}<span class="muted">unknown</span>{% endif %}</td>
  <td>{{ s.minimum_tls_version or '' }}</td>
  <td>{{ s.encryption_at_rest or '' }}</td>
  <td>{% if s.managed_vnet %}<span class="pill ok">yes</span>
      {% else %}<span class="pill warn">no</span>{% endif %}</td>
 </tr>
</table>
{% if r.aad_admins %}
<h3>AAD administrators</h3>
<ul>{% for a in r.aad_admins %}<li><code>{{ a }}</code></li>{% endfor %}</ul>
{% else %}
<p><span class="pill err">missing</span> No AAD workspace administrators configured.</p>
{% endif %}
{% endif %}

{% if r.firewall_rules %}
<h2 id="fw">Firewall rules</h2>
<input class="filter" type="text" placeholder="Filter firewall rules" oninput="smaFilter(this,'tbl-fw')"/>
<table id="tbl-fw">
 <tr><th>Resource</th><th>Name</th><th>Start IP</th><th>End IP</th><th>Note</th></tr>
 {% for fw in r.firewall_rules %}
 <tr>
  <td>{{ fw.resource_kind }}</td>
  <td><code>{{ fw.name }}</code></td>
  <td class="nowrap small">{{ fw.start_ip or '' }}</td>
  <td class="nowrap small">{{ fw.end_ip or '' }}</td>
  <td>
    {% if fw.is_allow_all %}<span class="pill err">allow all</span>
    {% elif fw.is_allow_azure_services %}<span class="pill warn">allow Azure services</span>
    {% endif %}
  </td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.pool_tde_status %}
<h2 id="tde">Dedicated pool Transparent Data Encryption</h2>
<table>
 <tr><th>Pool</th><th>Status</th></tr>
 {% for p in r.pool_tde_status %}
 <tr>
  <td><code>{{ p.pool_name }}</code></td>
  <td>{% if p.status == 'Enabled' %}<span class="pill ok">{{ p.status }}</span>
      {% elif p.status == 'Unknown' %}<span class="pill warn">{{ p.status }}</span>
      {% else %}<span class="pill err">{{ p.status }}</span>{% endif %}</td>
 </tr>
 {% endfor %}
</table>
{% endif %}

{% if r.credentials %}
<h2 id="creds">Linked-service credentials</h2>
{% if extras.cred_kind_summary %}
<table>
 <tr><th>Credential kind</th><th class="num">Count</th></tr>
 {% for kind, count in extras.cred_kind_summary %}
 <tr><td>{{ kind }}</td><td class="num">{{ count }}</td></tr>
 {% endfor %}
</table>
{% endif %}
<input class="filter" type="text" placeholder="Filter credentials" oninput="smaFilter(this,'tbl-cred')"/>
<table id="tbl-cred">
 <tr><th>Linked service</th><th>Kind</th><th>Inline?</th><th>Key Vault reference</th></tr>
 {% for c in r.credentials %}
 <tr>
  <td><code>{{ c.container_name }}</code></td>
  <td>{{ c.credential_kind }}</td>
  <td>{% if c.has_inline_secret %}<span class="pill err">inline secret</span>
      {% else %}<span class="pill ok">no</span>{% endif %}</td>
  <td class="small">{{ c.secret_reference or '' }}</td>
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


def write_html(result: SecurityAnalysis, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sorted_findings = sorted(
        result.findings,
        key=lambda f: (_SEV_ORDER.get(f.severity, 99), f.rule_id),
    )
    sev_counts = dict(Counter(f.severity for f in result.findings))
    cred_kind_summary = sorted(
        Counter(c.credential_kind for c in result.credentials).items(),
        key=lambda kv: (-kv[1], kv[0]),
    )

    extras: dict[str, Any] = {
        "sorted_findings": sorted_findings,
        "sev_pill": _SEV_PILL,
        "sev_counts": sev_counts,
        "cred_kind_summary": cred_kind_summary,
    }

    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    tpl = env.from_string(_TEMPLATE)
    html = tpl.render(r=result, extras=extras, css=SHARED_CSS, js=SHARED_FILTER_JS)
    out_path.write_text(html, encoding="utf-8")
    return out_path
