"""Tests for the OS-aware helpers in :mod:`usma.platform`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from usma import platform as p


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Wipe every XDG / Windows env var that might leak through from the host.
    for key in (
        "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        "LOCALAPPDATA", "APPDATA", "SMA_RUNS_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _force_platform(monkeypatch, name: str) -> None:
    monkeypatch.setattr(sys, "platform", name)


# --------------------------------------------------------------------------- #
# Cache / config / data dirs
# --------------------------------------------------------------------------- #

def test_cache_dir_linux_default(fake_home, monkeypatch):
    _force_platform(monkeypatch, "linux")
    assert p.cache_dir() == fake_home / ".cache" / "usma"


def test_cache_dir_linux_xdg_override(fake_home, monkeypatch, tmp_path):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdgc"))
    assert p.cache_dir() == tmp_path / "xdgc" / "usma"


def test_cache_dir_macos(fake_home, monkeypatch):
    _force_platform(monkeypatch, "darwin")
    assert p.cache_dir() == fake_home / ".cache" / "usma"


def test_cache_dir_windows(fake_home, monkeypatch, tmp_path):
    _force_platform(monkeypatch, "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert p.cache_dir() == tmp_path / "Local" / "USMA" / "Cache"


def test_config_dir_linux(fake_home, monkeypatch):
    _force_platform(monkeypatch, "linux")
    assert p.config_dir() == fake_home / ".config" / "usma"


def test_config_dir_windows(fake_home, monkeypatch, tmp_path):
    _force_platform(monkeypatch, "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert p.config_dir() == tmp_path / "Roaming" / "USMA"


def test_data_dir_linux(fake_home, monkeypatch):
    _force_platform(monkeypatch, "linux")
    assert p.data_dir() == fake_home / ".local" / "share" / "usma"


def test_data_dir_windows_no_localappdata(fake_home, monkeypatch):
    _force_platform(monkeypatch, "win32")
    # Fallback to ~/AppData/Local when %LOCALAPPDATA% is unset.
    assert p.data_dir() == fake_home / "AppData" / "Local" / "USMA"


# --------------------------------------------------------------------------- #
# Runs dir
# --------------------------------------------------------------------------- #

def test_default_runs_dir_env_wins(fake_home, monkeypatch, tmp_path):
    _force_platform(monkeypatch, "linux")
    target = tmp_path / "custom-runs"
    monkeypatch.setenv("SMA_RUNS_DIR", str(target))
    assert p.default_runs_dir() == target


def test_default_runs_dir_prefers_cwd_runs(fake_home, monkeypatch, tmp_path):
    _force_platform(monkeypatch, "linux")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    assert p.default_runs_dir() == Path("runs")


def test_default_runs_dir_falls_back_to_data_dir(fake_home, monkeypatch, tmp_path):
    _force_platform(monkeypatch, "linux")
    monkeypatch.chdir(tmp_path)  # cwd has no ./runs
    assert p.default_runs_dir() == fake_home / ".local" / "share" / "usma" / "runs"


# --------------------------------------------------------------------------- #
# ODBC helpers
# --------------------------------------------------------------------------- #

def test_default_odbc_driver_prefers_18(monkeypatch):
    monkeypatch.setattr(p, "installed_odbc_drivers",
                        lambda: ["ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"])
    assert p.default_odbc_driver() == "ODBC Driver 18 for SQL Server"


def test_default_odbc_driver_falls_back_to_17(monkeypatch):
    monkeypatch.setattr(p, "installed_odbc_drivers",
                        lambda: ["ODBC Driver 17 for SQL Server"])
    assert p.default_odbc_driver() == "ODBC Driver 17 for SQL Server"


def test_default_odbc_driver_no_pyodbc(monkeypatch):
    monkeypatch.setattr(p, "installed_odbc_drivers", lambda: [])
    # No drivers visible → still return the canonical default so config
    # validation has a sensible string to display.
    assert p.default_odbc_driver() == "ODBC Driver 18 for SQL Server"


def test_odbc_install_hint_per_os(monkeypatch):
    _force_platform(monkeypatch, "linux")
    assert "apt-get" in p.odbc_install_hint()
    _force_platform(monkeypatch, "darwin")
    assert "brew" in p.odbc_install_hint()
    _force_platform(monkeypatch, "win32")
    assert "learn.microsoft.com" in p.odbc_install_hint()
