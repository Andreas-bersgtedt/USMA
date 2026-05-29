"""Group serverless data-processed totals by storage account.

The serverless DMVs report per-query data-processed but not which storage account the
data lived on. We extract the storage host from:

1. ``external_data_sources.location`` (preferred — explicit host).
2. ``top_queries.command_text`` (fallback — parse OPENROWSET / FROM '<url>' string).

This lets us attribute the 30-day TB-processed and the rough cost back to a storage
account, which is the natural unit for OneLake-shortcut planning.

Not extracted today (potential v3): file-format breakdown (parquet / csv / delta).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse


@dataclass(frozen=True)
class StorageAccountAttribution:
    storage_account: str  # e.g. "mydatalake.dfs.core.windows.net"
    query_count: int
    data_processed_mb: int
    estimated_cost_usd: float


# Detect adls / blob hostnames inside arbitrary text.
_HOST_RE = re.compile(
    r"(?:abfss?://[^/?\s'\"`]+@)?([a-z0-9]+\.(?:dfs|blob)\.core\.windows\.net)",
    re.IGNORECASE,
)


def attribute(
    *,
    top_queries: Iterable[dict],
    external_data_sources: Iterable[dict] = (),
    list_price_usd_per_tb: float = 5.0,
) -> list[StorageAccountAttribution]:
    """Return per-storage-account totals over the supplied queries."""
    eds_hosts: dict[str, str] = {}
    for eds in external_data_sources:
        loc = eds.get("location") or ""
        m = _HOST_RE.search(loc)
        if m:
            eds_hosts[(eds.get("name") or "").lower()] = m.group(1).lower()

    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"queries": 0, "mb": 0})
    for q in top_queries:
        host = _extract_host(q.get("command_text") or "")
        if not host:
            continue
        mb = q.get("data_processed_mb") or 0
        totals[host]["queries"] += 1
        totals[host]["mb"] += int(mb)

    out: list[StorageAccountAttribution] = []
    for host, t in sorted(totals.items(), key=lambda kv: kv[1]["mb"], reverse=True):
        tb = t["mb"] / (1024 * 1024)
        out.append(StorageAccountAttribution(
            storage_account=host,
            query_count=t["queries"],
            data_processed_mb=t["mb"],
            estimated_cost_usd=round(tb * list_price_usd_per_tb, 2),
        ))
    return out


def _extract_host(text: str) -> str | None:
    m = _HOST_RE.search(text)
    if m:
        return m.group(1).lower()
    # Try urlparse on the first quoted token that looks like a URL.
    for token in re.findall(r"['\"`]([^'\"`]+)['\"`]", text):
        if token.startswith(("abfss://", "abfs://", "https://", "wasbs://")):
            netloc = urlparse(token).netloc
            netloc = netloc.split("@")[-1].lower()
            if netloc and (".dfs.core.windows.net" in netloc or ".blob.core.windows.net" in netloc):
                return netloc
    return None
