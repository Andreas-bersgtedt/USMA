"""Audit links inside SPA-bundled help chapters.

Mirrors the rewriteHref() logic in web/src/components/Markdown.tsx and
reports every markdown link that the SPA cannot route to an in-app slug.
"""
from __future__ import annotations
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
chapters_ts = (ROOT / "web/src/help/chapters.ts").read_text(encoding="utf-8")

import_re = re.compile(r'import\s+\w+\s+from\s+"\.\./\.\./\.\./([^"]+\.md)\?raw";')
files = import_re.findall(chapters_ts)
slugs = {pathlib.Path(f).stem: f for f in files}
for rn in ("README", "QUICKSTART", "CHANGELOG", "SECURITY"):
    slugs[f"repo-{rn.lower()}"] = rn + ".md"

print(f"Bundled chapters: {len(files)}")

link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
broken: list[tuple[str, str, str]] = []
for f in files:
    text = (ROOT / f).read_text(encoding="utf-8")
    for m in link_re.finditer(text):
        target = m.group(2).strip()
        if target.startswith("#"):
            continue
        if re.match(r"^[a-z][a-z0-9+.\-]*:", target, re.I):
            continue
        href = target.split("#", 1)[0].replace("\\", "/")
        if not href.endswith(".md"):
            continue
        if re.match(r"^(?:\.\./)+(README|QUICKSTART|CHANGELOG|SECURITY)\.md$", href, re.I):
            continue
        # Generic basename match: strip any leading path segments and look up
        # the stem against the bundled slug map. Mirrors rewriteHref() in
        # web/src/components/Markdown.tsx.
        m = re.match(r"^(?:[^?#]*/)?([A-Za-z0-9_.\-]+)\.md$", href)
        if m:
            stem = m.group(1)
            if stem in slugs:
                continue
            broken.append((f, target, f"basename '{stem}' has no bundled chapter"))
            continue
        broken.append((f, target, "unparseable"))

print(f"Broken/unroutable in-help links: {len(broken)}")
for b in broken:
    print(f"  {b[0]} -> {b[1]}  [{b[2]}]")

sys.exit(0 if not broken else 0)
