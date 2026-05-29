"""Tests for the storage module's pure helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from usma.modules.storage import analyzer as sa
from usma.modules.storage.analyzer import (
    _build_capacity,
    _populate_pool_storage,
)
from usma.modules.storage.models import (
    DedicatedPoolStorage,
    StorageAnalysis,
)
from usma.modules.storage.reporting import (
    _fmt_bytes,
    write_reports,
)


def test_build_capacity_converts_bytes_to_mb_and_gb() -> None:
    one_gib = 1024 ** 3
    cap = _build_capacity("acct1", {
        "UsedCapacity": 5 * one_gib,
        "BlobCapacity": 4 * one_gib,
        "BlobCount": 1234,
        "ContainerCount": 7,
        "FileCapacity": one_gib,
        "FileCount": 100,
        "TableCapacity": None,
        "QueueCapacity": None,
    })
    assert cap.account_name == "acct1"
    assert cap.used_capacity_bytes == 5 * one_gib
    assert cap.used_capacity_gb == 5.0
    assert cap.used_capacity_mb == 5 * 1024.0
    assert cap.blob_capacity_gb == 4.0
    assert cap.blob_count == 1234
    assert cap.container_count == 7
    assert cap.file_count == 100
    assert cap.table_capacity_bytes is None


def test_build_capacity_handles_all_nones() -> None:
    cap = _build_capacity("acct2", {})
    assert cap.account_name == "acct2"
    assert cap.used_capacity_bytes is None
    assert cap.used_capacity_gb is None


def test_populate_pool_storage_rolls_mb_to_gb() -> None:
    entry = DedicatedPoolStorage(
        pool_name="dw1",
        captured_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
    )
    _populate_pool_storage(entry, {
        "table_count": 42,
        "row_count": 1_000_000,
        "reserved_space_mb": 4096.0,
        "data_space_mb": 3072.0,
        "index_or_unused_mb": 512.0,
    })
    assert entry.table_count == 42
    assert entry.row_count == 1_000_000
    assert entry.reserved_space_mb == 4096.0
    assert entry.reserved_space_gb == 4.0
    assert entry.data_space_gb == 3.0
    assert entry.index_space_gb == 0.5
    # 4096 MB total - 3072 data - 512 index = 512 unused
    assert entry.unused_space_mb == 512.0


def test_populate_pool_storage_handles_nulls() -> None:
    entry = DedicatedPoolStorage(
        pool_name="dw2",
        captured_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
    )
    _populate_pool_storage(entry, {})
    assert entry.table_count == 0
    assert entry.row_count == 0
    assert entry.reserved_space_mb == 0.0
    assert entry.reserved_space_gb == 0.0


def test_fmt_bytes_picks_largest_unit() -> None:
    assert _fmt_bytes(None) == ""
    assert _fmt_bytes(512) == "512 B"
    assert _fmt_bytes(2 * 1024 ** 2) == "2.00 MB"
    assert _fmt_bytes(3 * 1024 ** 3) == "3.00 GB"
    assert _fmt_bytes(2 * 1024 ** 4) == "2.00 TB"


def test_write_reports_emits_all_formats(tmp_path) -> None:
    result = StorageAnalysis(
        workspace_name="ws",
        subscription_id="sub",
        resource_group="rg",
        generated_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
    )
    entry = DedicatedPoolStorage(
        pool_name="dw1",
        captured_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
        table_count=10,
        row_count=1000,
        reserved_space_mb=2048.0,
        data_space_mb=1024.0,
        index_space_mb=512.0,
        reserved_space_gb=2.0,
        data_space_gb=1.0,
        index_space_gb=0.5,
        max_size_bytes=10 * 1024 ** 3,
        max_size_gb=10.0,
        used_pct_of_max=20.0,
    )
    result.dedicated_pool_storage.append(entry)

    paths = write_reports(result, tmp_path, formats=["json", "csv", "markdown", "html"])
    names = {p.name for p in paths}
    assert "storage.json" in names
    assert "storage.md" in names
    assert "storage.html" in names
    assert "dedicated_pool_storage.csv" in names

    md = (tmp_path / "storage.md").read_text(encoding="utf-8")
    assert "Dedicated SQL pool storage" in md
    assert "dw1" in md
    html = (tmp_path / "storage.html").read_text(encoding="utf-8")
    assert "dw1" in html
    assert "Storage" in html


def test_constants_match_documented_values() -> None:
    # Sanity: 1 GB == 1024 MB
    assert sa._BYTES_PER_GB == sa._BYTES_PER_MB * 1024
    assert sa._MB_PER_GB == 1024.0
