"""OS-aware helpers for paths, drivers, and install hints.

USMA targets Windows, Linux, and macOS. Default cache / config / data
locations follow the XDG Base Directory spec on Linux + macOS and the
Known Folders convention (``%LOCALAPPDATA%`` / ``%APPDATA%``) on Windows.
Callers can always override with an explicit env var (documented next to
each helper) so behaviour is predictable in CI and containers.

Nothing here imports ``pyodbc`` at module load — driver detection is
deferred to the function call so the module is safe to import on hosts
without the ODBC driver installed.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

_APP_NAME = "usma"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def _xdg(env_var: str, fallback: Path) -> Path:
    raw = os.environ.get(env_var, "").strip()
    if raw:
        return Path(raw).expanduser()
    return fallback


def cache_dir() -> Path:
    """Per-user cache directory (e.g. fabric pricing snapshot).

    Linux/macOS: ``$XDG_CACHE_HOME/usma`` (default ``~/.cache/usma``).
    Windows: ``%LOCALAPPDATA%\\USMA\\Cache``.
    """
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "USMA" / "Cache"
    return _xdg("XDG_CACHE_HOME", Path.home() / ".cache") / _APP_NAME


def config_dir() -> Path:
    """Per-user config directory.

    Linux/macOS: ``$XDG_CONFIG_HOME/usma`` (default ``~/.config/usma``).
    Windows: ``%APPDATA%\\USMA``.
    """
    if is_windows():
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "USMA"
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / _APP_NAME


def data_dir() -> Path:
    """Per-user data directory (runs, exports, persistent state).

    Linux/macOS: ``$XDG_DATA_HOME/usma`` (default ``~/.local/share/usma``).
    Windows: ``%LOCALAPPDATA%\\USMA``.
    """
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "USMA"
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share") / _APP_NAME


def default_runs_dir() -> Path:
    """Where ``sma serve --with-api`` stores runs when nothing is set.

    Resolution order:

    1. ``SMA_RUNS_DIR`` env var (explicit override; honoured everywhere).
    2. ``./runs`` if that directory already exists in the current working
       directory (preserves the historical repo-relative behaviour for
       users who launch from a clone).
    3. Otherwise the OS-appropriate per-user data dir
       (``data_dir() / "runs"``).
    """
    explicit = os.environ.get("SMA_RUNS_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    cwd_runs = Path("runs")
    if cwd_runs.is_dir():
        return cwd_runs
    return data_dir() / "runs"


# ---------------------------------------------------------------------------
# ODBC driver discovery
# ---------------------------------------------------------------------------

_PREFERRED_DRIVERS: tuple[str, ...] = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
)


def installed_odbc_drivers() -> list[str]:
    """Return installed Microsoft SQL ODBC driver names (empty if pyodbc missing)."""
    try:
        import pyodbc  # type: ignore
    except ImportError:
        return []
    return [d for d in pyodbc.drivers() if "ODBC Driver" in d and "SQL Server" in d]


def default_odbc_driver() -> str:
    """Pick the best available Microsoft SQL ODBC driver name.

    Prefers Driver 18, falls back to 17 if only 17 is installed. Returns
    Driver 18 as the canonical default when pyodbc isn't importable yet
    so config validation still has a sensible string to show.
    """
    installed = installed_odbc_drivers()
    if installed:
        for preferred in _PREFERRED_DRIVERS:
            if any(d.lower() == preferred.lower() for d in installed):
                return preferred
        return installed[0]
    return _PREFERRED_DRIVERS[0]


def odbc_install_hint() -> str:
    """One-line, OS-appropriate install command for the Microsoft ODBC driver."""
    if is_linux():
        return (
            "Install Microsoft ODBC Driver 18 for SQL Server on Ubuntu/Debian:\n"
            "  curl -fsSL https://packages.microsoft.com/keys/microsoft.asc "
            "| sudo gpg --dearmor -o /usr/share/keyrings/microsoft.gpg\n"
            "  echo \"deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft.gpg] "
            "https://packages.microsoft.com/ubuntu/$(lsb_release -rs)/prod $(lsb_release -cs) main\" "
            "| sudo tee /etc/apt/sources.list.d/mssql-release.list\n"
            "  sudo apt-get update && sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev\n"
            "Docs: https://learn.microsoft.com/sql/connect/odbc/linux-mac/"
            "installing-the-microsoft-odbc-driver-for-sql-server"
        )
    if is_macos():
        return (
            "Install Microsoft ODBC Driver 18 for SQL Server on macOS:\n"
            "  brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release\n"
            "  brew update && HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18 mssql-tools18\n"
            "Docs: https://learn.microsoft.com/sql/connect/odbc/linux-mac/"
            "install-microsoft-odbc-driver-sql-server-macos"
        )
    return (
        "Install Microsoft ODBC Driver 18 for SQL Server (Windows MSI): "
        "https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"
    )


def os_label() -> str:
    """Short, human-friendly OS label for log / banner output."""
    if is_windows():
        return f"Windows ({platform.release()})"
    if is_macos():
        return f"macOS ({platform.mac_ver()[0] or platform.release()})"
    if is_linux():
        try:
            import distro  # type: ignore
            return f"Linux ({distro.name(pretty=True)})"
        except ImportError:
            return f"Linux ({platform.release()})"
    return platform.platform()
