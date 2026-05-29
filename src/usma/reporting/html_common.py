"""Shared CSS + tiny filter script for the SMA HTML reports.

Centralising this keeps the look-and-feel consistent across modules and lets
us evolve the styling in one place.
"""
from __future__ import annotations

# Reused by every module's HTML report. Scoped to the page (no external CSS).
SHARED_CSS = """
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
 .card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: .75rem; margin: .5rem 0 1rem; }
 .card { border: 1px solid var(--bd); border-left: 4px solid var(--brand); border-radius: 4px; padding: .75rem 1rem; background: #fff; }
 .card.warn { border-left-color: #b08800; background: #fffbe6; }
 .card.err  { border-left-color: var(--err); background: #fdecea; }
 .card.warn > summary, .card.err > summary { cursor: pointer; font-weight: 600; }
 .card { margin: .35rem 0; }
 .card h3 { margin: 0 0 .25rem; font-size: 1rem; }
 .card p { margin: .15rem 0; font-size: 13px; color: #444; }
 .card .small { color: #777; }
 .card a.btn { display: inline-block; margin-top: .35rem; color: var(--brand); text-decoration: none; font-weight: 600; font-size: 13px; }
 .card a.btn:hover { text-decoration: underline; }
 .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: .5rem 1rem; margin-bottom: 1rem; }
 .stat { background: var(--bg); border-left: 3px solid var(--brand); padding: .35rem .75rem; border-radius: 0 3px 3px 0; }
 .stat .label { font-size: 11px; text-transform: uppercase; color: #555; letter-spacing: .03em; }
 .stat .value { font-size: 1.2rem; font-weight: 600; color: var(--brand); }
"""

SHARED_FILTER_JS = """
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
"""
