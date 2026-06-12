#!/usr/bin/env python3
"""Fail if any tracked file contains a known customer identifier.

Runs both as a pre-commit hook (over staged files) and in CI (over the whole
tree). The denylist is base64-encoded so this very file doesn't reintroduce
the strings it's trying to keep out. Extend `_ENCODED_TOKENS` to add more.

Usage:
    python scripts/check_customer_data.py                # scan whole tree
    python scripts/check_customer_data.py path/a path/b  # scan given paths
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

# Each entry is base64(token). Lowercase ASCII tokens only.
_ENCODED_TOKENS: tuple[str, ...] = (
    "ZGV2bGViZGF0YWxha2VzcWw=",   # full SQL server name
    "ZGV2X2RhdGFsYWtlX3JnX3NxbA==",  # full RG name
    "ZGV2X2RhdGFsYWtlX3Jn",        # RG name prefix
    "dGVzdGRlZGljYXRlZHBvb2w=",    # DWU pool name
    "ZGVtb2RhdGEwMDE=",            # demo SQL server alias used in customer scope
)

_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "output", "runs",
}

# Binary-ish extensions we don't want to scan as text.
_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".zip", ".gz", ".tar", ".whl", ".so", ".dll", ".exe", ".bin",
    ".woff", ".woff2", ".ttf", ".otf",
}

# Don't flag this script itself — it carries the encoded denylist.
_SELF = Path(__file__).resolve()


def _decode_tokens() -> list[bytes]:
    return [base64.b64decode(t) for t in _ENCODED_TOKENS]


def _iter_paths(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if root.is_file():
            out.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() in _SKIP_SUFFIXES:
                    continue
                out.append(p)
    return out


def _scan_file(path: Path, tokens: list[bytes]) -> list[tuple[int, str, str]]:
    """Return (line_number, decoded_token_label, line) for every hit."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    lowered = data.lower()
    if not any(t in lowered for t in tokens):
        return []
    hits: list[tuple[int, str, str]] = []
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return []
    for lineno, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for t in tokens:
            if t in low.encode("utf-8", errors="ignore"):
                # Report a stable label (length only) so we don't echo the token.
                label = f"<denylist token len={len(t)}>"
                hits.append((lineno, label, line.rstrip()))
                break
    return hits


def _resolve_roots(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a).resolve() for a in argv]
    # No args: scan the repo root the script lives in.
    return [Path(__file__).resolve().parent.parent]


def main(argv: list[str]) -> int:
    tokens = _decode_tokens()
    roots = _resolve_roots(argv)
    paths = [p for p in _iter_paths(roots) if p.resolve() != _SELF]

    failures: list[tuple[Path, list[tuple[int, str, str]]]] = []
    for p in paths:
        hits = _scan_file(p, tokens)
        if hits:
            failures.append((p, hits))

    if not failures:
        return 0

    print(
        "ERROR: customer-data scanner found denylisted tokens. "
        "Edit scripts/check_customer_data.py to inspect or extend the list.\n",
        file=sys.stderr,
    )
    for path, hits in failures:
        try:
            rel = path.relative_to(Path.cwd())
        except ValueError:
            rel = path
        for lineno, label, line in hits:
            # Truncate the offending line so we don't echo the token into logs.
            snippet = re.sub(r"\s+", " ", line)[:80]
            print(f"  {rel}:{lineno}: {label} in: {snippet}", file=sys.stderr)
    print(
        "\nScrub the file or rebase the offending commit before pushing.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
